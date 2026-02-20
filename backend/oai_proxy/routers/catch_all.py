from collections.abc import AsyncIterator, Iterable
from functools import lru_cache
from urllib.parse import quote
import asyncio
import json
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from loguru import logger
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from api.util.aes_gcm import decrypt_token, derive_key
from oai_proxy.core.config import settings

# x402 payment support (optional - only loaded if configured)
_x402_keypair = None
_x402_lock = asyncio.Lock()


PROVIDER_BASE_URLS = {
    'openai': 'https://api.openai.com',
    'openrouter': 'https://openrouter.ai/api',
    'x402': settings.X402_GATEWAY_URL,
}
DEFAULT_PROVIDER = 'openai'
# Marker token that triggers use of the static key
STATIC_KEY_MARKER = 'STATIC'
# Marker token that triggers x402 paid mode
X402_MARKER = 'X402'

# x402 model endpoint mapping (model ID -> endpoint path)
X402_MODEL_ENDPOINTS = {
    # x402 native model IDs (llm-* format)
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
    'llm-kimi': 'llm/kimi',
    'llm-minimax': 'llm/minimax',
    'llm-glm': 'llm/glm',
    'llm-llama': 'llm/llama',
    'llm-qwen': 'llm/qwen',
    'llm-mistral': 'llm/mistral',
    # Legacy OpenAI/Anthropic-style model IDs
    'gpt-5.2-codex': 'llm/gpt-5.2-codex',
    'gpt-5.2-2025-12-11': 'llm/gpt-5.2-codex',
    'codex-gpt-5.2': 'llm/gpt-5.2-codex',
    'claude-opus-4-6': 'llm/claude-opus',
    'anthropic/claude-opus-4-6': 'llm/claude-opus',
    'anthropic/claude-opus-4-5': 'llm/claude-opus',
    'claude-sonnet-4-6': 'llm/claude-sonnet',
    'anthropic/claude-sonnet-4-6': 'llm/claude-sonnet',
    'anthropic/claude-sonnet-4-5': 'llm/claude-sonnet',
    'deepseek-chat-v3': 'llm/deepseek-v3',
    'deepseek/deepseek-chat-v3-0324': 'llm/deepseek-v3',
    'deepseek/deepseek-chat': 'llm/deepseek-v3',
}
HOP_BY_HOP_HEADERS = {
    'connection',
    'host',
    'keep-alive',
    'proxy-authenticate',
    'proxy-authorization',
    'te',
    'trailers',
    'transfer-encoding',
    'upgrade',
}

router = APIRouter()


@lru_cache(maxsize=1)
def _aesgcm_key() -> bytes:
    return derive_key(settings.OAI_PROXY_AES_KEY.get_secret_value())


@lru_cache(maxsize=1)
def _get_static_key() -> str | None:
    if settings.OAI_PROXY_STATIC_KEY:
        return settings.OAI_PROXY_STATIC_KEY.get_secret_value()
    return None


def _decrypt_token(token: str) -> str:
    try:
        return decrypt_token(token, key=_aesgcm_key())
    except ValueError as err:
        raise HTTPException(status_code=401, detail='Invalid token') from err


def _resolve_openai_key(token: str) -> str:
    """Resolve the actual OpenAI key from the provided token.

    If token is STATIC_KEY_MARKER, use the static key (if configured).
    If token is X402_MARKER, return marker (handled separately).
    Otherwise, decrypt the encrypted token.
    """
    if token == STATIC_KEY_MARKER:
        static_key = _get_static_key()
        if not static_key:
            raise HTTPException(
                status_code=501,
                detail='Static key not configured on proxy',
            )
        return static_key
    if token == X402_MARKER:
        # x402 mode - no API key needed, we pay per request
        return X402_MARKER
    return _decrypt_token(token)


