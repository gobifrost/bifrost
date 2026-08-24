"""Add server-authoritative application runtime isolation mode.

Revision ID: 20260807_app_runtime_mode
Revises: 20260807_builder_collaboration
Create Date: 2026-08-07

Existing applications remain trusted. Builder-owned applications are stamped
isolated by deploy and may be explicitly trusted only during admin promotion.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260807_app_runtime_mode"
down_revision: str = "20260807_builder_collaboration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column(
            "runtime_mode",
            sa.String(length=16),
            nullable=False,
            server_default="trusted",
        ),
    )
    op.create_check_constraint(
        "ck_applications_runtime_mode",
        "applications",
        "runtime_mode IN ('trusted', 'isolated')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_applications_runtime_mode",
        "applications",
        type_="check",
    )
    op.drop_column("applications", "runtime_mode")
