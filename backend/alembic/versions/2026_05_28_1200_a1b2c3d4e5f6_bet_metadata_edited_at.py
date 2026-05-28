"""bet.metadata_edited_at for reflective edits

Adds a nullable timestamp set when the user retags a bet's strategy /
source / timing / confidence / tags / human_reasoning after placement.

Revision ID: a1b2c3d4e5f6
Revises: 626876de83d5
Create Date: 2026-05-28 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '626876de83d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('metadata_edited_at', sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('bet', schema=None) as batch_op:
        batch_op.drop_column('metadata_edited_at')
