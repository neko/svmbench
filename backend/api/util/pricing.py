"""
Pricing calculation based on x402 engine per-request costs.

x402 engine provides pay-per-call LLM access via HTTP 402 payments.
Pricing is per-request, not per-token. We estimate requests per effort level.
"""

import asyncio
from datetime import datetime, timedelta
from typing import TypedDict

import httpx
from loguru import logger

from api.core.config import settings


class X402ModelPricing(TypedDict):
    price_per_request: float  # cost per API request in USD
    endpoint: str             # x402 API endpoint


# Token budgets per effort level - informational for users
# These represent rough token limits the audit can use
TOKEN_BUDGETS = {
    'low': {
        'input_tokens': 50_000,
        'output_tokens': 10_000,
    },
    'medium': {
        'input_tokens': 150_000,
        'output_tokens': 30_000,
    },
    'high': {
        'input_tokens': 300_000,
        'output_tokens': 60_000,
    },
}

# Estimated API requests per effort level
# Higher effort = more agent iterations = more API calls
REQUESTS_PER_EFFORT = {
    'low': 5,      # minimal investigation
    'medium': 15,  # moderate depth
    'high': 40,    # thorough audit
}

# x402 engine base URL
X402_BASE_URL = 'https://x402-gateway-production.up.railway.app'
X402_DISCOVERY_URL = 'https://x402engine.app/.well-known/x402.json'

# Cache for x402 pricing
_x402_pricing_cache: dict[str, X402ModelPricing] = {}
_x402_cache_timestamp: datetime | None = None
_x402_cache_lock = asyncio.Lock()
X402_CACHE_TTL = timedelta(hours=1)

# Model ID mapping: our model names -> x402 model IDs
MODEL_TO_X402 = {
    # OpenAI Codex models
    'codex-gpt-5.2': 'llm-gpt-5.2-codex',
    'gpt-5.2-codex': 'llm-gpt-5.2-codex',
    # Claude models
    'anthropic/claude-opus-4-6': 'llm-claude-opus',
    'claude-opus-4-6': 'llm-claude-opus',
    'anthropic/claude-sonnet-4-6': 'llm-claude-sonnet',
    'claude-sonnet-4-6': 'llm-claude-sonnet',
    # DeepSeek
    'deepseek/deepseek-chat-v3-0324': 'llm-deepseek-v3',
    'deepseek-chat-v3': 'llm-deepseek-v3',
}


def parse_price_string(price_str: str) -> float:
    """Parse x402 price string like '$0.06' to float."""
    if not price_str:
        return 0.0
    cleaned = price_str.replace('$', '').strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


async def fetch_x402_pricing() -> dict[str, X402ModelPricing]:
    """Fetch current pricing from x402 engine discovery endpoint."""
    async with httpx.AsyncClient() as client:
        response = await client.get(X402_DISCOVERY_URL, timeout=30.0)
        response.raise_for_status()
        data = response.json()

    pricing: dict[str, X402ModelPricing] = {}

    for service in data.get('services', []):
        service_id = service.get('id', '')
        price_str = service.get('price', '$0')
        endpoint = service.get('endpoint', '')

        pricing[service_id] = X402ModelPricing(
            price_per_request=parse_price_string(price_str),
            endpoint=endpoint,
        )

    return pricing


async def get_x402_pricing() -> dict[str, X402ModelPricing]:
    """Get x402 pricing (cached for 1 hour)."""
    global _x402_pricing_cache, _x402_cache_timestamp

    async with _x402_cache_lock:
        now = datetime.utcnow()
        if _x402_cache_timestamp and (now - _x402_cache_timestamp) < X402_CACHE_TTL and _x402_pricing_cache:
            return _x402_pricing_cache

        try:
            _x402_pricing_cache = await fetch_x402_pricing()
            _x402_cache_timestamp = now
            logger.info(f'Refreshed x402 pricing cache: {len(_x402_pricing_cache)} services')
        except Exception as e:
            logger.warning(f'Failed to fetch x402 pricing: {e}')
            if _x402_pricing_cache:
                return _x402_pricing_cache
            return {}

    return _x402_pricing_cache


# Fallback prices per request (if x402 discovery fails)
FALLBACK_X402_PRICES: dict[str, float] = {
    'llm-gpt-5.2-codex': 0.06,
    'llm-claude-opus': 0.09,
    'llm-claude-sonnet': 0.03,
    'llm-deepseek-v3': 0.005,
}


def get_x402_model_id(model: str) -> str:
    """Map our model name to x402 model ID."""
    return MODEL_TO_X402.get(model, model)


def get_request_price_sync(model: str, pricing_cache: dict[str, X402ModelPricing]) -> float:
    """Get per-request price for a model (synchronous)."""
    x402_id = get_x402_model_id(model)

    # Check x402 cache
    if x402_id in pricing_cache:
        return pricing_cache[x402_id]['price_per_request']

    # Check fallback
    if x402_id in FALLBACK_X402_PRICES:
        return FALLBACK_X402_PRICES[x402_id]

    # Default fallback for unknown models
    return 0.10  # conservative estimate


def calculate_model_price_sync(
    model: str,
    effort: str,
    pricing_cache: dict[str, X402ModelPricing],
) -> float:
    """Calculate price for a model at given effort level (synchronous)."""
    price_per_request = get_request_price_sync(model, pricing_cache)
    num_requests = REQUESTS_PER_EFFORT.get(effort, REQUESTS_PER_EFFORT['medium'])

    # Base cost = price per request * estimated requests
    base_cost = price_per_request * num_requests

    # Apply markup
    markup = settings.PAYMENT_MARKUP
    final_price = base_cost * (1 + markup)

    # Round to 2 decimal places, minimum $0.01
    return max(round(final_price, 2), 0.01)


async def calculate_model_price(model: str, effort: str) -> float:
    """Calculate price for a model at given effort level."""
    pricing_cache = await get_x402_pricing()
    return calculate_model_price_sync(model, effort, pricing_cache)


async def calculate_total_price(models: list[str], effort: str) -> float:
    """Calculate total price for multiple models."""
    pricing_cache = await get_x402_pricing()
    total = sum(calculate_model_price_sync(model, effort, pricing_cache) for model in models)
    return round(total, 2)


async def get_all_model_prices(effort: str) -> dict[str, float]:
    """Get prices for all available models at a given effort level."""
    from api.core.const import ALLOWED_MODELS, OPENROUTER_ALLOWED_MODELS

    pricing_cache = await get_x402_pricing()
    prices = {}

    all_models = list(ALLOWED_MODELS) + list(OPENROUTER_ALLOWED_MODELS)
    for model in all_models:
        prices[model] = calculate_model_price_sync(model, effort, pricing_cache)

    return prices


def get_token_budget(effort: str) -> dict[str, int]:
    """Get the token budget for an effort level."""
    return TOKEN_BUDGETS.get(effort, TOKEN_BUDGETS['medium'])


def get_requests_budget(effort: str) -> int:
    """Get the request budget for an effort level."""
    return REQUESTS_PER_EFFORT.get(effort, REQUESTS_PER_EFFORT['medium'])


def get_x402_endpoint(model: str) -> str | None:
    """Get the x402 API endpoint for a model."""
    x402_id = get_x402_model_id(model)
    # Return the constructed endpoint URL
    return f'{X402_BASE_URL}/api/{x402_id.replace("llm-", "llm/")}'
