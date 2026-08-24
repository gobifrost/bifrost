"""rename the persisted Agent Skill file-reader tool identifier

Revision ID: 20260819_skill_file_tool
Revises: 20260819_agent_skill_rev
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260819_skill_file_tool"
down_revision: str = "20260819_agent_skill_rev"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# read_skill_asset was a private dialect injected by the execution planner, not
# selected from Agent.system_tools. It is renamed to the canonical binding.
# Persisted references are still swept: an operator may have added the old id to
# an Agent grant or the MCP server allow-list by hand.
RENAMES: tuple[tuple[str, str], ...] = (
    ("read_skill_asset", "bifrost_read_agent_skill_file"),
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
