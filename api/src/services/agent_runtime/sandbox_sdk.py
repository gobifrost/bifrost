"""Catalog-private direct Python bindings for native CodeMode programs.

The wrapper keeps Bifrost's assembled toolset as the sole authority. It only
changes how already-authorized business tools are presented to Monty: the
sandbox gets Python-callable aliases while the outer model gets lazy discovery
helpers and no business catalog.
"""

from __future__ import annotations

import hashlib
import json
import keyword
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.tools import ToolDefinition as PydanticToolDefinition
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset
from pydantic_core import SchemaValidator, core_schema

from pydantic_ai_harness.code_mode import CodeMode

from src.services.agent_runtime.errors import AgentRunCancelled
from src.services.agent_runtime.toolset import get_code_mode_tool_result_scope
from src.services.tool_schema import validate_arguments_against_schema

SANDBOX_SDK_TOOL_NAMES = frozenset({"bifrost_search", "bifrost_describe"})
"""Fixed helpers available inside every catalog-private CodeMode sandbox."""

MAX_SANDBOX_SDK_BUSINESS_CALLS = 16
"""Maximum real Bifrost callbacks allowed from one outer ``run_code`` call."""

RECOMMENDED_SANDBOX_SDK_CODE_MODE_MAX_TOOL_CALLS = 64
"""Leave room for discovery without reducing the real business-call cap."""

SANDBOX_SDK_INSTRUCTIONS = """Use lazy Bifrost discovery inside run_code.
Call bifrost_search(query) to get a bounded list of approved Python callable names. Call
bifrost_describe(name) for a callable's exact input schema and generated Python signature. Then call
the discovered function directly using its documented keyword arguments.

Search and describe return {ok, data, error} envelopes and do not run business callbacks. Business
callables return their source result directly: JSON text is decoded before it reaches code, plain text
stays text, and source Error: receipts or JSON error payloads stay explicit source failures. A program
may make at most 16 real business calls. Keep raw records inside code and return compact evidence.
"""
"""Stable instructions for the fixed lazy discovery helpers."""

