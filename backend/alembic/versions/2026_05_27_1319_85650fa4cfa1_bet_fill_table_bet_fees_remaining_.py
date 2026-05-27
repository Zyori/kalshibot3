"""bet_fill table; bet fees + remaining_quantity + realized_pnl

Per-fill source-of-truth table keyed by Kalshi trade_id. Stores each fill's
authoritative fee_cost (parsed from /portfolio/fills) so the ledger never
estimates. Bets gain remaining_quantity (for partial closes / scaling out),
entry/exit_fees_cents (sums over bet_fill rows attached to this bet), and
realized_pnl_cents (running PnL as sells close shares of the bet).

The old "one bet per buy/sell order" model where a sell-order row got
deleted on fill is replaced by: one bet per buy decision, sells aggregate
onto the oldest matching OPEN bet via FIFO, the bet stays OPEN until
remaining_quantity hits 0.

Revision ID: 85650fa4cfa1
Revises: 4fab9c57deec
Create Date: 2026-05-27 13:19:15.595525
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '85650fa4cfa1'
down_revision: Union[str, Sequence[str], None] = '4fab9c57deec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'bet_fill',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bet_id', sa.Integer(), nullable=True),
        sa.Column('trade_id', sa.String(length=64), nullable=False),
        sa.Column('order_id', sa.String(length=64), nullable=False),
        sa.Column('ticker', sa.String(length=128), nullable=False),
        sa.Column('side', sa.String(length=8), nullable=False),
        sa.Column('action', sa.String(length=8), nullable=False),
        sa.Column('price_cents', sa.Integer(), nullable=False),
        sa.Column('quantity_centi', sa.Integer(), nullable=False),
        sa.Column('fee_cents', sa.Integer(), nullable=True),
        sa.Column('is_taker', sa.Boolean(), nullable=True),
        sa.Column('fee_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.CheckConstraint("action IN ('buy', 'sell')", name='ck_bet_fill_action'),
        sa.CheckConstraint("side IN ('yes', 'no')", name='ck_bet_fill_side'),
        sa.CheckConstraint('price_cents >= 1 AND price_cents <= 99', name='ck_bet_fill_price_range'),
        sa.CheckConstraint('quantity_centi >= 1', name='ck_bet_fill_quantity_positive'),
        sa.ForeignKeyConstraint(['bet_id'], ['bet.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('trade_id', name='uq_bet_fill_trade_id'),
    )
    with op.batch_alter_table('bet_fill', schema=None) as batch_op:
        batch_op.create_index('ix_bet_fill_bet_id', ['bet_id'], unique=False)
        batch_op.create_index('ix_bet_fill_order_id', ['order_id'], unique=False)
        batch_op.create_index('ix_bet_fill_ticker', ['ticker'], unique=False)
        batch_op.create_index('ix_bet_fill_fee_synced_at', ['fee_synced_at'], unique=False)

    # SQLite needs batch_alter_table for ADD COLUMN with defaults on existing rows.
    # Also relax ck_bet_exit_price_range to 0..100 (Kalshi settlement values can
    # be 0 = NO won or 100 = YES won; the old 1..99 constraint was wrong).
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.add_column(sa.Column('remaining_quantity', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('entry_fees_cents', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('exit_fees_cents', sa.Integer(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('realized_pnl_cents', sa.Integer(), nullable=True))
        batch_op.drop_constraint('ck_bet_exit_price_range', type_='check')
        batch_op.create_check_constraint(
            'ck_bet_exit_price_range',
            'exit_price_cents IS NULL OR (exit_price_cents >= 0 AND exit_price_cents <= 100)',
        )

    # Backfill remaining_quantity: OPEN bets still hold their full quantity;
    # terminal bets have 0 remaining. Use existing data so nothing breaks.
    op.execute(
        "UPDATE bet SET remaining_quantity = quantity WHERE status = 'open'"
    )
    op.execute(
        "UPDATE bet SET realized_pnl_cents = pnl_cents WHERE pnl_cents IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.drop_column('realized_pnl_cents')
        batch_op.drop_column('exit_fees_cents')
        batch_op.drop_column('entry_fees_cents')
        batch_op.drop_column('remaining_quantity')

    with op.batch_alter_table('bet_fill', schema=None) as batch_op:
        batch_op.drop_index('ix_bet_fill_fee_synced_at')
        batch_op.drop_index('ix_bet_fill_ticker')
        batch_op.drop_index('ix_bet_fill_order_id')
        batch_op.drop_index('ix_bet_fill_bet_id')

    op.drop_table('bet_fill')
