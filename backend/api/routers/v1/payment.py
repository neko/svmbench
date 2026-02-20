import secrets
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import settings
from api.core.const import ALLOWED_MODELS, OPENROUTER_ALLOWED_MODELS
from api.core.deps import get_db
from api.models.payment import Payment
from api.util.pricing import (
    REQUESTS_PER_AUDIT,
    REQUESTS_PER_EFFORT,
    TOKEN_BUDGETS,
    calculate_model_price,
    calculate_total_price,
    get_all_model_prices,
    get_pricing_breakdown,
)
from api.util.solana import verify_usdc_transfer

SessionDep = Annotated[AsyncSession, Depends(get_db)]

router = APIRouter(prefix='/payment', tags=['payment'])


class PaymentConfigResponse(BaseModel):
    enabled: bool
    receiver_wallet: str | None
    markup: float
    markup_percent: int
    requests_per_audit: int  # fixed 2 requests per model audit
    model_prices: dict[str, float]  # model -> total price (includes markup)


class CalculatePriceRequest(BaseModel):
    models: list[str]


class CalculatePriceResponse(BaseModel):
    total: float
    breakdown: dict[str, float]  # model -> price
    requests_per_audit: int
    markup_percent: int


class VerifyPaymentRequest(BaseModel):
    signature: str
    payer_wallet: str
    amount: float
    models: list[str]
    effort: Literal['low', 'medium', 'high'] = 'medium'  # kept for compatibility


class VerifyPaymentResponse(BaseModel):
    valid: bool
    payment_token: str


@router.get('/config')
async def get_payment_config() -> PaymentConfigResponse:
    """Get payment configuration including per-model pricing from x402 engine.

    x402 uses flat per-request pricing. Each audit makes 2 API calls per model.
    Price = x402_model_price × 2 × (1 + markup)
    """
    # Get prices for all models (already includes markup)
    model_prices = await get_all_model_prices()

    return PaymentConfigResponse(
        enabled=settings.PAYMENT_ENABLED,
        receiver_wallet=settings.PAYMENT_RECEIVER_WALLET,
        markup=settings.PAYMENT_MARKUP,
        markup_percent=int(settings.PAYMENT_MARKUP * 100),
        requests_per_audit=REQUESTS_PER_AUDIT,
        model_prices=model_prices,
    )


@router.post('/calculate')
async def calc_price_endpoint(request: CalculatePriceRequest) -> CalculatePriceResponse:
    """Calculate price for specific models.

    x402 uses flat per-request pricing. Price = x402_price × 2 × (1 + markup)
    """
    breakdown = {}
    for model in request.models:
        breakdown[model] = await calculate_model_price(model)

    total = await calculate_total_price(request.models)

    return CalculatePriceResponse(
        total=total,
        breakdown=breakdown,
        requests_per_audit=REQUESTS_PER_AUDIT,
        markup_percent=int(settings.PAYMENT_MARKUP * 100),
    )


@router.post('/breakdown')
async def get_price_breakdown(request: CalculatePriceRequest) -> dict:
    """Get detailed pricing breakdown showing x402 costs."""
    return await get_pricing_breakdown(request.models)


@router.post('/verify')
async def verify_payment(
    request: VerifyPaymentRequest,
    session: SessionDep,
) -> VerifyPaymentResponse:
    if not settings.PAYMENT_ENABLED:
        raise HTTPException(status_code=400, detail='Payments are not enabled')

    if not settings.PAYMENT_RECEIVER_WALLET:
        raise HTTPException(status_code=500, detail='Payment receiver wallet not configured')

    if not request.models:
        raise HTTPException(status_code=400, detail='At least one model is required')

    # Check if this signature has already been used
    existing = await session.execute(
        select(Payment).where(Payment.signature == request.signature)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail='Payment signature already used')

    # Calculate expected price based on models (x402 flat per-request pricing)
    expected_amount = await calculate_total_price(request.models)

    try:
        is_valid = await verify_usdc_transfer(
            signature=request.signature,
            expected_sender=request.payer_wallet,
            expected_receiver=settings.PAYMENT_RECEIVER_WALLET,
            expected_amount=request.amount,
            rpc_url=settings.PAYMENT_SOLANA_RPC_URL,
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

    # Store payment record with models
    payment = Payment(
        signature=request.signature,
        payer_wallet=request.payer_wallet,
        amount=request.amount,
        effort=request.effort,
        models=','.join(request.models),
        payment_token=payment_token,
    )
    session.add(payment)
    await session.commit()

    return VerifyPaymentResponse(valid=True, payment_token=payment_token)


async def validate_payment_token(
    session: SessionDep,
    payment_token: str,
    effort: str,
    models: list[str],
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

    # Verify models match (payment must cover requested models)
    paid_models = set(payment.models.split(',')) if payment.models else set()
    requested_models = set(models)

    if not requested_models.issubset(paid_models):
        return None

    payment.used = True
    from datetime import datetime, timezone
    payment.used_at = datetime.now(timezone.utc)
    await session.commit()

    return payment
