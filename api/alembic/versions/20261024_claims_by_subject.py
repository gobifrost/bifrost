"""Look up an executor's claim on one run directly (#769 stage 6)

Revision ID: 20261024_claims_by_subject
Revises: 20261023_mcp_tool_descriptors
Create Date: 2026-10-24

Every result an executor writes checks that it still holds the run's claim.
The claim index covered only the executor, so that check read all of the
executor's claims; it now covers the executor and the run.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261024_claims_by_subject"
down_revision: Union[str, None] = "20261023_mcp_tool_descriptors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_execution_worker_claims_worker_subject",
        "execution_worker_claims",
        ["worker_id", "subject_id"],
    )
    op.drop_index("ix_execution_worker_claims_worker", table_name="execution_worker_claims")


def downgrade() -> None:
    op.create_index(
        "ix_execution_worker_claims_worker", "execution_worker_claims", ["worker_id"]
    )
    op.drop_index(
        "ix_execution_worker_claims_worker_subject", table_name="execution_worker_claims"
    )
