"""Join workflow conversion and execution provenance migration histories.

Revision ID: 20261105_wf_provenance_merge
Revises: 20261105_wf_conversion_merge, 20261020_execution_provenance
"""

from __future__ import annotations

from collections.abc import Sequence


revision: str = "20261105_wf_provenance_merge"
down_revision: tuple[str, str] = (
    "20261105_wf_conversion_merge",
    "20261020_execution_provenance",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
