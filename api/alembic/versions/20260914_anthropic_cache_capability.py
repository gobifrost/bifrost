"""Store Anthropic prompt-cache capability on provider connections.

Revision ID: 20260914_anthropic_cache
Revises: 20260912_app_sdk_provenance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260914_anthropic_cache"
down_revision: str | None = "20260912_app_sdk_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_provider_connections",
        sa.Column("anthropic_prompt_cache_supported", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_provider_connections", "anthropic_prompt_cache_supported")
