"""Persist compact workflow conversion analysis and per-job review reports.

Revision ID: 20261018_workflow_conversion
Revises: 20261017_export_secret_scope
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20261018_workflow_conversion"
down_revision: str | None = "20261017_export_secret_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_conversion_analysis_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("analyzer_version", sa.String(length=64), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.UniqueConstraint(
            "source_key",
            "content_hash",
            "analyzer_version",
            name="uq_workflow_conversion_summary",
        ),
    )
    op.create_index(
        "ix_workflow_conversion_analysis_cache_hash",
        "workflow_conversion_analysis_cache",
        ["content_hash", "analyzer_version"],
    )
    op.create_table(
        "workflow_conversion_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("platform_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workflow_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("review", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.UniqueConstraint(
            "job_id", "workflow_id", name="uq_workflow_conversion_report_job_workflow"
        ),
    )
    op.create_index(
        "ix_workflow_conversion_reports_job_id",
        "workflow_conversion_reports",
        ["job_id"],
    )
    op.create_index(
        "ix_workflow_conversion_reports_job_status",
        "workflow_conversion_reports",
        ["job_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_conversion_reports_job_status",
        table_name="workflow_conversion_reports",
    )
    op.drop_index(
        "ix_workflow_conversion_reports_job_id",
        table_name="workflow_conversion_reports",
    )
    op.drop_table("workflow_conversion_reports")
    op.drop_index(
        "ix_workflow_conversion_analysis_cache_hash",
        table_name="workflow_conversion_analysis_cache",
    )
    op.drop_table("workflow_conversion_analysis_cache")
