"""Migrate retired MCP workflow aliases to stable workflow UUIDs.

Revision ID: 20261021_mcp_stable_ids
Revises: 20261019_durable_impersonation
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from copy import deepcopy
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261021_mcp_stable_ids"
down_revision: str | None = "20261019_durable_impersonation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen from the retired FastMCP registry; migrations never import its mutable catalog.
_NATIVE_TOOL_IDS = frozenset(
    (
        "bifrost_agent_create bifrost_agent_delete bifrost_agent_get "
        "bifrost_agent_list bifrost_agent_update bifrost_app_create "
        "bifrost_app_delete bifrost_app_dependencies_get "
        "bifrost_app_dependencies_update bifrost_app_get bifrost_app_list "
        "bifrost_app_publish bifrost_app_replace bifrost_app_update bifrost_app_validate "
        "bifrost_claim_create bifrost_claim_delete bifrost_claim_get bifrost_claim_list "
        "bifrost_claim_update bifrost_config_create bifrost_config_delete bifrost_config_get "
        "bifrost_config_list bifrost_config_update bifrost_event_source_create "
        "bifrost_event_source_delete bifrost_event_source_get bifrost_event_source_list "
        "bifrost_event_source_update bifrost_event_subscription_create "
        "bifrost_event_subscription_delete bifrost_event_subscription_get "
        "bifrost_event_subscription_list bifrost_event_subscription_update "
        "bifrost_event_webhook_adapter_list bifrost_execution_get "
        "bifrost_execution_list bifrost_file_policy_delete "
        "bifrost_file_policy_get bifrost_file_policy_list bifrost_file_policy_set "
        "bifrost_file_search bifrost_form_create bifrost_form_delete bifrost_form_get "
        "bifrost_form_list bifrost_form_update bifrost_integration_create "
        "bifrost_integration_get bifrost_integration_list bifrost_integration_mapping_create "
        "bifrost_integration_mapping_update bifrost_integration_update "
        "bifrost_organization_create bifrost_organization_delete bifrost_organization_get "
        "bifrost_organization_list bifrost_organization_update bifrost_platform_job_get "
        "bifrost_policy_rule_create "
        "bifrost_policy_rule_delete bifrost_policy_rule_get bifrost_policy_rule_list "
        "bifrost_policy_rule_update bifrost_policy_rule_usage_list bifrost_role_create "
        "bifrost_role_delete bifrost_role_get "
        "bifrost_role_list bifrost_role_update bifrost_table_create bifrost_table_delete "
        "bifrost_table_get bifrost_table_list bifrost_table_update bifrost_workflow_delete "
        "bifrost_workflow_execute bifrost_workflow_get bifrost_workflow_list "
        "bifrost_workflow_register "
        "bifrost_workflow_role_grant bifrost_workflow_role_revoke bifrost_workflow_update "
        "bifrost_workflow_validate bifrost_get_required_instructions "
        "bifrost_search_capabilities bifrost_execute_tool bifrost_get_execution "
        "bifrost_search_memory bifrost_save_memory "
        "bifrost_remove_memory delete_content get_content get_docs list_content "
        "patch_content push_files read_content_lines replace_content search_knowledge"
    ).split()
)
_SUFFIX = re.compile(r"_[a-z0-9]{3}$")


def _normalize(name: str) -> str:
    normalized = re.sub(r"[\s-]+", "_", name.lower().strip())
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return re.sub(r"_+", "_", normalized).strip("_")


def _uuid(value: str) -> bool:
    try:
        UUID(value)
    except (TypeError, ValueError):
        return False
    return True


def _aliases(workflows: list[tuple[str, str]]) -> dict[str, tuple[list[str], bool]]:
    groups: dict[str, list[str]] = {}
    for workflow_id, name in workflows:
        groups.setdefault(_normalize(name) or workflow_id, []).append(workflow_id)
    aliases: dict[str, tuple[list[str], bool]] = {}
    for base, ids in groups.items():
        exact = f"{base}_workflow" if base in _NATIVE_TOOL_IDS else base
        aliases[exact] = (ids, len(ids) == 1)
        aliases[f"{exact}_<suffix>"] = (ids, False)
    return aliases


def _candidate(
    value: str, aliases: dict[str, tuple[list[str], bool]]
) -> tuple[list[str], bool] | None:
    if direct := aliases.get(value):
        return direct
    if _SUFFIX.search(value):
        return aliases.get(f"{value[:-4]}_<suffix>")
    return None


def _migrate_config(
    value: dict[str, Any],
    workflows: list[tuple[str, str]],
    known_system_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    result, aliases = deepcopy(value), _aliases(workflows)
    native_ids = _NATIVE_TOOL_IDS | known_system_ids
    allowed = result.get("allowed_tool_ids")
    if isinstance(allowed, list):
        rewritten: list[str] = []
        for item in allowed:
            found = _candidate(item, aliases) if isinstance(item, str) else None
            if isinstance(item, str) and (item in native_ids or _uuid(item)):
                rewritten.append(item)
            elif found is not None and found[1]:
                rewritten.extend(found[0])
        result["allowed_tool_ids"] = list(dict.fromkeys(rewritten))
    blocked = result.get("blocked_tool_ids")
    if isinstance(blocked, list):
        rewritten, unresolved = [], []
        for item in blocked:
            found = _candidate(item, aliases) if isinstance(item, str) else None
            if isinstance(item, str) and (item in native_ids or _uuid(item)):
                rewritten.append(item)
            elif found is not None:
                rewritten.extend(found[0])
            elif isinstance(item, str):
                rewritten.append(item)
                unresolved.append(item)
        result["blocked_tool_ids"] = list(dict.fromkeys(rewritten))
        if unresolved:
            result["enabled"] = False
            result["legacy_mcp_workflow_ids_review_required"] = {
                "reason": (
                    "Unresolved legacy workflow blocks disabled MCP until "
                    "administrator review."
                ),
                "unresolved_blocked_tool_ids": unresolved,
            }
    return result


def upgrade() -> None:
    bind = op.get_bind()
    workflows = [
        (str(row.id), row.name)
        for row in bind.execute(
            sa.text(
                "SELECT id, name FROM workflows "
                "WHERE is_active IS TRUE AND type = 'tool'"
            )
        ).mappings()
    ]
    known_system_ids = frozenset(
        row.id
        for row in bind.execute(
            sa.text(
                "SELECT DISTINCT unnest(system_tools) AS id FROM agents "
                "WHERE system_tools IS NOT NULL"
            )
        ).mappings()
    )
    rows = bind.execute(
        sa.text(
            "SELECT id, value_json FROM system_configs "
            "WHERE category = 'mcp' AND key = 'server_config' "
            "AND organization_id IS NULL"
        )
    ).mappings()
    update = sa.text(
        "UPDATE system_configs SET value_json = :value WHERE id = :id"
    ).bindparams(sa.bindparam("value", type_=postgresql.JSONB))
    for row in rows:
        if isinstance(row.value_json, dict):
            migrated = _migrate_config(row.value_json, workflows, known_system_ids)
            if migrated != row.value_json:
                bind.execute(update, {"id": row.id, "value": migrated})


def downgrade() -> None:
    # Legacy aliases were process-local and ambiguous, so this conversion is one-way.
    pass
