"""Grant the User base role permission to use personal Chat.

Revision ID: 20261106_chat_execute
Revises: 20261105_wf_provenance_merge
Create Date: 2026-11-06

Chat is now its own capability rather than borrowing agent or AI execution.
Existing User-role holders retain Chat access. No other built-in or custom
role is changed: administrators continue through their wildcard, and custom
roles need an explicit Chat grant.

Migrations never import live application code; the identifiers and permission
below are frozen literals.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20261106_chat_execute"
down_revision: Union[str, None] = "20261105_wf_provenance_merge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

USER_ROLE_ID = "00000000-0000-0000-0000-000000000006"
ADDED_USER_PERMISSIONS: frozenset[str] = frozenset({"chat.execute"})


def upgrade() -> None:
    connection = op.get_bind()
    for permission in sorted(ADDED_USER_PERMISSIONS):
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission) "
                "VALUES (CAST(:role_id AS uuid), :permission) ON CONFLICT DO NOTHING"
            ),
            {"role_id": USER_ROLE_ID, "permission": permission},
        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions "
            "WHERE role_id = CAST(:role_id AS uuid) AND permission = ANY(:permissions)"
        ),
        {"role_id": USER_ROLE_ID, "permissions": list(ADDED_USER_PERMISSIONS)},
    )
