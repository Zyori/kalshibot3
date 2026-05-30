"""position: exact cost_basis + Kalshi realized_pnl + fees_paid

Adds three nullable columns to `position` so we mirror Kalshi's authoritative
per-position numbers instead of flooring market_exposure/qty to whole cents:

  cost_basis_cents    exact total cost (Kalshi market_exposure)
  realized_pnl_cents  Kalshi's authoritative realized PnL (fee-inclusive)
  fees_paid_cents     Kalshi's authoritative fees

No backfill in the migration — position_sync repopulates every open position
from Kalshi on its next tick (live self-heal), so existing rows correct
themselves within ~60s of the backend restarting.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-30 17:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('position', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cost_basis_cents', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('realized_pnl_cents', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('fees_paid_cents', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('position', schema=None) as batch_op:
        batch_op.drop_column('fees_paid_cents')
        batch_op.drop_column('realized_pnl_cents')
        batch_op.drop_column('cost_basis_cents')
