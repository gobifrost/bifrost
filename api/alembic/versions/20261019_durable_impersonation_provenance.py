"""retain browser-impersonation attribution for durable work

Revision ID: 20261019_durable_impersonation
Revises: 20261019_audit_impersonation
Create Date: 2026-10-19

The effective requester/actor fields retain their current authorization
meaning. These UUID stamps are audit attribution only and intentionally have
no foreign keys, so deleting the original operator cannot erase provenance.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20261019_durable_impersonation"
down_revision: str | None = "20261019_audit_impersonation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLES = ("executions", "agent_runs", "platform_jobs")


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column("impersonated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.add_column(
            table,
            sa.Column("impersonation_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_column(table, "impersonation_session_id")
        op.drop_column(table, "impersonated_by_user_id")
