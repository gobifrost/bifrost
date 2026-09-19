"""Persist the trusted caller authorization snapshot for durable runs.

Revision ID: 20260919_runtime_caller_auth
Revises: 20260919_eval_hardening
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260919_runtime_caller_auth"
down_revision: str | None = "20260919_eval_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("caller_auth_context", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "caller_auth_context")
