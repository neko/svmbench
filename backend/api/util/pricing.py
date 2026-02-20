"""
Pricing calculation for Daydreams router (ai.xgate.run) LLM access.

Price = (input_tokens * input_price / 1M) + (output_tokens * output_price / 1M) * (1 + markup)
"""

import asyncio
from datetime import datetime, timedelta

import httpx
from loguru import logger

from api.core.config import settings

DAYDREAMS_MODELS_URL = 'https://ai.xgate.run/v1/models'

# Fixed token budget (max effort)
TOKEN_BUDGET = {'input_tokens': 300_000, 'output_tokens': 60_000}

# Cache
_models_cache: list[dict] = []
_models_cache_timestamp: datetime | None = None
_models_cache_lock = asyncio.Lock()
CACHE_TTL = timedelta(hours=1)

# Fallback prices (per 1M tokens)
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

DEFAULT_INPUT_PRICE = 3.0
DEFAULT_OUTPUT_PRICE = 15.0


async def _fetch_models() -> list[dict]:
    """Fetch models from Daydreams router."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(DAYDREAMS_MODELS_URL)
        response.raise_for_status()
        data = response.json()

    models: list[dict] = []
    for m in data.get('data', []):
        caps = m.get('capabilities', {})
        if not caps.get('supports_function_calling'):
            continue

        provider = m.get('provider', '')
        model_id = m.get('id', '')
        pricing = m.get('pricing', {})

        def parse_price(v):
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                return float(v.replace('$', '').strip() or '0')
            return 0.0

        models.append({
            'id': f'{provider}:{model_id}',
            'name': m.get('display_name', model_id),
            'provider': provider,
            'model_id': model_id,
            'input_price': parse_price(pricing.get('input_per_1m', 0)),
            'output_price': parse_price(pricing.get('output_per_1m', 0)),
            'context_length': m.get('context_length', 0),
        })

    # Sort expensive first
    models.sort(key=lambda x: x['input_price'] * 3 + x['output_price'], reverse=True)
    return models


async def get_x402_models() -> list[dict]:
    """Get available models (cached)."""
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
            return [
                {'id': mid, 'name': mid.split(':')[-1].replace('-', ' ').title(),
                 'price': (p['input'] * 300 + p['output'] * 60) / 1000}
                for mid, p in FALLBACK_PRICES.items()
            ]

    return _models_cache


async def get_x402_pricing() -> dict[str, dict[str, float]]:
    """Get pricing dict: model_id -> {input, output} per 1M tokens."""
    models = await get_x402_models()
    return {m['id']: {'input': m['input_price'], 'output': m['output_price']} for m in models}


def get_model_prices(model: str, pricing: dict[str, dict[str, float]]) -> tuple[float, float]:
    """Get (input_price, output_price) per 1M tokens for a model."""
    if model in pricing:
        return (pricing[model]['input'], pricing[model]['output'])
    if model in FALLBACK_PRICES:
        return (FALLBACK_PRICES[model]['input'], FALLBACK_PRICES[model]['output'])
    for mid, p in pricing.items():
        if model in mid or mid in model:
            return (p['input'], p['output'])
    return (DEFAULT_INPUT_PRICE, DEFAULT_OUTPUT_PRICE)


def calculate_model_price_sync(model: str, pricing: dict[str, dict[str, float]], effort: str = 'high') -> float:
    """Calculate price for one model's audit."""
    input_price, output_price = get_model_prices(model, pricing)
    input_cost = (TOKEN_BUDGET['input_tokens'] / 1_000_000) * input_price
    output_cost = (TOKEN_BUDGET['output_tokens'] / 1_000_000) * output_price
    base_cost = input_cost + output_cost
    final_price = base_cost * (1 + settings.PAYMENT_MARKUP)
    return max(round(final_price, 2), 0.01)


async def calculate_model_price(model: str, effort: str = 'high') -> float:
    """Calculate price for a single model's audit."""
    pricing = await get_x402_pricing()
    return calculate_model_price_sync(model, pricing, effort)


async def calculate_total_price(models: list[str], effort: str = 'high') -> float:
    """Calculate total price for auditing with multiple models."""
    pricing = await get_x402_pricing()
    return round(sum(calculate_model_price_sync(m, pricing, effort) for m in models), 2)


async def get_all_model_prices(effort: str = 'high') -> dict[str, float]:
    """Get prices for all available models."""
    models = await get_x402_models()
    pricing = await get_x402_pricing()
    return {m['id']: calculate_model_price_sync(m['id'], pricing, effort) for m in models}


async def get_pricing_breakdown(models: list[str], effort: str = 'high') -> dict:
    """Get detailed pricing breakdown."""
    pricing = await get_x402_pricing()
    markup = settings.PAYMENT_MARKUP

    breakdown = {
        'models': {},
        'effort': 'high',
        'token_budget': TOKEN_BUDGET,
        'markup_percent': int(markup * 100),
        'total_before_markup': 0.0,
        'total': 0.0,
    }

    for model in models:
        input_price, output_price = get_model_prices(model, pricing)
        input_cost = (TOKEN_BUDGET['input_tokens'] / 1_000_000) * input_price
        output_cost = (TOKEN_BUDGET['output_tokens'] / 1_000_000) * output_price
        base_cost = input_cost + output_cost
        final_cost = base_cost * (1 + markup)

        breakdown['models'][model] = {
            'input_price_per_1m': input_price,
            'output_price_per_1m': output_price,
            'input_tokens': TOKEN_BUDGET['input_tokens'],
            'output_tokens': TOKEN_BUDGET['output_tokens'],
            'input_cost': round(input_cost, 4),
            'output_cost': round(output_cost, 4),
            'subtotal_before_markup': round(base_cost, 4),
            'subtotal': round(final_cost, 2),
        }
        breakdown['total_before_markup'] += base_cost

    breakdown['total_before_markup'] = round(breakdown['total_before_markup'], 4)
    breakdown['total'] = round(breakdown['total_before_markup'] * (1 + markup), 2)
    return breakdown
