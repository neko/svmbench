import express, { Request, Response } from 'express';
import { config, validateConfig } from './config.js';
import {
  bridgeSolanaToBase,
  getBridgeQuote,
  getBridgeStatus,
  getSolanaUsdcBalance,
  verifyPaymentReceived,
} from './bridge.js';
import crypto from 'crypto';

const app = express();
app.use(express.json());

// Health check
app.get('/health', (_req: Request, res: Response) => {
  res.json({ status: 'ok' });
});

// Get bridge quote
app.post('/quote', async (req: Request, res: Response) => {
  try {
    const { amount } = req.body;
    if (!amount || typeof amount !== 'number' || amount <= 0) {
      res.status(400).json({ error: 'Invalid amount' });
      return;
    }

    const quotes = await getBridgeQuote(amount);
    if (!quotes || quotes.length === 0) {
      res.status(404).json({ error: 'No bridge route available' });
      return;
    }

    const quote = quotes[0];
    res.json({
      expectedAmountOut: quote.expectedAmountOut,
      etaSeconds: quote.etaSeconds,
      bridgeFee: quote.bridgeFee,
      type: quote.type,
      slippageBps: quote.slippageBps,
    });
  } catch (error) {
    console.error('[Quote Error]', error);
    res.status(500).json({
      error: error instanceof Error ? error.message : 'Failed to get quote',
    });
  }
});

// Verify payment and initiate bridge
app.post('/bridge', async (req: Request, res: Response) => {
  try {
    const { txSignature, amount } = req.body;

    if (!txSignature || typeof txSignature !== 'string') {
      res.status(400).json({ error: 'Missing txSignature' });
      return;
    }
    if (!amount || typeof amount !== 'number' || amount <= 0) {
      res.status(400).json({ error: 'Invalid amount' });
      return;
    }

    // Generate operation ID
    const operationId = crypto.randomUUID();

    // Verify the payment was received
    const verification = await verifyPaymentReceived(txSignature, amount);
    if (!verification.verified) {
      res.status(400).json({
        error: 'Payment verification failed',
        details: verification.error,
      });
      return;
    }

    console.log(`[Bridge] Payment verified: ${txSignature}, amount: ${verification.actualAmount}`);

    // Initiate bridge (async - don't wait for completion)
    bridgeSolanaToBase(verification.actualAmount || amount, operationId)
      .then((result) => {
        console.log(`[Bridge] Operation ${operationId} completed:`, result.success);
      })
      .catch((error) => {
        console.error(`[Bridge] Operation ${operationId} failed:`, error);
      });

    res.json({
      operationId,
      status: 'pending',
      message: 'Bridge operation initiated',
    });
  } catch (error) {
    console.error('[Bridge Error]', error);
    res.status(500).json({
      error: error instanceof Error ? error.message : 'Bridge failed',
    });
  }
});

// Check bridge status
app.get('/status/:operationId', (req: Request, res: Response) => {
  const operationId = req.params.operationId as string;
  const status = getBridgeStatus(operationId);

  if (!status) {
    res.status(404).json({ error: 'Operation not found' });
    return;
  }

  res.json(status);
});

// Get wallet balances
app.get('/balance', async (_req: Request, res: Response) => {
  try {
    const solanaUsdc = await getSolanaUsdcBalance();
    res.json({
      solana: {
        wallet: config.solanaWalletAddress,
        usdc: solanaUsdc,
      },
      base: {
        wallet: config.baseWalletAddress,
      },
    });
  } catch (error) {
    res.status(500).json({
      error: error instanceof Error ? error.message : 'Failed to get balance',
    });
  }
});

// Get service wallet addresses (for frontend to know where to send USDC)
app.get('/wallets', (_req: Request, res: Response) => {
  res.json({
    solana: config.solanaWalletAddress,
    base: config.baseWalletAddress,
  });
});

// Start server
function start() {
  try {
    validateConfig();
  } catch (error) {
    console.error('Configuration error:', error);
    process.exit(1);
  }

  app.listen(config.port, () => {
    console.log(`Bridge helper listening on port ${config.port}`);
    console.log(`Solana wallet: ${config.solanaWalletAddress}`);
    console.log(`Base wallet: ${config.baseWalletAddress}`);
  });
}

start();
