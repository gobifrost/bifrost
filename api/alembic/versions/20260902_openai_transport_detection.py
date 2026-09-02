"""Store the detected OpenAI-compatible transport per model profile.

Revision ID: 20260902_openai_transport
Revises: 20260829_app_deployments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_openai_transport"
down_revision: str | None = "20260829_app_deployments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_model_profiles",
        sa.Column("openai_transport", sa.String(length=32), nullable=True),
    )
    op.create_check_constraint(
        "ck_ai_model_profiles_openai_transport",
        "ai_model_profiles",
        "openai_transport IS NULL OR openai_transport IN ('responses', 'chat_completions')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_ai_model_profiles_openai_transport",
        "ai_model_profiles",
        type_="check",
    )
    op.drop_column("ai_model_profiles", "openai_transport")