async def _get_x402_keypair():
    """Get or initialize x402 service wallet keypair."""
    global _x402_keypair

    async with _x402_lock:
        if _x402_keypair is not None:
            return _x402_keypair

        wallet_key = settings.X402_SERVICE_WALLET_KEY
        if not wallet_key:
            return None

        try:
            from solders.keypair import Keypair
            _x402_keypair = Keypair.from_base58_string(wallet_key.get_secret_value())
            logger.info(f'x402 service wallet loaded: {_x402_keypair.pubkey()}')
            return _x402_keypair
        except Exception as e:
            logger.warning(f'Failed to load x402 wallet: {e}')
            return None


def _get_x402_endpoint_for_model(model: str) -> str:
    """Get x402 API endpoint for a model."""
    endpoint = X402_MODEL_ENDPOINTS.get(model)
    if endpoint:
        return f"{settings.X402_GATEWAY_URL}/api/{endpoint}"

    # Fallback: try to construct from model name
    # Strip llm- prefix if present to avoid llm/llm-xxx
    clean_model = model.replace('/', '-').replace('_', '-')
    if clean_model.startswith('llm-'):
        clean_model = clean_model[4:]  # Remove 'llm-' prefix
    return f"{settings.X402_GATEWAY_URL}/api/llm/{clean_model}"


def _parse_x402_payment_required(response: httpx.Response) -> dict | None:
    """Parse x402 PAYMENT-REQUIRED header to extract payment info."""
    import base64

    payment_header = response.headers.get('payment-required')
    if not payment_header:
        return None

    try:
        decoded = base64.b64decode(payment_header)
        return json.loads(decoded)
    except Exception as e:
        logger.error(f'Failed to parse PAYMENT-REQUIRED header: {e}')
        return None


def _get_solana_payment_option(payment_info: dict) -> dict | None:
    """Extract Solana payment option from x402 accepts array."""
    accepts = payment_info.get('accepts', [])
    for option in accepts:
        network = option.get('network', '')
        # Solana mainnet: solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp
        if network.startswith('solana:'):
            return option
    return None


async def _make_x402_payment(amount_raw: str, receiver: str, asset: str, fee_payer: str | None = None) -> str:
    """Execute USDC payment for x402 and return transaction signature."""
    keypair = await _get_x402_keypair()
    if not keypair:
        raise HTTPException(
            status_code=501,
            detail='x402 service wallet not configured',
        )

    try:
        from solana.rpc.async_api import AsyncClient
        from solders.pubkey import Pubkey
        from solders.transaction import Transaction
        from spl.token.instructions import TransferParams, transfer, get_associated_token_address

        # USDC mint and token program
        USDC_MINT = Pubkey.from_string('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v')
        TOKEN_PROGRAM_ID = Pubkey.from_string('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA')

        # Amount is in USDC micro-units (6 decimals)
        amount_lamports = int(amount_raw)
        amount_usd = amount_lamports / 1_000_000

        receiver_pubkey = Pubkey.from_string(receiver)

        logger.info(f'x402 payment: ${amount_usd:.4f} USDC to {receiver}')

        # Get ATAs (synchronous function)
        payer_ata = get_associated_token_address(keypair.pubkey(), USDC_MINT)
        receiver_ata = get_associated_token_address(receiver_pubkey, USDC_MINT)

        rpc_url = settings.PAYMENT_SOLANA_RPC_URL
        async with AsyncClient(rpc_url) as client:
            # Create transfer instruction
            transfer_ix = transfer(
                TransferParams(
                    program_id=TOKEN_PROGRAM_ID,
                    source=payer_ata,
                    dest=receiver_ata,
                    owner=keypair.pubkey(),
                    amount=amount_lamports,
                )
            )

            # Build and sign transaction
            recent_blockhash = await client.get_latest_blockhash()
            tx = Transaction.new_signed_with_payer(
                [transfer_ix],
                keypair.pubkey(),
                [keypair],
                recent_blockhash.value.blockhash,
            )

            # Send transaction
            result = await client.send_transaction(tx)
            signature = str(result.value)

            # Wait for confirmation
            await client.confirm_transaction(signature, 'confirmed')

            logger.info(f'x402 payment sent: {signature}')
            return signature

    except Exception as e:
        logger.error(f'x402 payment failed: {e}')
        raise HTTPException(
            status_code=502,
            detail=f'x402 payment failed: {e}',
        ) from e


