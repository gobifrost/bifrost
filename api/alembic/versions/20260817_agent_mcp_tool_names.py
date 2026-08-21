"""rename persisted Agent MCP tool identifiers

Revision ID: 20260817_agent_mcp_names
Revises: 20260810_builder_global

Agent ``system_tools`` stores public Bifrost tool identifiers. Move the Agent
CRUD tools to the canonical ``bifrost_<verb>_<noun>`` namespace atomically with
their registrations so existing Agent assignments keep working after upgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260817_agent_mcp_names"
down_revision: str = "20260810_builder_global"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(old: str, new: str) -> None:
    op.execute(
        "UPDATE agents "
        f"SET system_tools = array_replace(system_tools, '{old}', '{new}') "
        f"WHERE '{old}' = ANY(system_tools)"
    )


def upgrade() -> None:
    _replace("list_agents", "bifrost_list_agents")
    _replace("get_agent", "bifrost_get_agent")
    _replace("create_agent", "bifrost_create_agent")
    _replace("update_agent", "bifrost_update_agent")
    _replace("delete_agent", "bifrost_delete_agent")


def downgrade() -> None:
    _replace("bifrost_list_agents", "list_agents")
    _replace("bifrost_get_agent", "get_agent")
    _replace("bifrost_create_agent", "create_agent")
    _replace("bifrost_update_agent", "update_agent")
    _replace("bifrost_delete_agent", "delete_agent")
