"""combo support: widen sport CHECK to allow 'combo' + add combo_leg table

Combo (multivariate / parlay) bets are their own sport category so they never
pollute soccer-only ledger stats. Widen ck_bet_sport and ck_market_sport to
admit 'combo' (SQLite can't alter a CHECK in place, so batch_alter_table does
the copy-rebuild). Add the combo_leg child table — one row per parlay leg,
descriptive metadata only (no money; the parent Bet owns stake/fees/P&L).

Frozen literals mirror core.types.Sport (migrations don't import app code).

Revision ID: a5138cd815e8
Revises: a7b9c1d2e3f4
Create Date: 2026-06-05 14:16:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a5138cd815e8'
down_revision: Union[str, Sequence[str], None] = 'a7b9c1d2e3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.drop_constraint('ck_bet_sport', type_='check')
        batch_op.create_check_constraint(
            'ck_bet_sport', "sport IN ('soccer', 'nfl', 'combo')"
        )

    with op.batch_alter_table('market', schema=None) as batch_op:
        batch_op.drop_constraint('ck_market_sport', type_='check')
        batch_op.create_check_constraint(
            'ck_market_sport', "sport IN ('soccer', 'nfl', 'combo')"
        )

    op.create_table(
        'combo_leg',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bet_id', sa.Integer(), nullable=False),
        sa.Column('leg_index', sa.Integer(), nullable=False),
        sa.Column('leg_ticker', sa.String(length=128), nullable=True),
        sa.Column('leg_event_ticker', sa.String(length=128), nullable=True),
        sa.Column('leg_title', sa.String(length=96), nullable=True),
        sa.Column('side', sa.String(length=8), nullable=True),
        sa.Column('result', sa.String(length=8), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(['bet_id'], ['bet.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('bet_id', 'leg_index', name='uq_combo_leg_bet_index'),
        sa.CheckConstraint("side IS NULL OR side IN ('yes', 'no')", name='ck_combo_leg_side'),
        sa.CheckConstraint("result IS NULL OR result IN ('yes', 'no')", name='ck_combo_leg_result'),
    )
    op.create_index('ix_combo_leg_bet_id', 'combo_leg', ['bet_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    Drop any combo bets/markets first: the batch rebuild copies via INSERT..SELECT
    into a table carrying the re-narrowed CHECK, so a single 'combo' row would
    abort the downgrade. combo_leg rows cascade from their parent bets.
    """
    op.drop_index('ix_combo_leg_bet_id', table_name='combo_leg')
    op.drop_table('combo_leg')

    op.execute("DELETE FROM bet WHERE sport = 'combo'")
    op.execute("DELETE FROM market WHERE sport = 'combo'")

    with op.batch_alter_table('market', schema=None) as batch_op:
        batch_op.drop_constraint('ck_market_sport', type_='check')
        batch_op.create_check_constraint(
            'ck_market_sport', "sport IN ('soccer', 'nfl')"
        )

    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.drop_constraint('ck_bet_sport', type_='check')
        batch_op.create_check_constraint(
            'ck_bet_sport', "sport IN ('soccer', 'nfl')"
        )
