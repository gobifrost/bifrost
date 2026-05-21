"""rename event_source_type 'internal' to 'topic'

Revision ID: 20260521_rename_internal_to_topic
Revises: 20260519_nullable_event_src
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260521_rename_int_topic"
down_revision: Union[str, Sequence[str]] = "20260519_nullable_event_src"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE event_source_type RENAME VALUE 'internal' TO 'topic'")


def downgrade() -> None:
    op.execute("ALTER TYPE event_source_type RENAME VALUE 'topic' TO 'internal'")
