"""
Pricing calculation for x402 engine pay-per-request LLM access.

x402 uses FLAT per-request pricing - each API call costs a fixed amount
regardless of tokens used. Different models have different prices.

API calls vary by effort level:
- Low (2 phases): file_select + basic_scan
- Medium (3 phases): file_select + thorough_scan + deep_dive
- High (7 phases): file_select + thorough_scan + access_control + state_handling + arithmetic + deep_dive + verify

Price calculation:
  total = sum(model_price * requests_for_effort * (1 + markup) for each model)
"""

import asyncio
from datetime import datetime, timedelta

import httpx
from loguru import logger

from api.core.config import settings


# x402 discovery endpoint
X402_DISCOVERY_URL = 'https://x402engine.app/.well-known/x402.json'
X402_BASE_URL = 'https://x402-gateway-production.up.railway.app'

# API requests per audit based on effort level
REQUESTS_PER_EFFORT: dict[str, int] = {
    'low': 2,      # file_select + basic_scan
    'medium': 3,   # file_select + thorough_scan + deep_dive
    'high': 7,     # file_select + thorough_scan + 3 focused scans + deep_dive + verify
}

# Default for backwards compatibility
REQUESTS_PER_AUDIT = 2

# Cache for x402 pricing: x402_model_id -> price_per_request
_x402_cache: dict[str, float] = {}
_x402_cache_timestamp: datetime | None = None
_x402_cache_lock = asyncio.Lock()
X402_CACHE_TTL = timedelta(hours=1)

# Cache for x402 models: list of {id, name, price, endpoint}
_x402_models_cache: list[dict] = []
_x402_models_cache_timestamp: datetime | None = None
_x402_models_cache_lock = asyncio.Lock()

# Map our model names to x402 model IDs
MODEL_TO_X402_ID: dict[str, str] = {
    # OpenAI models
    'codex-gpt-5.2': 'llm-gpt-5.2-codex',
    'codex-gpt-5.1-codex-max': 'llm-gpt-5.2-codex',
    'openai/gpt-5.2-codex': 'llm-gpt-5.2-codex',
    'openai/gpt-5.1-codex': 'llm-gpt-5.2-codex',
    'openai/gpt-5.1-codex-max': 'llm-gpt-5.2-codex',
    'openai/gpt-5.2': 'llm-gpt-5.2',
    'openai/gpt-5.1': 'llm-gpt-5.2',
    # Anthropic models
    'anthropic/claude-opus-4.5': 'llm-claude-opus',
    'anthropic/claude-opus-4.6': 'llm-claude-opus',
    'anthropic/claude-sonnet-4.5': 'llm-claude-sonnet',
    'anthropic/claude-sonnet-4.6': 'llm-claude-sonnet',
    # Google models
    'google/gemini-2.5-pro': 'llm-gemini-pro',
    'google/gemini-2.5-flash': 'llm-gemini-flash',
    # DeepSeek models
    'deepseek/deepseek-r1': 'llm-deepseek-r1',
    'deepseek/deepseek-chat': 'llm-deepseek',
    # Other models
    'moonshotai/kimi-k2.5': 'llm-kimi',
    'minimax/minimax-m2.5': 'llm-minimax',
    'z-ai/glm-5': 'llm-glm',
}

# Fallback prices per request (if x402 discovery fails)
FALLBACK_X402_PRICES: dict[str, float] = {
    'llm-gpt-5.2-codex': 0.06,
    'llm-gpt-5.2': 0.08,
    'llm-claude-opus': 0.09,
    'llm-claude-sonnet': 0.06,
    'llm-claude-haiku': 0.02,
    'llm-gemini-pro': 0.035,
    'llm-gemini-flash': 0.009,
    'llm-deepseek': 0.005,
    'llm-deepseek-r1': 0.01,
    'llm-kimi': 0.03,
    'llm-minimax': 0.01,
    'llm-glm': 0.03,
    'llm-grok': 0.06,
    'llm-llama': 0.002,
}

# Default price for unknown models
DEFAULT_REQUEST_PRICE = 0.10


def _parse_price(price_str: str) -> float:
    """Parse x402 price string like '$0.06' to float."""
    if not price_str:
        return 0.0
    cleaned = price_str.replace('$', '').strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


