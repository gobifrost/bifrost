"""Make Platform Admin an additional role, never a base role

Revision ID: 20261002_r3a_admin_additional
Revises: 20261001_r3a_operator_desc
Create Date: 2026-10-02

Until now a Platform Admin was a user whose base role was Platform Admin.
From R3a every user's base role is User or a custom role, and Platform
Admin is an additional role held with a platform boundary. Every user whose
base role is Platform Admin becomes a User who also holds Platform Admin.
The role stops being flagged as a base role (`roles.is_base`). `users.is_superuser` is untouched: it stays true for exactly the people who
hold the Platform Admin assignment, so live access does not change.

Data only and idempotent: a user already holding the assignment or its
boundary is left as is, and a user whose base role is no longer Platform
Admin is not touched again. Downgrade restores Platform Admin as the base
role of everyone who holds the assignment and removes the assignment.
"""

from __future__ import annotations

from typing import Sequence, Union
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "20261002_r3a_admin_additional"
down_revision: Union[str, None] = "20261001_r3a_operator_desc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLATFORM_ADMIN_ROLE_ID = UUID("00000000-0000-0000-0000-000000000005")
USER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000006")
ASSIGNED_BY = "migration:20261002_r3a_admin_additional"

_IDS = {"admin_id": str(PLATFORM_ADMIN_ROLE_ID), "user_id": str(USER_ROLE_ID)}


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO user_roles (user_id, role_id, assigned_by)
            SELECT u.id, CAST(:admin_id AS uuid), :assigned_by
            FROM users u
            WHERE u.base_role_id = CAST(:admin_id AS uuid)
              AND NOT EXISTS (
                SELECT 1 FROM user_roles ur
                WHERE ur.user_id = u.id AND ur.role_id = CAST(:admin_id AS uuid)
              )
            """
        ),
        {**_IDS, "assigned_by": ASSIGNED_BY},
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO user_role_boundaries (id, user_id, role_id, kind, organization_id)
            SELECT gen_random_uuid(), u.id, CAST(:admin_id AS uuid), 'platform', NULL
            FROM users u
            WHERE u.base_role_id = CAST(:admin_id AS uuid)
              AND NOT EXISTS (
                SELECT 1 FROM user_role_boundaries b
                WHERE b.user_id = u.id AND b.role_id = CAST(:admin_id AS uuid)
                  AND b.kind = 'platform'
              )
            """
        ),
        _IDS,
    )
    bind.execute(
        sa.text(
            "UPDATE users SET base_role_id = CAST(:user_id AS uuid) "
            "WHERE base_role_id = CAST(:admin_id AS uuid)"
        ),
        _IDS,
    )
    bind.execute(
        sa.text("UPDATE roles SET is_base = false WHERE id = CAST(:admin_id AS uuid)"),
        _IDS,
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE roles SET is_base = true WHERE id = CAST(:admin_id AS uuid)"),
        _IDS,
    )
    bind.execute(
        sa.text(
            """
            UPDATE users SET base_role_id = CAST(:admin_id AS uuid)
            WHERE id IN (
                SELECT user_id FROM user_roles WHERE role_id = CAST(:admin_id AS uuid)
            )
            """
        ),
        _IDS,
    )
    # The boundary rows go with the assignment through the composite foreign
    # key's ON DELETE CASCADE.
    bind.execute(
        sa.text("DELETE FROM user_roles WHERE role_id = CAST(:admin_id AS uuid)"),
        _IDS,
    )
