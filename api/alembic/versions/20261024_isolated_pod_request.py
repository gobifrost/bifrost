"""Record an isolated pod's memory request at claim time

Revision ID: 20261024_isolated_pod_request
Revises: 20261024_k8s_detection_notice
Create Date: 2026-10-24

The claim decides whether an isolated pod is standard (the deployment's
worker memory request) or sized (its whole limit). The decision is recorded
with the limit, so the pod is created as claimed even when Worker memory
changes before its Job exists.

Attempts already placed in Kubernetes record their limit as their request.
Only one whose Job does not exist yet is rendered from it, and such a pod
is then scheduled only where its whole limit exists.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261024_isolated_pod_request"
down_revision: Union[str, None] = "20261024_k8s_detection_notice"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "platform_jobs", sa.Column("memory_request_bytes", sa.BigInteger(), nullable=True)
    )
    op.execute(
        "UPDATE platform_jobs SET memory_request_bytes = memory_limit_bytes "
        "WHERE kubernetes_job_name IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_column("platform_jobs", "memory_request_bytes")
