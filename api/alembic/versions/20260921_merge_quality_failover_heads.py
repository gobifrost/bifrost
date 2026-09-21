"""Merge agent quality and model failover migration heads.

Revision ID: 20260921_merge_quality_failover
Revises: 20260921_test_logical_identity, 20260918_profile_failover
"""

from collections.abc import Sequence


revision: str = "20260921_merge_quality_failover"
down_revision: str | Sequence[str] | None = (
    "20260921_test_logical_identity",
    "20260918_profile_failover",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
