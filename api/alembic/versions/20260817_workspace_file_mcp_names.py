"""rename persisted Workspace File MCP tool identifiers

Revision ID: 20260817_workspace_file_names
Revises: 20260817_integration_mcp_names

The legacy code-editor tools and Application batch push collapse into one
canonical Workspace Files surface.  Reads and writes that previously had two
tool IDs are deduplicated after the rename.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "20260817_workspace_file_names"
down_revision: str = "20260817_integration_mcp_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(old: str, new: str) -> None:
    op.execute(
        "UPDATE agents "
        f"SET system_tools = array_replace(system_tools, '{old}', '{new}') "
        f"WHERE '{old}' = ANY(system_tools)"
    )


def _deduplicate_changed_arrays() -> None:
    op.execute(
        """
        UPDATE agents AS agent
        SET system_tools = (
            SELECT array_agg(item.tool ORDER BY item.first_position) AS tools
            FROM (
                SELECT expanded.tool, min(expanded.position) AS first_position
                FROM unnest(agent.system_tools) WITH ORDINALITY
                    AS expanded(tool, position)
                GROUP BY expanded.tool
            ) AS item
        )
        WHERE agent.system_tools && ARRAY[
            'bifrost_read_file',
            'bifrost_write_file'
        ]::varchar[]
        """
    )


def _remove(tool_id: str) -> None:
    op.execute(
        "UPDATE agents "
        f"SET system_tools = array_remove(system_tools, '{tool_id}') "
        f"WHERE '{tool_id}' = ANY(system_tools)"
    )


def upgrade() -> None:
    _replace("list_content", "bifrost_list_files")
    _replace("search_content", "bifrost_search_files")
    _replace("read_content_lines", "bifrost_read_file")
    _replace("get_content", "bifrost_read_file")
    _replace("patch_content", "bifrost_patch_file")
    _replace("replace_content", "bifrost_write_file")
    _replace("push_files", "bifrost_write_file")
    _replace("delete_content", "bifrost_delete_file")
    _deduplicate_changed_arrays()


def downgrade() -> None:
    _replace("bifrost_list_files", "list_content")
    _replace("bifrost_search_files", "search_content")
    _replace("bifrost_read_file", "get_content")
    _replace("bifrost_patch_file", "patch_content")
    _replace("bifrost_write_file", "replace_content")
    _replace("bifrost_delete_file", "delete_content")
    _remove("bifrost_stat_file")
    _remove("bifrost_exists_file")
