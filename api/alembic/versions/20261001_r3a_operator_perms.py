"""Give Platform Operator user support and seed the Secrets Reader role

Revision ID: 20261001_r3a_operator_perms
Revises: 20260929_user_base_perm_fix
Create Date: 2026-10-01

The permission vocabulary now has a `users` domain (the user directory and
support actions such as invites, password and MFA resets, deactivation) and a
`roleassignments` domain (assigning roles to users, separate from authoring
role definitions). This migration adds users.read, users.readwrite,
roleassignments.read and roleassignments.readwrite to the builtin Platform
Operator role and updates its description, which no longer describes a
read-only role.

It also seeds a fourth builtin role, Secrets Reader (fixed id ...0008, not a
base role), holding only secrets.read: the permission the Platform Admin
wildcard does not include, so decrypting a secret always takes an explicit
assignment.

Both roles are assigned to nobody and nothing reads role_permissions at
request time yet, so this changes no behaviour.

Migrations never import live application code; the sets below are frozen.
tests/unit/test_builtin_roles.py asserts they equal the live constants.
"""

from __future__ import annotations

from typing import Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from alembic import op

PLATFORM_OPERATOR_ROLE_ID = UUID("00000000-0000-0000-0000-000000000007")
PLATFORM_OPERATOR_PERMISSIONS: frozenset[str] = frozenset(
    {
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
        "workflows.read",
    }
)
ADDED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "roleassignments.read",
        "roleassignments.readwrite",
        "users.read",
        "users.readwrite",
    }
)
SECRETS_READER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000008")
SECRETS_READER_PERMISSIONS: frozenset[str] = frozenset({"secrets.read"})
SECRETS_READER_NAME = "Secrets Reader"
SECRETS_READER_DESCRIPTION = (
    "Decrypts secret values through the SDK secret paths, for local "
    "development. Not included in the Platform Admin wildcard. Not yet "
    "assignable."
)
SYSTEM_ACTOR = "system"

PREVIOUS_DESCRIPTION = "Read-only visibility into managed organizations. Not yet assignable."
DESCRIPTION = "Visibility into managed organizations, with user support and role assignment. Not yet assignable."

revision: str = "20261001_r3a_operator_perms"
down_revision: Union[str, None] = "20260929_user_base_perm_fix"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _set_description(description: str) -> None:
    op.get_bind().execute(
        sa.text("UPDATE roles SET description = :description WHERE id = CAST(:role_id AS uuid)"),
        {"description": description, "role_id": str(PLATFORM_OPERATOR_ROLE_ID)},
    )


def _insert_permissions(role_id: UUID, permissions: frozenset[str]) -> None:
    connection = op.get_bind()
    for permission in sorted(permissions):
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission) "
                "VALUES (CAST(:role_id AS uuid), :permission) ON CONFLICT DO NOTHING"
            ),
            {"role_id": str(role_id), "permission": permission},
        )


def _delete_permissions(role_id: UUID, permissions: frozenset[str]) -> None:
    connection = op.get_bind()
    for permission in sorted(permissions):
        connection.execute(
            sa.text(
                "DELETE FROM role_permissions "
                "WHERE role_id = CAST(:role_id AS uuid) AND permission = :permission"
            ),
            {"role_id": str(role_id), "permission": permission},
        )


def upgrade() -> None:
    _insert_permissions(PLATFORM_OPERATOR_ROLE_ID, ADDED_PERMISSIONS)
    _set_description(DESCRIPTION)

    op.get_bind().execute(
        sa.text(
            "INSERT INTO roles (id, name, description, is_base, is_builtin, created_by, created_at, updated_at) "
            "VALUES (CAST(:role_id AS uuid), :name, :description, false, true, :actor, NOW(), NOW()) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "role_id": str(SECRETS_READER_ROLE_ID),
            "name": SECRETS_READER_NAME,
            "description": SECRETS_READER_DESCRIPTION,
            "actor": SYSTEM_ACTOR,
        },
    )
    _insert_permissions(SECRETS_READER_ROLE_ID, SECRETS_READER_PERMISSIONS)


def downgrade() -> None:
    _delete_permissions(SECRETS_READER_ROLE_ID, SECRETS_READER_PERMISSIONS)
    op.get_bind().execute(
        sa.text("DELETE FROM roles WHERE id = CAST(:role_id AS uuid)"),
        {"role_id": str(SECRETS_READER_ROLE_ID)},
    )

    _delete_permissions(PLATFORM_OPERATOR_ROLE_ID, ADDED_PERMISSIONS)
    _set_description(PREVIOUS_DESCRIPTION)
