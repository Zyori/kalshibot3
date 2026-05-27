"""normalize bet timestamps to ISO with tz suffix

SQLite stores DateTime(timezone=True) as text — but SQLAlchemy's batch_alter
table copy through Python re-serializes naive datetimes differently
(space-separated, no tz suffix) than tz-aware ones (ISO with `+00:00`).
After the remaining_quantity migration the bet table ended up with both
formats, which sorts incorrectly under `ORDER BY placed_at`. This
migration rewrites all bet timestamps to a single canonical format:
ISO with `T` separator and `+00:00` suffix when missing.

Revision ID: 626876de83d5
Revises: 04a8f073a636
Create Date: 2026-05-27 21:29:55.050536
"""
from typing import Sequence, Union

from alembic import op


revision: str = '626876de83d5'
down_revision: Union[str, Sequence[str], None] = '04a8f073a636'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = ("placed_at", "settled_at", "created_at", "updated_at")


def upgrade() -> None:
    for col in _COLUMNS:
        # Replace the space between date and time with 'T' if present.
        op.execute(
            f"UPDATE bet SET {col} = REPLACE({col}, ' ', 'T') "
            f"WHERE {col} IS NOT NULL AND {col} LIKE '____-__-__ %'"
        )
        # Append '+00:00' if no offset is already present. We treat
        # naked datetimes as UTC (everything in this app is UTC at write).
        op.execute(
            f"UPDATE bet SET {col} = {col} || '+00:00' "
            f"WHERE {col} IS NOT NULL "
            f"AND {col} NOT LIKE '%+__:__' "
            f"AND {col} NOT LIKE '%Z'"
        )


def downgrade() -> None:
    # Reversible normalization is a no-op — we don't know which rows
    # originally had a tz suffix and which were padded. Leaving as-is.
    pass
