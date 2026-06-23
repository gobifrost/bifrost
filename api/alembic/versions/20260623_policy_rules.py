"""policy_rules + partial unique + expression GIN on policy arrays

Revision ID: 20260623_policy_rules
Revises: 20260621_add_file_policies
Create Date: 2026-06-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260623_policy_rules"
down_revision = "20260621_add_file_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "policy_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column(
            "solution_id",
            sa.Uuid(),
            sa.ForeignKey("solutions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("domain", sa.String(length=8), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Three mutually-exclusive partial unique indexes per scope tier (NULLs don't compare equal).
    op.create_index(
        "uq_policy_rules_global_name_domain",
        "policy_rules",
        ["name", "domain"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL AND solution_id IS NULL"),
    )
    op.create_index(
        "uq_policy_rules_org_name_domain",
        "policy_rules",
        ["organization_id", "name", "domain"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL AND solution_id IS NULL"),
    )
    op.create_index(
        "uq_policy_rules_solution_name_domain",
        "policy_rules",
        ["solution_id", "name", "domain"],
        unique=True,
        postgresql_where=sa.text("solution_id IS NOT NULL"),
    )
    # Expression GIN indexes for where-used query shape `(col -> 'policies') @> [...]`
    # (correction #8 — a GIN on the whole column would NOT serve the extracted-array query).
    op.create_index(
        "ix_file_policies_rules_gin",
        "file_policies",
        [sa.text("(policies -> 'policies') jsonb_path_ops")],
        postgresql_using="gin",
    )
    op.create_index(
        "ix_tables_access_rules_gin",
        "tables",
        [sa.text("(access -> 'policies') jsonb_path_ops")],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_tables_access_rules_gin", table_name="tables")
    op.drop_index("ix_file_policies_rules_gin", table_name="file_policies")
    op.drop_index("uq_policy_rules_solution_name_domain", table_name="policy_rules")
    op.drop_index("uq_policy_rules_org_name_domain", table_name="policy_rules")
    op.drop_index("uq_policy_rules_global_name_domain", table_name="policy_rules")
    op.drop_table("policy_rules")
