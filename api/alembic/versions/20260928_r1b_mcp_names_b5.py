"""rename persisted batch-5 MCP tool identifiers to the noun-first convention

Revision ID: 20260928_r1b_mcp_names_b5
Revises: 20260927_r1b_mcp_names_b4

RBAC R1b batch 5 renames the registered workflows/apps MCP tools to the
catalog's ``bifrost_<noun>_<verb>`` names (see ``operation_catalog.py``).
Rewrites two persisted locations atomically with the registration change,
following the exact pattern #820/#830/#833/#834 established:

a. ``agents.system_tools`` (text[]) — existing Agent tool assignments.
   ``execute_workflow`` is the most commonly attached agent system tool, so
   this migration matters most for it.
b. ``system_configs`` row ``category='mcp' AND key='server_config'``, JSON
   ``value_json`` keys ``allowed_tool_ids`` / ``blocked_tool_ids``. Renaming
   a registered MCP tool without also rewriting the platform-wide allow/block
   lists silently drops allow-listed tools and un-blocks previously blocked
   ones the next time the config is read.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# The two tool-id lists on the platform ``mcp.server_config`` row.
_TOOL_LIST_KEYS = ("allowed_tool_ids", "blocked_tool_ids")

revision: str = "20260928_r1b_mcp_names_b5"
down_revision: str | None = "20260927_r1b_mcp_names_b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Batch-5 renames: workflows, apps (this migration's own domain).
RENAMES: list[tuple[str, str]] = [
    ("execute_workflow", "bifrost_workflow_execute"),
    ("list_workflows", "bifrost_workflow_list"),
    ("validate_workflow", "bifrost_workflow_validate"),
    ("get_workflow", "bifrost_workflow_get"),
    ("register_workflow", "bifrost_workflow_register"),
    ("update_workflow", "bifrost_workflow_update"),
    ("delete_workflow", "bifrost_workflow_delete"),
    ("grant_workflow_role", "bifrost_workflow_role_grant"),
    ("revoke_workflow_role", "bifrost_workflow_role_revoke"),
    ("list_apps", "bifrost_app_list"),
    ("create_app", "bifrost_app_create"),
    ("get_app", "bifrost_app_get"),
    ("update_app", "bifrost_app_update"),
    ("publish_app", "bifrost_app_publish"),
    ("replace_app", "bifrost_app_replace"),
    ("validate_app", "bifrost_app_validate"),
    ("get_app_dependencies", "bifrost_app_dependencies_get"),
    ("update_app_dependencies", "bifrost_app_dependencies_update"),
]

# New tools with no old name: ``bifrost_app_delete`` is a net-new
# registration (no prior tool existed for apps.delete), so it needs no
# rename entry — a fresh install already registers it under its final name.
# ``push_files`` keeps its existing name (no catalog operation) and is
# untouched by this migration.


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


def _replace_in_tool_list(list_key: str, old: str, new: str) -> None:
    """Rename one tool id inside one list on the platform ``mcp.server_config`` row."""
    # ``system_configs.value_json`` is column type ``json`` (not ``jsonb``);
    # every jsonb-only function needs an explicit ``::jsonb`` cast, and the
    # result is cast back to ``::json`` for the assignment.
    op.execute(
        sa.text(
            "UPDATE system_configs "
            "SET value_json = (jsonb_set("
            "  value_json::jsonb, ARRAY[:list_key], "
            "  to_jsonb(array_replace("
            "    ARRAY(SELECT jsonb_array_elements_text(value_json::jsonb -> :list_key)), "
            "    :old, :new"
            "  )), "
            "  true"
            "))::json "
            "WHERE category = 'mcp' AND key = 'server_config' "
            "AND organization_id IS NULL "
            "AND jsonb_typeof(value_json::jsonb -> :list_key) = 'array' "
            "AND value_json::jsonb -> :list_key ? :old"
        ).bindparams(list_key=list_key, old=old, new=new)
    )


def _dedupe_tool_list(list_key: str) -> None:
    """Collapse duplicate entries a rename can produce when both the old and
    new spelling were already present (e.g. a prior partial fix). Order
    isn't semantically meaningful for an allow/block set.
    """
    op.execute(
        sa.text(
            "UPDATE system_configs "
            "SET value_json = (jsonb_set("
            "  value_json::jsonb, ARRAY[:list_key], "
            "  to_jsonb(ARRAY(SELECT DISTINCT x FROM unnest("
            "    ARRAY(SELECT jsonb_array_elements_text(value_json::jsonb -> :list_key))"
            "  ) AS t(x))), "
            "  true"
            "))::json "
            "WHERE category = 'mcp' AND key = 'server_config' "
            "AND organization_id IS NULL "
            "AND jsonb_typeof(value_json::jsonb -> :list_key) = 'array'"
        ).bindparams(list_key=list_key)
    )


def _rewrite_server_config(renames: list[tuple[str, str]]) -> None:
    """Rewrite tool ids in the platform ``mcp.server_config`` allow/block lists."""
    for list_key in _TOOL_LIST_KEYS:
        for old, new in renames:
            _replace_in_tool_list(list_key, old, new)
        _dedupe_tool_list(list_key)


def upgrade() -> None:
    for old, new in RENAMES:
        _replace(old, new)
    _rewrite_server_config(RENAMES)


def downgrade() -> None:
    for old, new in reversed(RENAMES):
        _replace(new, old)
    _rewrite_server_config([(new, old) for old, new in RENAMES])
