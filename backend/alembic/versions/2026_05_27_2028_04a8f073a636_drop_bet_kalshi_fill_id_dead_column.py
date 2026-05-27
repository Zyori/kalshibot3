"""drop bet.kalshi_fill_id (dead column)

bet.kalshi_fill_id was the old one-bet-per-order model's "which Kalshi
trade closed this bet" pointer. With bet_fill as the per-fill source of
truth, the column is dead state: written only on buy fills (last-fill-wins
on fragmented buys), explicitly NOT written on sells (the UNIQUE constraint
collided when one sell closed multiple openers), and never read anywhere.

Revision ID: 04a8f073a636
Revises: d193b8f26a27
Create Date: 2026-05-27 20:28:44.299032
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '04a8f073a636'
down_revision: Union[str, Sequence[str], None] = 'd193b8f26a27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.drop_constraint('uq_bet_kalshi_fill_id', type_='unique')
        batch_op.drop_column('kalshi_fill_id')


def downgrade() -> None:
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kalshi_fill_id', sa.String(length=64), nullable=True))
        batch_op.create_unique_constraint('uq_bet_kalshi_fill_id', ['kalshi_fill_id'])
