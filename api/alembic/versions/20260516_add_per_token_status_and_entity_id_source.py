"""add per-token status and entity_id_source

Revision ID: 20260516_per_token_status
Revises: 20260518_documents_composite_pk
Create Date: 2026-05-16

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260516_per_token_status"
# Chained after 20260518_documents_composite_pk (PR #257) to keep a
# single alembic head after the merge from main; this migration was
# originally authored against 20260506_knowledge_dim.
down_revision: Union[str, None] = "20260518_documents_composite_pk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "oauth_tokens",
        sa.Column("status", sa.String(50), nullable=False, server_default="not_connected"),
    )
    op.add_column(
        "oauth_tokens",
        sa.Column("status_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "oauth_tokens",
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "oauth_providers",
        sa.Column(
            "entity_id_source",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # Backfill existing tokens: completed if not expired, expired otherwise.
    op.execute("""
        UPDATE oauth_tokens
        SET status = CASE
            WHEN expires_at IS NULL OR expires_at > NOW() THEN 'completed'
            ELSE 'expired'
        END
        WHERE status = 'not_connected'
    """)


def downgrade() -> None:
    op.drop_column("oauth_providers", "entity_id_source")
    op.drop_column("oauth_tokens", "last_refresh_at")
    op.drop_column("oauth_tokens", "status_message")
    op.drop_column("oauth_tokens", "status")
