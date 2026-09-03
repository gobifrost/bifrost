"""Allow durable agentless chat runs.

Revision ID: 20260902_chat_run_agentless
Revises: 20260902_openai_transport
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_chat_run_agentless"
down_revision: str | None = "20260902_openai_transport"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "agent_runs",
        "agent_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "agent_runs",
        "agent_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
