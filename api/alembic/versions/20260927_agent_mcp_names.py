"""rename persisted Agent MCP tool identifiers to the noun-first convention

Revision ID: 20260927_agent_mcp_names
Revises: 20260926_audit_execution_id

Agent ``system_tools`` stores public Bifrost tool identifiers. RBAC R1b flips
the Agent CRUD tools' MCP names from the verb-first
``bifrost_{list,get,create,update,delete}_agent`` to the noun-first
``bifrost_agent_{list,get,create,update,delete}`` convention (see
``operation_catalog.py``). Rename existing Agent ``system_tools`` entries
atomically with the registration change so existing Agent assignments keep
working after upgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_agent_mcp_names"
down_revision: str | None = "20260926_audit_execution_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace(old: str, new: str) -> None:
    # Bound parameters only — ``old``/``new`` are call-site constants below,
    # never caller-supplied, but this keeps CodeQL's string-concat-in-SQL
    # heuristic from flagging the statement.
    op.execute(
        sa.text(
            "UPDATE agents "
            "SET system_tools = array_replace(system_tools, :old, :new) "
            "WHERE :old = ANY(system_tools)"
        ).bindparams(old=old, new=new)
    )


def upgrade() -> None:
    _replace("bifrost_list_agents", "bifrost_agent_list")
    _replace("bifrost_get_agent", "bifrost_agent_get")
    _replace("bifrost_create_agent", "bifrost_agent_create")
    _replace("bifrost_update_agent", "bifrost_agent_update")
    _replace("bifrost_delete_agent", "bifrost_agent_delete")


def downgrade() -> None:
    _replace("bifrost_agent_list", "bifrost_list_agents")
    _replace("bifrost_agent_get", "bifrost_get_agent")
    _replace("bifrost_agent_create", "bifrost_create_agent")
    _replace("bifrost_agent_update", "bifrost_update_agent")
    _replace("bifrost_agent_delete", "bifrost_delete_agent")