async def _proxy_x402_request(
    request: Request,
    model: str,
    body: dict,
) -> Response:
    """Proxy a request through x402 with payment."""
    import base64

    endpoint = _get_x402_endpoint_for_model(model)

    keypair = await _get_x402_keypair()
    if not keypair:
        raise HTTPException(
            status_code=501,
            detail='x402 service wallet not configured for paid requests',
        )

    headers = {
        'Content-Type': 'application/json',
        'X-Payer-Wallet': str(keypair.pubkey()),
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, read=None)) as client:
        # Initial request to x402
        logger.info(f'x402 request to {endpoint} for model {model}')
        response = await client.post(endpoint, json=body, headers=headers)

        # Handle 402 Payment Required
        if response.status_code == 402:
            # Parse PAYMENT-REQUIRED header (x402 protocol v2)
            payment_info = _parse_x402_payment_required(response)
            if not payment_info:
                raise HTTPException(
                    status_code=502,
                    detail='x402 returned 402 but no PAYMENT-REQUIRED header',
                )

            # Get Solana payment option
            solana_option = _get_solana_payment_option(payment_info)
            if not solana_option:
                raise HTTPException(
                    status_code=502,
                    detail='x402 does not accept Solana payments',
                )

            amount_raw = solana_option.get('amount', '0')
            receiver = solana_option.get('payTo', '')
            asset = solana_option.get('asset', '')
            fee_payer = solana_option.get('extra', {}).get('feePayer')

            amount_usd = int(amount_raw) / 1_000_000
            logger.info(f'x402 requires payment: ${amount_usd:.4f} USDC for {model}')

            # Make USDC payment on Solana
            signature = await _make_x402_payment(amount_raw, receiver, asset, fee_payer)

            # Build PAYMENT-RESPONSE header (x402 protocol v2)
            # Format: base64 encoded JSON with network, payload (signature), and payer
            payment_response = {
                'x402Version': 2,
                'network': solana_option.get('network'),
                'payload': signature,
                'payer': str(keypair.pubkey()),
            }
            payment_response_b64 = base64.b64encode(
                json.dumps(payment_response).encode()
            ).decode()

            # Retry with payment proof
            headers['Payment-Response'] = payment_response_b64

            logger.info(f'x402 retrying with payment proof: {signature[:20]}...')
            response = await client.post(endpoint, json=body, headers=headers)

        if response.status_code != 200:
            logger.warning(f'x402 response: {response.status_code} - {response.text[:500]}')
            return Response(
                content=response.content,
                status_code=response.status_code,
                media_type='application/json',
            )

        return Response(
            content=response.content,
            status_code=200,
            media_type='application/json',
        )


def _get_authorization_token(request: Request) -> str:
    auth_header = request.headers.get('authorization')
    if not auth_header:
        raise HTTPException(status_code=401, detail='Missing Authorization header')
    scheme, _, token = auth_header.partition(' ')
    if scheme.lower() != 'bearer' or not token:
        raise HTTPException(status_code=401, detail='Invalid Authorization header')
    return token


