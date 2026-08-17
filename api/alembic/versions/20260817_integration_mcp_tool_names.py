"""rename persisted Integration MCP tool identifiers

Revision ID: 20260817_integration_mcp_names
Revises: 20260817_organization_mcp_names

Integration system-tool identifiers stored in Agent ``system_tools`` move
atomically with the canonical FastMCP registrations.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260817_integration_mcp_names"
down_revision: str = "20260817_organization_mcp_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(old: str, new: str) -> None:
    op.execute(
        "UPDATE agents "
        f"SET system_tools = array_replace(system_tools, '{old}', '{new}') "
        f"WHERE '{old}' = ANY(system_tools)"
    )


def upgrade() -> None:
    _replace("list_integrations", "bifrost_list_integrations")
    _replace("get_integration", "bifrost_get_integration")
    _replace("create_integration", "bifrost_create_integration")
    _replace("update_integration", "bifrost_update_integration")
    _replace("add_integration_mapping", "bifrost_create_integration_mapping")
    _replace("update_integration_mapping", "bifrost_update_integration_mapping")


def downgrade() -> None:
    _replace("bifrost_list_integrations", "list_integrations")
    _replace("bifrost_get_integration", "get_integration")
    _replace("bifrost_create_integration", "create_integration")
    _replace("bifrost_update_integration", "update_integration")
    _replace("bifrost_create_integration_mapping", "add_integration_mapping")
    _replace("bifrost_update_integration_mapping", "update_integration_mapping")
