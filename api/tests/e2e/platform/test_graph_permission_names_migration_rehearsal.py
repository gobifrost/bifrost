"""True pre-head rehearsal for ``alembic/versions/20261009_graph_permission_names.py``.

Builds a disposable database at the migration's ``down_revision``, seeds a
custom role holding renamed, split and unchanged permissions and a restricted
workflow's permission grants, upgrades, and checks every role and grant: the
built-in roles end exactly at their new sets (the readbasic rename plus the
launch permissions), the custom role's strings move to their Graph names, and
the grants' dotted sub-resource names are renamed. The downgrade restores the
previous rows exactly. The custom role holds only strings the downgrade can
restore (the merged reads cannot be split back; the migration says so).

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

PREVIOUS_REVISION = "20261009_merge_integ_identity"
REVISION = "20261009_graph_permission_names"

PLATFORM_ADMIN_ROLE_ID = UUID("00000000-0000-0000-0000-000000000005")
USER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000006")
PLATFORM_OPERATOR_ROLE_ID = UUID("00000000-0000-0000-0000-000000000007")
DECRYPTION_ROLE_ID = UUID("00000000-0000-0000-0000-000000000008")
ACTOR = "migration-rehearsal"

USER_BEFORE = {
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
USER_AFTER = {
    "agentruns.read",
    "agents.execute",
    "agents.readbasic",
    "ai.execute",
    "ai.read",
    "apps.readbasic",
    "executions.readbasic",
    "forms.readbasic",
    "knowledge.read",
    "mcp.readbasic",
    "metrics.read",
    "settings.readbasic",
    "workflows.execute",
}
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
    "roleassignments.read",
    "roleassignments.readwrite",
    "users.read",
    "users.readwrite",
    "workflows.execute",
    "workflows.read",
}
OPERATOR_AFTER = {
    "agentruns.read",
    "agents.execute",
    "agents.readbasic",
    "apps.readbasic",
    "configs.read",
    "executions.readbasic",
    "forms.readbasic",
    "integrations.read",
    "metrics.read",
    "organizations.read",
    "roleassignments.read",
    "roleassignments.readwrite",
    "users.read",
    "users.readwrite",
    "workflows.execute",
    "workflows.read",
}
CUSTOM_BEFORE = {
    "agents.readwrite",
    "apps.deploy.execute",
    "apps.read",
    "apps.read.all",
    "executions.read",
    "settings.read.all",
    "users.lifecycle.readwrite",
}
CUSTOM_AFTER = {
    "agents.readwrite",
    "apps.publish",
    "apps.read",
    "apps.readbasic",
    "executions.readbasic",
    "settings.read",
    "userlifecycle.readwrite",
}
GRANTS_BEFORE = {
    "publish": "apps.deploy.execute",
    "build": "solutions.build.execute",
    "unchanged": "users.read",
}
GRANTS_AFTER = {
    "publish": "apps.publish",
    "build": "solutions.build",
    "unchanged": "users.read",
}

_ROLES = {
    "admin": str(PLATFORM_ADMIN_ROLE_ID),
    "user": str(USER_ROLE_ID),
    "operator": str(PLATFORM_OPERATOR_ROLE_ID),
    "reader": str(DECRYPTION_ROLE_ID),
}


def _downgrade(database_url: str, revision: str) -> None:
    with _temporary_migration_database_url(database_url):
        command.downgrade(_alembic_config(), revision)


async def _seed(database_url: str, ids: dict[str, str]) -> None:
    async def seed(connection: AsyncConnection) -> None:
        await connection.execute(
            sa.text(
                "INSERT INTO roles (id, name, description, is_base, is_builtin, created_by) "
                "VALUES (CAST(:id AS uuid), 'Rehearsal Custom', 'custom', FALSE, FALSE, :actor)"
            ),
            {"id": ids["custom"], "actor": ACTOR},
        )
        for permission in sorted(CUSTOM_BEFORE):
            await connection.execute(
                sa.text(
                    "INSERT INTO role_permissions (role_id, permission) VALUES (CAST(:id AS uuid), :permission)"
                ),
                {"id": ids["custom"], "permission": permission},
            )
        await connection.execute(
            sa.text(
                "INSERT INTO workflows (id, name, function_name, path, organization_id, endpoint_enabled) "
                "VALUES (CAST(:id AS uuid), 'rehearsal', 'rehearsal', 'workflows/rehearsal.py', NULL, FALSE)"
            ),
            {"id": ids["workflow"]},
        )
        for key, permission in sorted(GRANTS_BEFORE.items()):
            await connection.execute(
                sa.text(
                    "INSERT INTO workflow_permission_grants (id, workflow_id, permission, boundary_kind, created_by) "
                    "VALUES (CAST(:id AS uuid), CAST(:workflow AS uuid), :permission, 'platform', :actor)"
                ),
                {"id": ids[key], "workflow": ids["workflow"], "permission": permission, "actor": ACTOR},
            )

    await _run_in_database(database_url, seed)


async def _snapshot(database_url: str, ids: dict[str, str]) -> dict[str, Any]:
    async def snapshot(connection: AsyncConnection) -> dict[str, Any]:
        roles = {**_ROLES, "custom": ids["custom"]}
        permissions: dict[str, set[str]] = {}
        for name, role_id in roles.items():
            rows = await connection.execute(
                sa.text("SELECT permission FROM role_permissions WHERE role_id = CAST(:id AS uuid)"),
                {"id": role_id},
            )
            permissions[name] = set(rows.scalars())
        grants = {
            key: await connection.scalar(
                sa.text("SELECT permission FROM workflow_permission_grants WHERE id = CAST(:id AS uuid)"),
                {"id": ids[key]},
            )
            for key in GRANTS_BEFORE
        }
        return {"permissions": permissions, "grants": grants}

    return await _run_in_database(database_url, snapshot)


def test_roles_and_grants_move_to_graph_names_and_back() -> None:
    database_name = f"bifrost_r2b_rehearsal_{uuid4().hex[:12]}"
    _assert_safe_database_name(database_name)
    database_url = _direct_database_url(database_name)
    ids = {key: str(uuid4()) for key in ("custom", "workflow", *GRANTS_BEFORE)}

    try:
        asyncio.run(_create_database(database_name))
        _upgrade(database_url, PREVIOUS_REVISION)
        asyncio.run(_seed(database_url, ids))
        before = asyncio.run(_snapshot(database_url, ids))
        assert before == {
            "permissions": {
                "admin": {"*"},
                "user": USER_BEFORE,
                "operator": OPERATOR_BEFORE,
                "reader": {"secrets.read"},
                "custom": CUSTOM_BEFORE,
            },
            "grants": GRANTS_BEFORE,
        }

        _upgrade(database_url, REVISION)
        after = asyncio.run(_snapshot(database_url, ids))
        assert after == {
            "permissions": {
                "admin": {"*"},
                "user": USER_AFTER,
                "operator": OPERATOR_AFTER,
                "reader": {"secrets.read"},
                "custom": CUSTOM_AFTER,
            },
            "grants": GRANTS_AFTER,
        }

        _upgrade(database_url, "head")
        at_head = asyncio.run(_snapshot(database_url, ids))
        assert at_head == after

        _downgrade(database_url, PREVIOUS_REVISION)
        downgraded = asyncio.run(_snapshot(database_url, ids))
        assert downgraded == before
    finally:
        asyncio.run(_drop_database(database_name))