async def _fetch_x402_discovery() -> dict:
    """Fetch full discovery data from x402 endpoint."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(X402_DISCOVERY_URL)
        response.raise_for_status()
        return response.json()


async def _fetch_x402_pricing() -> dict[str, float]:
    """Fetch pricing from x402 discovery endpoint."""
    data = await _fetch_x402_discovery()

    prices: dict[str, float] = {}

    # x402 uses categories -> list of services structure
    for category_services in data.get('categories', {}).values():
        for service in category_services:
            service_id = service.get('id', '')
            if service_id.startswith('llm-'):
                price = _parse_price(service.get('price', ''))
                if price > 0:
                    prices[service_id] = price

    return prices


async def _fetch_x402_models() -> list[dict]:
    """Fetch LLM models from x402 discovery endpoint."""
    data = await _fetch_x402_discovery()

    models: list[dict] = []

    # x402 uses categories -> list of services structure
    # LLM models are typically under 'llm' category
    for category_name, category_services in data.get('categories', {}).items():
        for service in category_services:
            service_id = service.get('id', '')
            if service_id.startswith('llm-'):
                price = _parse_price(service.get('price', ''))
                models.append({
                    'id': service_id,
                    'name': service.get('name', service_id),
                    'price': price,
                    'endpoint': service.get('endpoint', ''),
                })

    # Sort by price (cheapest first) then by name
    models.sort(key=lambda m: (m['price'], m['name']))

    return models


async def get_x402_models() -> list[dict]:
    """Get x402 LLM models (cached for 1 hour)."""
    global _x402_models_cache, _x402_models_cache_timestamp

    async with _x402_models_cache_lock:
        now = datetime.utcnow()
        if _x402_models_cache_timestamp and (now - _x402_models_cache_timestamp) < X402_CACHE_TTL and _x402_models_cache:
            return _x402_models_cache

        try:
            _x402_models_cache = await _fetch_x402_models()
            _x402_models_cache_timestamp = now
            logger.info(f'Refreshed x402 models: {len(_x402_models_cache)} LLM models')
        except Exception as e:
            logger.warning(f'Failed to fetch x402 models: {e}')
            if _x402_models_cache:
                return _x402_models_cache
            # Return fallback models from FALLBACK_X402_PRICES
            return [
                {'id': model_id, 'name': model_id.replace('llm-', '').replace('-', ' ').title(), 'price': price, 'endpoint': ''}
                for model_id, price in FALLBACK_X402_PRICES.items()
            ]

    return _x402_models_cache


async def get_x402_pricing() -> dict[str, float]:
    """Get x402 pricing (cached for 1 hour)."""
    global _x402_cache, _x402_cache_timestamp

    async with _x402_cache_lock:
        now = datetime.utcnow()
        if _x402_cache_timestamp and (now - _x402_cache_timestamp) < X402_CACHE_TTL and _x402_cache:
            return _x402_cache

        try:
            _x402_cache = await _fetch_x402_pricing()
            _x402_cache_timestamp = now
            logger.info(f'Refreshed x402 pricing: {len(_x402_cache)} models')
        except Exception as e:
            logger.warning(f'Failed to fetch x402 pricing: {e}')
            if _x402_cache:
                return _x402_cache
            return FALLBACK_X402_PRICES.copy()

    return _x402_cache


def get_x402_model_id(our_model: str) -> str:
    """Map our model name to x402 model ID."""
    if our_model in MODEL_TO_X402_ID:
        return MODEL_TO_X402_ID[our_model]

    # Try to find a partial match
    model_lower = our_model.lower()
    for our_name, x402_id in MODEL_TO_X402_ID.items():
        if our_name.lower() in model_lower or model_lower in our_name.lower():
            return x402_id

    # Return as-is if no mapping found
    return our_model


def get_model_request_price(our_model: str, x402_prices: dict[str, float]) -> float:
    """Get the per-request price for a model."""
    x402_id = get_x402_model_id(our_model)

    # Try exact match
    if x402_id in x402_prices:
        return x402_prices[x402_id]

    # Try fallback
    if x402_id in FALLBACK_X402_PRICES:
        return FALLBACK_X402_PRICES[x402_id]

    # Try matching by suffix (e.g., 'claude-opus' matches 'llm-claude-opus')
    for price_id, price in x402_prices.items():
        if price_id.endswith(x402_id) or x402_id.endswith(price_id.replace('llm-', '')):
            return price

    return DEFAULT_REQUEST_PRICE


def calculate_model_price_sync(
    model: str,
    x402_prices: dict[str, float],
    effort: str = 'medium',
) -> float:
    """
    Calculate the total price for one model's audit.

    Price = request_price × requests_for_effort × (1 + markup)
    """
    request_price = get_model_request_price(model, x402_prices)

    # Get number of API calls for this effort level
    num_requests = REQUESTS_PER_EFFORT.get(effort, REQUESTS_PER_EFFORT['medium'])

    # Base cost for the audit
    base_cost = request_price * num_requests

    # Apply markup
    markup = settings.PAYMENT_MARKUP
    final_price = base_cost * (1 + markup)

    # Round to 2 decimal places, minimum $0.01
    return max(round(final_price, 2), 0.01)


async def calculate_model_price(model: str, effort: str = 'medium') -> float:
    """Calculate price for a single model's audit."""
    x402_prices = await get_x402_pricing()
    return calculate_model_price_sync(model, x402_prices, effort)


