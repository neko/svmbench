"""
x402 engine client for making paid LLM API calls.

x402 is an HTTP 402 payment protocol that enables pay-per-call API access.
This client handles the payment flow:
1. Make API request
2. Receive 402 response with payment requirements
3. Execute USDC payment on Solana
4. Retry request with payment proof

The client provides an OpenAI-compatible interface for LLM calls.
"""

import base64
import json
from typing import Any

import httpx
from loguru import logger
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.transaction import Transaction

from api.core.config import settings


X402_GATEWAY_URL = settings.X402_GATEWAY_URL

# Model ID mapping for x402 endpoints (model ID -> endpoint path)
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
    'codex-gpt-5.2': 'llm/gpt-5.2-codex',
    'claude-opus-4-6': 'llm/claude-opus',
    'anthropic/claude-opus-4-6': 'llm/claude-opus',
    'claude-sonnet-4-6': 'llm/claude-sonnet',
    'anthropic/claude-sonnet-4-6': 'llm/claude-sonnet',
    'deepseek-chat-v3': 'llm/deepseek-v3',
    'deepseek/deepseek-chat-v3-0324': 'llm/deepseek-v3',
}


class X402PaymentRequired(Exception):
    """Raised when x402 gateway returns 402 requiring payment."""

    def __init__(
        self,
        amount: float,
        receiver: str,
        payment_id: str,
        network: str = 'solana',
    ):
        self.amount = amount
        self.receiver = receiver
        self.payment_id = payment_id
        self.network = network
        super().__init__(f'Payment required: ${amount} USDC to {receiver}')


