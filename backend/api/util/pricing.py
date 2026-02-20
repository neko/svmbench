"""
Pricing calculation for Daydreams router (ai.xgate.run) LLM access.

Daydreams uses per-token pricing (input/output per 1M tokens).
We estimate token usage based on audit effort level.

Token budgets by effort:
- Low: ~50k input, ~10k output
- Medium: ~150k input, ~30k output
- High: ~300k input, ~60k output

Price calculation:
  tokens_cost = (input_tokens * input_price / 1M) + (output_tokens * output_price / 1M)
  total = tokens_cost * (1 + markup)
"""

import asyncio
from datetime import datetime, timedelta

import httpx
from loguru import logger

from api.core.config import settings


# Daydreams router endpoint
DAYDREAMS_MODELS_URL = 'https://ai.xgate.run/v1/models'

# Token budgets per audit effort level (estimated usage)
TOKEN_BUDGETS: dict[str, dict[str, int]] = {
    'low': {'input_tokens': 50_000, 'output_tokens': 10_000},
    'medium': {'input_tokens': 150_000, 'output_tokens': 30_000},
    'high': {'input_tokens': 300_000, 'output_tokens': 60_000},
}

# Legacy: requests per effort (for API compatibility, not used in pricing)
REQUESTS_PER_EFFORT: dict[str, int] = {
    'low': 2,
    'medium': 3,
    'high': 7,
}
REQUESTS_PER_AUDIT = 2  # Legacy default

# Cache for models
_models_cache: list[dict] = []
_models_cache_timestamp: datetime | None = None
_models_cache_lock = asyncio.Lock()
CACHE_TTL = timedelta(hours=1)

# Fallback model prices (per 1M tokens) if API fails
FALLBACK_PRICES: dict[str, dict[str, float]] = {
    'anthropic:claude-sonnet-4-6': {'input': 3.0, 'output': 15.0},
    'anthropic:claude-opus-4-6': {'input': 5.0, 'output': 25.0},
    'anthropic:claude-opus-4-5': {'input': 5.0, 'output': 25.0},
    'openai:gpt-5': {'input': 1.25, 'output': 10.0},
    'openai:gpt-5-mini': {'input': 0.25, 'output': 2.0},
    'openai:gpt-5.2-codex': {'input': 1.75, 'output': 14.0},
    'openai:gpt-5.3-codex': {'input': 1.75, 'output': 14.0},
    'moonshot:kimi-k2.5': {'input': 0.6, 'output': 3.0},
}

DEFAULT_INPUT_PRICE = 3.0  # per 1M tokens
DEFAULT_OUTPUT_PRICE = 15.0  # per 1M tokens


