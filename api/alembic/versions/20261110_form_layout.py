"""Store portable form presentation layouts separately from normalized fields."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20261110_form_layout"
down_revision = "20261111_provider_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("forms", sa.Column("layout", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("forms", "layout")
