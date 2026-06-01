"""suggestion.kind — entry vs exit

Adds a NOT NULL `kind` column to `suggestion` distinguishing entry (open a
position) from exit (close a held one). The two share one model and differ
only by this discriminator — `strategy` says *why*, `kind` says *what*.

Backfill: the column is added with a temporary server_default of 'entry' so
any existing rows (there are none in practice) satisfy NOT NULL, then the
default is dropped so future inserts must supply `kind` explicitly — the app
always does, and a lingering default would mask a missing-kind bug.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('suggestion', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'kind',
                sa.String(length=8),
                nullable=False,
                server_default='entry',
            )
        )
        batch_op.create_check_constraint(
            'ck_suggestion_kind', "kind IN ('entry', 'exit')"
        )
        batch_op.alter_column('kind', server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('suggestion', schema=None) as batch_op:
        batch_op.drop_constraint('ck_suggestion_kind', type_='check')
        batch_op.drop_column('kind')
