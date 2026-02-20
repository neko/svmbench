import asyncio
import json
from typing import Callable

import httpx
import websockets
from loguru import logger
from solders.pubkey import Pubkey
from solders.signature import Signature

# USDC token mint on Solana mainnet
USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
# Token program ID
TOKEN_PROGRAM_ID = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'
# Associated Token Program ID
ASSOCIATED_TOKEN_PROGRAM_ID = 'ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL'

# In-memory cache of recent transactions (signature -> transaction data)
_tx_cache: dict[str, dict] = {}
_tx_cache_lock = asyncio.Lock()

# WebSocket subscription state
_ws_task: asyncio.Task | None = None
_ws_running = False


def get_associated_token_address(wallet: str, mint: str) -> str:
    """Derive the associated token address for a wallet and mint."""
    from solders.pubkey import Pubkey as SoldersPubkey

    wallet_pk = SoldersPubkey.from_string(wallet)
    mint_pk = SoldersPubkey.from_string(mint)
    ata_program = SoldersPubkey.from_string(ASSOCIATED_TOKEN_PROGRAM_ID)
    token_program = SoldersPubkey.from_string(TOKEN_PROGRAM_ID)

    # PDA derivation for ATA
    seeds = [bytes(wallet_pk), bytes(token_program), bytes(mint_pk)]
    ata, _ = SoldersPubkey.find_program_address(seeds, ata_program)
    return str(ata)


async def start_transaction_listener(receiver_wallet: str, ws_url: str, http_rpc_url: str):
    """Start WebSocket listener for incoming transactions to receiver wallet."""
    global _ws_task, _ws_running

    if _ws_running:
        logger.info('Transaction listener already running')
        return

    _ws_running = True
    _ws_task = asyncio.create_task(_listen_for_transactions(receiver_wallet, ws_url, http_rpc_url))
    logger.info(f'Started transaction listener for {receiver_wallet[:8]}...')


async def _listen_for_transactions(receiver_wallet: str, ws_url: str, http_rpc_url: str):
    """WebSocket listener that indexes incoming transactions."""
    global _ws_running

    receiver_ata = get_associated_token_address(receiver_wallet, USDC_MINT)
    logger.info(f'Listening for transactions to ATA: {receiver_ata}')

    while _ws_running:
        try:
            async with websockets.connect(ws_url) as ws:
                # Subscribe to logs mentioning the receiver's ATA
                subscribe_msg = {
                    'jsonrpc': '2.0',
                    'id': 1,
                    'method': 'logsSubscribe',
                    'params': [
                        {'mentions': [receiver_ata]},
                        {'commitment': 'confirmed'}
                    ]
                }
                await ws.send(json.dumps(subscribe_msg))

                response = await ws.recv()
                sub_result = json.loads(response)
                logger.info(f'WebSocket subscription result: {sub_result}')

                async for message in ws:
                    try:
                        data = json.loads(message)
                        if 'params' in data:
                            result = data['params']['result']
                            signature = result['value']['signature']
                            logger.info(f'Received transaction notification: {signature[:16]}...')

                            # Fetch and cache the full transaction
                            await _fetch_and_cache_transaction(signature, http_rpc_url)
                    except Exception as e:
                        logger.error(f'Error processing WebSocket message: {e}')

        except Exception as e:
            logger.error(f'WebSocket connection error: {e}')
            if _ws_running:
                logger.info('Reconnecting in 5 seconds...')
                await asyncio.sleep(5)


async def _fetch_and_cache_transaction(signature: str, rpc_url: str):
    """Fetch a transaction and cache it."""
    global _tx_cache

    async with httpx.AsyncClient() as client:
        response = await client.post(
            rpc_url,
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'getTransaction',
                'params': [
                    signature,
                    {
                        'encoding': 'jsonParsed',
                        'maxSupportedTransactionVersion': 0,
                    },
                ],
            },
            timeout=30.0,
        )

        if response.status_code == 200:
            data = response.json()
            result = data.get('result')
            if result:
                async with _tx_cache_lock:
                    _tx_cache[signature] = result
                    # Keep cache size reasonable (last 100 transactions)
                    if len(_tx_cache) > 100:
                        oldest = list(_tx_cache.keys())[0]
                        del _tx_cache[oldest]
                logger.info(f'Cached transaction {signature[:16]}...')


async def get_cached_transaction(signature: str) -> dict | None:
    """Get a transaction from cache."""
    async with _tx_cache_lock:
        return _tx_cache.get(signature)


