import asyncio
import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.core.deps import get_db
from api.models.payment import Payment
from api.util.pricing import (
    TOKEN_BUDGETS,
    calculate_model_price,
    calculate_model_price_sync,
    calculate_total_price,
    get_pricing_breakdown,
    get_x402_models,
    get_x402_pricing,
)
from api.util.solana import verify_usdc_transfer
from api.util.telegram import log_payment_received

SessionDep = Annotated[AsyncSession, Depends(get_db)]

router = APIRouter(prefix='/payment', tags=['payment'])


class ModelInfo(BaseModel):
    id: str
    name: str
    input_price: float  # per 1M input tokens
    output_price: float  # per 1M output tokens
    audit_price: float  # total price for audit (with markup)


class PaymentConfigResponse(BaseModel):
    enabled: bool
    receiver_wallet: str | None
    markup: float
    markup_percent: int
    token_budgets: dict[str, dict[str, int]]  # effort -> {input_tokens, output_tokens}
    models: list[ModelInfo]  # available models with pricing
    model_prices: dict[str, dict[str, float]]  # effort -> {model -> price}


class CalculatePriceRequest(BaseModel):
    models: list[str]
    effort: Literal['low', 'medium', 'high'] = 'medium'


class CalculatePriceResponse(BaseModel):
    total: float
    breakdown: dict[str, float]  # model -> price
    effort: str
    token_budget: dict[str, int]  # input_tokens, output_tokens
    markup_percent: int


class VerifyPaymentRequest(BaseModel):
    signature: str
    payer_wallet: str
    amount: float
    model: str
    effort: Literal['low', 'medium', 'high'] = 'medium'


class VerifyPaymentResponse(BaseModel):
    valid: bool
    payment_token: str


@router.get('/config')
async def get_payment_config() -> PaymentConfigResponse:
    """Get payment configuration including per-model pricing from Daydreams router.

    Daydreams uses token-based pricing. Token budgets vary by effort level:
    - low: ~50k input, ~10k output
    - medium: ~150k input, ~30k output
    - high: ~300k input, ~60k output

    Price = (input_tokens * input_price/1M) + (output_tokens * output_price/1M) * (1 + markup)

    Models are fetched dynamically from Daydreams router.
    """
    # Fetch models and prices from Daydreams
    models_raw = await get_x402_models()
    pricing = await get_x402_pricing()

    # Build model info list with audit prices (using medium as default display price)
    models: list[ModelInfo] = []

    for model in models_raw:
        model_id = model['id']
        audit_price = calculate_model_price_sync(model_id, pricing, effort='medium')
        models.append(ModelInfo(
            id=model_id,
            name=model['name'],
            input_price=model.get('input_price', 0),
            output_price=model.get('output_price', 0),
            audit_price=audit_price,
        ))

    # Build prices for all effort levels
    model_prices: dict[str, dict[str, float]] = {}
    for effort in ['low', 'medium', 'high']:
        model_prices[effort] = {}
        for model in models_raw:
            model_id = model['id']
            model_prices[effort][model_id] = calculate_model_price_sync(model_id, pricing, effort=effort)

    return PaymentConfigResponse(
        enabled=settings.PAYMENT_ENABLED,
        receiver_wallet=settings.SOLANA_SERVICE_WALLET_ADDRESS,
        markup=settings.PAYMENT_MARKUP,
        markup_percent=int(settings.PAYMENT_MARKUP * 100),
        token_budgets=TOKEN_BUDGETS,
        models=models,
        model_prices=model_prices,
    )


@router.post('/calculate')
async def calc_price_endpoint(request: CalculatePriceRequest) -> CalculatePriceResponse:
    """Calculate price for specific models and effort level.

    Daydreams uses token-based pricing.
    Price = (input_tokens * input_price/1M) + (output_tokens * output_price/1M) * (1 + markup)
    """
    breakdown = {}
    for model in request.models:
        breakdown[model] = await calculate_model_price(model, request.effort)

    total = await calculate_total_price(request.models, request.effort)

    return CalculatePriceResponse(
        total=total,
        breakdown=breakdown,
        effort=request.effort,
        token_budget=TOKEN_BUDGETS.get(request.effort, TOKEN_BUDGETS['medium']),
        markup_percent=int(settings.PAYMENT_MARKUP * 100),
    )


@router.post('/breakdown')
async def get_price_breakdown(request: CalculatePriceRequest) -> dict:
    """Get detailed pricing breakdown showing x402 costs."""
    return await get_pricing_breakdown(request.models, request.effort)


@router.post('/verify')
async def verify_payment(
    request: VerifyPaymentRequest,
    session: SessionDep,
) -> VerifyPaymentResponse:
    if not settings.PAYMENT_ENABLED:
        raise HTTPException(status_code=400, detail='Payments are not enabled')

    if not settings.SOLANA_SERVICE_WALLET_ADDRESS:
        raise HTTPException(status_code=500, detail='Payment receiver wallet not configured')

    if not request.model:
        raise HTTPException(status_code=400, detail='Model is required')

    # Check if this signature has already been used
    existing = await session.execute(
        select(Payment).where(Payment.signature == request.signature)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail='Payment signature already used')

    # Calculate expected price based on model and effort (x402 flat per-request pricing)
    expected_amount = await calculate_total_price([request.model], request.effort)

    try:
        is_valid = await verify_usdc_transfer(
            signature=request.signature,
            expected_sender=request.payer_wallet,
            expected_receiver=settings.SOLANA_SERVICE_WALLET_ADDRESS,
            expected_amount=request.amount,
            rpc_url=settings.SOLANA_RPC_URL,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'Failed to verify transaction: {e}') from e

    if not is_valid:
        raise HTTPException(status_code=400, detail='Transaction verification failed')

    # Check amount matches expected price (allow small tolerance for floating point)
    if request.amount < expected_amount - 0.01:
        raise HTTPException(
            status_code=400,
            detail=f'Payment amount ${request.amount:.2f} is less than required ${expected_amount:.2f}',
        )

    # Generate payment token
    payment_token = secrets.token_hex(32)

    # Store payment record with model
    payment = Payment(
        signature=request.signature,
        payer_wallet=request.payer_wallet,
        amount=request.amount,
        effort=request.effort,
        models=request.model,
        payment_token=payment_token,
    )
    session.add(payment)
    await session.commit()

    # Log to Telegram (fire and forget)
    asyncio.create_task(log_payment_received(
        signature=request.signature,
        amount=request.amount,
        payer_wallet=request.payer_wallet,
        model=request.model,
    ))

    return VerifyPaymentResponse(valid=True, payment_token=payment_token)


async def validate_payment_token(
    session: SessionDep,
    payment_token: str,
    effort: str,
    model: str,
) -> Payment | None:
    """Validate and consume a payment token. Returns the Payment if valid, None otherwise."""
    result = await session.execute(
        select(Payment).where(
            Payment.payment_token == payment_token,
            Payment.used == False,  # noqa: E712
            Payment.effort == effort,
        )
    )
    payment = result.scalar_one_or_none()

    if not payment:
        return None

    # Verify model matches
    if payment.models != model:
        return None

    payment.used = True
    from datetime import datetime, timezone
    payment.used_at = datetime.now(timezone.utc)
    await session.commit()

    return payment
