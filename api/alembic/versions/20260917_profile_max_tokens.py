"""Add profile-level default max output tokens.

Revision ID: 20260917_profile_max_tokens
Revises: 20260916_solution_inbound
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_profile_max_tokens"
down_revision: str | None = "20260916_solution_inbound"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_model_profiles",
        sa.Column("default_max_tokens", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_ai_model_profiles_default_max_tokens_range",
        "ai_model_profiles",
        sa.text(
            "default_max_tokens IS NULL OR "
            "(default_max_tokens >= 1 AND default_max_tokens <= 200000)"
        ),
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_ai_model_profiles_default_max_tokens_range",
        "ai_model_profiles",
        type_="check",
    )
    op.drop_column("ai_model_profiles", "default_max_tokens")
