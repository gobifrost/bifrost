"""Preserve integration choice display labels alongside existing stored values."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20261111_config_option_labels"
down_revision = "20261110_profile_prompt_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("integration_config_schema", sa.Column("option_labels", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("integration_config_schema", "option_labels")