def _filter_headers(items: Iterable[tuple[str, str]]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in items:
        key_lower = key.lower()
        if key_lower in HOP_BY_HOP_HEADERS:
            continue
        if key_lower == 'content-length':
            continue
        headers[key_lower] = value
    return headers


def _get_provider_from_request(request: Request, path: str) -> tuple[str, str, str]:
    """Extract provider from path prefix or query params.

    Supports both:
      /provider/openrouter/v1/responses  (path-based, preferred)
      /v1/responses?provider=openrouter  (query-based, legacy)

    Returns (provider_name, provider_base_url, cleaned_path).
    """
    # Check path-based provider first: /provider/<name>/...
    if path.startswith('provider/'):
        parts = path.split('/', 2)
        if len(parts) >= 2:
            provider = parts[1].lower()
            cleaned_path = parts[2] if len(parts) > 2 else ''
            base_url = PROVIDER_BASE_URLS.get(provider, PROVIDER_BASE_URLS[DEFAULT_PROVIDER])
            return provider, base_url, cleaned_path

    # Fall back to query param
    provider = request.query_params.get('provider', DEFAULT_PROVIDER).lower()
    base_url = PROVIDER_BASE_URLS.get(provider, PROVIDER_BASE_URLS[DEFAULT_PROVIDER])
    return provider, base_url, path


def _filter_query_params(params: dict) -> dict:
    """Remove internal params like 'provider' from forwarded query."""
    return {k: v for k, v in params.items() if k != 'provider'}


def _extract_content_text(content) -> str:
    """Extract text from Responses API content which can be string or array."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Content is an array of content parts
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if part.get('type') in ('input_text', 'text', 'output_text'):
                    parts.append(part.get('text', ''))
                elif 'text' in part:
                    parts.append(part['text'])
        return '\n'.join(parts)
    return ''


def _responses_to_chat_request(body: dict) -> dict:
    """Convert Responses API request to Chat Completions format."""
    messages = []

    # Handle instructions (system prompt)
    if 'instructions' in body and body['instructions']:
        messages.append({'role': 'system', 'content': body['instructions']})

    # Handle input - can be string or array of message objects
    input_val = body.get('input')
    if isinstance(input_val, str):
        messages.append({'role': 'user', 'content': input_val})
    elif isinstance(input_val, list):
        # Input can be an array of content items or messages
        for item in input_val:
            if isinstance(item, dict):
                if item.get('type') == 'message' and 'role' in item:
                    # It's a Responses API message object
                    role = item['role']
                    # Map developer role to system (Chat Completions doesn't have developer)
                    if role == 'developer':
                        role = 'system'
                    content = _extract_content_text(item.get('content', ''))
                    messages.append({'role': role, 'content': content})
                elif 'role' in item:
                    # Legacy message format
                    role = item['role']
                    if role == 'developer':
                        role = 'system'
                    content = _extract_content_text(item.get('content', ''))
                    messages.append({'role': role, 'content': content})
                elif item.get('type') == 'input_text':
                    messages.append({'role': 'user', 'content': item.get('text', '')})
                elif item.get('type') == 'text':
                    messages.append({'role': 'user', 'content': item.get('text', '')})
            elif isinstance(item, str):
                messages.append({'role': 'user', 'content': item})

    chat_body = {
        'model': body.get('model'),
        'messages': messages,
    }

    # Copy over common parameters
    if 'temperature' in body:
        chat_body['temperature'] = body['temperature']
    if 'max_output_tokens' in body:
        chat_body['max_tokens'] = body['max_output_tokens']
    if 'max_tokens' in body:
        chat_body['max_tokens'] = body['max_tokens']
    if 'stream' in body:
        chat_body['stream'] = body['stream']
    if 'top_p' in body:
        chat_body['top_p'] = body['top_p']
    if 'stop' in body:
        chat_body['stop'] = body['stop']

    # Handle tools - Responses API uses flat structure, Chat Completions uses nested
    # Responses API: {"type": "function", "name": "...", "description": "...", "parameters": {...}}
    # Chat Completions: {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
    if 'tools' in body and body['tools']:
        chat_tools = []
        for tool in body['tools']:
            if 'function' in tool and isinstance(tool['function'], dict):
                # Already in Chat Completions format
                chat_tools.append(tool)
            elif tool.get('type') == 'function' and 'name' in tool:
                # Responses API format - convert to Chat Completions
                func_def = {
                    'name': tool['name'],
                }
                if 'description' in tool:
                    func_def['description'] = tool['description']
                if 'parameters' in tool:
                    func_def['parameters'] = tool['parameters']
                chat_tools.append({
                    'type': 'function',
                    'function': func_def,
                })
            elif 'name' in tool:
                # Simple function definition
                func_def = {
                    'name': tool['name'],
                }
                if 'description' in tool:
                    func_def['description'] = tool['description']
                if 'parameters' in tool:
                    func_def['parameters'] = tool['parameters']
                chat_tools.append({
                    'type': 'function',
                    'function': func_def,
                })
        if chat_tools:
            chat_body['tools'] = chat_tools

    return chat_body


def _chat_to_responses_response(chat_resp: dict) -> dict:
    """Convert Chat Completions response to Responses API format."""
    response_id = f"resp_{uuid.uuid4().hex[:24]}"
    created_at = chat_resp.get('created', int(time.time()))

    output = []
    choices = chat_resp.get('choices', [])

    for choice in choices:
        message = choice.get('message', {})
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"

        # Handle text content as a message output item
        if message.get('content'):
            content_items = [{
                'type': 'output_text',
                'text': message['content'],
            }]
            output.append({
                'type': 'message',
                'id': msg_id,
                'status': 'completed',
                'role': message.get('role', 'assistant'),
                'content': content_items,
            })

        # Handle tool calls as separate function_call output items
        tool_calls = message.get('tool_calls', [])
        for tc in tool_calls:
            call_id = tc.get('id', f"call_{uuid.uuid4().hex[:24]}")
            output.append({
                'type': 'function_call',
                'id': call_id,
                'call_id': call_id,
                'name': tc.get('function', {}).get('name', ''),
                'arguments': tc.get('function', {}).get('arguments', '{}'),
                'status': 'completed',
            })

    # Map finish reason
    finish_reason = choices[0].get('finish_reason', 'stop') if choices else 'stop'
    status = 'completed'
    if finish_reason == 'tool_calls':
        status = 'completed'
    elif finish_reason == 'length':
        status = 'incomplete'

    return {
        'id': response_id,
        'object': 'response',
        'created_at': created_at,
        'status': status,
        'output': output,
        'usage': chat_resp.get('usage', {}),
        'model': chat_resp.get('model', ''),
    }


async def _chat_stream_to_responses_stream(
    response: httpx.Response,
    client: httpx.AsyncClient,
) -> AsyncIterator[bytes]:
    """Convert streaming Chat Completions to streaming Responses format.

    Emits events in the Responses API streaming format:
    - response.created: Initial response object
    - response.output_item.added: When output item is added
    - response.content_part.added: When content part is added
    - response.output_text.delta: Text deltas
    - response.content_part.done: Content part completed
    - response.output_item.done: Item completed
    - response.completed: Final completion event
    """
    response_id = f"resp_{uuid.uuid4().hex[:24]}"
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    content_part_id = f"cp_{uuid.uuid4().hex[:24]}"
    created_at = int(time.time())
    model = ''
    accumulated_content = ''
    accumulated_tool_calls: dict[int, dict] = {}
    first_chunk = True

    try:
        async for line in response.aiter_lines():
            if not line or not line.startswith('data: '):
                continue

            data = line[6:]  # Remove 'data: ' prefix
            if data == '[DONE]':
                # Build output items - messages and tool calls are separate items
                output_items = []
                output_idx = 0

                # If there's text content, create a message output item
                if accumulated_content:
                    content_items = [{
                        'type': 'output_text',
                        'text': accumulated_content,
                    }]
                    # Emit content_part.done
                    part_done = {
                        'type': 'response.content_part.done',
                        'item_id': msg_id,
                        'output_index': output_idx,
                        'content_index': 0,
                        'part': {
                            'type': 'output_text',
                            'text': accumulated_content,
                        },
                    }
                    yield f"data: {json.dumps(part_done)}\n\n".encode()

                    # Emit output_item.done for message
                    msg_item = {
                        'type': 'message',
                        'id': msg_id,
                        'status': 'completed',
                        'role': 'assistant',
                        'content': content_items,
                    }
                    item_done = {
                        'type': 'response.output_item.done',
                        'output_index': output_idx,
                        'item': msg_item,
                    }
                    yield f"data: {json.dumps(item_done)}\n\n".encode()
                    output_items.append(msg_item)
                    output_idx += 1

                # Add function_call output items for each tool call
                for tc in accumulated_tool_calls.values():
                    call_id = tc.get('id', f"call_{uuid.uuid4().hex[:24]}")
                    func_call_item = {
                        'type': 'function_call',
                        'id': call_id,
                        'call_id': call_id,
                        'name': tc.get('name', ''),
                        'arguments': tc.get('arguments', '{}'),
                        'status': 'completed',
                    }
                    # Emit output_item.done for function call
                    item_done = {
                        'type': 'response.output_item.done',
                        'output_index': output_idx,
                        'item': func_call_item,
                    }
                    yield f"data: {json.dumps(item_done)}\n\n".encode()
                    output_items.append(func_call_item)
                    output_idx += 1

                # Emit response.completed
                completed_event = {
                    'type': 'response.completed',
                    'response': {
                        'id': response_id,
                        'object': 'response',
                        'created_at': created_at,
                        'status': 'completed',
                        'model': model,
                        'output': output_items,
                    },
                }
                yield f"data: {json.dumps(completed_event)}\n\n".encode()
                break

            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            model = chunk.get('model', model)
            choices = chunk.get('choices', [])
            if not choices:
                continue

            # On first chunk with content, emit setup events
            if first_chunk:
                first_chunk = False
                # Emit response.created
                created_event = {
                    'type': 'response.created',
                    'response': {
                        'id': response_id,
                        'object': 'response',
                        'created_at': created_at,
                        'status': 'in_progress',
                        'model': model,
                        'output': [],
                    },
                }
                yield f"data: {json.dumps(created_event)}\n\n".encode()

                # Emit output_item.added
                item_added = {
                    'type': 'response.output_item.added',
                    'output_index': 0,
                    'item': {
                        'type': 'message',
                        'id': msg_id,
                        'status': 'in_progress',
                        'role': 'assistant',
                        'content': [],
                    },
                }
                yield f"data: {json.dumps(item_added)}\n\n".encode()

                # Emit content_part.added
                part_added = {
                    'type': 'response.content_part.added',
                    'item_id': msg_id,
                    'output_index': 0,
                    'content_index': 0,
                    'part': {
                        'type': 'output_text',
                        'text': '',
                    },
                }
                yield f"data: {json.dumps(part_added)}\n\n".encode()

            delta = choices[0].get('delta', {})

            # Handle content deltas
            if delta.get('content'):
                accumulated_content += delta['content']
                # Emit a streaming delta event
                delta_event = {
                    'type': 'response.output_text.delta',
                    'item_id': msg_id,
                    'output_index': 0,
                    'content_index': 0,
                    'delta': delta['content'],
                }
                yield f"data: {json.dumps(delta_event)}\n\n".encode()

            # Handle tool call deltas
            if delta.get('tool_calls'):
                for tc in delta['tool_calls']:
                    idx = tc.get('index', 0)
                    if idx not in accumulated_tool_calls:
                        accumulated_tool_calls[idx] = {
                            'id': tc.get('id', ''),
                            'name': tc.get('function', {}).get('name', ''),
                            'arguments': '',
                        }
                    if tc.get('id'):
                        accumulated_tool_calls[idx]['id'] = tc['id']
                    if tc.get('function', {}).get('name'):
                        accumulated_tool_calls[idx]['name'] = tc['function']['name']
                    if tc.get('function', {}).get('arguments'):
                        accumulated_tool_calls[idx]['arguments'] += tc['function']['arguments']
    finally:
        await response.aclose()
        await client.aclose()


async def _proxy_responses_to_chat(
    request: Request,
    base_url: str,
    forward_headers: dict[str, str],
    filtered_params: dict,
) -> Response:
    """Proxy a Responses API request to Chat Completions API."""
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail='Invalid JSON body')

    logger.debug(f"Responses API request: model={body.get('model')}, stream={body.get('stream')}")
    chat_body = _responses_to_chat_request(body)
    logger.debug(f"Translated to Chat: messages={len(chat_body.get('messages', []))}, tools={len(chat_body.get('tools', []))}")
    is_streaming = chat_body.get('stream', False)

    target_url = f"{base_url}/v1/chat/completions"

    # Don't use context manager for streaming - we need to keep the connection alive
    client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None))
    try:
        upstream = await client.send(
            client.build_request(
                'POST',
                target_url,
                params=filtered_params,
                headers=forward_headers,
                json=chat_body,
            ),
            stream=True,
        )

        if upstream.status_code != 200:
            # Pass through error response
            error_body = await upstream.aread()
            await upstream.aclose()
            await client.aclose()
            return Response(
                content=error_body,
                status_code=upstream.status_code,
                headers=dict(upstream.headers),
            )

        if is_streaming:
            # Client and response cleanup handled by the generator
            return StreamingResponse(
                _chat_stream_to_responses_stream(upstream, client),
                status_code=200,
                media_type='text/event-stream',
            )
        else:
            chat_response = await upstream.aread()
            await upstream.aclose()
            await client.aclose()
            try:
                chat_data = json.loads(chat_response)
            except json.JSONDecodeError:
                return Response(content=chat_response, status_code=200)

            responses_data = _chat_to_responses_response(chat_data)
            return Response(
                content=json.dumps(responses_data),
                status_code=200,
                media_type='application/json',
            )
    except Exception:
        await client.aclose()
        raise


async def _proxy_request(request: Request, path: str) -> Response:
    token = _get_authorization_token(request)
    openai_key = _resolve_openai_key(token)
    provider, base_url, cleaned_path = _get_provider_from_request(request, path)

    # Handle x402 provider - paid mode
    if provider == 'x402' or openai_key == X402_MARKER:
        # Parse request body to get model
        body_bytes = await request.body()
        try:
            body = json.loads(body_bytes)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail='Invalid JSON body')

        model = body.get('model', '')
        if not model:
            raise HTTPException(status_code=400, detail='Model is required for x402 requests')

        # Route to x402 paid endpoint
        return await _proxy_x402_request(request, model, body)

    forward_headers = _filter_headers(request.headers.items())
    forward_headers['authorization'] = f'Bearer {openai_key}'

    # Add OpenRouter-specific headers if routing to OpenRouter
    if 'openrouter.ai' in base_url:
        forward_headers['http-referer'] = 'https://svmbench.io'
        forward_headers['x-title'] = 'svmbench'

    # Filter out internal params
    filtered_params = _filter_query_params(dict(request.query_params))

    # Check if this is a Responses API request going to OpenRouter
    # OpenRouter doesn't fully support Responses API, so translate to Chat Completions
    target_path = cleaned_path.lstrip('/')
    if provider == 'openrouter' and target_path.startswith('v1/responses'):
        return await _proxy_responses_to_chat(
            request, base_url, forward_headers, filtered_params
        )

    encoded_path = quote(target_path, safe='/')
    target_url = f'{base_url}/{encoded_path}' if encoded_path else base_url

    body = request.stream()

    client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None))
    upstream = await client.send(
        client.build_request(
            request.method,
            target_url,
            params=filtered_params,
            headers=forward_headers,
            content=body,
        ),
        stream=True,
    )

    response_headers = _filter_headers(upstream.headers.items())

    async def _cleanup() -> None:
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(_cleanup),
    )


@router.api_route('/', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'])
@router.api_route('/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'])
async def proxy_all(request: Request, path: str = '') -> Response:
    return await _proxy_request(request, path)
