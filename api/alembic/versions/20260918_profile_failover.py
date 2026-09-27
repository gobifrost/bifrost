"""Add per-profile failover pointer for transport-terminal recovery.

Revision ID: 20260918_profile_failover
Revises: 20260918_merge_plat_prof_heads
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260918_profile_failover"
down_revision: str | None = "20260918_merge_plat_prof_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_model_profiles",
        sa.Column("failover_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_ai_model_profiles_failover_profile_id",
        "ai_model_profiles",
        "ai_model_profiles",
        ["failover_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_ai_model_profiles_failover_profile_id",
        "ai_model_profiles",
        ["failover_profile_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_model_profiles_failover_profile_id",
        table_name="ai_model_profiles",
    )
    op.drop_constraint(
        "fk_ai_model_profiles_failover_profile_id",
        "ai_model_profiles",
        type_="foreignkey",
    )
    op.drop_column("ai_model_profiles", "failover_profile_id")
