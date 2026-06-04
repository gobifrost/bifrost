"""add solutions table and nullable solution_id on managed entities

Creates the `solutions` install table and adds a nullable `solution_id` FK
(ON DELETE CASCADE) to the five portable entities — workflows, applications,
forms, agents, tables. A non-null `solution_id` marks an entity as
solution-managed (read-only on the platform; one writer per install). NULL
means an ad-hoc `_repo/` entity, i.e. existing behavior is unchanged.

See docs/plans/2026-06-04-solutions-success-criteria.md §3.2.

Revision ID: 20260604_add_solutions
Revises: 20260601_promote_global_tok
Create Date: 2026-06-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260604_add_solutions"
down_revision = "20260601_promote_global_tok"
branch_labels = None
depends_on = None

# Entities that gain solution_id. (table, index name)
_MANAGED = [
    ("workflows", "ix_workflows_solution_id"),
    ("applications", "ix_applications_solution_id"),
    ("forms", "ix_forms_solution_id"),
    ("agents", "ix_agents_solution_id"),
    ("tables", "ix_tables_solution_id"),
]


def upgrade() -> None:
    op.create_table(
        "solutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "global_repo_access",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "git_connected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("git_repo_url", sa.String(length=1024), nullable=True),
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
    )
    op.create_index("ix_solutions_organization_id", "solutions", ["organization_id"])
    # Install identity is unique per (slug, scope) — no scope overlap between
    # installs (success-criteria §3.4). Partial unique indexes because
    # organization_id is nullable (NULL == global; NULL != NULL in a plain
    # UNIQUE). The org-scoped index also serves slug lookups.
    op.create_index(
        "uq_solutions_slug_global",
        "solutions",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )
    op.create_index(
        "uq_solutions_slug_org",
        "solutions",
        ["slug", "organization_id"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )

    for table, index_name in _MANAGED:
        op.add_column(
            table,
            sa.Column(
                "solution_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("solutions.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
        op.create_index(index_name, table, ["solution_id"])

    # Workflow (path, function_name) uniqueness must be scoped per source so a
    # solution can reuse the same relative path as _repo/ or another install
    # (self-contained worlds, criterion 2 / §3.5). Replace the single global
    # constraint with two partial unique indexes.
    op.drop_constraint("workflows_path_function_key", "workflows", type_="unique")
    op.create_index(
        "uq_workflows_path_function_repo",
        "workflows",
        ["path", "function_name"],
        unique=True,
        postgresql_where=sa.text("solution_id IS NULL"),
    )
    op.create_index(
        "uq_workflows_path_function_solution",
        "workflows",
        ["path", "function_name", "solution_id"],
        unique=True,
        postgresql_where=sa.text("solution_id IS NOT NULL"),
    )

    # Workflow NAME uniqueness must likewise be scoped per source: a solution
    # workflow may share a name with a _repo/ workflow or another install
    # (criterion 2). Restrict the existing _repo/ name indexes to non-solution
    # rows and add solution-scoped equivalents.
    op.drop_index("uq_workflows_global_name", table_name="workflows")
    op.drop_index("uq_workflows_org_name", table_name="workflows")
    op.execute(sa.text("""
        CREATE UNIQUE INDEX uq_workflows_global_name
        ON workflows (name)
        WHERE organization_id IS NULL AND solution_id IS NULL AND is_active = true
    """))
    op.execute(sa.text("""
        CREATE UNIQUE INDEX uq_workflows_org_name
        ON workflows (organization_id, name)
        WHERE organization_id IS NOT NULL AND solution_id IS NULL AND is_active = true
    """))
    # Solution-scoped: one name per install (active rows only).
    op.execute(sa.text("""
        CREATE UNIQUE INDEX uq_workflows_solution_name
        ON workflows (solution_id, name)
        WHERE solution_id IS NOT NULL AND is_active = true
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_workflows_solution_name"))
    op.drop_index("uq_workflows_org_name", table_name="workflows")
    op.drop_index("uq_workflows_global_name", table_name="workflows")
    op.execute(sa.text("""
        CREATE UNIQUE INDEX uq_workflows_org_name
        ON workflows (organization_id, name)
        WHERE organization_id IS NOT NULL AND is_active = true
    """))
    op.execute(sa.text("""
        CREATE UNIQUE INDEX uq_workflows_global_name
        ON workflows (name)
        WHERE organization_id IS NULL AND is_active = true
    """))

    op.drop_index("uq_workflows_path_function_solution", table_name="workflows")
    op.drop_index("uq_workflows_path_function_repo", table_name="workflows")
    op.create_unique_constraint(
        "workflows_path_function_key", "workflows", ["path", "function_name"]
    )

    for table, index_name in _MANAGED:
        op.drop_index(index_name, table_name=table)
        op.drop_column(table, "solution_id")

    op.drop_index("uq_solutions_slug_org", table_name="solutions")
    op.drop_index("uq_solutions_slug_global", table_name="solutions")
    op.drop_index("ix_solutions_organization_id", table_name="solutions")
    op.drop_table("solutions")
