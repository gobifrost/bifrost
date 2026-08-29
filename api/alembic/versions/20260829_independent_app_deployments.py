"""Add atomic deployment pointer for independent apps.

Revision ID: 20260829_app_deployments
Revises: 20260827_history_timeline
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_app_deployments"
down_revision: str | None = "20260827_history_timeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("applications", "repo_path", existing_type=sa.String(500), nullable=True)
    op.add_column("applications", sa.Column("active_deployment_id", sa.Uuid(), nullable=True))
    op.add_column(
        "applications",
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("applications", "deployed_at")
    op.drop_column("applications", "active_deployment_id")
    op.alter_column("applications", "repo_path", existing_type=sa.String(500), nullable=False)
