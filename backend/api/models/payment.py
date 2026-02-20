import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from api.core.database import Base


class Payment(Base):
    __tablename__ = 'payments'
    __table_args__ = (
        Index('ix_payments_signature', 'signature', unique=True),
        Index('ix_payments_payer_wallet', 'payer_wallet'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )

    signature: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    payer_wallet: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(precision=18, scale=6), nullable=False)
    effort: Mapped[str] = mapped_column(String(16), nullable=False)
    # Comma-separated list of model IDs this payment covers
    models: Mapped[str] = mapped_column(String(1024), nullable=False)

    # Token for claiming this payment when submitting a job
    payment_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    # Whether this payment has been used
    used: Mapped[bool] = mapped_column(default=False, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
