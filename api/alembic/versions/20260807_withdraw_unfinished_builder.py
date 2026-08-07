"""withdraw the accidentally published unfinished Builder feature

Revision ID: 20260807_withdraw_builder
Revises: 20260806_scheduler_slots

The original Builder revisions remain as no-op tombstones so fresh databases do
not install the withdrawn schema and databases that briefly observed those
revision IDs can still advance. Existing extra columns/tables are intentionally
left inert: dropping potentially populated schema in an emergency correction
would turn a source-scope mistake into data loss.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_withdraw_builder"
down_revision: str = "20260806_scheduler_slots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORM_ADMIN_ROLE_ID = "00000000-0000-0000-0000-000000000003"
PLATFORM_OPERATOR_ROLE_ID = "00000000-0000-0000-0000-000000000004"


def upgrade() -> None:
    """Remove only deterministic Builder seed data from already-upgraded databases."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("roles"):
        return

    role_ids = (PLATFORM_ADMIN_ROLE_ID, PLATFORM_OPERATOR_ROLE_ID)
    if inspector.has_table("user_roles"):
        op.execute(
            sa.text(
                "DELETE FROM user_roles WHERE role_id IN "
                "(CAST(:admin AS uuid), CAST(:operator AS uuid))"
            ).bindparams(admin=role_ids[0], operator=role_ids[1])
        )
    op.execute(
        sa.text(
            "DELETE FROM roles WHERE id IN "
            "(CAST(:admin AS uuid), CAST(:operator AS uuid))"
        ).bindparams(admin=role_ids[0], operator=role_ids[1])
    )


def downgrade() -> None:
    # The withdrawn feature is not recreated when moving backward through the
    # compatibility tombstones.
    pass
