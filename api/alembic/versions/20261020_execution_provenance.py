"""retain trusted execution origin for Guardian policy

Revision ID: 20261020_execution_provenance
Revises: 20261019_durable_impersonation
Create Date: 2026-10-20

Existing rows intentionally remain NULL.  Their origin cannot be proven and
therefore resolves to ``unknown`` rather than a direct-human exception.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20261020_execution_provenance"
down_revision: str | None = "20261019_durable_impersonation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("executions", sa.Column("execution_origin", sa.String(length=16), nullable=True))
    op.add_column("executions", sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("executions", "agent_id")
    op.drop_column("executions", "execution_origin")
