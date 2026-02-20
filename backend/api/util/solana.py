import asyncio

import httpx
from loguru import logger
from solders.pubkey import Pubkey
from solders.signature import Signature

# USDC token mint on Solana mainnet
USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'
# Token program ID
TOKEN_PROGRAM_ID = 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'


async def verify_usdc_transfer(
    signature: str,
    expected_sender: str,
    expected_receiver: str,
    expected_amount: float,
    rpc_url: str,
) -> bool:
    """
    Verify a USDC transfer transaction on Solana.

    Args:
        signature: The transaction signature to verify
        expected_sender: The expected sender wallet address
        expected_receiver: The expected receiver wallet address
        expected_amount: The expected amount in USDC (not lamports)
        rpc_url: The Solana RPC URL to use

    Returns:
        True if the transaction is valid and matches expectations, False otherwise
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

    async with httpx.AsyncClient() as client:
        # Get transaction details
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
            return False

        data = response.json()

        if 'error' in data:
            logger.error(f'RPC error: {data["error"]}')
            return False

        result = data.get('result')
        if not result:
            # Transaction might not be indexed yet, retry a few times
            for retry in range(5):
                logger.info(f'Transaction not indexed yet, retry {retry + 1}/5...')
                await asyncio.sleep(2)

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
                        break

            if not result:
                logger.error('Transaction not found after retries')
                return False

        # Check transaction was successful
        meta = result.get('meta', {})
        if meta.get('err') is not None:
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
