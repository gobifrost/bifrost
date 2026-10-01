"""Give the Platform Operator role user support and role assignment

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

The Operator role is assigned to nobody and nothing reads role_permissions at
request time yet, so this changes no behaviour.

Migrations never import live application code; the sets below are frozen.
tests/unit/test_builtin_roles.py asserts PLATFORM_OPERATOR_PERMISSIONS equals
the live constant.
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


def upgrade() -> None:
    connection = op.get_bind()
    for permission in sorted(ADDED_PERMISSIONS):
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission) "
                "VALUES (CAST(:role_id AS uuid), :permission) ON CONFLICT DO NOTHING"
            ),
            {"role_id": str(PLATFORM_OPERATOR_ROLE_ID), "permission": permission},
        )
    _set_description(DESCRIPTION)


def downgrade() -> None:
    connection = op.get_bind()
    for permission in sorted(ADDED_PERMISSIONS):
        connection.execute(
            sa.text(
                "DELETE FROM role_permissions "
                "WHERE role_id = CAST(:role_id AS uuid) AND permission = :permission"
            ),
            {"role_id": str(PLATFORM_OPERATOR_ROLE_ID), "permission": permission},
        )
    _set_description(PREVIOUS_DESCRIPTION)
