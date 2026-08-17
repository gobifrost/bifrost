"""rename persisted Knowledge MCP tool identifier

Revision ID: 20260817_knowledge_mcp_name
Revises: 20260817_execution_mcp_names
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260817_knowledge_mcp_name"
down_revision: str = "20260817_execution_mcp_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_agent_tool(old: str, new: str) -> None:
    op.execute(
        "UPDATE agents "
        f"SET system_tools = array_replace(system_tools, '{old}', '{new}') "
        f"WHERE '{old}' = ANY(system_tools)"
    )


def _replace_mcp_config_tool(old: str, new: str) -> None:
    """Preserve platform allow/block decisions stored in MCP configuration."""
    op.execute(
        "UPDATE system_configs "
        "SET value_json = CAST(replace(CAST(value_json AS TEXT), "
        f"'\"{old}\"', '\"{new}\"') AS JSON) "
        "WHERE category = 'mcp' AND key = 'server_config' "
        "AND value_json IS NOT NULL "
        f"AND CAST(value_json AS TEXT) LIKE '%\"{old}\"%'"
    )


def upgrade() -> None:
    _replace_agent_tool("search_knowledge", "bifrost_search_knowledge")
    _replace_mcp_config_tool("search_knowledge", "bifrost_search_knowledge")


def downgrade() -> None:
    _replace_agent_tool("bifrost_search_knowledge", "search_knowledge")
    _replace_mcp_config_tool("bifrost_search_knowledge", "search_knowledge")
