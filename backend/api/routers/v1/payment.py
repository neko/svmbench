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
    TOKEN_BUDGET,
    calculate_model_price,
    calculate_model_price_sync,
    calculate_total_price,
    get_pricing_breakdown,
    get_x402_models,
    get_x402_pricing,
)
from api.util.solana import verify_usdc_transfer

SessionDep = Annotated[AsyncSession, Depends(get_db)]

router = APIRouter(prefix='/payment', tags=['payment'])


class ModelInfo(BaseModel):
    id: str
    name: str
    input_price: float
    output_price: float
    audit_price: float


class PaymentConfigResponse(BaseModel):
    enabled: bool
    receiver_wallet: str | None
    markup: float
    markup_percent: int
    token_budgets: dict[str, dict[str, int]]
    models: list[ModelInfo]
    model_prices: dict[str, dict[str, float]]


class CalculatePriceRequest(BaseModel):
    models: list[str]
    effort: Literal['low', 'medium', 'high'] = 'high'


class CalculatePriceResponse(BaseModel):
    total: float
    breakdown: dict[str, float]
    effort: str
    token_budget: dict[str, int]
    markup_percent: int


class VerifyPaymentRequest(BaseModel):
    signature: str
    payer_wallet: str
    amount: float
    model: str
    effort: Literal['low', 'medium', 'high'] = 'high'


class VerifyPaymentResponse(BaseModel):
    valid: bool
    payment_token: str


@router.get('/config')
async def get_payment_config() -> PaymentConfigResponse:
    """Get payment configuration with per-model pricing."""
    models_raw = await get_x402_models()
    pricing = await get_x402_pricing()

    models: list[ModelInfo] = []
    for model in models_raw:
        model_id = model['id']
        models.append(ModelInfo(
            id=model_id,
            name=model['name'],
            input_price=model.get('input_price', 0),
            output_price=model.get('output_price', 0),
            audit_price=calculate_model_price_sync(model_id, pricing),
        ))

    # All efforts use the same (max) price now
    model_prices: dict[str, dict[str, float]] = {}
    for effort in ['low', 'medium', 'high']:
        model_prices[effort] = {m['id']: calculate_model_price_sync(m['id'], pricing) for m in models_raw}

    return PaymentConfigResponse(
        enabled=settings.PAYMENT_ENABLED,
        receiver_wallet=settings.SOLANA_SERVICE_WALLET_ADDRESS,
        markup=settings.PAYMENT_MARKUP,
        markup_percent=int(settings.PAYMENT_MARKUP * 100),
        token_budgets={'low': TOKEN_BUDGET, 'medium': TOKEN_BUDGET, 'high': TOKEN_BUDGET},
        models=models,
        model_prices=model_prices,
    )


@router.post('/calculate')
async def calc_price_endpoint(request: CalculatePriceRequest) -> CalculatePriceResponse:
    """Calculate price for models (effort ignored, always max)."""
    breakdown = {model: await calculate_model_price(model) for model in request.models}
    total = await calculate_total_price(request.models)

    return CalculatePriceResponse(
        total=total,
        breakdown=breakdown,
        effort='high',
        token_budget=TOKEN_BUDGET,
        markup_percent=int(settings.PAYMENT_MARKUP * 100),
    )


@router.post('/breakdown')
async def get_price_breakdown(request: CalculatePriceRequest) -> dict:
    """Get detailed pricing breakdown."""
    return await get_pricing_breakdown(request.models)


@router.post('/verify')
async def verify_payment(request: VerifyPaymentRequest, session: SessionDep) -> VerifyPaymentResponse:
    if not settings.PAYMENT_ENABLED:
        raise HTTPException(status_code=400, detail='Payments are not enabled')

    if not settings.SOLANA_SERVICE_WALLET_ADDRESS:
        raise HTTPException(status_code=500, detail='Payment receiver wallet not configured')

    if not request.model:
        raise HTTPException(status_code=400, detail='Model is required')

    existing = await session.execute(select(Payment).where(Payment.signature == request.signature))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail='Payment signature already used')

    expected_amount = await calculate_total_price([request.model])

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

    if request.amount < expected_amount - 0.01:
        raise HTTPException(status_code=400, detail=f'Payment ${request.amount:.2f} < required ${expected_amount:.2f}')

    payment_token = secrets.token_hex(32)
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

    return VerifyPaymentResponse(valid=True, payment_token=payment_token)
