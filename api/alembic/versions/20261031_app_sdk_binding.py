"""Record the public API major required by hosted App SDK bindings.

Revision ID: 20261031_app_sdk_binding
Revises: 20261031_human_auth_sessions
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20261031_app_sdk_binding"
down_revision: str | None = "20261031_human_auth_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("sdk_binding_api_major", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "sdk_binding_api_major")
