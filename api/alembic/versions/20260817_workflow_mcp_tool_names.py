"""rename persisted Workflow MCP tool identifiers

Revision ID: 20260817_workflow_mcp_names
Revises: 20260817_event_mcp_names

Workflow system-tool identifiers stored in Agent ``system_tools`` move
atomically with the canonical FastMCP registrations.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260817_workflow_mcp_names"
down_revision: str = "20260817_event_mcp_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(old: str, new: str) -> None:
    op.execute(
        "UPDATE agents "
        f"SET system_tools = array_replace(system_tools, '{old}', '{new}') "
        f"WHERE '{old}' = ANY(system_tools)"
    )


def upgrade() -> None:
    _replace("list_workflows", "bifrost_list_workflows")
    _replace("get_workflow", "bifrost_get_workflow")
    _replace("validate_workflow", "bifrost_validate_workflow")
    _replace("register_workflow", "bifrost_register_workflow")
    _replace("execute_workflow", "bifrost_execute_workflow")
    _replace("update_workflow", "bifrost_update_workflow")
    _replace("delete_workflow", "bifrost_delete_workflow")
    _replace("grant_workflow_role", "bifrost_grant_workflow_role")
    _replace("revoke_workflow_role", "bifrost_revoke_workflow_role")


def downgrade() -> None:
    _replace("bifrost_list_workflows", "list_workflows")
    _replace("bifrost_get_workflow", "get_workflow")
    _replace("bifrost_validate_workflow", "validate_workflow")
    _replace("bifrost_register_workflow", "register_workflow")
    _replace("bifrost_execute_workflow", "execute_workflow")
    _replace("bifrost_update_workflow", "update_workflow")
    _replace("bifrost_delete_workflow", "delete_workflow")
    _replace("bifrost_grant_workflow_role", "grant_workflow_role")
    _replace("bifrost_revoke_workflow_role", "revoke_workflow_role")
