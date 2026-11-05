"""Retain browser impersonation provenance on audit events.

Revision ID: 20261019_audit_impersonation
Revises: 20261017_export_secret_scope
Create Date: 2026-10-19

``user_id`` remains the effective browser target. The impersonating operator
is deliberately a plain UUID stamp: deleting an operator account must not
erase who performed an audited action.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20261019_audit_impersonation"
down_revision: str | None = "20261017_export_secret_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column("impersonated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "audit_logs",
        sa.Column("impersonation_session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audit_logs", "impersonation_session_id")
    op.drop_column("audit_logs", "impersonated_by_user_id")
