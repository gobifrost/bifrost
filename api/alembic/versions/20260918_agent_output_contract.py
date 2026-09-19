"""Invocation-owned output contract outcome columns.

Revision ID: 20260918_agent_output_contract
Revises: 20260918_durable_agent_runtime

Forward-only additive migration. Stores the engine-side validation verdict
for invocations that carried a caller-supplied output_schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260918_agent_output_contract"
down_revision: str | None = "20260918_durable_agent_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("contract_valid", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("contract_errors", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "contract_errors")
    op.drop_column("agent_runs", "contract_valid")
