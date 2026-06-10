"""user external-isolation flag

EXT-1: ``User.is_external`` marks portal/guest users whose visibility is
restricted to their own org tier — an external, non-bypass principal gets no
global (NULL-org) entities from the cascade and no ``access_level=
"authenticated"`` entitlement. Enforced in ``OrgScopedRepository``; this
migration only adds the flag.

Revision ID: 20260610_user_is_external
Revises: 20260610_solution_version
Create Date: 2026-06-10 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "20260610_user_is_external"
down_revision = "20260610_solution_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_external",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_external")
