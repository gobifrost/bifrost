"""add spam protection setting to public form publications

Revision ID: 20260805_form_captcha
Revises: 20260804_form_publication
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_form_captcha"
down_revision: str = "20260804_form_publication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "form_publications",
        sa.Column(
            "spam_protection_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("form_publications", "spam_protection_enabled")
