"""Shared tool-selection policy for Automatic native CodeMode runs."""

from __future__ import annotations

from typing import Any

from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.tools import RunContext, ToolDefinition

from src.services.agent_runtime.code_mode import build_code_mode_capabilities

# These are Pydantic AI control-plane tools. They must retain their native
# lifecycle, particularly ToolSearch's dynamic catalog and capability loading.
_NATIVE_FRAMEWORK_TOOL_NAMES = frozenset(
    {"search_tools", "load_capability", "delegate_task", "run_code"}
)
CODE_MODE_AUDIT_METADATA_KEY = "__bifrost_code_mode_audit__"
"""Private key on a completed outer ``run_code`` audit receipt."""

CODE_MODE_NESTED_TOOL_CALL_IDS_KEY = "nested_tool_call_ids"


def is_automatic_code_mode_tool(name: str) -> bool:
    """Whether an already-authorized Bifrost callable is a code-mode function."""
    return name not in _NATIVE_FRAMEWORK_TOOL_NAMES


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


def _select_automatic_code_mode_tool(
    ctx: RunContext[object], tool_def: ToolDefinition,
) -> bool:
    """Keep discovery and framework controls outside the Monty sandbox."""
    del ctx
    return is_automatic_code_mode_tool(tool_def.name)


def build_automatic_code_mode_capabilities(
    *,
    has_tool_search: bool,
) -> list[Any]:
    """Return native CodeMode policy for an Automatic tool-using runtime.

    A composition already owns ToolSearch. Direct Bifrost toolsets need the
    normal public capability so the same framework control surface remains
    available if their catalog becomes deferred.
    """
    capabilities: list[Any] = []
    if not has_tool_search:
        capabilities.append(ToolSearch(strategy="keywords"))
    capabilities.extend(
        build_code_mode_capabilities(tools=_select_automatic_code_mode_tool)
    )
    return capabilities
