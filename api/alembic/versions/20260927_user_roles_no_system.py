"""guard: user_roles cannot reference the system account

Revision ID: 20260927_no_sys_role
Revises: 20260927_drop_local_runner
Create Date: 2026-09-27
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_no_sys_role"
down_revision: str | None = "20260927_drop_local_runner"
branch_labels = None
depends_on = None

# System (sentinel) user UUID from src.core.constants.SYSTEM_USER_ID
SYSTEM_USER_UUID = "00000000-0000-0000-0000-000000000001"
CONSTRAINT_NAME = "ck_user_roles_not_system_user"


def upgrade() -> None:
    # Defense in depth alongside the application-level guard: the system
    # account is never a valid target for a role assignment, so make it
    # unrepresentable at the schema level too.
    op.execute(
        sa.text("DELETE FROM user_roles WHERE user_id = CAST(:system_user_id AS uuid)").bindparams(
            system_user_id=SYSTEM_USER_UUID
        )
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "user_roles",
        # DDL can't take bound parameters; SYSTEM_USER_UUID is a fixed constant.
        f"user_id <> '{SYSTEM_USER_UUID}'",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "user_roles", type_="check")
