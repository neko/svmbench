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
    REQUESTS_PER_EFFORT,
    TOKEN_BUDGETS,
    calculate_model_price,
    calculate_total_price,
    get_all_model_prices,
)
from api.util.solana import verify_usdc_transfer

SessionDep = Annotated[AsyncSession, Depends(get_db)]

router = APIRouter(prefix='/payment', tags=['payment'])


class PaymentConfigResponse(BaseModel):
    enabled: bool
    receiver_wallet: str | None
    markup: float
    model_prices: dict[str, dict[str, float]]  # model -> {effort -> price}
    token_budgets: dict[str, dict[str, int]]   # effort -> {input_tokens, output_tokens}
    requests_per_effort: dict[str, int]        # effort -> estimated API requests


class CalculatePriceRequest(BaseModel):
    models: list[str]
    effort: Literal['low', 'medium', 'high']


class CalculatePriceResponse(BaseModel):
    total: float
    breakdown: dict[str, float]  # model -> price


class VerifyPaymentRequest(BaseModel):
    signature: str
    payer_wallet: str
    amount: float
    models: list[str]
    effort: Literal['low', 'medium', 'high']


class VerifyPaymentResponse(BaseModel):
    valid: bool
    payment_token: str


@router.get('/config')
async def get_payment_config() -> PaymentConfigResponse:
    """Get payment configuration including per-model pricing from x402 engine."""
    # Build model prices for all effort levels
    model_prices: dict[str, dict[str, float]] = {}

    all_models = list(ALLOWED_MODELS) + list(OPENROUTER_ALLOWED_MODELS)

    # Fetch prices for each effort level (uses cached x402 pricing)
    prices_low = await get_all_model_prices('low')
    prices_medium = await get_all_model_prices('medium')
    prices_high = await get_all_model_prices('high')

    for model in all_models:
        model_prices[model] = {
            'low': prices_low.get(model, 0),
            'medium': prices_medium.get(model, 0),
            'high': prices_high.get(model, 0),
        }

    return PaymentConfigResponse(
        enabled=settings.PAYMENT_ENABLED,
        receiver_wallet=settings.PAYMENT_RECEIVER_WALLET,
        markup=settings.PAYMENT_MARKUP,
        model_prices=model_prices,
        token_budgets=TOKEN_BUDGETS,
        requests_per_effort=REQUESTS_PER_EFFORT,
    )


@router.post('/calculate')
async def calc_price_endpoint(request: CalculatePriceRequest) -> CalculatePriceResponse:
    """Calculate price for specific models and effort level."""
    breakdown = {}
    for model in request.models:
        breakdown[model] = await calculate_model_price(model, request.effort)

    total = await calculate_total_price(request.models, request.effort)

    return CalculatePriceResponse(
        total=total,
        breakdown=breakdown,
    )


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

    # Calculate expected price based on models and effort
    expected_amount = await calculate_total_price(request.models, request.effort)

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
