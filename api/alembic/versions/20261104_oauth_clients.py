"""Retain OAuth client registrations independently of login/token expiration."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261104_oauth_clients"
down_revision = "20261101_table_name_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_client_registrations",
        sa.Column("client_id", sa.String(255), primary_key=True),
        sa.Column("client_metadata", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("oauth_client_registrations")