async def _fetch_models() -> list[dict]:
    """Fetch models from Daydreams router."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(DAYDREAMS_MODELS_URL)
        response.raise_for_status()
        data = response.json()

    models: list[dict] = []
    for m in data.get('data', []):
        caps = m.get('capabilities', {})
        # Only include LLMs that support function calling (chat models)
        if not caps.get('supports_function_calling'):
            continue

        provider = m.get('provider', '')
        model_id = m.get('id', '')
        pricing = m.get('pricing', {})

        # Parse pricing (can be string or number)
        def parse_price(v):
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                return float(v.replace('$', '').strip() or '0')
            return 0.0

        input_price = parse_price(pricing.get('input_per_1m', 0))
        output_price = parse_price(pricing.get('output_per_1m', 0))

        models.append({
            'id': f'{provider}:{model_id}',
            'name': m.get('display_name', model_id),
            'provider': provider,
            'model_id': model_id,
            'input_price': input_price,  # per 1M tokens
            'output_price': output_price,  # per 1M tokens
            'context_length': m.get('context_length', 0),
        })

    # Sort by total cost descending (expensive first, assuming 3:1 input/output ratio)
    models.sort(key=lambda x: x['input_price'] * 3 + x['output_price'], reverse=True)
    return models


async def get_x402_models() -> list[dict]:
    """Get available models (cached). Returns list with 'id', 'name', 'price' keys."""
    global _models_cache, _models_cache_timestamp

    async with _models_cache_lock:
        now = datetime.utcnow()
        if _models_cache_timestamp and (now - _models_cache_timestamp) < CACHE_TTL and _models_cache:
            return _models_cache

        try:
            _models_cache = await _fetch_models()
            _models_cache_timestamp = now
            logger.info(f'Refreshed Daydreams models: {len(_models_cache)} models')
        except Exception as e:
            logger.warning(f'Failed to fetch Daydreams models: {e}')
            if _models_cache:
                return _models_cache
            # Return fallback
            return [
                {
                    'id': model_id,
                    'name': model_id.split(':')[-1].replace('-', ' ').title(),
                    'price': (p['input'] * 150 + p['output'] * 30) / 1000,  # Medium effort estimate
                }
                for model_id, p in FALLBACK_PRICES.items()
            ]

    return _models_cache


async def get_x402_pricing() -> dict[str, dict[str, float]]:
    """Get pricing dict: model_id -> {input, output} per 1M tokens."""
    models = await get_x402_models()
    return {
        m['id']: {'input': m['input_price'], 'output': m['output_price']}
        for m in models
    }


def get_model_prices(model: str, pricing: dict[str, dict[str, float]]) -> tuple[float, float]:
    """Get (input_price, output_price) per 1M tokens for a model."""
    if model in pricing:
        p = pricing[model]
        return (p['input'], p['output'])

    # Try fallback
    if model in FALLBACK_PRICES:
        p = FALLBACK_PRICES[model]
        return (p['input'], p['output'])

    # Try partial match
    for mid, p in pricing.items():
        if model in mid or mid in model:
            return (p['input'], p['output'])

    return (DEFAULT_INPUT_PRICE, DEFAULT_OUTPUT_PRICE)


def calculate_model_price_sync(
    model: str,
    pricing: dict[str, dict[str, float]],
    effort: str = 'medium',
) -> float:
    """
    Calculate the total price for one model's audit.

    Price = (input_tokens * input_price / 1M) + (output_tokens * output_price / 1M)
    Price = base_cost * (1 + markup)
    """
    input_price, output_price = get_model_prices(model, pricing)
    budget = TOKEN_BUDGETS.get(effort, TOKEN_BUDGETS['medium'])

    # Calculate token cost
    input_cost = (budget['input_tokens'] / 1_000_000) * input_price
    output_cost = (budget['output_tokens'] / 1_000_000) * output_price
    base_cost = input_cost + output_cost

    # Apply markup
    markup = settings.PAYMENT_MARKUP
    final_price = base_cost * (1 + markup)

    # Round to 2 decimal places, minimum $0.01
    return max(round(final_price, 2), 0.01)


async def calculate_model_price(model: str, effort: str = 'medium') -> float:
    """Calculate price for a single model's audit."""
    pricing = await get_x402_pricing()
    return calculate_model_price_sync(model, pricing, effort)


async def calculate_total_price(models: list[str], effort: str = 'medium') -> float:
    """Calculate total price for auditing with multiple models."""
    pricing = await get_x402_pricing()
    total = sum(calculate_model_price_sync(model, pricing, effort) for model in models)
    return round(total, 2)


async def get_all_model_prices(effort: str = 'medium') -> dict[str, float]:
    """Get prices for all available models."""
    models = await get_x402_models()
    pricing = await get_x402_pricing()
    return {m['id']: calculate_model_price_sync(m['id'], pricing, effort) for m in models}


async def get_pricing_breakdown(models: list[str], effort: str = 'medium') -> dict:
    """Get detailed pricing breakdown for display."""
    pricing = await get_x402_pricing()
    markup = settings.PAYMENT_MARKUP
    budget = TOKEN_BUDGETS.get(effort, TOKEN_BUDGETS['medium'])

    breakdown = {
        'models': {},
        'effort': effort,
        'token_budget': budget,
        'markup_percent': int(markup * 100),
        'total_before_markup': 0.0,
        'total': 0.0,
    }

    for model in models:
        input_price, output_price = get_model_prices(model, pricing)
        input_cost = (budget['input_tokens'] / 1_000_000) * input_price
        output_cost = (budget['output_tokens'] / 1_000_000) * output_price
        base_cost = input_cost + output_cost
        final_cost = base_cost * (1 + markup)

        breakdown['models'][model] = {
            'input_price_per_1m': input_price,
            'output_price_per_1m': output_price,
            'input_tokens': budget['input_tokens'],
            'output_tokens': budget['output_tokens'],
            'input_cost': round(input_cost, 4),
            'output_cost': round(output_cost, 4),
            'subtotal_before_markup': round(base_cost, 4),
            'subtotal': round(final_cost, 2),
        }
        breakdown['total_before_markup'] += base_cost

    breakdown['total_before_markup'] = round(breakdown['total_before_markup'], 4)
    breakdown['total'] = round(breakdown['total_before_markup'] * (1 + markup), 2)

    return breakdown
