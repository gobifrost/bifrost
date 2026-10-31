"""True pre-head rehearsal for restored live _repo/ table-name indexes.

``20260624_sol_status`` dropped ``tables.orphaned_at`` and PostgreSQL removed
the two partial indexes that referenced it. This test starts a disposable
database at the immediately preceding head to exercise that exact schema,
then proves the repair preserves Solution namespaces and refuses a populated
duplicate state without modifying data.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _assert_safe_database_name,
    _create_database,
    _current_revision,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _upgrade,
)


pytestmark = pytest.mark.e2e

# The migration must extend the current Alembic head rather than create a
# second head.  Starting the rehearsal here still recreates the schema after
# ``orphaned_at`` and its dependent partial indexes were removed.
LEGACY_REVISION = "20261031_app_sdk_binding"
MIGRATION_REVISION = "20261031_table_name_unique"
REPO_INDEXES = {
    "ix_tables_org_name_unique",
    "ix_tables_global_name_unique",
}


async def _seed_name_spaces(
    database_url: str, *, duplicate_repo_rows: bool = False
) -> dict[str, str]:
    ids = {
        "organization": str(uuid4()),
        "solution_a": str(uuid4()),
        "solution_b": str(uuid4()),
        "global_name": f"global-{uuid4().hex[:12]}",
        "org_name": f"org-{uuid4().hex[:12]}",
    }

    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                """
                INSERT INTO organizations
                    (id, name, domain, is_active, is_provider, settings, created_by)
                VALUES
                    (CAST(:organization AS uuid), 'Table Rehearsal', 'table-rehearsal.test',
                     TRUE, FALSE, '{}'::jsonb, 'migration-rehearsal')
                """
            ),
            ids,
        )
        await connection.execute(
            sa.text(
                """
                INSERT INTO solutions (id, slug, name, organization_id)
                VALUES
                    (CAST(:solution_a AS uuid), 'table-rehearsal-a', 'Table Rehearsal A', NULL),
                    (CAST(:solution_b AS uuid), 'table-rehearsal-b', 'Table Rehearsal B', NULL)
                """
            ),
            ids,
        )
        await connection.execute(
            sa.text(
                """
                INSERT INTO tables (id, name, organization_id, solution_id)
                VALUES
                    (CAST(:repo_global_id AS uuid), :global_name, NULL, NULL),
                    (CAST(:repo_org_id AS uuid), :org_name, CAST(:organization AS uuid), NULL),
                    (CAST(:solution_global_a_id AS uuid), :global_name, NULL, CAST(:solution_a AS uuid)),
                    (CAST(:solution_global_b_id AS uuid), :global_name, NULL, CAST(:solution_b AS uuid)),
                    (CAST(:solution_org_a_id AS uuid), :org_name, CAST(:organization AS uuid), CAST(:solution_a AS uuid)),
                    (CAST(:solution_org_b_id AS uuid), :org_name, CAST(:organization AS uuid), CAST(:solution_b AS uuid))
                """
            ),
            {
                **ids,
                "repo_global_id": str(uuid4()),
                "repo_org_id": str(uuid4()),
                "solution_global_a_id": str(uuid4()),
                "solution_global_b_id": str(uuid4()),
                "solution_org_a_id": str(uuid4()),
                "solution_org_b_id": str(uuid4()),
            },
        )
        if duplicate_repo_rows:
            await connection.execute(
                sa.text(
                    """
                    INSERT INTO tables (id, name, organization_id, solution_id)
                    VALUES
                        (CAST(:duplicate_global_id AS uuid), :global_name, NULL, NULL),
                        (CAST(:duplicate_org_id AS uuid), :org_name, CAST(:organization AS uuid), NULL)
                    """
                ),
                {
                    **ids,
                    "duplicate_global_id": str(uuid4()),
                    "duplicate_org_id": str(uuid4()),
                },
            )

    await _run_in_database(database_url, seed)
    return ids


async def _table_indexes(database_url: str) -> dict[str, str]:
    async def inspect(connection: AsyncConnection) -> dict[str, str]:
        return {
            row[0]: row[1]
            for row in await connection.execute(
                sa.text(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'public' AND tablename = 'tables'
                    """
                )
            )
        }

    return await _run_in_database(database_url, inspect)


async def _assert_repaired_contract(database_url: str, ids: dict[str, str]) -> None:
    async def assert_contract(connection: AsyncConnection) -> None:
        indexes = {
            row[0]: row[1]
            for row in await connection.execute(
                sa.text(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'public' AND tablename = 'tables'
                    """
                )
            )
        }
        assert REPO_INDEXES <= indexes.keys()
        assert "solution_id IS NULL" in indexes["ix_tables_global_name_unique"]
        assert "solution_id IS NULL" in indexes["ix_tables_org_name_unique"]
        assert "orphaned_at" not in indexes["ix_tables_global_name_unique"]
        assert "orphaned_at" not in indexes["ix_tables_org_name_unique"]
        assert "ix_tables_solution_name_unique" in indexes

        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    sa.text(
                        """
                        INSERT INTO tables (id, name, organization_id, solution_id)
                        VALUES (CAST(:id AS uuid), :global_name, NULL, NULL)
                        """
                    ),
                    {"id": str(uuid4()), **ids},
                )
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    sa.text(
                        """
                        INSERT INTO tables (id, name, organization_id, solution_id)
                        VALUES (CAST(:id AS uuid), :org_name, CAST(:organization AS uuid), NULL)
                        """
                    ),
                    {"id": str(uuid4()), **ids},
                )

        solution_rows = await connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM tables
                WHERE solution_id IS NOT NULL
                  AND (name = :global_name OR name = :org_name)
                """
            ),
            ids,
        )
        assert solution_rows == 4

    await _run_in_database(database_url, assert_contract)


def test_upgrade_restores_repo_indexes_without_collapsing_solution_namespaces() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)
        ids = asyncio.run(_seed_name_spaces(database_url))

        legacy_indexes = asyncio.run(_table_indexes(database_url))
        assert not REPO_INDEXES & legacy_indexes.keys()
        assert "ix_tables_solution_name_unique" in legacy_indexes

        _upgrade(database_url, "head")
        assert _current_revision(database_url) == MIGRATION_REVISION
        asyncio.run(_assert_repaired_contract(database_url, ids))
    finally:
        asyncio.run(_drop_database(database_name))


def test_upgrade_refuses_existing_repo_duplicates_without_changing_rows() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)
        ids = asyncio.run(_seed_name_spaces(database_url, duplicate_repo_rows=True))

        with pytest.raises(RuntimeError, match="Cannot restore live _repo/ table-name uniqueness"):
            _upgrade(database_url, "head")

        assert _current_revision(database_url) == LEGACY_REVISION

        async def count_duplicates(connection: AsyncConnection) -> tuple[int, int]:
            global_count = await connection.scalar(
                sa.text(
                    """
                    SELECT count(*) FROM tables
                    WHERE organization_id IS NULL AND solution_id IS NULL AND name = :global_name
                    """
                ),
                ids,
            )
            org_count = await connection.scalar(
                sa.text(
                    """
                    SELECT count(*) FROM tables
                    WHERE organization_id = CAST(:organization AS uuid)
                      AND solution_id IS NULL AND name = :org_name
                    """
                ),
                ids,
            )
            return global_count, org_count

        assert asyncio.run(_run_in_database(database_url, count_duplicates)) == (2, 2)
    finally:
        asyncio.run(_drop_database(database_name))