_ARGS_VALIDATOR = SchemaValidator(schema=core_schema.dict_schema())
_CONTROL_TOOL_NAMES = frozenset({"load_capability", "delegate_task", "run_code"})
_SEARCH_TOOL_NAME = "search_tools"
_SEARCH_RESULT_LIMIT = 8
_SEARCH_SNIPPET_CHARS = 240
_SEARCH_TERMS = re.compile(r"[a-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_INVALID_IDENTIFIER = re.compile(r"[^0-9A-Za-z_]")
_DIRECT_RETURN_TYPES = frozenset({"object", "array", "number", "integer", "boolean", "null"})
_RETURN_SCHEMA_COMBINATORS = frozenset({"allOf", "anyOf", "oneOf", "not"})
_SDK_ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "data": {}, "error": {}},
    "required": ["ok", "data", "error"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class _BoundTool:
    alias: str
    source_name: str
    tool: ToolsetTool[object]


def is_sandbox_sdk_tool(
    ctx: RunContext[object], tool_def: PydanticToolDefinition
) -> bool:
    """Select SDK helpers and direct bindings, leaving framework controls native."""
    del ctx
    return tool_def.name not in _CONTROL_TOOL_NAMES and tool_def.name != _SEARCH_TOOL_NAME


@dataclass
class BifrostSandboxSDK(AbstractCapability[object]):
    """Expose lazy discovery plus live direct bindings inside CodeMode."""

    max_business_calls: int = MAX_SANDBOX_SDK_BUSINESS_CALLS

    def get_instructions(self) -> str:
        return SANDBOX_SDK_INSTRUCTIONS

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(wrapped_by=[CodeMode])

    def get_wrapper_toolset(
        self, toolset: AbstractToolset[object]
    ) -> AbstractToolset[object]:
        return _BifrostSandboxSDKToolset(
            wrapped=toolset,
            max_business_calls=self.max_business_calls,
        )


@dataclass
class _BifrostSandboxSDKToolset(WrapperToolset[object]):
    """Hide raw definitions while dispatching aliases through the wrapped chain."""

    max_business_calls: int

    async def get_tools(self, ctx: RunContext[object]) -> dict[str, ToolsetTool[object]]:
        live_tools = await self.wrapped.get_tools(ctx)
        result = {
            name: tool
            for name, tool in live_tools.items()
            if _is_native_control(name, tool)
        }
        result.update(self._sdk_tools())
        result.update(
            {
                binding.alias: _direct_tool(self, binding)
                for binding in (await self._bound_tools(ctx)).values()
            }
        )
        return result

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[object],
        tool: ToolsetTool[object],
    ) -> Any:
        if name == "bifrost_search":
            return await self._search(ctx, tool_args)
        if name == "bifrost_describe":
            return await self._describe(ctx, tool_args)

        binding = (await self._bound_tools(ctx)).get(name)
        if binding is not None:
            return await self._dispatch(binding, tool_args, ctx)
        if tool.toolset is self:
            return f"Error: Bifrost callable {name!r} is no longer available."
        return await self.wrapped.call_tool(name, tool_args, ctx, tool)

    def _sdk_tools(self) -> dict[str, ToolsetTool[object]]:
        return {
            definition.name: ToolsetTool(
                toolset=self,
                tool_def=definition,
                max_retries=0,
                args_validator=_ARGS_VALIDATOR,
            )
            for definition in _sdk_definitions()
        }

    async def _search(self, ctx: RunContext[object], tool_args: dict[str, Any]) -> dict[str, Any]:
        query = tool_args.get("query")
        if not isinstance(query, str):
            return _error("Error: bifrost_search requires a string query.")
        terms = _search_terms(query)
        if not terms:
            return _success([])

        matches: list[tuple[int, _BoundTool]] = []
        for binding in (await self._bound_tools(ctx)).values():
            description = binding.tool.tool_def.description or ""
            name_words = _search_terms(binding.alias)
            text_words = _search_terms(description)
            overlap = len(terms & (name_words | text_words))
            if overlap:
                score = overlap * 10 + len(terms & name_words) * 10
                matches.append((score, binding))
        matches.sort(key=lambda item: (-item[0], item[1].alias))
        return _success(
            [
                {
                    "name": binding.alias,
                    "description": _snippet(binding.tool.tool_def.description),
                }
                for _, binding in matches[:_SEARCH_RESULT_LIMIT]
            ]
        )

    async def _describe(
        self, ctx: RunContext[object], tool_args: dict[str, Any]
    ) -> dict[str, Any]:
        name = tool_args.get("name")
        if not isinstance(name, str):
            return _error("Error: bifrost_describe requires a string name.")
        binding = (await self._bound_tools(ctx)).get(name)
        if binding is None:
            return _unknown_tool(name)
        direct_definition = _direct_definition(binding)
        signature = direct_definition.function_signature
        type_definitions = type(signature).render_type_definitions([signature], frozenset())
        return _success(
            {
                "name": binding.alias,
                "description": binding.tool.tool_def.description,
                "python_signature": signature.render(
                    "...",
                    name=binding.alias,
                    is_async=not direct_definition.sequential,
                ),
                "python_type_definitions": type_definitions,
                "input_schema": binding.tool.tool_def.parameters_json_schema,
                "source_return_schema": binding.tool.tool_def.return_schema,
                "return_transport": (
                    "The direct callable decodes JSON text from the source adapter before "
                    "returning it. Plain text remains text. Source Error: receipts and decoded "
                    "JSON error payloads remain source failures without an SDK envelope."
                ),
            }
        )

    async def _dispatch(
        self,
        binding: _BoundTool,
        arguments: dict[str, Any],
        ctx: RunContext[object],
    ) -> Any:
        issues, schema_error = validate_arguments_against_schema(
            binding.tool.tool_def.parameters_json_schema, arguments
        )
        if schema_error:
            return (
                "Error: The live tool schema is invalid and cannot be executed: "
                f"{schema_error}"
            )
        if issues:
            return f"Error: Arguments do not match the live tool schema. Issues: {issues}"

        scope = get_code_mode_tool_result_scope()
        if scope is None:
            return "Error: Bifrost direct bindings are only available inside run_code."
        if scope.cancellation:
            raise scope.cancellation
        if scope.business_calls >= self.max_business_calls:
            return (
                "Error: Bifrost sandbox business call limit of "
                f"{self.max_business_calls} reached."
            )
        scope.business_calls += 1

        child_ctx = replace(
            ctx,
            tool_name=binding.source_name,
            tool_call_id=f"bifrost_sdk_{uuid4().hex}",
        )
        try:
            value = await self.wrapped.call_tool(
                binding.source_name,
                arguments,
                child_ctx,
                binding.tool,
            )
        except AgentRunCancelled as exc:
            scope.cancellation = exc
            raise
        return _decode_business_result(value)

    async def _bound_tools(self, ctx: RunContext[object]) -> dict[str, _BoundTool]:
        raw_tools = {
            name: tool
            for name, tool in (await self.wrapped.get_tools(ctx)).items()
            if not _is_native_control(name, tool)
            and name != _SEARCH_TOOL_NAME
            and tool.tool_def.tool_kind != "tool-search"
            and name not in SANDBOX_SDK_TOOL_NAMES
            and _is_live_business_tool(ctx, tool)
        }
        aliases = _aliases_for(raw_tools)
        return {
            alias: _BoundTool(alias=alias, source_name=source_name, tool=raw_tools[source_name])
            for source_name, alias in aliases.items()
        }


def _sdk_definitions() -> Sequence[PydanticToolDefinition]:
    return (
        PydanticToolDefinition(
            name="bifrost_search",
            description="Search approved Bifrost callable names by a short query.",
            parameters_json_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            return_schema=_SDK_ENVELOPE_SCHEMA,
            include_return_schema=True,
            sequential=True,
        ),
        PydanticToolDefinition(
            name="bifrost_describe",
            description="Describe one approved Bifrost callable's Python signature and transport.",
            parameters_json_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            return_schema=_SDK_ENVELOPE_SCHEMA,
            include_return_schema=True,
            sequential=True,
        ),
    )


