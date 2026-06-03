"""add trade_snapshot

Revision ID: 58f4848513ef
Revises: d4e5f6a7b8c9
Create Date: 2026-06-03 00:17:31.104140

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58f4848513ef'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('trade_snapshot',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('bet_id', sa.Integer(), nullable=False),
    sa.Column('phase', sa.String(length=16), nullable=False),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('game_clock', sa.String(length=16), nullable=True),
    sa.Column('score_home', sa.Integer(), nullable=True),
    sa.Column('score_away', sa.Integer(), nullable=True),
    sa.Column('run_of_play_json', sa.JSON(), nullable=True),
    sa.Column('market_mid_cents', sa.Integer(), nullable=True),
    sa.Column('price_history_json', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("phase IN ('entry', 'exit_open', 'exit_close')", name='ck_trade_snapshot_phase'),
    sa.ForeignKeyConstraint(['bet_id'], ['bet.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bet_id', 'phase', name='uq_trade_snapshot_bet_phase')
    )
    with op.batch_alter_table('trade_snapshot', schema=None) as batch_op:
        batch_op.create_index('ix_trade_snapshot_bet_id', ['bet_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('trade_snapshot', schema=None) as batch_op:
        batch_op.drop_index('ix_trade_snapshot_bet_id')

    op.drop_table('trade_snapshot')
