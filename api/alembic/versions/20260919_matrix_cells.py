"""Durable matrix cell membership.

Revision ID: 20260919_matrix_cells
Revises: 20260919_eval_matrix
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260919_matrix_cells"
down_revision: str | None = "20260919_eval_matrix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Backfill pre-migration rows: membership is exactly the executions that
# already point at the matrix. Matrices without executions keep the
# column default. ``jsonb_agg`` renders UUIDs as JSON strings, matching
# the application write path (``str(execution.id)``).
BACKFILL_MEMBERSHIP_SQL = """
UPDATE agent_evaluation_matrixes AS m
SET cell_execution_ids = sub.ids
FROM (
    SELECT e.matrix_id AS id,
        coalesce(jsonb_agg(e.id), '[]'::jsonb) AS ids
    FROM agent_evaluation_executions AS e
    WHERE e.matrix_id IS NOT NULL
    GROUP BY e.matrix_id
) AS sub
WHERE m.id = sub.id
"""


def upgrade() -> None:
    op.add_column(
        "agent_evaluation_matrixes",
        sa.Column(
            "cell_execution_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(sa.text(BACKFILL_MEMBERSHIP_SQL))


def downgrade() -> None:
    op.drop_column("agent_evaluation_matrixes", "cell_execution_ids")
