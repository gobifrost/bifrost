"""remove persisted per-Solution Builder Agents

Revision ID: 20260820_builder_agentless
Revises: 20260819_builder_authz

Builder now uses one maintained, transient execution profile. Existing
conversation history stays intact while obsolete deterministic Agent rows are
removed.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid5

import sqlalchemy as sa
from alembic import op


revision: str = "20260820_builder_agentless"
down_revision: str = "20260819_builder_authz"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_BUILDER_AGENT_ID_KEY = "bifrost-private-solution-builder-agent"


def upgrade() -> None:
    connection = op.get_bind()
    solution_ids = connection.execute(
        sa.text("SELECT solution_id FROM solution_builder_projects")
    ).scalars()
    agent_ids = [
        uuid5(UUID(str(solution_id)), _LEGACY_BUILDER_AGENT_ID_KEY)
        for solution_id in solution_ids
    ]
    if not agent_ids:
        return
    connection.execute(
        sa.text(
            "UPDATE conversations SET agent_id = NULL "
            "WHERE agent_id IN :agent_ids"
        ).bindparams(sa.bindparam("agent_ids", expanding=True)),
        {"agent_ids": agent_ids},
    )
    connection.execute(
        sa.text("DELETE FROM agents WHERE id IN :agent_ids").bindparams(
            sa.bindparam("agent_ids", expanding=True)
        ),
        {"agent_ids": agent_ids},
    )


def downgrade() -> None:
    # Persisted Builder Agents are intentionally not recreated.
    pass
