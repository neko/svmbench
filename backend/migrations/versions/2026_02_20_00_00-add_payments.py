"""add payments table

Revision ID: 6a7b8c9d0e1f
Revises: 5f8a2b3c4d5e
Create Date: 2026-02-20 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '6a7b8c9d0e1f'
down_revision: str | None = '5f8a2b3c4d5e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'payments',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('signature', sa.String(128), nullable=False),
        sa.Column('payer_wallet', sa.String(64), nullable=False),
        sa.Column('amount', sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column('effort', sa.String(16), nullable=False),
        sa.Column('models', sa.String(1024), nullable=False),
        sa.Column('payment_token', sa.String(64), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_payments_signature', 'payments', ['signature'], unique=True)
    op.create_index('ix_payments_payer_wallet', 'payments', ['payer_wallet'], unique=False)
    op.create_index('ix_payments_payment_token', 'payments', ['payment_token'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_payments_payment_token', table_name='payments')
    op.drop_index('ix_payments_payer_wallet', table_name='payments')
    op.drop_index('ix_payments_signature', table_name='payments')
    op.drop_table('payments')
