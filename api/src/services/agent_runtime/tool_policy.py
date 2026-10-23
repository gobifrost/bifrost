"""Shared tool-selection policy for Automatic native CodeMode runs."""

from __future__ import annotations

from typing import Any

from src.services.agent_runtime.code_mode import build_catalog_private_code_mode_capabilities
from src.services.agent_runtime.sandbox_sdk import (
    BifrostSandboxSDK,
    RECOMMENDED_SANDBOX_SDK_CODE_MODE_MAX_TOOL_CALLS,
    is_sandbox_sdk_tool,
)
CODE_MODE_AUDIT_METADATA_KEY = "__bifrost_code_mode_audit__"
"""Private key on a completed outer ``run_code`` audit receipt."""

CODE_MODE_NESTED_TOOL_CALL_IDS_KEY = "nested_tool_call_ids"


def code_mode_nested_tool_call_ids_from_audit(
    tool_name: str | None,
    tool_result: object,
) -> frozenset[str]:
    """Read application-owned nested IDs from an outer ``run_code`` receipt."""
    if tool_name != "run_code" or not isinstance(tool_result, dict):
        return frozenset()
    audit = tool_result.get(CODE_MODE_AUDIT_METADATA_KEY)
    if not isinstance(audit, dict):
        return frozenset()
    nested_ids = audit.get(CODE_MODE_NESTED_TOOL_CALL_IDS_KEY)
    if not isinstance(nested_ids, list):
        return frozenset()
    return frozenset(item for item in nested_ids if isinstance(item, str))


def build_automatic_code_mode_capabilities() -> list[Any]:
    """Return the catalog-private SDK policy for Automatic runtimes.

    The outer model receives ``run_code`` plus any native composition controls.
    Fixed search and describe helpers lazily reveal direct Python bindings that
    resolve the live authorized Bifrost catalog at each dispatch.
    """
    return [
        *build_catalog_private_code_mode_capabilities(
            tools=is_sandbox_sdk_tool,
            max_tool_calls=RECOMMENDED_SANDBOX_SDK_CODE_MODE_MAX_TOOL_CALLS,
        ),
        BifrostSandboxSDK(),
    ]
