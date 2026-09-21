"""shared recurring platform job triggers and fire fence

Revision ID: 20260920_recurring_triggers
Revises: 20260920_agent_reviews
Create Date: 2026-09-20 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260920_recurring_triggers"
down_revision: str | Sequence[str] | None = "20260920_agent_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recurring_platform_job_triggers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("operation_type", sa.String(length=40), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "operation_params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("cron_expression", sa.String(length=120), nullable=False),
        sa.Column(
            "timezone",
            sa.String(length=80),
            nullable=False,
            server_default="UTC",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "overlap_policy",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'skip'"),
        ),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_email", sa.String(length=320), nullable=False),
        sa.Column("requested_by_name", sa.String(length=255), nullable=False),
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
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "operation_type IN ('agent_review', 'agent_evaluation_suite')",
            name="ck_recurring_triggers_operation_type",
        ),
        sa.CheckConstraint(
            "overlap_policy IN ('skip')",
            name="ck_recurring_triggers_overlap_policy",
        ),
    )
    op.create_index(
        "ix_recurring_triggers_org_id",
        "recurring_platform_job_triggers",
        ["org_id"],
    )
    op.create_index(
        "ix_recurring_triggers_operation",
        "recurring_platform_job_triggers",
        ["operation_type", "operation_id"],
    )
    op.create_index(
        "ix_recurring_triggers_enabled",
        "recurring_platform_job_triggers",
        ["enabled"],
    )
    op.create_table(
        "recurring_trigger_fires",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="claimed",
        ),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.Column("platform_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("domain_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
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
        sa.ForeignKeyConstraint(
            ["trigger_id"],
            ["recurring_platform_job_triggers.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["platform_job_id"], ["platform_jobs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trigger_id",
            "scheduled_for",
            name="uq_recurring_fires_trigger_scheduled_for",
        ),
        sa.CheckConstraint(
            "status IN ('claimed', 'admitted', 'skipped', 'failed')",
            name="ck_recurring_fires_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 1",
            name="ck_recurring_fires_attempt_count",
        ),
        sa.CheckConstraint(
            "(status = 'admitted' AND platform_job_id IS NOT NULL)"
            " OR (status <> 'admitted')",
            name="ck_recurring_fires_admitted_has_job",
        ),
    )
    op.create_index(
        "ix_recurring_fires_trigger_id",
        "recurring_trigger_fires",
        ["trigger_id"],
    )
    op.create_index(
        "ix_recurring_fires_status",
        "recurring_trigger_fires",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_recurring_fires_status", table_name="recurring_trigger_fires")
    op.drop_index("ix_recurring_fires_trigger_id", table_name="recurring_trigger_fires")
    op.drop_table("recurring_trigger_fires")
    op.drop_index(
        "ix_recurring_triggers_enabled",
        table_name="recurring_platform_job_triggers",
    )
    op.drop_index(
        "ix_recurring_triggers_operation",
        table_name="recurring_platform_job_triggers",
    )
    op.drop_index(
        "ix_recurring_triggers_org_id",
        table_name="recurring_platform_job_triggers",
    )
    op.drop_table("recurring_platform_job_triggers")
