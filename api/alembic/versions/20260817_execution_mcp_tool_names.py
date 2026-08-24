"""rename persisted workflow execution-history MCP tool identifiers

Revision ID: 20260817_execution_mcp_names
Revises: 20260817_workspace_file_names
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "20260817_execution_mcp_names"
down_revision: str = "20260817_workspace_file_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(old: str, new: str) -> None:
    op.execute(
        "UPDATE agents "
        f"SET system_tools = array_replace(system_tools, '{old}', '{new}') "
        f"WHERE '{old}' = ANY(system_tools)"
    )


def upgrade() -> None:
    _replace("list_executions", "bifrost_list_workflow_executions")
    _replace("get_execution", "bifrost_get_workflow_execution")


def downgrade() -> None:
    _replace("bifrost_list_workflow_executions", "list_executions")
    _replace("bifrost_get_workflow_execution", "get_execution")
