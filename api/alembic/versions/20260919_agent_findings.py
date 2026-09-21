"""First-class agent findings table.

Revision ID: 20260919_agent_findings
Revises: 20260919_testing_assignment
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260919_agent_findings"
down_revision: str | None = "20260919_testing_assignment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_findings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("expected_behavior", sa.Text(), nullable=True),
        sa.Column(
            "source_kind", sa.String(length=20), nullable=False, server_default="manual"
        ),
        sa.Column("source_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_sequence", sa.Integer(), nullable=True),
        sa.Column("external_ref", sa.String(length=500), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"], ["agent_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('open', 'dismissed')", name="ck_agent_findings_status"
        ),
        sa.CheckConstraint(
            "source_kind IN ('run', 'manual', 'external')",
            name="ck_agent_findings_source_kind",
        ),
    )
    op.create_index(
        "ix_agent_findings_agent_id", "agent_findings", ["agent_id"]
    )
    op.create_index("ix_agent_findings_org_id", "agent_findings", ["org_id"])
    op.create_index("ix_agent_findings_status", "agent_findings", ["status"])


def downgrade() -> None:
    op.drop_index("ix_agent_findings_status", table_name="agent_findings")
    op.drop_index("ix_agent_findings_org_id", table_name="agent_findings")
    op.drop_index("ix_agent_findings_agent_id", table_name="agent_findings")
    op.drop_table("agent_findings")
