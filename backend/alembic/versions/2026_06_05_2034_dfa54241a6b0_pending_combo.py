"""pending_combo: stash legs for an accepted RFQ combo awaiting its async fill

Combos fill via RFQ — we accept a quote (no order_id), the maker confirms, and
the order fills async. The fill carries the combo ticker + real order_id but not
the legs, so we stash them here at accept time and consume the row when the fill
lands. DB-backed so a combo accepted before a restart still records.

Revision ID: dfa54241a6b0
Revises: 772adcff7c69
Create Date: 2026-06-05 20:34:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dfa54241a6b0'
down_revision: Union[str, Sequence[str], None] = '772adcff7c69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'pending_combo',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('combo_ticker', sa.String(length=128), nullable=False),
        sa.Column('side', sa.String(length=8), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False),
        sa.Column('legs_json', sa.JSON(), nullable=False),
        sa.Column('strategy', sa.String(length=32), nullable=False),
        sa.Column('confidence', sa.String(length=8), nullable=False),
        sa.Column('timing', sa.String(length=16), nullable=False),
        sa.Column('tags_json', sa.JSON(), nullable=True),
        sa.Column('human_reasoning', sa.String(length=2048), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('combo_ticker', name='uq_pending_combo_ticker'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('pending_combo')
