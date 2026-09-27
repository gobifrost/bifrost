"""guard: user_roles cannot reference the system account

Revision ID: 20260927_no_sys_role
Revises: 20260927_r1b_mcp_names_b2
Create Date: 2026-09-27
"""

from alembic import op

revision: str = "20260927_no_sys_role"
down_revision: str | None = "20260927_r1b_mcp_names_b2"
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
        f"DELETE FROM user_roles WHERE user_id = '{SYSTEM_USER_UUID}'"
    )
    op.create_check_constraint(
        CONSTRAINT_NAME,
        "user_roles",
        f"user_id <> '{SYSTEM_USER_UUID}'",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT_NAME, "user_roles", type_="check")