async def calculate_total_price(models: list[str], effort: str = 'medium') -> float:
    """Calculate total price for auditing with multiple models."""
    x402_prices = await get_x402_pricing()
    total = sum(calculate_model_price_sync(model, x402_prices, effort) for model in models)
    return round(total, 2)


async def get_all_model_prices(effort: str = 'medium') -> dict[str, float]:
    """Get prices for all our supported models."""
    from api.core.const import ALLOWED_MODELS, OPENROUTER_ALLOWED_MODELS

    x402_prices = await get_x402_pricing()
    prices = {}

    all_models = list(ALLOWED_MODELS) + list(OPENROUTER_ALLOWED_MODELS)
    for model in all_models:
        prices[model] = calculate_model_price_sync(model, x402_prices, effort)

    return prices


async def get_pricing_breakdown(models: list[str], effort: str = 'medium') -> dict:
    """
    Get detailed pricing breakdown for display.

    Returns:
        {
            'models': {model: {'x402_id': x, 'price_per_request': p, 'requests': n, 'subtotal': y}},
            'effort': effort,
            'requests_per_audit': n,
            'markup_percent': x,
            'total_before_markup': x,
            'total': y
        }
    """
    x402_prices = await get_x402_pricing()
    markup = settings.PAYMENT_MARKUP
    num_requests = REQUESTS_PER_EFFORT.get(effort, REQUESTS_PER_EFFORT['medium'])

    breakdown = {
        'models': {},
        'effort': effort,
        'requests_per_audit': num_requests,
        'markup_percent': int(markup * 100),
        'total_before_markup': 0.0,
        'total': 0.0,
    }

    for model in models:
        x402_id = get_x402_model_id(model)
        request_price = get_model_request_price(model, x402_prices)
        subtotal_before_markup = request_price * num_requests
        subtotal = subtotal_before_markup * (1 + markup)

        breakdown['models'][model] = {
            'x402_model_id': x402_id,
            'price_per_request': round(request_price, 4),
            'requests': num_requests,
            'subtotal_before_markup': round(subtotal_before_markup, 4),
            'subtotal': round(subtotal, 2),
        }
        breakdown['total_before_markup'] += subtotal_before_markup

    breakdown['total_before_markup'] = round(breakdown['total_before_markup'], 4)
    breakdown['total'] = round(breakdown['total_before_markup'] * (1 + markup), 2)

    return breakdown


def get_x402_endpoint(model: str) -> str:
    """Get the x402 API endpoint for a model."""
    x402_id = get_x402_model_id(model)
    # Convert llm-xxx to llm/xxx for the endpoint
    endpoint_path = x402_id.replace('llm-', 'llm/')
    return f'{X402_BASE_URL}/api/{endpoint_path}'


# Legacy exports for API compatibility
TOKEN_BUDGETS = {
    'low': {'input_tokens': 50_000, 'output_tokens': 10_000},
    'medium': {'input_tokens': 150_000, 'output_tokens': 30_000},
    'high': {'input_tokens': 300_000, 'output_tokens': 60_000},
}

# Note: REQUESTS_PER_EFFORT is now defined at module top level with actual values
