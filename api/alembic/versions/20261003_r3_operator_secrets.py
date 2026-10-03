"""Give Platform Operator workflow execution and assign Operator and Secrets Reader

Revision ID: 20261003_r3_operator_secrets
Revises: 20261002_r3a_admin_additional
Create Date: 2026-10-03

Platform Operator gains `workflows.execute`, so provider-organization staff
can run workflows, such as onboarding, against customer organizations. Its
description says so. It still never holds `secrets.read` or a
platform-wide permission.

Then it assigns two built-in roles:

- Platform Operator goes to every provider-organization user who is not a
  Platform Admin (`is_superuser` false and no Platform Admin assignment), not
  a system account and not external, inactive users included, at the
  `managed_organizations` boundary (every customer organization). This takes
  effect at once on the routes the authorization evaluator decides (users,
  role assignments, organizations).
- Secrets Reader goes to every user who holds the Platform Admin assignment,
  apart from system accounts. Decrypting a secret is decided per target:
  Global needs a `platform` boundary, the provider organization needs an
  `organization` boundary naming it, and every customer organization is
  covered by `managed_organizations` (which excludes the provider
  organization and Global). `platform` does not cover organizations, so all
  three boundaries are written; each is the narrowest boundary that reaches
  its target. Nothing checks `secrets.read` yet, so this assignment takes
  effect when secret decryption is enforced.

Secrets Reader's description stops saying it is not assignable. New users get
neither role automatically.

Data only and idempotent: an existing assignment is left as is, with its own
boundaries. Downgrade removes only the assignments this migration made
(matched by `assigned_by`), Platform Operator's `workflows.execute`, and
restores both descriptions.

Migrations never import live application code; the literals below are frozen.
tests/unit/test_builtin_roles.py asserts they equal the live constants.
"""

from __future__ import annotations

from typing import Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "20261003_r3_operator_secrets"
down_revision: Union[str, None] = "20261002_r3a_admin_additional"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLATFORM_ADMIN_ROLE_ID = UUID("00000000-0000-0000-0000-000000000005")
PLATFORM_OPERATOR_ROLE_ID = UUID("00000000-0000-0000-0000-000000000007")
DECRYPTION_ROLE_ID = UUID("00000000-0000-0000-0000-000000000008")
PROVIDER_ORG_ID = UUID("00000000-0000-0000-0000-000000000002")

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
        "workflows.execute",
        "workflows.read",
    }
)
ADDED_OPERATOR_PERMISSIONS: frozenset[str] = frozenset({"workflows.execute"})

PREVIOUS_OPERATOR_DESCRIPTION = (
    "Support for customer organizations: view organizations and users, invite users, "
    "reset MFA, deactivate ordinary users, and assign roles that carry no permissions. "
    "Additional role only."
)
OPERATOR_DESCRIPTION = (
    "Support for customer organizations: view organizations and users, invite users, "
    "reset MFA, deactivate ordinary users, assign roles that carry no permissions, "
    "and run workflows in customer organizations. Additional role only."
)
PREVIOUS_DECRYPTION_DESCRIPTION = (
    "Decrypts secret values through the SDK secret paths, for local "
    "development. Not included in the Platform Admin wildcard. Not yet "
    "assignable."
)
DECRYPTION_DESCRIPTION = (
    "Decrypts secret values through the SDK secret paths, for local "
    "development. Not included in the Platform Admin wildcard."
)

ASSIGNED_BY = "migration:20261003_r3_operator_secrets"

_IDS = {
    "admin_id": str(PLATFORM_ADMIN_ROLE_ID),
    "operator_id": str(PLATFORM_OPERATOR_ROLE_ID),
    "decryption_id": str(DECRYPTION_ROLE_ID),
    "provider_id": str(PROVIDER_ORG_ID),
}

# The (kind, organization_id) boundaries Platform Operator is written with.
_OPERATOR_BOUNDARIES = (("managed_organizations", None),)

# The (kind, organization_id) boundaries Secrets Reader is written with.
_DECRYPTION_BOUNDARIES = (
    ("platform", None),
    ("managed_organizations", None),
    ("organization", str(PROVIDER_ORG_ID)),
)

# Provider-organization users who are not Platform Admins, system accounts or
# external users.
_ASSIGN_OPERATOR = """
    INSERT INTO user_roles (user_id, role_id, assigned_by)
    SELECT u.id, CAST(:operator_id AS uuid), :assigned_by
    FROM users u
    WHERE u.organization_id = CAST(:provider_id AS uuid)
      AND u.is_superuser = false
      AND u.is_system = false
      AND u.is_external = false
      AND NOT EXISTS (
        SELECT 1 FROM user_roles admin_ur
        WHERE admin_ur.user_id = u.id AND admin_ur.role_id = CAST(:admin_id AS uuid)
      )
      AND NOT EXISTS (
        SELECT 1 FROM user_roles ur
        WHERE ur.user_id = u.id AND ur.role_id = CAST(:operator_id AS uuid)
      )
"""

