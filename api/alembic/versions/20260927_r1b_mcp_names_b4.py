"""rename persisted batch-4 MCP tool identifiers to the noun-first convention

Revision ID: 20260927_r1b_mcp_names_b4
Revises: 20260927_r1b_mcp_names_b3

RBAC R1b batch 4 renames the registered forms/tables/events MCP tools to the
catalog's ``bifrost_<noun>_<verb>`` names (see ``operation_catalog.py``).
Rewrites two persisted locations atomically with the registration change,
following the exact pattern #820 and #830 (``20260927_r1b_mcp_names_b2``)
established:

a. ``agents.system_tools`` (text[]) — existing Agent tool assignments.
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

revision: str = "20260927_r1b_mcp_names_b4"
down_revision: str | None = "20260927_r1b_mcp_names_b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Batch-4 renames: forms, tables, events (this migration's own domain).
RENAMES: list[tuple[str, str]] = [
    ("list_forms", "bifrost_form_list"),
    ("get_form", "bifrost_form_get"),
    ("create_form", "bifrost_form_create"),
    ("update_form", "bifrost_form_update"),
    ("list_tables", "bifrost_table_list"),
    ("get_table", "bifrost_table_get"),
    ("create_table", "bifrost_table_create"),
    ("update_table", "bifrost_table_update"),
    ("delete_table", "bifrost_table_delete"),
    ("list_event_sources", "bifrost_event_source_list"),
    ("get_event_source", "bifrost_event_source_get"),
    ("create_event_source", "bifrost_event_source_create"),
    ("update_event_source", "bifrost_event_source_update"),
    ("delete_event_source", "bifrost_event_source_delete"),
    ("list_event_subscriptions", "bifrost_event_subscription_list"),
    ("create_event_subscription", "bifrost_event_subscription_create"),
    ("update_event_subscription", "bifrost_event_subscription_update"),
    ("delete_event_subscription", "bifrost_event_subscription_delete"),
    ("list_webhook_adapters", "bifrost_event_webhook_adapter_list"),
]

# New tool with no old name: ``bifrost_form_delete`` and
# ``bifrost_event_subscription_get`` are net-new registrations (no prior tool
# existed for forms.delete or events.subscriptions.get), so they need no
# rename entry — a fresh install already registers them under their final name.


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
