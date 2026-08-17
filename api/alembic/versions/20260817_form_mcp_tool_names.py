"""rename persisted Form MCP tool identifiers

Revision ID: 20260817_form_mcp_names
Revises: 20260817_agent_mcp_names

Form CRUD identifiers stored in Agent ``system_tools`` move to the canonical
``bifrost_<verb>_<noun>`` namespace with their public MCP registrations.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260817_form_mcp_names"
down_revision: str = "20260817_agent_mcp_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(old: str, new: str) -> None:
    op.execute(
        "UPDATE agents "
        f"SET system_tools = array_replace(system_tools, '{old}', '{new}') "
        f"WHERE '{old}' = ANY(system_tools)"
    )


def upgrade() -> None:
    _replace("list_forms", "bifrost_list_forms")
    _replace("get_form", "bifrost_get_form")
    _replace("create_form", "bifrost_create_form")
    _replace("update_form", "bifrost_update_form")


def downgrade() -> None:
    _replace("bifrost_list_forms", "list_forms")
    _replace("bifrost_get_form", "get_form")
    _replace("bifrost_create_form", "create_form")
    _replace("bifrost_update_form", "update_form")