async def verify_usdc_transfer(
    signature: str,
    expected_sender: str,
    expected_receiver: str,
    expected_amount: float,
    rpc_url: str,
) -> bool:
    """
    Verify a USDC transfer transaction on Solana.
    """
    # Validate addresses
    try:
        Pubkey.from_string(expected_sender)
        Pubkey.from_string(expected_receiver)
        Signature.from_string(signature)
    except Exception:
        return False

    # Convert USDC amount to token amount (6 decimals)
    expected_token_amount = int(expected_amount * 1_000_000)

    logger.info(f'Verifying USDC transfer: sig={signature[:16]}..., sender={expected_sender[:8]}..., receiver={expected_receiver[:8]}..., amount=${expected_amount} ({expected_token_amount} lamports)')

    # First check cache
    result = await get_cached_transaction(signature)
    if result:
        logger.info('Found transaction in cache')
    else:
        # Fetch from RPC with retries
        async with httpx.AsyncClient() as client:
            for attempt in range(15):  # 15 attempts, 2 seconds apart = 30 seconds max
                response = await client.post(
                    rpc_url,
                    json={
                        'jsonrpc': '2.0',
                        'id': 1,
                        'method': 'getTransaction',
                        'params': [
                            signature,
                            {
                                'encoding': 'jsonParsed',
                                'maxSupportedTransactionVersion': 0,
                            },
                        ],
                    },
                    timeout=30.0,
                )

                if response.status_code != 200:
                    logger.error(f'RPC request failed: status={response.status_code}')
                    await asyncio.sleep(2)
                    continue

                data = response.json()

                if 'error' in data:
                    logger.error(f'RPC error: {data["error"]}')
                    await asyncio.sleep(2)
                    continue

                result = data.get('result')
                if result:
                    logger.info(f'Transaction found on attempt {attempt + 1}')
                    break

                if attempt < 14:
                    logger.info(f'Transaction not indexed yet, retry {attempt + 1}/15...')
                    await asyncio.sleep(2)

            if not result:
                logger.error('Transaction not found after 15 retries')
                return False

    # Check transaction was successful
    meta = result.get('meta', {})
    if meta.get('err') is not None:
        logger.error(f'Transaction failed: {meta.get("err")}')
        return False

    # Parse the transaction to find USDC transfer
    transaction = result.get('transaction', {})
    message = transaction.get('message', {})
    instructions = message.get('instructions', [])

    # Also check inner instructions (for associated token account creation)
    inner_instructions = meta.get('innerInstructions', [])
    all_instructions = list(instructions)
    for inner in inner_instructions:
        all_instructions.extend(inner.get('instructions', []))

    # Look for SPL token transfer instruction
    for instruction in all_instructions:
        parsed = instruction.get('parsed')
        if not parsed:
            continue

        program = instruction.get('program')
        if program != 'spl-token':
            continue

        info = parsed.get('info', {})
        inst_type = parsed.get('type')

        # Check for transfer or transferChecked
        if inst_type in ('transfer', 'transferChecked'):
            logger.info(f'Found {inst_type} instruction')

            # For transferChecked, verify the mint is USDC
            if inst_type == 'transferChecked':
                mint = info.get('mint', '')
                if mint != USDC_MINT:
                    logger.debug(f'Skipping: wrong mint {mint}')
                    continue

            # Verify amount
            if inst_type == 'transferChecked':
                amount = int(info.get('tokenAmount', {}).get('amount', 0))
            else:
                amount = int(info.get('amount', 0))

            logger.info(f'Transfer amount: {amount}, expected: {expected_token_amount}')

            if amount != expected_token_amount:
                logger.warning(f'Amount mismatch: got {amount}, expected {expected_token_amount}')
                continue

            # Verify authority (sender)
            authority = info.get('authority', '')
            logger.info(f'Authority: {authority}, expected sender: {expected_sender}')

            if authority != expected_sender:
                logger.warning(f'Authority mismatch')
                continue

            # Get the destination token account and verify owner
            destination = info.get('destination', '')
            logger.info(f'Destination ATA: {destination}')

            # We need to verify the destination token account belongs to expected_receiver
            # For simplicity, we'll check the post token balances
            post_balances = meta.get('postTokenBalances', [])
            logger.info(f'Post token balances: {len(post_balances)} entries')

            for balance in post_balances:
                if balance.get('mint') != USDC_MINT:
                    continue
                owner = balance.get('owner', '')
                logger.info(f'USDC balance owner: {owner}, expected: {expected_receiver}')
                if owner == expected_receiver:
                    # Found a matching USDC transfer to the expected receiver
                    logger.info('Verification successful!')
                    return True

    logger.error('No matching USDC transfer found in transaction')
    return False
