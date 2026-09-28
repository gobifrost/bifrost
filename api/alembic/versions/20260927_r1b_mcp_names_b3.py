"""rename persisted batch-3 MCP tool identifiers to the noun-first convention

Revision ID: 20260927_r1b_mcp_names_b3
Revises: 20260927_no_sys_role

RBAC R1b batch 3 renames the registered organizations/configs/policy-rules/
integrations MCP tools to the catalog's ``bifrost_<noun>_<verb>`` names (see
``operation_catalog.py``). Rewrites two persisted locations atomically with
the registration change, same shape as batch 2
(``20260927_r1b_batch2_mcp_names``):

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

revision: str = "20260927_r1b_mcp_names_b3"
down_revision: str | None = "20260927_no_sys_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Batch-3 renames (this migration's own domain).
RENAMES: list[tuple[str, str]] = [
    ("list_organizations", "bifrost_organization_list"),
    ("get_organization", "bifrost_organization_get"),
    ("create_organization", "bifrost_organization_create"),
    ("update_organization", "bifrost_organization_update"),
    ("delete_organization", "bifrost_organization_delete"),
    ("list_configs", "bifrost_config_list"),
    ("get_config", "bifrost_config_get"),
    ("create_config", "bifrost_config_create"),
    ("update_config", "bifrost_config_update"),
    ("delete_config", "bifrost_config_delete"),
    ("list_policy_rules", "bifrost_policy_rule_list"),
    ("create_policy_rule", "bifrost_policy_rule_create"),
    ("delete_policy_rule", "bifrost_policy_rule_delete"),
    ("list_integrations", "bifrost_integration_list"),
    ("get_integration", "bifrost_integration_get"),
    ("create_integration", "bifrost_integration_create"),
    ("update_integration", "bifrost_integration_update"),
    ("add_integration_mapping", "bifrost_integration_mapping_create"),
    ("update_integration_mapping", "bifrost_integration_mapping_update"),
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

    Pure SQL, no Python-side JSON read-modify-write round trip. Only
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
    _rewrite_server_config(RENAMES)


def downgrade() -> None:
    for old, new in reversed(RENAMES):
        _replace(new, old)
    _rewrite_server_config([(new, old) for old, new in RENAMES])
