"""rename persisted Role MCP tool identifiers

Revision ID: 20260817_role_mcp_names
Revises: 20260817_knowledge_mcp_name
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260817_role_mcp_names"
down_revision: str = "20260817_knowledge_mcp_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RENAMES: tuple[tuple[str, str], ...] = (
    ("list_roles", "bifrost_list_roles"),
    ("get_role", "bifrost_get_role"),
    ("create_role", "bifrost_create_role"),
    ("update_role", "bifrost_update_role"),
    ("delete_role", "bifrost_delete_role"),
)


def _replace_agent_tool(old: str, new: str) -> None:
    op.execute(
        "UPDATE agents "
        f"SET system_tools = array_replace(system_tools, '{old}', '{new}') "
        f"WHERE '{old}' = ANY(system_tools)"
    )


def _replace_mcp_config_tool(old: str, new: str) -> None:
    op.execute(
        "UPDATE system_configs "
        "SET value_json = CAST(replace(CAST(value_json AS TEXT), "
        f"'\"{old}\"', '\"{new}\"') AS JSON) "
        "WHERE category = 'mcp' AND key = 'server_config' "
        "AND value_json IS NOT NULL "
        f"AND CAST(value_json AS TEXT) LIKE '%\"{old}\"%'"
    )


def upgrade() -> None:
    for old, new in RENAMES:
        _replace_agent_tool(old, new)
        _replace_mcp_config_tool(old, new)


def downgrade() -> None:
    for old, new in reversed(RENAMES):
        _replace_agent_tool(new, old)
        _replace_mcp_config_tool(new, old)
