"""Merge integration-name reuse and custom-identity migration heads.

Revision ID: 20261009_merge_integ_identity
Revises: 20261007_custom_global_identity, 20261009_integration_active_name
Create Date: 2026-10-09
"""

from __future__ import annotations

from typing import Sequence, Union


revision: str = "20261009_merge_integ_identity"
down_revision: Union[str, Sequence[str]] = (
    "20261007_custom_global_identity",
    "20261009_integration_active_name",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
