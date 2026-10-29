"""Desired package generation

Revision ID: 20261023_package_changes
Revises: 20261022_execution_workers
Create Date: 2026-10-23

package_changes records every workspace package change; its id is the package
generation. Executors apply changes newer than their own generation and
record it on execution_workers.package_generation.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261023_package_changes"
down_revision: Union[str, None] = "20261022_execution_workers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "package_changes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("package", sa.String(length=255), nullable=True),
        sa.Column("version", sa.String(length=100), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.add_column(
        "execution_workers",
        sa.Column("package_generation", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("execution_workers", "package_generation")
    op.drop_table("package_changes")
