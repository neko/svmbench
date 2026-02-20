/**
 * x402 Solana Payment Helper Service
 *
 * Uses the official @x402/svm SDK to create properly formatted payment payloads
 * that work with x402's partial signing protocol.
 *
 * The Python oai_proxy calls this service when it needs to make x402 Solana payments.
 */

import express from 'express';
import { ExactSvmScheme, SOLANA_MAINNET_CAIP2 } from '@x402/svm';
import { x402Client, wrapFetchWithPayment } from '@x402/fetch';
import { createKeyPairSignerFromBytes } from '@solana/kit';

const app = express();
app.use(express.json());

const PORT = process.env.X402_HELPER_PORT || 8085;
const SOLANA_RPC_URL = process.env.SOLANA_RPC_URL || 'https://api.mainnet-beta.solana.com';

// Lazily initialized client
let paidFetch = null;
let svmSigner = null;

/**
 * Initialize the x402 client with Solana signer
 */
async function initClient() {
  if (paidFetch) return paidFetch;

  const privateKey = process.env.X402_SERVICE_WALLET_KEY;
  if (!privateKey) {
    throw new Error('X402_SERVICE_WALLET_KEY environment variable not set');
  }

  try {
    // Parse the private key - it's stored as a JSON array of bytes in base58
    // Solana keypair format: 64 bytes (32 private + 32 public)
    let keyBytes;

    // Check if it's a JSON array (like [1,2,3,...])
    if (privateKey.startsWith('[')) {
      keyBytes = Uint8Array.from(JSON.parse(privateKey));
    } else {
      // It's a base58 string - need to decode it
      // Import base58 decoder
      const { base58 } = await import('@scure/base');
      keyBytes = base58.decode(privateKey);
    }

    // Create Solana keypair signer
    const keypair = await createKeyPairSignerFromBytes(keyBytes);
    svmSigner = keypair;

    console.log(`[x402helper] Loaded wallet: ${keypair.address}`);

    // Create x402 SVM scheme
    const svmScheme = new ExactSvmScheme(keypair, { rpcUrl: SOLANA_RPC_URL });

    // Create x402 client and register Solana scheme
    const client = new x402Client();
    client.register('solana:*', svmScheme);

    // Create wrapped fetch that handles 402 automatically
    paidFetch = wrapFetchWithPayment(fetch, client, {
      paymentRequirementsSelector: (accepts) =>
        accepts.find(a => a.network?.startsWith('solana:')),
    });

    console.log('[x402helper] x402 client initialized');
    return paidFetch;
  } catch (err) {
    console.error('[x402helper] Failed to init client:', err);
    throw err;
  }
}

/**
 * Health check endpoint
 */
app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'x402helper' });
});

/**
 * Proxy endpoint - forwards request to x402 with automatic payment handling
 *
 * POST /proxy
 * Body: { url: string, method: string, headers: object, body: object }
 *
 * Returns the x402 response after handling payment automatically
 */
app.post('/proxy', async (req, res) => {
  try {
    const { url, method = 'POST', headers = {}, body } = req.body;

    if (!url) {
      return res.status(400).json({ error: 'url is required' });
    }

    console.log(`[x402helper] Proxying ${method} ${url}`);

    const fetch = await initClient();

    // Make the paid request
    const response = await fetch(url, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...headers,
      },
      body: body ? JSON.stringify(body) : undefined,
    });

    // Get response body
    const responseBody = await response.text();

    console.log(`[x402helper] Response: ${response.status}`);

    // Return response to caller
    res.status(response.status).set({
      'Content-Type': response.headers.get('content-type') || 'application/json',
    }).send(responseBody);

  } catch (err) {
    console.error('[x402helper] Proxy error:', err);
    res.status(500).json({
      error: 'x402 payment failed',
      details: err.message,
    });
  }
});

/**
 * Transform x402 response to OpenAI chat completions format
 * x402 returns: { content, model, usage }
 * OpenAI expects: { id, object, created, model, choices: [{ index, message, finish_reason }], usage }
 */
function toOpenAIFormat(x402Response, requestModel) {
  // If already has choices, return as-is
  if (x402Response.choices) {
    return x402Response;
  }

  // Transform x402 format to OpenAI format
  return {
    id: `chatcmpl-x402-${Date.now()}`,
    object: 'chat.completion',
    created: Math.floor(Date.now() / 1000),
    model: x402Response.model || requestModel,
    choices: [
      {
        index: 0,
        message: {
          role: 'assistant',
          content: x402Response.content || '',
        },
        finish_reason: 'stop',
      },
    ],
    usage: x402Response.usage || {
      prompt_tokens: 0,
      completion_tokens: 0,
      total_tokens: 0,
    },
  };
}

/**
 * Reset the x402 client (force re-initialization on next request)
 */
function resetClient() {
  paidFetch = null;
  svmSigner = null;
  console.log('[x402helper] Client reset, will re-initialize on next request');
}

/**
 * Make request with retry logic for transient errors
 */
