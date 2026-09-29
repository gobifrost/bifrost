"""model catalog: cached models.dev snapshot, catalog provider, reasoning

Revision ID: 20260929_model_catalog
Revises: 20260928_audit_op_surface
Create Date: 2026-09-29
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20260929_model_catalog"
down_revision: str | None = "20260928_audit_op_surface"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_model_catalog",
        sa.Column("source", sa.String(50), primary_key=True),
        sa.Column("etag", sa.String(200), nullable=True),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("provider_count", sa.Integer, nullable=False),
        sa.Column("model_count", sa.Integer, nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "ai_provider_connections",
        sa.Column("catalog_provider_id", sa.String(100), nullable=True),
    )
    # Native connections on their provider's own endpoint are that catalog
    # provider. Custom endpoints (Azure on an "openai" connection, any
    # "openai_compatible") stay unlinked: the catalog cannot vouch for them.
    op.execute(
        """
        UPDATE ai_provider_connections SET catalog_provider_id = CASE provider
            WHEN 'opencode_go' THEN 'opencode-go'
            ELSE provider
        END
        WHERE provider IN ('openrouter', 'opencode_go')
           OR (provider = 'openai' AND (endpoint IS NULL OR rtrim(endpoint, '/') = 'https://api.openai.com/v1'))
           OR (provider = 'anthropic' AND (endpoint IS NULL OR rtrim(endpoint, '/') = 'https://api.anthropic.com'))
           OR (provider = 'google' AND (endpoint IS NULL OR rtrim(endpoint, '/') = 'https://generativelanguage.googleapis.com'))
        """
    )
    op.add_column(
        "ai_model_profiles",
        sa.Column("reasoning_effort", sa.String(32), nullable=True),
    )
    op.add_column(
        "ai_usage",
        sa.Column(
            "reasoning_tokens",
            sa.Integer,
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("ai_usage", "reasoning_tokens")
    op.drop_column("ai_model_profiles", "reasoning_effort")
    op.drop_column("ai_provider_connections", "catalog_provider_id")
    op.drop_table("ai_model_catalog")
