"""drop the schema left behind by the withdrawn Solution Builder

Revision ID: 20260929_drop_builder_residue
Revises: 20260929_model_catalog
Create Date: 2026-09-29

PR #557 accidentally shipped unfinished Solution Builder migrations; PR #560
turned them into no-op tombstones but deliberately left the schema they had
created in place. Databases that ran #557 still carry it, and it collides with
later migrations (20260929_r2b_roles adds roles.is_builtin, which the residue
already has). Every object dropped here has been unreachable since #560
removed the code that used it.

Forward-only, raw DDL, IF EXISTS throughout so it is a no-op on databases that
never had the residue. There is deliberately no CASCADE: an unexpected
dependent object must fail this migration loudly instead of being dropped
silently. roles.is_builtin is NOT dropped here; R2b owns it now.

The #557 rewrite of the solution slug indexes (filtering on visibility) is
undone by recreating them exactly as 20260605_solution_unique_scope defined
them, because dropping solutions.visibility would otherwise take them with it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260929_drop_builder_residue"
down_revision: str = "20260929_model_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Referrers first: turns -> sessions/revisions, build jobs -> revisions,
    # projects -> revisions, revisions last.
    op.execute("DROP TABLE IF EXISTS solution_builder_turns")
    op.execute("DROP TABLE IF EXISTS solution_build_jobs")
    op.execute("DROP TABLE IF EXISTS solution_builder_sessions")
    op.execute("DROP TABLE IF EXISTS solution_builder_projects")
    op.execute("DROP TABLE IF EXISTS solution_source_revisions")

    op.execute("DROP INDEX IF EXISTS uq_roles_key")
    op.execute("ALTER TABLE roles DROP COLUMN IF EXISTS key")
    op.execute("ALTER TABLE roles DROP COLUMN IF EXISTS scopes")
    op.execute("ALTER TABLE roles DROP COLUMN IF EXISTS assignable_to_resources")

    op.execute("DROP INDEX IF EXISTS ix_configs_solution_key_unique")
    op.execute("ALTER TABLE configs DROP COLUMN IF EXISTS solution_id")

    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS bundle_path")

    op.execute(
        "ALTER TABLE solution_deploy_jobs "
        "DROP CONSTRAINT IF EXISTS ck_solution_deploy_jobs_kind"
    )
    for column in ("kind", "encrypted_options", "input_key", "input_sha256"):
        op.execute(f"ALTER TABLE solution_deploy_jobs DROP COLUMN IF EXISTS {column}")

    op.execute("DROP INDEX IF EXISTS ix_solutions_owner_slug_private_unique")
    op.execute("DROP INDEX IF EXISTS ix_solutions_owner_user_id")
    op.execute("DROP INDEX IF EXISTS ix_solutions_slug_org_unique")
    op.execute("DROP INDEX IF EXISTS ix_solutions_slug_global_unique")
    op.execute(
        "CREATE UNIQUE INDEX ix_solutions_slug_org_unique "
        "ON solutions (slug, organization_id) WHERE organization_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_solutions_slug_global_unique "
        "ON solutions (slug) WHERE organization_id IS NULL"
    )
    op.execute("ALTER TABLE solutions DROP COLUMN IF EXISTS visibility")
    op.execute("ALTER TABLE solutions DROP COLUMN IF EXISTS owner_user_id")


def downgrade() -> None:
    # Forward-only: the withdrawn Builder schema is not recreated.
    pass
