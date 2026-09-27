"""rename persisted batch-2 MCP tool identifiers to the noun-first convention

Revision ID: 20260927_r1b_mcp_names_b2
Revises: 20260825_delivery_attempt

RBAC R1b batch 2 renames the registered executions/roles/platform-job/claims/
file-policy MCP tools to the catalog's ``bifrost_<noun>_<verb>`` names (see
``operation_catalog.py``). Rewrites two persisted locations atomically with
the registration change:

a. ``agents.system_tools`` (text[]) — existing Agent tool assignments.
b. ``system_configs`` row ``category='mcp' AND key='server_config'``, JSON
   ``value_json`` keys ``allowed_tool_ids`` / ``blocked_tool_ids``. Renaming
   a registered MCP tool without also rewriting the platform-wide allow/block
   lists silently drops allow-listed tools and un-blocks previously blocked
   ones the next time the config is read. This migration also carries the
   five Agent renames from #820 (``20260927_agent_mcp_names``), which rewrote
   ``agents.system_tools`` but missed this second location.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# The two tool-id lists on the platform ``mcp.server_config`` row.
_TOOL_LIST_KEYS = ("allowed_tool_ids", "blocked_tool_ids")

revision: str = "20260927_r1b_mcp_names_b2"
down_revision: str | None = "20260825_delivery_attempt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Batch-2 renames (this migration's own domain).
RENAMES: list[tuple[str, str]] = [
    ("list_executions", "bifrost_execution_list"),
    ("get_execution", "bifrost_execution_get"),
    ("list_roles", "bifrost_role_list"),
    ("get_role", "bifrost_role_get"),
    ("create_role", "bifrost_role_create"),
    ("update_role", "bifrost_role_update"),
    ("delete_role", "bifrost_role_delete"),
    ("get_app_publish_status", "bifrost_platform_job_get"),
    ("list_claims", "bifrost_claim_list"),
    ("get_claim", "bifrost_claim_get"),
    ("create_claim", "bifrost_claim_create"),
    ("update_claim", "bifrost_claim_update"),
    ("delete_claim", "bifrost_claim_delete"),
    ("list_file_policies", "bifrost_file_policy_list"),
    ("get_file_policy", "bifrost_file_policy_get"),
    ("set_file_policy", "bifrost_file_policy_set"),
    ("delete_file_policy", "bifrost_file_policy_delete"),
]

# #820 (20260927_agent_mcp_names) renamed these in ``agents.system_tools``
# but never rewrote the platform allow/block lists — do so here.
AGENT_RENAMES: list[tuple[str, str]] = [
    ("list_agents", "bifrost_agent_list"),
    ("get_agent", "bifrost_agent_get"),
    ("create_agent", "bifrost_agent_create"),
    ("update_agent", "bifrost_agent_update"),
    ("delete_agent", "bifrost_agent_delete"),
]


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
    """Rename one tool id inside one list on the platform ``mcp.server_config`` row.

    Pure SQL, no Python-side JSON read-modify-write round trip — matches the
    ``_replace`` idiom above (bound params, blind idempotent UPDATE). Only
    touches a row where the list is a real JSON array containing ``old``:
    ``jsonb_typeof(...) = 'array'`` skips a null list (``allowed_tool_ids:
    null`` = all tools; an absent/null ``blocked_tool_ids`` = none blocked),
    and the ``?`` containment check skips a row that doesn't have ``old``.
    """
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
    """Rewrite tool ids in the platform ``mcp.server_config`` allow/block lists.

    Renaming a registered MCP tool without also rewriting these lists
    silently drops allow-listed tools and un-blocks previously blocked ones
    the next time the config is read.
    """
    for list_key in _TOOL_LIST_KEYS:
        for old, new in renames:
            _replace_in_tool_list(list_key, old, new)
        _dedupe_tool_list(list_key)


def upgrade() -> None:
    for old, new in RENAMES:
        _replace(old, new)
    _rewrite_server_config(RENAMES + AGENT_RENAMES)


def downgrade() -> None:
    for old, new in reversed(RENAMES):
        _replace(new, old)
    # Reverse only the batch-2 names. The Agent renames stay: at this
    # revision the registered tools are still ``bifrost_agent_*`` (#820's
    # migration is a separate, earlier revision this one doesn't touch), so
    # un-renaming them in the config here would desync it from what's
    # actually registered.
    _rewrite_server_config([(new, old) for old, new in RENAMES])
