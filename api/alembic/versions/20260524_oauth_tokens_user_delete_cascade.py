"""Cascade user-owned OAuth tokens on user delete.

Revision ID: 20260524_oauth_user_cascade
Revises: 20260522_codex_gateway
Create Date: 2026-05-24
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260524_oauth_user_cascade"
down_revision: str | Sequence[str] | None = "20260522_codex_gateway"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("oauth_tokens_user_id_fkey", "oauth_tokens", type_="foreignkey")
    op.create_foreign_key(
        "oauth_tokens_user_id_fkey",
        "oauth_tokens",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("oauth_tokens_user_id_fkey", "oauth_tokens", type_="foreignkey")
    op.create_foreign_key(
        "oauth_tokens_user_id_fkey",
        "oauth_tokens",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
