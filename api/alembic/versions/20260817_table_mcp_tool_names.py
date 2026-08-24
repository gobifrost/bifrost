"""rename persisted Table MCP tool identifiers

Revision ID: 20260817_table_mcp_names
Revises: 20260817_form_mcp_names

Table metadata CRUD identifiers stored in Agent ``system_tools`` move to the
canonical ``bifrost_<verb>_<noun>`` namespace with their public MCP
registrations. The migration also restores the live ``_repo`` Table name
indexes lost when ``20260624_sol_status`` dropped ``orphaned_at`` and its
dependent partial indexes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_table_mcp_names"
down_revision: str = "20260817_form_mcp_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(old: str, new: str) -> None:
    op.execute(
        "UPDATE agents "
        f"SET system_tools = array_replace(system_tools, '{old}', '{new}') "
        f"WHERE '{old}' = ANY(system_tools)"
    )


def upgrade() -> None:
    _replace("list_tables", "bifrost_list_tables")
    _replace("get_table", "bifrost_get_table")
    _replace("create_table", "bifrost_create_table")
    _replace("update_table", "bifrost_update_table")
    _replace("delete_table", "bifrost_delete_table")
    op.create_index(
        "ix_tables_org_name_unique",
        "tables",
        ["organization_id", "name"],
        unique=True,
        postgresql_where=sa.text(
            "organization_id IS NOT NULL AND solution_id IS NULL"
        ),
    )
    op.create_index(
        "ix_tables_global_name_unique",
        "tables",
        ["name"],
        unique=True,
        postgresql_where=sa.text(
            "organization_id IS NULL AND solution_id IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_tables_global_name_unique", table_name="tables")
    op.drop_index("ix_tables_org_name_unique", table_name="tables")
    _replace("bifrost_list_tables", "list_tables")
    _replace("bifrost_get_table", "get_table")
    _replace("bifrost_create_table", "create_table")
    _replace("bifrost_update_table", "update_table")
    _replace("bifrost_delete_table", "delete_table")
