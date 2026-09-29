"""audit operation/surface tagging + workflow operation usage counter

Revision ID: 20260928_audit_op_surface
Revises: 20260928_r1b_mcp_names_b5
Create Date: 2026-09-28

Part 1 (R2a-2): tags every audit row with the catalog operation id that
produced it (``operation_id``) and which client surface made the call
(``surface``: web/cli/mcp/embed/workflow/service). ``source`` is unchanged
(actor type: http/workflow/service/sso_sync).

Part 2: a new ``workflow_operation_usage`` table holds a cheap daily counter
of which catalog operations (reads included) each workflow's engine-token
calls actually touched, flushed from a Redis buffer every 15 minutes. Both
additions are attribution only — no permission changes.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260928_audit_op_surface"
down_revision = "20260928_r1b_mcp_names_b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_logs", sa.Column("operation_id", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "audit_logs", sa.Column("surface", sa.String(length=16), nullable=True)
    )

    op.create_table(
        "workflow_operation_usage",
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_key", sa.String(length=256), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("count", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("workflow_id", "operation_key", "day"),
    )


def downgrade() -> None:
    op.drop_table("workflow_operation_usage")
    op.drop_column("audit_logs", "surface")
    op.drop_column("audit_logs", "operation_id")
