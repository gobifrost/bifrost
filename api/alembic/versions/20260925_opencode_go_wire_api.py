"""Persist the probed OpenCode Go wire surface per model profile.

Revision ID: 20260925_opencode_go_wire_api
Revises: 20260924_opencode_go_provider
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260925_opencode_go_wire_api"
down_revision: str | None = "20260924_opencode_go_provider"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_ai_model_profiles_wire_api"
ALLOWED = "wire_api IS NULL OR wire_api IN ('chat_completions', 'responses', 'messages')"


def upgrade() -> None:
    op.add_column(
        "ai_model_profiles",
        sa.Column("wire_api", sa.String(32), nullable=True),
    )
    op.create_check_constraint(CONSTRAINT, "ai_model_profiles", ALLOWED)


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "ai_model_profiles", type_="check")
    op.drop_column("ai_model_profiles", "wire_api")
