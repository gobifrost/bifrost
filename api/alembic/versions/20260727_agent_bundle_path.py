"""Add portable skill-bundle root to agents.

Revision ID: 20260727_agent_bundle_path
Revises: 20260725_build_jobs
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_agent_bundle_path"
down_revision: str | None = "20260725_build_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("bundle_path", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agents", "bundle_path")
