"""Delete the stored Kubernetes detection-notice snapshots

Revision ID: 20261024_k8s_detection_notice
Revises: 20261024_worker_version_launcher
Create Date: 2026-10-24

The API no longer announces when a deployment configures isolated builds
(isolation is now an admin's own toggle in Settings -> Kubernetes), so the
snapshots it compared against are deleted. Downgrade restores nothing: an
earlier release recreates its snapshot at startup, which at worst repeats
one admin notice.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "20261024_k8s_detection_notice"
down_revision: Union[str, None] = "20261024_worker_version_launcher"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM system_configs "
        "WHERE category = 'kubernetes' AND key = 'detection_notice'"
    )


def downgrade() -> None:
    pass
