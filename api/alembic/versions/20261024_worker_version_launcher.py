"""Record each executor's build and the controller that launched it (#1052)

Revision ID: 20261024_worker_version_launcher
Revises: 20261024_claims_by_subject
Create Date: 2026-10-24

The worker controller replaces Jobs whose build no live controller runs and
whose launching controller is gone, so an upgrade never leaves an old
executor running. A worker reports its build when it is admitted; the
controller that created its Job is recorded with the Job's UID.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261024_worker_version_launcher"
down_revision: Union[str, None] = "20261024_claims_by_subject"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("execution_workers", sa.Column("version", sa.String(length=100), nullable=True))
    op.add_column("execution_workers", sa.Column("launched_by", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("execution_workers", "launched_by")
    op.drop_column("execution_workers", "version")
