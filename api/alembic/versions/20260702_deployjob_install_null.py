"""make solution_deploy_jobs.install_id nullable

A zip install now runs as an async SolutionDeployJob (Task H1). The install
resolves-or-creates its target install INSIDE the job, so the install_id is not
known when the job row is enqueued — the succeeded ``result`` carries the
solution_id instead. Deploy and install-from-repo jobs still populate install_id
(their install exists before enqueue).

Revision ID: 20260702_deployjob_install_null
Revises: 20260625_solution_export_jobs
Create Date: 2026-07-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260702_deployjob_install_null"
down_revision: str = "20260625_solution_export_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "solution_deploy_jobs",
        "install_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "solution_deploy_jobs",
        "install_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
