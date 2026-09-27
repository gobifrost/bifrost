"""Add audit_logs.execution_id for workflow-sourced attribution.

Revision ID: 20260926_audit_execution_id
Revises: 20260925_opencode_go_wire_api
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260926_audit_execution_id"
down_revision: str | None = "20260925_opencode_go_wire_api"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable, no FK: service attempt ids are not executions, and executions
    # are retention-deleted.
    op.add_column(
        "audit_logs",
        sa.Column("execution_id", sa.UUID(), nullable=True),
    )
    op.create_index(
        "ix_audit_logs_execution_id",
        "audit_logs",
        ["execution_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_execution_id", table_name="audit_logs")
    op.drop_column("audit_logs", "execution_id")
