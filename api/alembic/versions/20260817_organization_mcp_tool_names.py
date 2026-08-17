"""rename persisted Organization MCP tool identifiers

Revision ID: 20260817_organization_mcp_names
Revises: 20260817_workflow_mcp_names

Organization system-tool identifiers stored in Agent ``system_tools`` move
atomically with the canonical FastMCP registrations.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260817_organization_mcp_names"
down_revision: str = "20260817_workflow_mcp_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(old: str, new: str) -> None:
    op.execute(
        "UPDATE agents "
        f"SET system_tools = array_replace(system_tools, '{old}', '{new}') "
        f"WHERE '{old}' = ANY(system_tools)"
    )


def upgrade() -> None:
    _replace("list_organizations", "bifrost_list_organizations")
    _replace("get_organization", "bifrost_get_organization")
    _replace("create_organization", "bifrost_create_organization")
    _replace("update_organization", "bifrost_update_organization")
    _replace("delete_organization", "bifrost_delete_organization")


def downgrade() -> None:
    _replace("bifrost_list_organizations", "list_organizations")
    _replace("bifrost_get_organization", "get_organization")
    _replace("bifrost_create_organization", "create_organization")
    _replace("bifrost_update_organization", "update_organization")
    _replace("bifrost_delete_organization", "delete_organization")
