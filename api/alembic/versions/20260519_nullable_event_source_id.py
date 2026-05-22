"""make event_source_id nullable on events and event_subscriptions for internal events

Revision ID: 20260519_nullable_event_source_id
Revises: 20260519_drop_email_config
Create Date: 2026-05-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260519_nullable_event_src"
down_revision: Union[str, Sequence[str]] = "20260519_drop_email_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "event_subscriptions",
        "event_source_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "events",
        "event_source_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # Only safe to reverse if no NULL rows exist
    op.alter_column(
        "events",
        "event_source_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "event_subscriptions",
        "event_source_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
    )
