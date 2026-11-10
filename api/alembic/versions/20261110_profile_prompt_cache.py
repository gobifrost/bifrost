"""Track learned prompt-cache support per model without dropping legacy fields."""

from alembic import op
import sqlalchemy as sa

revision = "20261110_profile_prompt_cache"
down_revision = "20261110_merge_lanes_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ai_model_profiles", sa.Column("anthropic_prompt_cache_supported", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_model_profiles", "anthropic_prompt_cache_supported")
