"""Correct the User base role's seeded read permissions

Revision ID: 20260929_user_base_perm_fix
Revises: 20260929_model_catalog
Create Date: 2026-09-29

The R2b derivation of the User base role's permissions counted an MCP tool's
transport-floor gate, instead of the gate of the REST route it wraps, and it
ignored entries an inline check narrows to admins. Seven read permissions
were therefore seeded that no signed-in user could exercise through any
route: configs.read, events.read, integrations.read, policyrules.read,
roles.read, tables.read and workflows.read. This migration removes those
rows from the User role. The derivation now decides each entry the way the
authorization matrix does (see shared/builtin_roles.py).

Nothing reads role_permissions at request time yet, so this changes no
behaviour: it only makes the seeded data describe today's access correctly.

Migrations never import live application code; the sets below are frozen.
tests/unit/test_builtin_roles.py asserts USER_BASE_PERMISSIONS equals the
live constant and the access-list derivation.
"""

from __future__ import annotations

from typing import Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from alembic import op

USER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000006")
USER_BASE_PERMISSIONS: frozenset[str] = frozenset(
    {
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
)
REMOVED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "configs.read",
        "events.read",
        "integrations.read",
        "policyrules.read",
        "roles.read",
        "tables.read",
        "workflows.read",
    }
)
ADDED_PERMISSIONS: frozenset[str] = frozenset()

revision: str = "20260929_user_base_perm_fix"
down_revision: Union[str, None] = "20260929_model_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _delete(permissions: frozenset[str]) -> None:
    connection = op.get_bind()
    for permission in sorted(permissions):
        connection.execute(
            sa.text(
                "DELETE FROM role_permissions "
                "WHERE role_id = CAST(:role_id AS uuid) AND permission = :permission"
            ),
            {"role_id": str(USER_ROLE_ID), "permission": permission},
        )


def _insert(permissions: frozenset[str]) -> None:
    connection = op.get_bind()
    for permission in sorted(permissions):
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission) "
                "VALUES (CAST(:role_id AS uuid), :permission) ON CONFLICT DO NOTHING"
            ),
            {"role_id": str(USER_ROLE_ID), "permission": permission},
        )


def upgrade() -> None:
    _delete(REMOVED_PERMISSIONS)
    _insert(ADDED_PERMISSIONS)


def downgrade() -> None:
    _delete(ADDED_PERMISSIONS)
    _insert(REMOVED_PERMISSIONS)