def _direct_tool(
    toolset: _BifrostSandboxSDKToolset, binding: _BoundTool
) -> ToolsetTool[object]:
    return ToolsetTool(
        toolset=toolset,
        tool_def=_direct_definition(binding),
        max_retries=binding.tool.max_retries,
        args_validator=binding.tool.args_validator,
        args_validator_func=binding.tool.args_validator_func,
    )


def _direct_definition(binding: _BoundTool) -> PydanticToolDefinition:
    """Keep the source input contract but document decoded direct return transport."""
    return_schema = _direct_return_schema(binding.tool.tool_def.return_schema)
    return replace(
        binding.tool.tool_def,
        name=binding.alias,
        return_schema=return_schema,
        include_return_schema=return_schema is not None,
        defer_loading=False,
    )


def _direct_return_schema(source_return_schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """Retain only source types unchanged by adapter JSON-text decoding."""
    if not isinstance(source_return_schema, dict):
        return None
    source_type = source_return_schema.get("type")
    if not isinstance(source_type, str) or source_type not in _DIRECT_RETURN_TYPES:
        return None
    if "$ref" in source_return_schema or _RETURN_SCHEMA_COMBINATORS & source_return_schema.keys():
        return None
    return source_return_schema


def _aliases_for(raw_tools: dict[str, ToolsetTool[object]]) -> dict[str, str]:
    """Create snapshot-stable Python aliases without parsing presentation descriptions."""
    requested = {
        source_name: _requested_alias(source_name, tool)
        for source_name, tool in raw_tools.items()
    }
    grouped: dict[str, list[str]] = {}
    for source_name, alias in requested.items():
        grouped.setdefault(alias, []).append(source_name)

    aliases: dict[str, str] = {}
    for alias, source_names in grouped.items():
        if len(source_names) == 1:
            aliases[source_names[0]] = alias
            continue
        for source_name in sorted(source_names):
            metadata = raw_tools[source_name].tool_def.metadata or {}
            owner = metadata.get("bifrost_owner_id")
            owner_part = "".join(char for char in owner if char.isalnum()) if isinstance(owner, str) else ""
            if owner_part:
                aliases[source_name] = _safe_identifier(f"owner_{owner_part}_{alias}")
            else:
                digest = hashlib.sha256(source_name.encode()).hexdigest()[:12]
                aliases[source_name] = _safe_identifier(f"{alias}_{digest}")

    used: set[str] = set()
    for source_name in sorted(aliases):
        base_alias = aliases[source_name]
        alias = base_alias
        attempt = 0
        while alias in used:
            digest = hashlib.sha256(source_name.encode()).hexdigest()[:12]
            suffix = digest if attempt == 0 else f"{digest}_{attempt}"
            alias = _safe_identifier(f"{base_alias}_{suffix}")
            attempt += 1
        used.add(alias)
        aliases[source_name] = alias
    return aliases


def _requested_alias(source_name: str, tool: ToolsetTool[object]) -> str:
    metadata = tool.tool_def.metadata or {}
    preferred = metadata.get("bifrost_python_name")
    candidate = preferred if isinstance(preferred, str) else source_name
    return _safe_identifier(candidate)


def _safe_identifier(value: str) -> str:
    normalized = _INVALID_IDENTIFIER.sub("_", value)
    if not normalized:
        normalized = "bifrost_tool"
    if normalized[0].isdigit():
        normalized = f"bifrost_{normalized}"
    if keyword.iskeyword(normalized) or normalized in SANDBOX_SDK_TOOL_NAMES | _CONTROL_TOOL_NAMES:
        normalized = f"bifrost_{normalized}"
    return normalized


def _is_native_control(name: str, tool: ToolsetTool[object]) -> bool:
    if name == _SEARCH_TOOL_NAME or tool.tool_def.tool_kind == "tool-search":
        return False
    return name in _CONTROL_TOOL_NAMES or tool.tool_def.tool_kind is not None


def _is_live_business_tool(ctx: RunContext[object], tool: ToolsetTool[object]) -> bool:
    """Respect native deferred capability gates without hiding root discovery."""
    definition = tool.tool_def
    return not (
        definition.capability_id is not None
        and definition.capability_id not in ctx.active_capability_ids
    )


def _snippet(description: str | None) -> str:
    if not description:
        return ""
    return " ".join(description.split())[:_SEARCH_SNIPPET_CHARS]


def _search_terms(value: str) -> set[str]:
    normalized = _CAMEL_BOUNDARY.sub(" ", value).replace("_", " ")
    return set(_SEARCH_TERMS.findall(normalized.casefold()))


def _success(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _error(error: Any, *, data: Any = None) -> dict[str, Any]:
    return {"ok": False, "data": data, "error": error}


def _unknown_tool(name: str) -> dict[str, Any]:
    return _error(f"Unknown Bifrost tool: {name}")


def _decode_business_result(value: Any) -> Any:
    """Decode only successful JSON adapter text; preserve source failure forms."""
    if not isinstance(value, str) or value.startswith("Error:"):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
