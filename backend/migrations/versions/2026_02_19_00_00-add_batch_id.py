"""add batch_id

Revision ID: 5f8a2b3c4d5e
Revises: 8fb963fca070
Create Date: 2026-02-19 00:00:00.000000+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5f8a2b3c4d5e'
down_revision: str | None = '8fb963fca070'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('batch_id', sa.Uuid(), nullable=True))
    op.create_index('ix_jobs_batch_id', 'jobs', ['batch_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_jobs_batch_id', table_name='jobs')
    op.drop_column('jobs', 'batch_id')
