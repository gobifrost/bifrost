"""record scheduler job slots

Revision ID: 20260806_scheduler_slots
Revises: 20260805_scheduler_diagnostics, 20260730_role_auth_scopes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_scheduler_slots"
down_revision: str | Sequence[str] = (
    "20260805_scheduler_diagnostics",
    "20260730_role_auth_scopes",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "scheduler_replicas",
        sa.Column("job_slots", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("scheduler_replicas", "job_slots")