# Platform Admin holders who are not system accounts.
_ASSIGN_DECRYPTION = """
    INSERT INTO user_roles (user_id, role_id, assigned_by)
    SELECT admin_ur.user_id, CAST(:decryption_id AS uuid), :assigned_by
    FROM user_roles admin_ur
    JOIN users u ON u.id = admin_ur.user_id
    WHERE admin_ur.role_id = CAST(:admin_id AS uuid)
      AND u.is_system = false
      AND NOT EXISTS (
        SELECT 1 FROM user_roles ur
        WHERE ur.user_id = admin_ur.user_id AND ur.role_id = CAST(:decryption_id AS uuid)
      )
"""

# One boundary for every assignment this migration made for :role_id.
_ADD_BOUNDARY = """
    INSERT INTO user_role_boundaries (id, user_id, role_id, kind, organization_id)
    SELECT gen_random_uuid(), ur.user_id, ur.role_id, CAST(:kind AS varchar), CAST(:organization_id AS uuid)
    FROM user_roles ur
    WHERE ur.role_id = CAST(:role_id AS uuid)
      AND ur.assigned_by = :assigned_by
      AND NOT EXISTS (
        SELECT 1 FROM user_role_boundaries b
        WHERE b.user_id = ur.user_id AND b.role_id = ur.role_id AND b.kind = CAST(:kind AS varchar)
          AND b.organization_id IS NOT DISTINCT FROM CAST(:organization_id AS uuid)
      )
"""


def _set_description(role_id: UUID, description: str) -> None:
    op.get_bind().execute(
        sa.text("UPDATE roles SET description = :description WHERE id = CAST(:role_id AS uuid)"),
        {"description": description, "role_id": str(role_id)},
    )


def _assign(statement: str, role_id: UUID, boundaries: tuple[tuple[str, str | None], ...]) -> None:
    bind = op.get_bind()
    bind.execute(sa.text(statement), {**_IDS, "assigned_by": ASSIGNED_BY})
    for kind, organization_id in boundaries:
        bind.execute(
            sa.text(_ADD_BOUNDARY),
            {
                "role_id": str(role_id),
                "assigned_by": ASSIGNED_BY,
                "kind": kind,
                "organization_id": organization_id,
            },
        )


def upgrade() -> None:
    bind = op.get_bind()
    for permission in sorted(ADDED_OPERATOR_PERMISSIONS):
        bind.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission) "
                "VALUES (CAST(:role_id AS uuid), :permission) ON CONFLICT DO NOTHING"
            ),
            {"role_id": str(PLATFORM_OPERATOR_ROLE_ID), "permission": permission},
        )
    _set_description(PLATFORM_OPERATOR_ROLE_ID, OPERATOR_DESCRIPTION)
    _set_description(DECRYPTION_ROLE_ID, DECRYPTION_DESCRIPTION)

    _assign(_ASSIGN_OPERATOR, PLATFORM_OPERATOR_ROLE_ID, _OPERATOR_BOUNDARIES)
    _assign(_ASSIGN_DECRYPTION, DECRYPTION_ROLE_ID, _DECRYPTION_BOUNDARIES)


def downgrade() -> None:
    bind = op.get_bind()
    # The boundary rows go with the assignment through the composite foreign
    # key's ON DELETE CASCADE.
    bind.execute(
        sa.text(
            "DELETE FROM user_roles "
            "WHERE role_id IN (CAST(:operator_id AS uuid), CAST(:decryption_id AS uuid)) "
            "AND assigned_by = :assigned_by"
        ),
        {**_IDS, "assigned_by": ASSIGNED_BY},
    )
    _set_description(DECRYPTION_ROLE_ID, PREVIOUS_DECRYPTION_DESCRIPTION)
    _set_description(PLATFORM_OPERATOR_ROLE_ID, PREVIOUS_OPERATOR_DESCRIPTION)
    for permission in sorted(ADDED_OPERATOR_PERMISSIONS):
        bind.execute(
            sa.text(
                "DELETE FROM role_permissions "
                "WHERE role_id = CAST(:role_id AS uuid) AND permission = :permission"
            ),
            {"role_id": str(PLATFORM_OPERATOR_ROLE_ID), "permission": permission},
        )
