"""make agent run limits optional

Revision ID: 20260815_optional_agent_limits
Revises: 20260814_logo_thumbnails
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_optional_agent_limits"
down_revision: str | None = "20260814_logo_thumbnails"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "agents",
        "max_iterations",
        existing_type=sa.Integer(),
        server_default=None,
    )
    op.alter_column(
        "agents",
        "max_token_budget",
        existing_type=sa.Integer(),
        server_default=None,
    )


def downgrade() -> None:
    op.alter_column(
        "agents",
        "max_iterations",
        existing_type=sa.Integer(),
        server_default=sa.text("50"),
    )
    op.alter_column(
        "agents",
        "max_token_budget",
        existing_type=sa.Integer(),
        server_default=sa.text("100000"),
    )
