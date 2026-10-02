"""True pre-head rehearsal for ``alembic/versions/20261001_r3a_operator_perms.py``.

Builds a disposable database at the migration's ``down_revision``, records the
seeded builtin-role permissions plus a custom role's, upgrades to head, and
checks the two builtin-role changes and nothing else: Platform Operator gains
user support and role assignment, and a new Secrets Reader role holding only
secrets.read appears. The upgrade is idempotent at head, and a downgrade
restores the previous rows exactly.

The expected sets are frozen here, like the migration's own, so a later edit to
the live constants cannot silently change what this revision is checked to do.
"""

from __future__ import annotations

import asyncio
from typing import Any
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

PREVIOUS_REVISION = "20260929_user_base_perm_fix"
REVISION = "20261001_r3a_operator_perms"

PLATFORM_ADMIN_ROLE_ID = UUID("00000000-0000-0000-0000-000000000005")
USER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000006")
PLATFORM_OPERATOR_ROLE_ID = UUID("00000000-0000-0000-0000-000000000007")
DECRYPTION_ROLE_ID = UUID("00000000-0000-0000-0000-000000000008")

OPERATOR_BEFORE = {
    "agentruns.read",
    "agents.read",
    "apps.read",
    "configs.read",
    "executions.read",
    "forms.read",
    "integrations.read",
    "metrics.read",
    "organizations.read",
    "workflows.read",
}
OPERATOR_ADDED = {
    "roleassignments.read",
    "roleassignments.readwrite",
    "users.read",
    "users.readwrite",
}
USER_PERMISSIONS = {
    "agentruns.read",
    "agents.read",
    "apps.read",
    "executions.read",
    "forms.read",
    "knowledge.read",
    "mcp.read",
    "metrics.read",
    "settings.read",
}
CUSTOM_PERMISSIONS = {"forms.readwrite", "users.read"}
# Set by the later 20261001_r3a_operator_desc revision.
OPERATOR_DESCRIPTION_AT_HEAD = (
    "Support for customer organizations: view organizations and users, invite users, "
    "reset MFA, deactivate ordinary users, and assign roles that carry no permissions. "
    "Additional role only."
)


def _downgrade(database_url: str, revision: str) -> None:
    with _temporary_migration_database_url(database_url):
        command.downgrade(_alembic_config(), revision)


async def _seed_custom_role(database_url: str, role_id: str) -> None:
    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                "INSERT INTO roles (id, name, description, is_base, is_builtin, created_by) "
                "VALUES (CAST(:id AS uuid), 'Rehearsal Custom', 'custom', FALSE, FALSE, 'migration-rehearsal')"
            ),
            {"id": role_id},
        )
        for permission in sorted(CUSTOM_PERMISSIONS):
            await connection.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission) VALUES (CAST(:id AS uuid), :permission)"
                ),
                {"id": role_id, "permission": permission},
            )

    await _run_in_database(database_url, seed)


async def _snapshot(database_url: str, custom_role_id: str) -> dict[str, Any]:
    async def snapshot(connection: AsyncConnection) -> dict[str, Any]:
        rows = (
            await connection.execute(
                sa.text(
                    "SELECT role_id, permission FROM role_permissions "
                    "WHERE role_id IN (CAST(:admin AS uuid), CAST(:user AS uuid), "
                    "CAST(:operator AS uuid), CAST(:reader AS uuid), CAST(:custom AS uuid))"
                ),
                {
                    "admin": str(PLATFORM_ADMIN_ROLE_ID),
                    "user": str(USER_ROLE_ID),
                    "operator": str(PLATFORM_OPERATOR_ROLE_ID),
                    "reader": str(DECRYPTION_ROLE_ID),
                    "custom": custom_role_id,
                },
            )
        ).all()
        permissions: dict[str, set[str]] = {
            key: set() for key in ("admin", "user", "operator", "reader", "custom")
        }
        names = {
            str(PLATFORM_ADMIN_ROLE_ID): "admin",
            str(USER_ROLE_ID): "user",
            str(PLATFORM_OPERATOR_ROLE_ID): "operator",
            str(DECRYPTION_ROLE_ID): "reader",
            custom_role_id: "custom",
        }
        for role_id, permission in rows:
            permissions[names[str(role_id)]].add(permission)
        operator_description = await connection.scalar(
            sa.text("SELECT description FROM roles WHERE id = CAST(:id AS uuid)"),
            {"id": str(PLATFORM_OPERATOR_ROLE_ID)},
        )
        reader = (
            await connection.execute(
                sa.text(
                    "SELECT name, description, is_base, is_builtin FROM roles "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"id": str(DECRYPTION_ROLE_ID)},
            )
        ).mappings().one_or_none()
        reader_assignments = await connection.scalar(
            sa.text("SELECT COUNT(*) FROM user_roles WHERE role_id = CAST(:id AS uuid)"),
            {"id": str(DECRYPTION_ROLE_ID)},
        )
        return {
            "permissions": permissions,
            "operator_description": operator_description,
            "reader": dict(reader) if reader else None,
            "reader_assignments": reader_assignments,
        }

    return await _run_in_database(database_url, snapshot)


def test_operator_gains_user_support_and_secrets_reader_is_seeded() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    custom_role_id = str(uuid4())

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PREVIOUS_REVISION)
        asyncio.run(_seed_custom_role(database_url, custom_role_id))
        before = asyncio.run(_snapshot(database_url, custom_role_id))
        assert before["permissions"] == {
            "admin": set(),
            "user": USER_PERMISSIONS,
            "operator": OPERATOR_BEFORE,
            "reader": set(),
            "custom": CUSTOM_PERMISSIONS,
        }
        assert before["reader"] is None

        _upgrade(database_url, REVISION)
        after = asyncio.run(_snapshot(database_url, custom_role_id))
        assert after["permissions"] == {
            **before["permissions"],
            "operator": OPERATOR_BEFORE | OPERATOR_ADDED,
            "reader": {"secrets.read"},
        }
        assert "user support" in after["operator_description"]
        reader = after["reader"]
        assert (reader["name"], reader["is_base"], reader["is_builtin"]) == ("Secrets Reader", False, True)
        assert "local development" in reader["description"]
        assert "wildcard" in reader["description"]
        assert after["reader_assignments"] == 0

        # At head the later 20261001_r3a_operator_desc revision has rewritten
        # the Operator description; everything else this revision wrote holds.
        _upgrade(database_url, "head")
        assert asyncio.run(_snapshot(database_url, custom_role_id)) == {
            **after,
            "operator_description": OPERATOR_DESCRIPTION_AT_HEAD,
        }

        _downgrade(database_url, PREVIOUS_REVISION)
        assert asyncio.run(_snapshot(database_url, custom_role_id)) == before
    finally:
        asyncio.run(_drop_database(database_name))
