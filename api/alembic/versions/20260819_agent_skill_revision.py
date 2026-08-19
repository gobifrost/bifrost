"""add a durable Agent Skill revision digest

Revision ID: 20260819_agent_skill_rev
Revises: 20260819_policy_rule_names
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260819_agent_skill_rev"
down_revision: str = "20260819_policy_rule_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add ``agents.skill_revision``.

    Deliberately NOT backfilled. The digest is derived from Skill content held
    in object storage (S3/Solution/repo tiers), which a migration cannot read.
    NULL means "not yet computed"; read paths compute and persist it lazily,
    and every write that can change Skill content recomputes it.
    """
    op.add_column(
        "agents",
        sa.Column("skill_revision", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agents", "skill_revision")
