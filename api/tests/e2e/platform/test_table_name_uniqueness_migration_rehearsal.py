"""Rehearse restoring ad-hoc table uniqueness over the pre-repair schema."""

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
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _upgrade,
)


pytestmark = pytest.mark.e2e

LEGACY_REVISION = "20261101_capacity_cleanup"
INDEX_PREDICATES = {
    "ix_tables_org_name_unique": "organization_id IS NOT NULL AND solution_id IS NULL",
    "ix_tables_global_name_unique": "organization_id IS NULL AND solution_id IS NULL",
    "ix_tables_solution_name_unique": "solution_id IS NOT NULL",
}


async def _seed_table(
    database_url: str,
    *,
    table_id: str,
    name: str,
    organization_id: str | None,
    solution_id: str | None = None,
) -> None:
    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                """
                INSERT INTO tables (id, name, organization_id, solution_id, access)
                VALUES (CAST(:table_id AS uuid), :name, CAST(:organization_id AS uuid),
                        CAST(:solution_id AS uuid), '{}'::jsonb)
                """
            ),
            {
                "table_id": table_id,
                "name": name,
                "organization_id": organization_id,
                "solution_id": solution_id,
            },
        )

    await _run_in_database(database_url, seed)


async def _seed_organization(database_url: str, organization_id: str) -> None:
    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                """
                INSERT INTO organizations (id, name, domain, is_active, is_provider, settings, created_by)
                VALUES (CAST(:organization_id AS uuid), 'Table migration rehearsal',
                        'table-migration.example', TRUE, FALSE, '{}'::jsonb, 'migration-rehearsal')
                """
            ),
            {"organization_id": organization_id},
        )

    await _run_in_database(database_url, seed)


async def _index_predicates(database_url: str) -> dict[str, str]:
    async def inspect(connection: AsyncConnection) -> dict[str, str]:
        rows = await connection.execute(
            sa.text(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'public' AND tablename = 'tables'
                """
            )
        )
        return {
            str(index_name): str(index_definition)
            for index_name, index_definition in rows.all()
        }

    return await _run_in_database(database_url, inspect)


async def _count_tables(database_url: str, name: str, organization_id: str) -> int:
    async def count(connection: AsyncConnection) -> int:
        return await connection.scalar(
            sa.text(
                """
                SELECT count(*)
                FROM tables
                WHERE name = :name AND organization_id = CAST(:organization_id AS uuid)
                """
            ),
            {"name": name, "organization_id": organization_id},
        )

    return await _run_in_database(database_url, count)


def test_upgrade_restores_only_ad_hoc_table_name_indexes() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    organization_id = str(uuid4())

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)
        asyncio.run(_seed_organization(database_url, organization_id))
        asyncio.run(
            _seed_table(
                database_url,
                table_id=str(uuid4()),
                name="orders",
                organization_id=organization_id,
            )
        )
        asyncio.run(
            _seed_table(
                database_url,
                table_id=str(uuid4()),
                name="orders",
                organization_id=None,
            )
        )

        _upgrade(database_url, "head")
        indexes = asyncio.run(_index_predicates(database_url))

        for index_name, predicate in INDEX_PREDICATES.items():
            assert index_name in indexes
            assert predicate in indexes[index_name].replace("(", "").replace(")", "")

        with pytest.raises(IntegrityError):
            asyncio.run(
                _seed_table(
                    database_url,
                    table_id=str(uuid4()),
                    name="orders",
                    organization_id=organization_id,
                )
            )
    finally:
        asyncio.run(_drop_database(database_name))


def test_upgrade_refuses_duplicate_ad_hoc_tables_without_changing_them() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    organization_id = str(uuid4())

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, LEGACY_REVISION)
        asyncio.run(_seed_organization(database_url, organization_id))
        for _ in range(2):
            asyncio.run(
                _seed_table(
                    database_url,
                    table_id=str(uuid4()),
                    name="duplicate_orders",
                    organization_id=organization_id,
                )
            )

        with pytest.raises(RuntimeError, match="No rows were changed"):
            _upgrade(database_url, "head")

        assert asyncio.run(
            _count_tables(database_url, "duplicate_orders", organization_id)
        ) == 2
        indexes = asyncio.run(_index_predicates(database_url))
        assert "ix_tables_org_name_unique" not in indexes
        assert "ix_tables_global_name_unique" not in indexes
    finally:
        asyncio.run(_drop_database(database_name))
