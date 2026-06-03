"""trade_snapshot final phase: status_detail column + 'final' in phase CHECK

Adds the `final` lifecycle phase (the game's own ending, stamped on every
positioned bet when ESPN flips the match in->post) and the `status_detail`
column that carries its FT/AET/Penalties label. The phase CHECK is rebuilt to
admit 'final' — SQLite can't alter a CHECK in place, so batch_alter_table does
the copy-rebuild. Frozen literal mirrors core.types.SnapshotPhase (migrations
don't import app code).

Revision ID: a7b9c1d2e3f4
Revises: 58f4848513ef
Create Date: 2026-06-03 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7b9c1d2e3f4'
down_revision: Union[str, Sequence[str], None] = '58f4848513ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('trade_snapshot', schema=None) as batch_op:
        batch_op.add_column(sa.Column('status_detail', sa.String(length=16), nullable=True))
        batch_op.drop_constraint('ck_trade_snapshot_phase', type_='check')
        batch_op.create_check_constraint(
            'ck_trade_snapshot_phase',
            "phase IN ('entry', 'exit_open', 'exit_close', 'final')",
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('trade_snapshot', schema=None) as batch_op:
        batch_op.drop_constraint('ck_trade_snapshot_phase', type_='check')
        batch_op.create_check_constraint(
            'ck_trade_snapshot_phase',
            "phase IN ('entry', 'exit_open', 'exit_close')",
        )
        batch_op.drop_column('status_detail')
