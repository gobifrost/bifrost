"""rename persisted Application MCP tool identifiers

Revision ID: 20260817_app_mcp_names
Revises: 20260817_table_mcp_names

Application identifiers stored in Agent ``system_tools`` move to the canonical
``bifrost_<verb>_<noun>`` namespace with their public MCP registrations.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260817_app_mcp_names"
down_revision: str = "20260817_table_mcp_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(old: str, new: str) -> None:
    op.execute(
        "UPDATE agents "
        f"SET system_tools = array_replace(system_tools, '{old}', '{new}') "
        f"WHERE '{old}' = ANY(system_tools)"
    )


def upgrade() -> None:
    _replace("list_apps", "bifrost_list_apps")
    _replace("get_app", "bifrost_get_app")
    _replace("create_app", "bifrost_create_app")
    _replace("update_app", "bifrost_update_app")
    _replace("publish_app", "bifrost_publish_app")
    _replace("get_app_publish_status", "bifrost_get_app_publish_status")
    _replace("replace_app", "bifrost_replace_app")
    _replace("validate_app", "bifrost_validate_app")
    _replace("get_app_dependencies", "bifrost_get_app_dependencies")
    _replace("update_app_dependencies", "bifrost_update_app_dependencies")


def downgrade() -> None:
    _replace("bifrost_list_apps", "list_apps")
    _replace("bifrost_get_app", "get_app")
    _replace("bifrost_create_app", "create_app")
    _replace("bifrost_update_app", "update_app")
    _replace("bifrost_publish_app", "publish_app")
    _replace("bifrost_get_app_publish_status", "get_app_publish_status")
    _replace("bifrost_replace_app", "replace_app")
    _replace("bifrost_validate_app", "validate_app")
    _replace("bifrost_get_app_dependencies", "get_app_dependencies")
    _replace("bifrost_update_app_dependencies", "update_app_dependencies")
