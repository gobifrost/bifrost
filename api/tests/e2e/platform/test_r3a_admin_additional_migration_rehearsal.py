"""True pre-head rehearsal for ``alembic/versions/20261002_r3a_admin_additional.py``.

Builds a disposable database at the migration's ``down_revision``, seeds a
Platform Admin whose base role is Platform Admin (with a home organization
and Global), a regular User and a user with a custom base role, upgrades,
and checks that each admin becomes a User who also holds Platform Admin at
the platform boundary, that ``is_superuser`` and everyone else are untouched,
that Platform Admin stops being flagged as a base role, that the upgrade is
idempotent at head, and that the downgrade restores the earlier shape.

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

PREVIOUS_REVISION = "20261001_r3a_operator_desc"
REVISION = "20261002_r3a_admin_additional"

PLATFORM_ADMIN_ROLE_ID = UUID("00000000-0000-0000-0000-000000000005")
USER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000006")
PROVIDER_ORG_ID = UUID("00000000-0000-0000-0000-000000000002")
ACTOR = "migration-rehearsal"


def _downgrade(database_url: str, revision: str) -> None:
    with _temporary_migration_database_url(database_url):
        command.downgrade(_alembic_config(), revision)


async def _seed(database_url: str, ids: dict[str, str]) -> None:
    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                "INSERT INTO roles (id, name, description, is_base, is_builtin, created_by) "
                "VALUES (CAST(:custom_role AS uuid), 'Rehearsal Custom', 'r', FALSE, FALSE, :actor)"
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
                    (CAST(:admin AS uuid), 'admin@rehearsal.test', 'Admin', TRUE, TRUE,
                     TRUE, TRUE, FALSE, FALSE, CAST(:provider AS uuid), CAST(:admin_role AS uuid)),
                    (CAST(:global_admin AS uuid), 'global@rehearsal.test', 'Global', TRUE, TRUE,
                     TRUE, TRUE, FALSE, FALSE, NULL, CAST(:admin_role AS uuid)),
                    (CAST(:regular AS uuid), 'regular@rehearsal.test', 'Regular', TRUE, FALSE,
                     TRUE, TRUE, FALSE, FALSE, CAST(:provider AS uuid), CAST(:user_role AS uuid)),
                    (CAST(:custom AS uuid), 'custom@rehearsal.test', 'Custom', TRUE, FALSE,
                     TRUE, TRUE, FALSE, FALSE, CAST(:provider AS uuid), CAST(:custom_role AS uuid))
                """
            ),
            {
                **ids,
                "provider": str(PROVIDER_ORG_ID),
                "user_role": str(USER_ROLE_ID),
                "admin_role": str(PLATFORM_ADMIN_ROLE_ID),
            },
        )

    await _run_in_database(database_url, seed)


async def _state(database_url: str, ids: dict[str, str]) -> dict:
    async def read(connection: AsyncConnection) -> dict:
        users = {
            str(user_id): (str(base_role_id), is_superuser)
            for user_id, base_role_id, is_superuser in (
                await connection.execute(
                    sa.text(
                        "SELECT id, base_role_id, is_superuser FROM users "
                        "WHERE id IN (CAST(:admin AS uuid), CAST(:global_admin AS uuid), "
                        "CAST(:regular AS uuid), CAST(:custom AS uuid))"
                    ),
                    ids,
                )
            ).all()
        }
        assignments = {
            (str(user_id), str(role_id))
            for user_id, role_id in (
                await connection.execute(
                    sa.text(
                        "SELECT user_id, role_id FROM user_roles "
                        "WHERE role_id = CAST(:admin_role AS uuid)"
                    ),
                    {"admin_role": str(PLATFORM_ADMIN_ROLE_ID)},
                )
            ).all()
        }
        boundaries = {
            (str(user_id), str(role_id), kind, org)
            for user_id, role_id, kind, org in (
                await connection.execute(
                    sa.text(
                        "SELECT user_id, role_id, kind, organization_id FROM user_role_boundaries "
                        "WHERE role_id = CAST(:admin_role AS uuid)"
                    ),
                    {"admin_role": str(PLATFORM_ADMIN_ROLE_ID)},
                )
            ).all()
        }
        is_base = (
            await connection.execute(
                sa.text("SELECT is_base FROM roles WHERE id = CAST(:admin_role AS uuid)"),
                {"admin_role": str(PLATFORM_ADMIN_ROLE_ID)},
            )
        ).scalar_one()
        return {
            "users": users,
            "assignments": assignments,
            "boundaries": boundaries,
            "is_base": is_base,
        }

    return await _run_in_database(database_url, read)


def test_platform_admins_become_users_who_hold_platform_admin() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    ids = {key: str(uuid4()) for key in ("admin", "global_admin", "regular", "custom", "custom_role")}
    admin_role, user_role = str(PLATFORM_ADMIN_ROLE_ID), str(USER_ROLE_ID)

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PREVIOUS_REVISION)
        asyncio.run(_seed(database_url, ids))
        before = asyncio.run(_state(database_url, ids))
        assert before["users"] == {
            ids["admin"]: (admin_role, True),
            ids["global_admin"]: (admin_role, True),
            ids["regular"]: (user_role, False),
            ids["custom"]: (ids["custom_role"], False),
        }
        assert before["assignments"] == set()
        assert before["boundaries"] == set()
        assert before["is_base"] is True

        _upgrade(database_url, REVISION)
        after = asyncio.run(_state(database_url, ids))
        assert after["users"] == {
            ids["admin"]: (user_role, True),
            ids["global_admin"]: (user_role, True),
            ids["regular"]: (user_role, False),
            ids["custom"]: (ids["custom_role"], False),
        }
        assert after["assignments"] == {(ids["admin"], admin_role), (ids["global_admin"], admin_role)}
        assert after["boundaries"] == {
            (ids["admin"], admin_role, "platform", None),
            (ids["global_admin"], admin_role, "platform", None),
        }
        assert after["is_base"] is False

        _upgrade(database_url, "head")
        assert asyncio.run(_state(database_url, ids)) == after

        _downgrade(database_url, PREVIOUS_REVISION)
        assert asyncio.run(_state(database_url, ids)) == before
    finally:
        asyncio.run(_drop_database(database_name))
