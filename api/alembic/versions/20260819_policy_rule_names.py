"""rename persisted Policy Rule MCP tool identifiers

Revision ID: 20260819_policy_rule_names
Revises: 20260819_config_mcp_names
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260819_policy_rule_names"
down_revision: str = "20260819_config_mcp_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Only the three tools that already existed are renamed. get / update /
# usages are new in this slice, so no persisted reference to them can exist.
RENAMES: tuple[tuple[str, str], ...] = (
    ("list_policy_rules", "bifrost_list_policy_rules"),
    ("create_policy_rule", "bifrost_create_policy_rule"),
    ("delete_policy_rule", "bifrost_delete_policy_rule"),
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
