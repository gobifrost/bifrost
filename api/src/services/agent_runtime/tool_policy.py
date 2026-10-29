"""Shared tool-selection policy for Automatic native CodeMode runs."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.tools import ToolDefinition as PydanticToolDefinition

from src.services.agent_runtime.code_mode import build_code_mode_capabilities
from src.services.capabilities.search import CapabilitySearchItem, rank_capabilities
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


def build_bifrost_tool_search() -> ToolSearch[object]:
    """Build the one native discovery policy used by every Bifrost runtime."""
    return ToolSearch(strategy=_rank_bifrost_tools)


def _rank_bifrost_tools(
    _ctx: RunContext[object],
    queries: Sequence[str],
    tools: Sequence[PydanticToolDefinition],
) -> list[str]:
    """Adapt the shared authorized-capability ranking to native ToolSearch."""
    by_path: dict[str, str] = {}
    candidates: list[CapabilitySearchItem] = []
    for tool in tools:
        metadata = tool.metadata or {}
        path = metadata.get("bifrost_search_path")
        if not isinstance(path, str) or not path or path in by_path:
            path = tool.name
        by_path[path] = tool.name
        connector = metadata.get("bifrost_connector")
        method = metadata.get("bifrost_original_name")
        description = metadata.get("bifrost_tool_description")
        candidates.append(
            CapabilitySearchItem(
                path=path,
                connector=connector if isinstance(connector, str) else "tools",
                method=method if isinstance(method, str) else tool.name,
                description=description if isinstance(description, str) else tool.description,
            )
        )

    names: list[str] = []
    seen: set[str] = set()
    for query in queries:
        for result in rank_capabilities(query, candidates):
            name = by_path[result["path"]]
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def build_automatic_code_mode_capabilities() -> list[Any]:
    """Return the native discovery and CodeMode policy for Automatic runtimes."""
    return [
        build_bifrost_tool_search(),
        *build_code_mode_capabilities(tools=_is_automatic_business_tool),
    ]


def _is_automatic_business_tool(
    _ctx: RunContext[object], tool: PydanticToolDefinition,
) -> bool:
    # SubAgents' delegate_task has no tool_kind in the pinned harness. Keep
    # its framework lifecycle native, alongside upstream's search/load controls.
    return tool.name != "delegate_task"
