"""bet market-label fields — structured event/teams/selection

Adds six nullable columns to `bet` so each row carries the structured pieces of
its market for a readable ledger label ("League — Home v Away — Selection SIDE")
and future analysis:

  event_series, home_code, away_code, selection_code  — from the ticker (always
    present for a per-game market)
  home_name, away_name                                — from the live ESPN feed
    at placement; null when no match was resolved (futures, early pre-match)

All nullable: no backfill. Pre-existing rows and unparseable tickers fall back
to showing the raw ticker at display. (Beta data is wiped before the World Cup,
so there is no history worth backfilling.)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-02 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = (
    ('event_series', sa.String(length=48)),
    ('home_code', sa.String(length=8)),
    ('away_code', sa.String(length=8)),
    ('home_name', sa.String(length=64)),
    ('away_name', sa.String(length=64)),
    ('selection_code', sa.String(length=8)),
)


def upgrade() -> None:
    with op.batch_alter_table('bet', schema=None) as batch_op:
        for name, type_ in _COLUMNS:
            batch_op.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('bet', schema=None) as batch_op:
        for name, _ in reversed(_COLUMNS):
            batch_op.drop_column(name)
