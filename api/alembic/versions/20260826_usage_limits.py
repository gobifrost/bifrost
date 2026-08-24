"""add provider-neutral usage limit policies and period ledger

Revision ID: 20260826_usage_limits
Revises: 20260825_global_rel_final
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260826_usage_limits"
down_revision: str = "20260825_global_rel_final"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_usage",
        sa.Column("solution_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_ai_usage_solution_id",
        "ai_usage",
        "solutions",
        ["solution_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_ai_usage_solution", "ai_usage", ["solution_id"])

    op.create_table(
        "usage_limit_policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("solution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "per_run_ceilings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "aggregate_ceilings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "aggregate_period",
            sa.String(length=16),
            server_default="monthly",
            nullable=False,
        ),
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
            "scope IN ('platform', 'organization', 'user', 'solution')",
            name="ck_usage_limit_policies_scope",
        ),
        sa.CheckConstraint(
            "aggregate_period IN ('daily', 'monthly')",
            name="ck_usage_limit_policies_aggregate_period",
        ),
        sa.CheckConstraint(
            "(scope = 'platform' AND scope_key = 'platform' "
            "AND organization_id IS NULL AND user_id IS NULL "
            "AND solution_id IS NULL) OR "
            "(scope = 'organization' AND organization_id IS NOT NULL "
            "AND scope_key = organization_id::text AND user_id IS NULL "
            "AND solution_id IS NULL) OR "
            "(scope = 'user' AND user_id IS NOT NULL "
            "AND scope_key = user_id::text AND solution_id IS NULL) OR "
            "(scope = 'solution' AND solution_id IS NOT NULL "
            "AND scope_key = solution_id::text)",
            name="ck_usage_limit_policies_scope_target",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_usage_limit_policies_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["solution_id"],
            ["solutions.id"],
            name="fk_usage_limit_policies_solution_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_usage_limit_policies_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope",
            "scope_key",
            name="uq_usage_limit_policies_scope",
        ),
    )
    op.create_index(
        "ix_usage_limit_policies_org",
        "usage_limit_policies",
        ["organization_id"],
    )
    op.create_index(
        "ix_usage_limit_policies_solution",
        "usage_limit_policies",
        ["solution_id"],
    )
    op.create_index(
        "ix_usage_limit_policies_user",
        "usage_limit_policies",
        ["user_id"],
    )

    op.create_table(
        "usage_ledger_periods",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("solution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "model_requests",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("input_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column(
            "cache_read_tokens",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "cache_write_tokens",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "runner_duration_ms",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "sandbox_compute_ms",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
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
            "period IN ('daily', 'monthly')",
            name="ck_usage_ledger_periods_period",
        ),
        sa.CheckConstraint(
            "scope IN ('platform', 'organization', 'user', 'solution')",
            name="ck_usage_ledger_periods_scope",
        ),
        sa.CheckConstraint(
            "(scope = 'platform' AND scope_key = 'platform' "
            "AND organization_id IS NULL AND user_id IS NULL "
            "AND solution_id IS NULL) OR "
            "(scope = 'organization' AND organization_id IS NOT NULL "
            "AND scope_key = organization_id::text AND user_id IS NULL "
            "AND solution_id IS NULL) OR "
            "(scope = 'user' AND user_id IS NOT NULL "
            "AND scope_key = user_id::text AND solution_id IS NULL) OR "
            "(scope = 'solution' AND solution_id IS NOT NULL "
            "AND scope_key = solution_id::text)",
            name="ck_usage_ledger_periods_scope_target",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_usage_ledger_periods_organization_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["solution_id"],
            ["solutions.id"],
            name="fk_usage_ledger_periods_solution_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_usage_ledger_periods_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "period",
            "period_start",
            "scope",
            "scope_key",
            name="uq_usage_ledger_periods_scope_period",
        ),
    )
    op.create_index(
        "ix_usage_ledger_periods_period_start",
        "usage_ledger_periods",
        ["period", "period_start"],
    )
    op.create_index(
        "ix_usage_ledger_periods_org",
        "usage_ledger_periods",
        ["organization_id"],
    )
    op.create_index(
        "ix_usage_ledger_periods_solution",
        "usage_ledger_periods",
        ["solution_id"],
    )
    op.create_index(
        "ix_usage_ledger_periods_user",
        "usage_ledger_periods",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_ledger_periods_user", table_name="usage_ledger_periods")
    op.drop_index(
        "ix_usage_ledger_periods_solution",
        table_name="usage_ledger_periods",
    )
    op.drop_index("ix_usage_ledger_periods_org", table_name="usage_ledger_periods")
    op.drop_index(
        "ix_usage_ledger_periods_period_start",
        table_name="usage_ledger_periods",
    )
    op.drop_table("usage_ledger_periods")

    op.drop_index("ix_usage_limit_policies_user", table_name="usage_limit_policies")
    op.drop_index(
        "ix_usage_limit_policies_solution",
        table_name="usage_limit_policies",
    )
    op.drop_index("ix_usage_limit_policies_org", table_name="usage_limit_policies")
    op.drop_table("usage_limit_policies")

    op.drop_index("ix_ai_usage_solution", table_name="ai_usage")
    op.drop_constraint("fk_ai_usage_solution_id", "ai_usage", type_="foreignkey")
    op.drop_column("ai_usage", "solution_id")
