"""Allow the OpenCode Go provider on AI provider connections.

Revision ID: 20260924_opencode_go_provider
Revises: 20260924_exec_resource
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260924_opencode_go_provider"
down_revision: str | None = "20260924_exec_resource"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_ai_provider_connections_provider"
WITHOUT_OPENCODE_GO = (
    "provider IN ('openai', 'anthropic', 'google', 'openrouter', 'openai_compatible')"
)
WITH_OPENCODE_GO = (
    "provider IN ('openai', 'anthropic', 'google', 'openrouter', 'openai_compatible', "
    "'opencode_go')"
)


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, "ai_provider_connections", type_="check")
    op.create_check_constraint(CONSTRAINT, "ai_provider_connections", WITH_OPENCODE_GO)


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "ai_provider_connections", type_="check")
    op.create_check_constraint(CONSTRAINT, "ai_provider_connections", WITHOUT_OPENCODE_GO)
