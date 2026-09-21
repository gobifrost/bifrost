"""Simulation-only proposed tool contracts (Phase 4e).

A proposed tool is a name plus input/output JSON Schema contracts plus
behavior expressed in the existing fixture/rules vocabulary. Proposed tools
are snapshot metadata only: they never carry a workflow UUID, ``target_id``,
or production executor binding, and production apply stays blocked while any
remain unresolved. Name collisions fail closed at every layer.
"""

from __future__ import annotations

import re
from typing import Any

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_MAX_NAME_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 2000
_FORBIDDEN_KEYS = frozenset({"target_id", "workflow_id", "tool_id", "connection_id"})
_RULE_KEYS = frozenset({"tool", "match_args", "return", "mutate"})


class ProposedToolError(ValueError):
    """Invalid proposed tool definition or unsafe binding attempt."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def validate_definition(raw: Any) -> dict[str, Any]:
    """Validate one proposed tool definition into canonical form."""
    if not isinstance(raw, dict):
        raise ProposedToolError("invalid_definition", "Proposed tool must be an object.")
    unknown = set(raw) - {
        "name",
        "description",
        "input_schema",
        "output_schema",
        "behavior",
        "simulation_only",
    }
    if unknown:
        raise ProposedToolError(
            "invalid_definition",
            f"Unsupported proposed tool fields: {', '.join(sorted(unknown))}.",
        )
    forbidden = set(raw) & _FORBIDDEN_KEYS
    if forbidden:
        raise ProposedToolError(
            "production_binding",
            f"Proposed tools must not carry production bindings: {', '.join(sorted(forbidden))}.",
        )
    if raw.get("simulation_only") is not True:
        raise ProposedToolError(
            "simulation_only_required",
            "Proposed tools must declare simulation_only: true.",
        )
    name = raw.get("name")
    if (
        not isinstance(name, str)
        or not name
        or len(name) > _MAX_NAME_LENGTH
        or not _NAME_RE.match(name)
    ):
        raise ProposedToolError(
            "invalid_name",
            "Proposed tool name must match ^[A-Za-z][A-Za-z0-9_]*$ (max 64).",
        )
    description = raw.get("description", "")
    if not isinstance(description, str) or len(description) > _MAX_DESCRIPTION_LENGTH:
        raise ProposedToolError(
            "invalid_description", "Proposed tool description must be short text."
        )
    for key in ("input_schema", "output_schema"):
        schema = raw.get(key)
        if not isinstance(schema, dict):
            raise ProposedToolError(
                "invalid_schema", f"Proposed tool {key} must be an object."
            )
    behavior = raw.get("behavior", [])
    if not isinstance(behavior, list):
        raise ProposedToolError(
            "invalid_behavior", "Proposed tool behavior must be a rule list."
        )
    for rule in behavior:
        if not isinstance(rule, dict) or rule.get("tool") != name:
            raise ProposedToolError(
                "invalid_behavior",
                "Every behavior rule must target the proposed tool name.",
            )
        unknown_rule = set(rule) - _RULE_KEYS
        if unknown_rule:
            raise ProposedToolError(
                "invalid_behavior",
                f"Unsupported behavior rule fields: {', '.join(sorted(unknown_rule))}.",
            )
        if not isinstance(rule.get("match_args", {}), dict):
            raise ProposedToolError(
                "invalid_behavior",
                "Behavior rule match_args must be an object.",
            )
        if not isinstance(rule.get("mutate", []), list):
            raise ProposedToolError(
                "invalid_behavior",
                "Behavior rule mutate must be a list.",
            )
    return {
        "name": name,
        "description": description,
        "input_schema": dict(raw["input_schema"]),
        "output_schema": dict(raw["output_schema"]),
        "behavior": [dict(rule) for rule in behavior],
        "simulation_only": True,
    }


def validate_definitions(
    raw_list: Any,
    *,
    real_tool_names: set[str] | None = None,
    delegated_names: set[str] | None = None,
    system_names: set[str] | None = None,
    mcp_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Validate definitions and reject collisions with real tool namespaces."""
    if not isinstance(raw_list, list):
        raise ProposedToolError(
            "invalid_definition", "Proposed tools must be a list."
        )
    canonical = [validate_definition(item) for item in raw_list]
    seen: set[str] = set()
    namespaces: list[tuple[str, set[str]]] = [
        ("proposed tool", set()),
        ("real tool", real_tool_names or set()),
        ("delegated tool", delegated_names or set()),
        ("system tool", system_names or set()),
        ("MCP tool", mcp_names or set()),
    ]
    for item in canonical:
        name = item["name"]
        if name in seen:
            raise ProposedToolError(
                "name_collision", f"Duplicate proposed tool name: {name}."
            )
        seen.add(name)
        for label, names in namespaces[1:]:
            if name in names:
                raise ProposedToolError(
                    "name_collision",
                    f"Proposed tool {name!r} collides with an existing {label}.",
                )
    return canonical


def find_unresolved_proposed_tools(container: Any) -> list[str]:
    """Names of simulation-only proposed tools declared in a snapshot/fixture.

    Malformed declarations (non-list, entries without string names) surface
    as ``"<malformed>"`` so guards fail closed instead of treating them as
    clean.
    """
    found: list[str] = []
    if isinstance(container, dict):
        tools = container.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                if tool.get("simulation_only") is not True:
                    continue
                if isinstance(tool.get("name"), str):
                    found.append(tool["name"])
                else:
                    found.append("<malformed>")
        proposed = container.get("proposed_tools")
        if "proposed_tools" in container and not isinstance(proposed, list):
            found.append("<malformed>")
        elif isinstance(proposed, list):
            for item in proposed:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    found.append(item["name"])
                else:
                    found.append("<malformed>")
    return sorted(set(found))


def assert_no_unresolved_proposed_tools(container: Any, *, context: str) -> None:
    """Reject production use while proposed tools remain unresolved."""
    unresolved = find_unresolved_proposed_tools(container)
    if unresolved:
        raise ProposedToolError(
            "unresolved_proposed_tools",
            f"{context} still references unresolved simulation-only tools: "
            + ", ".join(unresolved)
            + ". Create or map real tools first.",
        )
