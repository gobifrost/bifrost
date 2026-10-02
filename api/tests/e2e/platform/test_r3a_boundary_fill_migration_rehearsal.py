"""True pre-head rehearsal for ``alembic/versions/20261001_r3a_boundary_fill.py``.

Builds a disposable database at the migration's ``down_revision``, seeds role
assignments with and without boundaries, upgrades, and checks that exactly
the assignments without a boundary (of users with a home organization) gain
one at that organization, that existing boundaries are untouched, that a
Global user's assignment is skipped, that the upgrade is idempotent at head,
and that the downgrade is a no-op.

Ids and expectations are frozen here, like the migration's own SQL.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.e2e.platform.test_r2b_roles_migration_rehearsal import (
    _alembic_config,
    _assert_safe_database_name,
    _create_database,
    _direct_database_url,
    _drop_database,
    _run_in_database,
    _temporary_migration_database_url,
    _upgrade,
)

pytestmark = pytest.mark.e2e

PREVIOUS_REVISION = "20261001_unified_file_search"
REVISION = "20261001_r3a_boundary_fill"

PLATFORM_ADMIN_ROLE_ID = UUID("00000000-0000-0000-0000-000000000005")
USER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000006")
ACTOR = "migration-rehearsal"


def _downgrade(database_url: str, revision: str) -> None:
    with _temporary_migration_database_url(database_url):
        command.downgrade(_alembic_config(), revision)


async def _seed(database_url: str, ids: dict[str, str]) -> None:
    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                "INSERT INTO organizations (id, name, is_active, is_provider, settings, created_by) "
                "VALUES (CAST(:org_a AS uuid), 'Rehearsal A', TRUE, FALSE, '{}'::jsonb, :actor), "
                "(CAST(:org_b AS uuid), 'Rehearsal B', TRUE, FALSE, '{}'::jsonb, :actor)"
            ),
            {**ids, "actor": ACTOR},
        )
        await connection.execute(
            sa.text(
                """
                INSERT INTO users (
                    id, email, name, is_active, is_superuser, is_verified, is_registered,
                    is_system, is_external, organization_id, base_role_id
                )
                VALUES
                    (CAST(:unbounded AS uuid), 'unbounded@rehearsal.test', 'Unbounded', TRUE, FALSE,
                     TRUE, TRUE, FALSE, FALSE, CAST(:org_a AS uuid), CAST(:user_role AS uuid)),
                    (CAST(:bounded AS uuid), 'bounded@rehearsal.test', 'Bounded', TRUE, FALSE,
                     TRUE, TRUE, FALSE, FALSE, CAST(:org_a AS uuid), CAST(:user_role AS uuid)),
                    (CAST(:global_admin AS uuid), 'global@rehearsal.test', 'Global', TRUE, TRUE,
                     TRUE, TRUE, FALSE, FALSE, NULL, CAST(:admin_role AS uuid))
                """
            ),
            {**ids, "user_role": str(USER_ROLE_ID), "admin_role": str(PLATFORM_ADMIN_ROLE_ID)},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO roles (id, name, description, is_base, is_builtin, created_by) "
                "VALUES (CAST(:role_1 AS uuid), 'Rehearsal Role 1', 'r', FALSE, FALSE, :actor), "
                "(CAST(:role_2 AS uuid), 'Rehearsal Role 2', 'r', FALSE, FALSE, :actor)"
            ),
            {**ids, "actor": ACTOR},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO user_roles (user_id, role_id, assigned_by) VALUES "
                "(CAST(:unbounded AS uuid), CAST(:role_1 AS uuid), :actor), "
                "(CAST(:unbounded AS uuid), CAST(:role_2 AS uuid), :actor), "
                "(CAST(:bounded AS uuid), CAST(:role_1 AS uuid), :actor), "
                "(CAST(:global_admin AS uuid), CAST(:role_1 AS uuid), :actor)"
            ),
            {**ids, "actor": ACTOR},
        )
        # Already bounded, at another organization and at Managed: left as is.
        await connection.execute(
            sa.text(
                "INSERT INTO user_role_boundaries (id, user_id, role_id, kind, organization_id) VALUES "
                "(gen_random_uuid(), CAST(:bounded AS uuid), CAST(:role_1 AS uuid), 'organization', CAST(:org_b AS uuid)), "
                "(gen_random_uuid(), CAST(:bounded AS uuid), CAST(:role_1 AS uuid), 'managed_organizations', NULL)"
            ),
            ids,
        )

    await _run_in_database(database_url, seed)


async def _boundaries(database_url: str, ids: dict[str, str]) -> set[tuple[str, str, str, str | None]]:
    async def read(connection: AsyncConnection) -> set[tuple[str, str, str, str | None]]:
        rows = (
            await connection.execute(
                sa.text(
                    "SELECT user_id, role_id, kind, organization_id FROM user_role_boundaries "
                    "WHERE user_id IN (CAST(:unbounded AS uuid), CAST(:bounded AS uuid), "
                    "CAST(:global_admin AS uuid)) AND role_id IN (CAST(:role_1 AS uuid), CAST(:role_2 AS uuid))"
                ),
                ids,
            )
        ).all()
        return {
            (str(user_id), str(role_id), kind, str(org) if org else None)
            for user_id, role_id, kind, org in rows
        }

    return await _run_in_database(database_url, read)


def test_unbounded_assignments_gain_their_home_org_boundary() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    ids = {
        key: str(uuid4())
        for key in ("org_a", "org_b", "unbounded", "bounded", "global_admin", "role_1", "role_2")
    }

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PREVIOUS_REVISION)
        asyncio.run(_seed(database_url, ids))
        before = asyncio.run(_boundaries(database_url, ids))
        assert before == {
            (ids["bounded"], ids["role_1"], "organization", ids["org_b"]),
            (ids["bounded"], ids["role_1"], "managed_organizations", None),
        }

        _upgrade(database_url, REVISION)
        after = asyncio.run(_boundaries(database_url, ids))
        assert after == before | {
            (ids["unbounded"], ids["role_1"], "organization", ids["org_a"]),
            (ids["unbounded"], ids["role_2"], "organization", ids["org_a"]),
        }

        _upgrade(database_url, "head")
        assert asyncio.run(_boundaries(database_url, ids)) == after

        _downgrade(database_url, PREVIOUS_REVISION)
        assert asyncio.run(_boundaries(database_url, ids)) == after
    finally:
        asyncio.run(_drop_database(database_name))
