"""Rename the documentation tool in stored agent assignments and MCP filters.

Revision ID: 20261114_docs_mcp_name
Revises: 20261114_agent_wf_authority
"""

import sqlalchemy as sa
from alembic import op

revision = "20261114_docs_mcp_name"
down_revision = "20261114_agent_wf_authority"
branch_labels = None
depends_on = None


def _rename(old: str, new: str) -> None:
    op.execute(sa.text(
        "UPDATE agents "
        "SET system_tools = array_replace(system_tools, :old, :new) "
        "WHERE :old = ANY(system_tools)"
    ).bindparams(old=old, new=new))
    for list_key in ("allowed_tool_ids", "blocked_tool_ids"):
        # Match the existing tool-rename migrations: preserve null/missing
        # lists, unrelated settings, and the configured allow/block policy.
        op.execute(sa.text(
            "UPDATE system_configs "
            "SET value_json = (jsonb_set("
            "  value_json::jsonb, ARRAY[:list_key], "
            "  to_jsonb(array_replace("
            "    ARRAY(SELECT jsonb_array_elements_text(value_json::jsonb -> :list_key)), "
            "    :old, :new"
            "  )), true"
            "))::json "
            "WHERE category = 'mcp' AND key = 'server_config' "
            "AND organization_id IS NULL "
            "AND jsonb_typeof(value_json::jsonb -> :list_key) = 'array' "
            "AND value_json::jsonb -> :list_key ? :old"
        ).bindparams(list_key=list_key, old=old, new=new))


def upgrade() -> None:
    _rename("get_docs", "bifrost_get_docs")


def downgrade() -> None:
    _rename("bifrost_get_docs", "get_docs")