async function fetchWithRetry(fetchFn, endpoint, options, maxRetries = 5) {
  let lastError;
  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      // Re-get fetch in case client was reset
      const fetch = await initClient();

      const response = await fetch(endpoint, options);

      // Retry on transient errors
      if (response.status === 429 || response.status === 408 || response.status === 502 || response.status === 503 || response.status === 504) {
        const retryAfter = response.headers.get('retry-after');
        const waitTime = retryAfter
          ? parseInt(retryAfter, 10) * 1000
          : Math.min(2000 * Math.pow(2, attempt), 30000); // Exponential backoff, max 30s

        console.log(`[x402helper] Got ${response.status}, waiting ${waitTime}ms before retry ${attempt + 1}/${maxRetries}`);
        await new Promise(resolve => setTimeout(resolve, waitTime));
        continue;
      }

      // On 402, the x402 SDK should have handled payment but didn't - reset and retry
      if (response.status === 402) {
        const waitTime = Math.min(3000 * Math.pow(2, attempt), 30000);
        console.log(`[x402helper] Payment failed (402), resetting client and retrying in ${waitTime}ms (attempt ${attempt + 1}/${maxRetries})`);
        resetClient();
        await new Promise(resolve => setTimeout(resolve, waitTime));
        continue;
      }

      return response;
    } catch (err) {
      lastError = err;
      const waitTime = Math.min(2000 * Math.pow(2, attempt), 30000);

      // Check if it's a retryable error
      if (err.message && (err.message.includes('429') || err.message.includes('timeout') || err.message.includes('ETIMEDOUT') || err.message.includes('ECONNRESET'))) {
        console.log(`[x402helper] SDK error: ${err.message}, waiting ${waitTime}ms before retry ${attempt + 1}/${maxRetries}`);
        await new Promise(resolve => setTimeout(resolve, waitTime));
        continue;
      }

      // On payment-related errors, reset client and retry
      if (err.message && (err.message.includes('402') || err.message.includes('payment'))) {
        console.log(`[x402helper] Payment error: ${err.message}, resetting client and retrying in ${waitTime}ms`);
        resetClient();
        await new Promise(resolve => setTimeout(resolve, waitTime));
        continue;
      }

      throw err;
    }
  }
  throw lastError || new Error('Max retries exceeded');
}

/**
 * Direct x402 chat completions endpoint
 * Simplified interface for LLM requests
 *
 * POST /v1/chat/completions
 * Body: standard OpenAI chat completions format with model field
 * Returns: OpenAI-compatible response format
 */
app.post('/v1/chat/completions', async (req, res) => {
  try {
    const body = req.body;
    const model = body.model || '';

    if (!model) {
      return res.status(400).json({ error: 'model is required' });
    }

    // Map model to x402 endpoint
    const endpoint = getX402Endpoint(model);
    console.log(`[x402helper] Chat completion: ${model} -> ${endpoint}`);

    const response = await fetchWithRetry(null, endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    const responseText = await response.text();
    console.log(`[x402helper] Response: ${response.status}`);

    // Transform response to OpenAI format if successful
    if (response.status === 200) {
      try {
        const x402Data = JSON.parse(responseText);
        const openaiData = toOpenAIFormat(x402Data, model);
        return res.status(200).json(openaiData);
      } catch (parseErr) {
        console.error('[x402helper] Failed to parse response:', parseErr);
        // Return raw response if parsing fails
        return res.status(response.status).set({
          'Content-Type': 'application/json',
        }).send(responseText);
      }
    }

    // Return error responses as-is
    res.status(response.status).set({
      'Content-Type': response.headers.get('content-type') || 'application/json',
    }).send(responseText);

  } catch (err) {
    console.error('[x402helper] Chat error:', err);
    res.status(500).json({
      error: 'x402 payment failed',
      details: err.message,
    });
  }
});

/**
 * Map model ID to x402 gateway endpoint
 */
function getX402Endpoint(model) {
  const BASE_URL = 'https://x402-gateway-production.up.railway.app/api';

  // x402 native model IDs (llm-* format)
  const modelMap = {
    'llm-gpt-5.2-codex': 'llm/gpt-5.2-codex',
    'llm-gpt-5.2': 'llm/gpt-5.2',
    'llm-claude-opus': 'llm/claude-opus',
    'llm-claude-sonnet': 'llm/claude-sonnet',
    'llm-claude-haiku': 'llm/claude-haiku',
    'llm-deepseek': 'llm/deepseek-v3',
    'llm-deepseek-r1': 'llm/deepseek-r1',
    'llm-gemini-pro': 'llm/gemini-pro',
    'llm-gemini-flash': 'llm/gemini-flash',
    'llm-grok': 'llm/grok',
  };

  const path = modelMap[model];
  if (path) {
    return `${BASE_URL}/${path}`;
  }

  // Fallback: construct from model name
  let cleanModel = model.replace(/\//g, '-').replace(/_/g, '-');
  if (cleanModel.startsWith('llm-')) {
    cleanModel = cleanModel.slice(4);
  }
  return `${BASE_URL}/llm/${cleanModel}`;
}

// Start server
app.listen(PORT, '0.0.0.0', () => {
  console.log(`[x402helper] Server running on port ${PORT}`);
  console.log(`[x402helper] RPC: ${SOLANA_RPC_URL}`);
});