class X402Client:
    """Client for making paid API calls via x402 gateway."""

    def __init__(
        self,
        service_wallet_key: str | None = None,
        gateway_url: str = X402_GATEWAY_URL,
        rpc_url: str | None = None,
    ):
        """
        Initialize x402 client.

        Args:
            service_wallet_key: Base58 encoded private key for paying x402
            gateway_url: x402 gateway base URL
            rpc_url: Solana RPC URL for transactions
        """
        self.gateway_url = gateway_url.rstrip('/')
        self.rpc_url = rpc_url or settings.PAYMENT_SOLANA_RPC_URL

        if service_wallet_key:
            # Decode base58 private key
            self.keypair = Keypair.from_base58_string(service_wallet_key)
            self.wallet_address = str(self.keypair.pubkey())
        else:
            self.keypair = None
            self.wallet_address = None

    def get_endpoint_for_model(self, model: str) -> str:
        """Get x402 endpoint path for a model."""
        endpoint = X402_MODEL_ENDPOINTS.get(model)
        if endpoint:
            return f'{self.gateway_url}/api/{endpoint}'

        # Fallback: try to construct from model name
        # Strip llm- prefix if present to avoid llm/llm-xxx
        clean_model = model.replace('/', '-').replace('_', '-')
        if clean_model.startswith('llm-'):
            clean_model = clean_model[4:]  # Remove 'llm-' prefix
        return f'{self.gateway_url}/api/llm/{clean_model}'

    async def _parse_402_response(
        self,
        response: httpx.Response,
    ) -> X402PaymentRequired:
        """Parse 402 response to extract payment requirements."""
        # x402 sends payment info in headers or body
        payment_info = {}

        # Check X-Payment-* headers
        if 'X-Payment-Amount' in response.headers:
            payment_info['amount'] = float(response.headers['X-Payment-Amount'])
        if 'X-Payment-Receiver' in response.headers:
            payment_info['receiver'] = response.headers['X-Payment-Receiver']
        if 'X-Payment-Id' in response.headers:
            payment_info['payment_id'] = response.headers['X-Payment-Id']
        if 'X-Payment-Network' in response.headers:
            payment_info['network'] = response.headers['X-Payment-Network']

        # Also try JSON body
        try:
            body = response.json()
            if 'payment' in body:
                p = body['payment']
                payment_info.setdefault('amount', p.get('amount', 0))
                payment_info.setdefault('receiver', p.get('receiver', ''))
                payment_info.setdefault('payment_id', p.get('id', ''))
                payment_info.setdefault('network', p.get('network', 'solana'))
        except Exception:
            pass

        return X402PaymentRequired(
            amount=payment_info.get('amount', 0),
            receiver=payment_info.get('receiver', ''),
            payment_id=payment_info.get('payment_id', ''),
            network=payment_info.get('network', 'solana'),
        )

    async def _make_payment(
        self,
        payment_req: X402PaymentRequired,
    ) -> str:
        """Execute USDC payment and return signature."""
        if not self.keypair:
            raise ValueError('No service wallet configured for payments')

        from solana.rpc.async_api import AsyncClient
        from spl.token.instructions import TransferParams, transfer

        # USDC on Solana mainnet
        USDC_MINT = Pubkey.from_string('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v')

        async with AsyncClient(self.rpc_url) as client:
            # Get associated token accounts
            from spl.token.async_client import AsyncToken

            payer_ata = await AsyncToken.get_associated_token_address(
                USDC_MINT,
                self.keypair.pubkey(),
            )
            receiver_ata = await AsyncToken.get_associated_token_address(
                USDC_MINT,
                Pubkey.from_string(payment_req.receiver),
            )

            # USDC has 6 decimals
            amount_lamports = int(payment_req.amount * 1_000_000)

            # Create transfer instruction
            transfer_ix = transfer(
                TransferParams(
                    program_id=Pubkey.from_string('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'),
                    source=payer_ata,
                    dest=receiver_ata,
                    owner=self.keypair.pubkey(),
                    amount=amount_lamports,
                )
            )

            # Build and sign transaction
            recent_blockhash = await client.get_latest_blockhash()
            tx = Transaction.new_signed_with_payer(
                [transfer_ix],
                self.keypair.pubkey(),
                [self.keypair],
                recent_blockhash.value.blockhash,
            )

            # Send transaction
            result = await client.send_transaction(tx)
            signature = str(result.value)

            # Wait for confirmation
            await client.confirm_transaction(signature, 'confirmed')

            logger.info(f'x402 payment sent: {signature} for ${payment_req.amount}')
            return signature

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Make a chat completion request via x402.

        Args:
            model: Model identifier
            messages: Chat messages
            **kwargs: Additional OpenAI-compatible parameters

        Returns:
            OpenAI-compatible completion response
        """
        endpoint = self.get_endpoint_for_model(model)

        payload = {
            'model': model,
            'messages': messages,
            **kwargs,
        }

        headers = {
            'Content-Type': 'application/json',
        }

        # Add wallet address if available (for payment tracking)
        if self.wallet_address:
            headers['X-Payer-Wallet'] = self.wallet_address

        async with httpx.AsyncClient(timeout=120.0) as client:
            # Initial request
            response = await client.post(endpoint, json=payload, headers=headers)

            # Handle 402 Payment Required
            if response.status_code == 402:
                payment_req = await self._parse_402_response(response)

                if not self.keypair:
                    raise payment_req  # Re-raise if no wallet to pay

                # Make payment
                signature = await self._make_payment(payment_req)

                # Retry with payment proof
                headers['X-Payment-Signature'] = signature
                headers['X-Payment-Id'] = payment_req.payment_id

                response = await client.post(endpoint, json=payload, headers=headers)

            response.raise_for_status()
            return response.json()

    async def completion(
        self,
        model: str,
        prompt: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Make a completion request via x402.

        Args:
            model: Model identifier
            prompt: Text prompt
            **kwargs: Additional parameters

        Returns:
            Completion response
        """
        # Convert to chat format
        messages = [{'role': 'user', 'content': prompt}]
        return await self.chat_completion(model, messages, **kwargs)


async def get_x402_client() -> X402Client | None:
    """Get configured x402 client, or None if not configured."""
    wallet_key = settings.X402_SERVICE_WALLET_KEY
    if wallet_key:
        return X402Client(
            service_wallet_key=wallet_key.get_secret_value(),
            gateway_url=settings.X402_GATEWAY_URL,
            rpc_url=settings.PAYMENT_SOLANA_RPC_URL,
        )
    return None
