"""widen ck_position_sport to allow 'combo'

The combo migration (a5138cd815e8) widened the bet and market sport CHECKs but
missed the position table. position_sync now writes combo positions with
sport='combo' (previously hardcoded 'soccer', which mislabeled them); without
this widening the sync would crash on the CHECK for every combo position held.

Frozen literal mirrors core.types.Sport (migrations don't import app code).

Revision ID: 772adcff7c69
Revises: a5138cd815e8
Create Date: 2026-06-05 17:09:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '772adcff7c69'
down_revision: Union[str, Sequence[str], None] = 'a5138cd815e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('position', schema=None) as batch_op:
        batch_op.drop_constraint('ck_position_sport', type_='check')
        batch_op.create_check_constraint(
            'ck_position_sport', "sport IN ('soccer', 'nfl', 'combo')"
        )


def downgrade() -> None:
    """Downgrade schema.

    Drop combo positions first: the batch rebuild copies via INSERT..SELECT into
    a table carrying the re-narrowed CHECK, so a single 'combo' row would abort
    the downgrade. Positions are a mirror of Kalshi state (re-synced on the next
    poll), so deleting them to roll back is safe.
    """
    op.execute("DELETE FROM position WHERE sport = 'combo'")
    with op.batch_alter_table('position', schema=None) as batch_op:
        batch_op.drop_constraint('ck_position_sport', type_='check')
        batch_op.create_check_constraint(
            'ck_position_sport', "sport IN ('soccer', 'nfl')"
        )
