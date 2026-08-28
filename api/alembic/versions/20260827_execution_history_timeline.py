"""index execution history by its effective timeline timestamp

Revision ID: 20260827_history_timeline
Revises: 20260823_job_memory_profiles
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260827_history_timeline"
down_revision: str | Sequence[str] = "20260823_job_memory_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY ix_executions_history_timeline "
            "ON executions ("
            "COALESCE(started_at, scheduled_at, completed_at, created_at) DESC, "
            "id DESC"
            ")"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_executions_history_timeline"
        )
