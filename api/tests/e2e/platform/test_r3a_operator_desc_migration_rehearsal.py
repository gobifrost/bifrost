"""True pre-head rehearsal for ``alembic/versions/20261001_r3a_operator_desc.py``.

Builds a disposable database at the migration's ``down_revision``, upgrades,
and checks that only Platform Operator's description changes (its
permissions stay), that the upgrade is idempotent at head, and that the
downgrade restores the previous description. Texts are frozen here.
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

PREVIOUS_REVISION = "20261001_r3a_boundary_fill"
REVISION = "20261001_r3a_operator_desc"
PLATFORM_OPERATOR_ROLE_ID = UUID("00000000-0000-0000-0000-000000000007")
BEFORE = (
    "Visibility into managed organizations, with user support and role assignment. "
    "Not yet assignable."
)
AFTER = (
    "Support for customer organizations: view organizations and users, invite users, "
    "reset MFA, deactivate ordinary users, and assign roles that carry no permissions. "
    "Additional role only."
)


def _downgrade(database_url: str, revision: str) -> None:
    with _temporary_migration_database_url(database_url):
        command.downgrade(_alembic_config(), revision)


async def _operator(database_url: str) -> tuple[str, set[str]]:
    async def read(connection: AsyncConnection) -> tuple[str, set[str]]:
        params = {"id": str(PLATFORM_OPERATOR_ROLE_ID)}
        description = await connection.scalar(
            sa.text("SELECT description FROM roles WHERE id = CAST(:id AS uuid)"), params
        )
        permissions = (
            await connection.execute(
                sa.text("SELECT permission FROM role_permissions WHERE role_id = CAST(:id AS uuid)"),
                params,
            )
        ).scalars()
        return description, set(permissions)

    return await _run_in_database(database_url, read)


def test_operator_description_says_what_the_role_does() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PREVIOUS_REVISION)
        description, permissions = asyncio.run(_operator(database_url))
        assert description == BEFORE

        _upgrade(database_url, REVISION)
        assert asyncio.run(_operator(database_url)) == (AFTER, permissions)

        _upgrade(database_url, "head")
        assert asyncio.run(_operator(database_url)) == (AFTER, permissions)

        _downgrade(database_url, PREVIOUS_REVISION)
        assert asyncio.run(_operator(database_url)) == (BEFORE, permissions)
    finally:
        asyncio.run(_drop_database(database_name))
