"""bet.remaining_quantity_centi for sub-contract precision

remaining_quantity stored in whole contracts (int) silently floor-divided
sub-contract residuals to 0, flipping bets terminal while real Kalshi
exposure remained (e.g. a fee-tier split that closes 0.97 of 1 contract
leaves 0.03 contracts; floor(0.03) = 0, bet incorrectly flips WON/LOST).
remaining_quantity_centi (int * 100) keeps Kalshi's centi-precision exact
so the terminal flip only fires when truly 0.

Revision ID: d193b8f26a27
Revises: 85650fa4cfa1
Create Date: 2026-05-27 20:15:51.584646
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd193b8f26a27'
down_revision: Union[str, Sequence[str], None] = '85650fa4cfa1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.add_column(sa.Column('remaining_quantity_centi', sa.Integer(), nullable=False, server_default='0'))

    op.execute(
        "UPDATE bet SET remaining_quantity_centi = remaining_quantity * 100"
    )


def downgrade() -> None:
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.drop_column('remaining_quantity_centi')
