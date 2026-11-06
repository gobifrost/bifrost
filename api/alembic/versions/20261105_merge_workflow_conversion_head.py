"""Merge the workflow-conversion branch with the current platform head.

Revision ID: 20261105_wf_conversion_merge
Revises: 20261104_oauth_clients, 20261018_workflow_conversion
"""

from __future__ import annotations

from collections.abc import Sequence


revision: str = "20261105_wf_conversion_merge"
down_revision: tuple[str, str] = (
    "20261104_oauth_clients",
    "20261018_workflow_conversion",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
