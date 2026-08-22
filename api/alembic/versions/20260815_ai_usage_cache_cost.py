"""record cache-aware AI usage and pricing

Revision ID: 20260815_ai_usage_cache_cost
Revises: 20260815_chat_attachments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_ai_usage_cache_cost"
down_revision: str | None = "20260815_chat_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_usage",
        sa.Column(
            "cache_read_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "ai_usage",
        sa.Column(
            "cache_write_tokens",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "ai_usage",
        sa.Column("provider_cost", sa.Numeric(12, 8), nullable=True),
    )
    op.add_column(
        "ai_model_pricing",
        sa.Column(
            "cache_read_price_per_million",
            sa.Numeric(10, 4),
            nullable=True,
        ),
    )
    op.add_column(
        "ai_model_pricing",
        sa.Column(
            "cache_write_price_per_million",
            sa.Numeric(10, 4),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_model_pricing", "cache_write_price_per_million")
    op.drop_column("ai_model_pricing", "cache_read_price_per_million")
    op.drop_column("ai_usage", "provider_cost")
    op.drop_column("ai_usage", "cache_write_tokens")
    op.drop_column("ai_usage", "cache_read_tokens")
