"""add agent review data foundation

Revision ID: 20260920_agent_reviews
Revises: 20260920_quality_usage
Create Date: 2026-09-20 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260920_agent_reviews"
down_revision: str | Sequence[str] | None = "20260920_quality_usage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_review_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("latest_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')",
            name="ck_agent_review_definitions_status",
        ),
        sa.CheckConstraint(
            "latest_version > 0",
            name="ck_agent_review_definitions_latest_version_positive",
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0", name="ck_agent_review_definitions_name_nonblank"
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_review_definitions_agent_id", "agent_review_definitions", ["agent_id"]
    )
    op.create_index(
        "ix_agent_review_definitions_org_id", "agent_review_definitions", ["org_id"]
    )
    op.create_index(
        "ix_agent_review_definitions_status", "agent_review_definitions", ["status"]
    )

    op.create_table(
        "agent_review_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("review_statement", sa.Text(), nullable=False),
        sa.Column("evidence_format_instructions", sa.Text(), nullable=True),
        sa.Column("model_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version > 0", name="ck_agent_review_versions_version_positive"
        ),
        sa.CheckConstraint(
            "length(btrim(review_statement)) > 0",
            name="ck_agent_review_versions_statement_nonblank",
        ),
        sa.CheckConstraint(
            "length(review_statement) <= 8000",
            name="ck_agent_review_versions_statement_length",
        ),
        sa.CheckConstraint(
            "evidence_format_instructions IS NULL OR length(evidence_format_instructions) <= 4000",
            name="ck_agent_review_versions_format_instructions_length",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["review_id"], ["agent_review_definitions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "review_id", "version", name="uq_agent_review_versions_review_version"
        ),
    )
    op.create_index(
        "ix_agent_review_versions_review_id", "agent_review_versions", ["review_id"]
    )

    op.create_table(
        "agent_review_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_version", sa.Integer(), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("platform_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "requested_run_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column(
            "selected_run_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            server_default=sa.text("'{}'::uuid[]"),
            nullable=False,
        ),
        sa.Column(
            "source_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "source_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "profile_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("profile_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("input_bytes", sa.Integer(), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "review_version > 0", name="ck_agent_review_runs_version_positive"
        ),
        sa.CheckConstraint(
            "cardinality(requested_run_ids) BETWEEN 1 AND 20",
            name="ck_agent_review_runs_requested_run_count",
        ),
        sa.CheckConstraint(
            "cardinality(selected_run_ids) BETWEEN 1 AND 20",
            name="ck_agent_review_runs_selected_run_count",
        ),
        sa.CheckConstraint(
            "input_bytes > 0 AND input_bytes <= 4194304",
            name="ck_agent_review_runs_input_bytes_bounds",
        ),
        sa.CheckConstraint(
            "length(btrim(profile_fingerprint)) > 0",
            name="ck_agent_review_runs_profile_fingerprint_nonblank",
        ),
        sa.CheckConstraint(
            "length(btrim(request_fingerprint)) > 0",
            name="ck_agent_review_runs_request_fingerprint_nonblank",
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["platform_job_id"], ["platform_jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["review_id"], ["agent_review_definitions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["review_version_id"], ["agent_review_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform_job_id", name="uq_agent_review_runs_platform_job_id"
        ),
    )
    op.create_index("ix_agent_review_runs_agent_id", "agent_review_runs", ["agent_id"])
    op.create_index("ix_agent_review_runs_org_id", "agent_review_runs", ["org_id"])
    op.create_index(
        "ix_agent_review_runs_platform_job_id", "agent_review_runs", ["platform_job_id"]
    )
    op.create_index(
        "ix_agent_review_runs_review_id", "agent_review_runs", ["review_id"]
    )

    op.add_column(
        "agent_findings",
        sa.Column(
            "finding_kind",
            sa.String(length=20),
            server_default=sa.text("'problem'"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_findings", sa.Column("evidence_markdown", sa.Text(), nullable=True)
    )
    op.add_column(
        "agent_findings",
        sa.Column("source_review_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_findings",
        sa.Column(
            "source_review_version_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.add_column(
        "agent_findings",
        sa.Column("source_review_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_findings",
        sa.Column("source_review_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_findings",
        sa.Column(
            "source_run_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_findings", sa.Column("source_ordinal", sa.Integer(), nullable=True)
    )
    op.create_index(
        "ix_agent_findings_source_review_id", "agent_findings", ["source_review_id"]
    )
    op.create_index(
        "ix_agent_findings_source_review_run_id",
        "agent_findings",
        ["source_review_run_id"],
    )
    op.create_index(
        "ix_agent_findings_finding_kind", "agent_findings", ["finding_kind"]
    )
    op.create_index(
        "uq_agent_findings_review_run_ordinal",
        "agent_findings",
        ["source_review_run_id", "source_ordinal"],
        unique=True,
        postgresql_where=sa.text(
            "source_review_run_id IS NOT NULL AND source_ordinal IS NOT NULL"
        ),
    )
    op.create_check_constraint(
        "ck_agent_findings_finding_kind",
        "agent_findings",
        "finding_kind IN ('problem', 'opportunity')",
    )
    op.create_check_constraint(
        "ck_agent_findings_evidence_markdown_length",
        "agent_findings",
        "evidence_markdown IS NULL OR length(evidence_markdown) <= 20000",
    )
    op.create_check_constraint(
        "ck_agent_findings_review_provenance_complete",
        "agent_findings",
        "(source_review_id IS NULL AND source_review_version_id IS NULL "
        "AND source_review_run_id IS NULL AND source_review_version IS NULL "
        "AND source_ordinal IS NULL) OR (source_review_id IS NOT NULL "
        "AND source_review_version_id IS NOT NULL AND source_review_run_id IS NOT NULL "
        "AND source_review_version IS NOT NULL AND source_review_version > 0 "
        "AND source_ordinal IS NOT NULL AND source_ordinal >= 0 "
        "AND jsonb_typeof(source_run_refs) = 'array' "
        "AND jsonb_array_length(source_run_refs) > 0)",
    )


def downgrade() -> None:
    bind = op.get_bind()
    has_review_domain = bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM agent_review_definitions) "
            "OR EXISTS (SELECT 1 FROM agent_review_versions) "
            "OR EXISTS (SELECT 1 FROM agent_review_runs)"
        )
    ).scalar()
    has_review_finding_data = bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM agent_findings WHERE "
            "finding_kind <> 'problem' OR evidence_markdown IS NOT NULL "
            "OR source_review_id IS NOT NULL OR source_review_version_id IS NOT NULL "
            "OR source_review_run_id IS NOT NULL OR source_review_version IS NOT NULL "
            "OR source_ordinal IS NOT NULL OR source_run_refs <> '[]'::jsonb)"
        )
    ).scalar()
    if has_review_domain or has_review_finding_data:
        raise RuntimeError(
            "Refusing to downgrade agent review data foundation while review data exists."
        )

    op.drop_constraint(
        "ck_agent_findings_review_provenance_complete", "agent_findings", type_="check"
    )
    op.drop_constraint(
        "ck_agent_findings_evidence_markdown_length", "agent_findings", type_="check"
    )
    op.drop_constraint(
        "ck_agent_findings_finding_kind", "agent_findings", type_="check"
    )
    op.drop_index("uq_agent_findings_review_run_ordinal", table_name="agent_findings")
    op.drop_index("ix_agent_findings_finding_kind", table_name="agent_findings")
    op.drop_index("ix_agent_findings_source_review_run_id", table_name="agent_findings")
    op.drop_index("ix_agent_findings_source_review_id", table_name="agent_findings")
    op.drop_column("agent_findings", "source_ordinal")
    op.drop_column("agent_findings", "source_run_refs")
    op.drop_column("agent_findings", "source_review_version")
    op.drop_column("agent_findings", "source_review_run_id")
    op.drop_column("agent_findings", "source_review_version_id")
    op.drop_column("agent_findings", "source_review_id")
    op.drop_column("agent_findings", "evidence_markdown")
    op.drop_column("agent_findings", "finding_kind")

    op.drop_index("ix_agent_review_runs_review_id", table_name="agent_review_runs")
    op.drop_index(
        "ix_agent_review_runs_platform_job_id", table_name="agent_review_runs"
    )
    op.drop_index("ix_agent_review_runs_org_id", table_name="agent_review_runs")
    op.drop_index("ix_agent_review_runs_agent_id", table_name="agent_review_runs")
    op.drop_table("agent_review_runs")
    op.drop_index(
        "ix_agent_review_versions_review_id", table_name="agent_review_versions"
    )
    op.drop_table("agent_review_versions")
    op.drop_index(
        "ix_agent_review_definitions_status", table_name="agent_review_definitions"
    )
    op.drop_index(
        "ix_agent_review_definitions_org_id", table_name="agent_review_definitions"
    )
    op.drop_index(
        "ix_agent_review_definitions_agent_id", table_name="agent_review_definitions"
    )
    op.drop_table("agent_review_definitions")
