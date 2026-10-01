"""True pre-head rehearsal for ``alembic/versions/20260929_drop_builder_residue.py``.

Databases that ran the accidentally shipped Solution Builder migrations
(PR #557, tombstoned by PR #560) still carry the schema those migrations
created; fresh databases never do, so fresh-database tests cannot see it.
This test builds a disposable database at the revision before the R2b roles
migration, applies that residue from ``tests/fixtures/builder_residue.sql``,
upgrades to head, and verifies that the residue is gone, that R2b applied over
it, and that the resulting schema matches the ORM.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from shared.builtin_roles import (
    DECRYPTION_ROLE_ID,
    PLATFORM_ADMIN_ROLE_ID,
    PLATFORM_OPERATOR_ROLE_ID,
    USER_ROLE_ID,
)
from src.models.orm import Base
from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _assert_safe_database_name,
    _create_database,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _upgrade,
)

pytestmark = pytest.mark.e2e

# The revision before 20260929_r2b_roles, the first migration to collide.
LEGACY_REVISION = "20260928_audit_op_surface"
RESIDUE_SQL = Path(__file__).parents[2] / "fixtures" / "builder_residue.sql"

RESIDUE_TABLES = {
    "solution_builder_turns",
    "solution_builder_sessions",
    "solution_build_jobs",
    "solution_builder_projects",
    "solution_source_revisions",
}
# (table, column) pairs. roles.is_builtin is deliberately absent: it is a
# legitimate column now, owned by the R2b roles migration.
RESIDUE_COLUMNS = {
    ("roles", "key"),
    ("roles", "scopes"),
    ("roles", "assignable_to_resources"),
    ("configs", "solution_id"),
    ("agents", "bundle_path"),
    ("solution_deploy_jobs", "kind"),
    ("solution_deploy_jobs", "encrypted_options"),
    ("solution_deploy_jobs", "input_key"),
    ("solution_deploy_jobs", "input_sha256"),
    ("solutions", "visibility"),
    ("solutions", "owner_user_id"),
}
RESIDUE_INDEXES = {
    "uq_roles_key",
    "ix_configs_solution_key_unique",
    "ix_solutions_owner_slug_private_unique",
    "ix_solutions_owner_user_id",
}

# Objects that legitimately exist in the migrated schema without being
# declared on the ORM metadata. Anything else present in the database but
# absent from Base.metadata is unexplained schema and fails the test.
NON_ORM_TABLES = {
    "alembic_version",  # Alembic's own bookkeeping
    "schedules",  # workflow schedules, managed outside the ORM
    "solution_file_jobs",  # Solution file jobs, managed outside the ORM
}
NON_ORM_COLUMNS = {
    ("agent_runs", "search_tsv"),  # generated tsvector column
    ("workflows", "schedule"),
    ("agents", "is_coding_mode"),
}

# Main's definitions, as created by 20260605_solution_unique_scope.
EXPECTED_SLUG_INDEXES = {
    "ix_solutions_slug_org_unique": (
        "CREATE UNIQUE INDEX ix_solutions_slug_org_unique ON public.solutions "
        "USING btree (slug, organization_id) WHERE (organization_id IS NOT NULL)"
    ),
    "ix_solutions_slug_global_unique": (
        "CREATE UNIQUE INDEX ix_solutions_slug_global_unique ON public.solutions "
        "USING btree (slug) WHERE (organization_id IS NULL)"
    ),
}


async def _apply_residue(database_url: str, org_id: str) -> None:
    async def apply(connection: AsyncConnection) -> None:
        raw = await connection.get_raw_connection()
        await raw.driver_connection.execute(RESIDUE_SQL.read_text())
        # Rows carrying the residue's default values, as an upgraded-and-idle
        # production database has them.
        await connection.execute(
            sa.text(
                "INSERT INTO organizations (id, name, domain, is_active, is_provider, settings, created_by) "
                "VALUES (CAST(:org AS uuid), 'Rehearsal Org', 'rehearsal.example', TRUE, FALSE, '{}'::jsonb, 'rehearsal')"
            ),
            {"org": org_id},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO roles (id, name, description, permissions, created_by) VALUES "
                "(gen_random_uuid(), 'Residue Role 1', 'pre-R2b', '{}'::jsonb, 'rehearsal'), "
                "(gen_random_uuid(), 'Residue Role 2', 'pre-R2b', '{}'::jsonb, 'rehearsal')"
            )
        )
        await connection.execute(
            sa.text(
                "INSERT INTO solutions (id, slug, name, organization_id) VALUES "
                "(gen_random_uuid(), 'shared-org', 'Shared Org', CAST(:org AS uuid)), "
                "(gen_random_uuid(), 'shared-global', 'Shared Global', NULL)"
            ),
            {"org": org_id},
        )

    await _run_in_database(database_url, apply)


async def _inspect_schema(database_url: str) -> dict:
    async def inspect(connection: AsyncConnection) -> dict:
        tables = {
            row[0]
            for row in await connection.execute(
                sa.text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
            )
        }
        columns = {
            (row[0], row[1])
            for row in await connection.execute(
                sa.text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public'"
                )
            )
        }
        indexes = {
            row[0]: row[1]
            for row in await connection.execute(
                sa.text(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
                )
            )
        }
        constraints = {
            row[0]
            for row in await connection.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE connamespace = 'public'::regnamespace"
                )
            )
        }
        builtin_roles = {
            row[0]
            for row in await connection.execute(
                sa.text("SELECT id FROM roles WHERE is_builtin IS TRUE")
            )
        }
        surviving_roles = await connection.scalar(
            sa.text("SELECT COUNT(*) FROM roles WHERE name LIKE 'Residue Role %'")
        )
        surviving_solutions = await connection.scalar(
            sa.text("SELECT COUNT(*) FROM solutions WHERE slug IN ('shared-org', 'shared-global')")
        )
        return {
            "tables": tables,
            "columns": columns,
            "indexes": indexes,
            "constraints": constraints,
            "builtin_roles": builtin_roles,
            "surviving_roles": surviving_roles,
            "surviving_solutions": surviving_solutions,
        }

    return await _run_in_database(database_url, inspect)


def test_upgrade_over_builder_residue_removes_it_and_matches_the_orm() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)
        asyncio.run(_apply_residue(database_url, str(uuid4())))

        _upgrade(database_url, "head")
        schema = asyncio.run(_inspect_schema(database_url))

        assert not RESIDUE_TABLES & schema["tables"]
        assert not RESIDUE_COLUMNS & schema["columns"]
        assert not RESIDUE_INDEXES & set(schema["indexes"])
        assert "ck_solution_deploy_jobs_kind" not in schema["constraints"]

        # R2b applied over the residue: is_builtin is its column, seeded
        # (Secrets Reader is seeded by a later migration).
        assert ("roles", "is_builtin") in schema["columns"]
        assert schema["builtin_roles"] == {
            PLATFORM_ADMIN_ROLE_ID,
            USER_ROLE_ID,
            PLATFORM_OPERATOR_ROLE_ID,
            DECRYPTION_ROLE_ID,
        }

        # Rows written before the upgrade are untouched.
        assert schema["surviving_roles"] == 2
        assert schema["surviving_solutions"] == 2

        for name, definition in EXPECTED_SLUG_INDEXES.items():
            assert schema["indexes"][name] == definition

        orm_tables = set(Base.metadata.tables)
        orm_columns = {
            (table.name, column.name)
            for table in Base.metadata.tables.values()
            for column in table.columns
        }
        assert schema["tables"] - orm_tables == NON_ORM_TABLES
        orm_table_columns = {c for c in schema["columns"] if c[0] in orm_tables}
        assert orm_table_columns - orm_columns == NON_ORM_COLUMNS
        assert orm_columns - schema["columns"] == set()
    finally:
        asyncio.run(_drop_database(database_name))
