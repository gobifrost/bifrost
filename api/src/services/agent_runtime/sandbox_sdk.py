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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.function_signature import FunctionSignature
from pydantic_ai.tools import ToolDefinition as PydanticToolDefinition
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset
from pydantic_core import SchemaValidator, core_schema

from pydantic_ai_harness.code_mode import CodeMode

from src.services.agent_runtime.errors import AgentRunCancelled
from src.services.capabilities.search import (
    CapabilitySearchItem,
    search_capabilities,
)
from src.services.agent_runtime.toolset import get_code_mode_tool_result_scope
from src.services.capabilities.catalog import (
    CapabilityArgumentError,
    require_valid_capability_arguments,
)

SANDBOX_SDK_TOOL_NAMES = frozenset({"bifrost_search", "bifrost_describe"})
"""Fixed helpers available inside every catalog-private CodeMode sandbox."""

MAX_SANDBOX_SDK_BUSINESS_CALLS = 16
"""Maximum real Bifrost callbacks allowed from one outer ``run_code`` call."""

RECOMMENDED_SANDBOX_SDK_CODE_MODE_MAX_TOOL_CALLS = 64
"""Leave room for discovery without reducing the real business-call cap."""

SANDBOX_SDK_INSTRUCTIONS = """Use lazy Bifrost discovery inside run_code.
Call bifrost_search(query) to get ranked approved Python callable names, then call
bifrost_describe(target) for direct Python declarations. Call the discovered function directly using
its documented keyword arguments.

Search and describe return plain structured documentation and do not run business callbacks. Business
callables return source data directly. Adapter error receipts and JSON error payloads are opaque source
data; preserve useful uncertainty in your compact result. A program may make at most 16 real business
calls. Keep raw records inside code and return compact evidence.
"""
"""Stable instructions for the fixed lazy discovery helpers."""

_ARGS_VALIDATOR = SchemaValidator(schema=core_schema.dict_schema())
_CONTROL_TOOL_NAMES = frozenset({"load_capability", "delegate_task", "run_code"})
_SEARCH_TOOL_NAME = "search_tools"
_INVALID_IDENTIFIER = re.compile(r"[^0-9A-Za-z_]")
_DIRECT_RETURN_TYPES = frozenset({"object", "array", "number", "integer", "boolean", "null"})
_RETURN_SCHEMA_COMBINATORS = frozenset({"allOf", "anyOf", "oneOf", "not"})
_JSON_OR_TEXT_TRANSPORT = "json_or_text"
_SEARCH_RETURN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "connector": {"type": "string"},
                    "method": {"type": "string"},
                    "description": {"type": ["string", "null"]},
                    "kind": {"const": "method"},
                    "score": {"type": "integer"},
                },
                "required": ["path", "connector", "method", "kind", "score"],
            },
        },
        "total": {"type": "integer"},
        "truncated": {"type": "boolean"},
    },
    "required": ["results", "total", "truncated"],
}
_DESCRIBE_RETURN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "kind": {"enum": ["method", "connector"]},
        "description": {"type": "string"},
        "types": {"type": "string"},
    },
    "required": ["path", "kind", "types"],
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
            raise LookupError(f"Bifrost callable {name!r} is no longer available.")
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
            raise ValueError("bifrost_search requires a string query.")
        bindings = await self._bound_tools(ctx)
        by_path = {_search_path(binding): binding for binding in bindings.values()}
        result = search_capabilities(
            query,
            (
                CapabilitySearchItem(
                    path=path,
                    connector=_connector_name(binding),
                    method=_method_name(binding),
                    description=_source_description(binding),
                )
                for path, binding in by_path.items()
            ),
        )
        for item in result["results"]:
            item["path"] = by_path[item["path"]].alias
        return result

    async def _describe(
        self, ctx: RunContext[object], tool_args: dict[str, Any]
    ) -> dict[str, Any]:
        target = tool_args.get("target")
        if not isinstance(target, str):
            raise ValueError("bifrost_describe requires a string target.")
        bindings = await self._bound_tools(ctx)
        binding = bindings.get(target)
        if binding is not None:
            return _describe_method(binding, bindings)
        group = [candidate for candidate in bindings.values() if _connector_name(candidate) == target]
        if group:
            return _describe_connector(target, group, bindings)
        return {"path": target, "types": f'"{target}" not found.', "kind": "method"}

    async def _dispatch(
        self,
        binding: _BoundTool,
        arguments: dict[str, Any],
        ctx: RunContext[object],
    ) -> Any:
        try:
            require_valid_capability_arguments(
                binding.tool.tool_def.parameters_json_schema, arguments
            )
        except CapabilityArgumentError as exc:
            if exc.schema_error:
                raise RuntimeError(
                    f"The live tool schema is invalid and cannot be executed: {exc.schema_error}"
                ) from exc
            raise ValueError(
                f"Arguments do not match the live tool schema. Issues: {exc.issues}"
            ) from exc

        scope = get_code_mode_tool_result_scope()
        if scope is None:
            raise RuntimeError("Bifrost direct bindings are only available inside run_code.")
        if scope.cancellation:
            raise scope.cancellation
        if scope.business_calls >= self.max_business_calls:
            raise RuntimeError(
                f"Bifrost sandbox business call limit of {self.max_business_calls} reached."
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
        return _decode_business_result(value, _result_transport(binding))

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
            return_schema=_SEARCH_RETURN_SCHEMA,
            include_return_schema=True,
            sequential=True,
        ),
        PydanticToolDefinition(
            name="bifrost_describe",
            description="Describe a live Bifrost callable or connector with Python declarations.",
            parameters_json_schema={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
                "additionalProperties": False,
            },
            return_schema=_DESCRIBE_RETURN_SCHEMA,
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
    return_schema = _direct_return_schema(
        binding.tool.tool_def.return_schema,
        _result_transport(binding),
    )
    return replace(
        binding.tool.tool_def,
        name=binding.alias,
        return_schema=return_schema,
        include_return_schema=return_schema is not None,
        defer_loading=False,
    )


def _direct_return_schema(
    source_return_schema: dict[str, Any] | None,
    result_transport: str | None,
) -> dict[str, Any] | None:
    """Retain native schemas while omitting types changed by JSON-text adapters."""
    if result_transport != _JSON_OR_TEXT_TRANSPORT:
        return source_return_schema
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


def _connector_name(binding: _BoundTool) -> str:
    metadata = binding.tool.tool_def.metadata or {}
    connector = metadata.get("bifrost_connector")
    if isinstance(connector, str) and connector:
        return connector
    toolset_id = binding.tool.tool_def.toolset_id
    if isinstance(toolset_id, str) and toolset_id:
        return toolset_id
    toolset_id = binding.tool.toolset.id
    return toolset_id if isinstance(toolset_id, str) and toolset_id else "tools"


def _connector_description(bindings: Sequence[_BoundTool]) -> str | None:
    for binding in bindings:
        metadata = binding.tool.tool_def.metadata or {}
        description = metadata.get("bifrost_connector_description")
        if isinstance(description, str) and description:
            return description
    return None


def _method_name(binding: _BoundTool) -> str:
    metadata = binding.tool.tool_def.metadata or {}
    original_name = metadata.get("bifrost_original_name")
    return original_name if isinstance(original_name, str) and original_name else binding.alias


def _search_path(binding: _BoundTool) -> str:
    metadata = binding.tool.tool_def.metadata or {}
    path = metadata.get("bifrost_search_path")
    return path if isinstance(path, str) and path else binding.alias


def _source_description(binding: _BoundTool) -> str | None:
    metadata = binding.tool.tool_def.metadata or {}
    description = metadata.get("bifrost_tool_description")
    return description if isinstance(description, str) else binding.tool.tool_def.description


def _result_transport(binding: _BoundTool) -> str | None:
    metadata = binding.tool.tool_def.metadata or {}
    transport = metadata.get("bifrost_result_transport")
    return transport if isinstance(transport, str) else None


def _describe_method(
    binding: _BoundTool, bindings: dict[str, _BoundTool]
) -> dict[str, Any]:
    signatures, payloads, conflicts = _signatures_for(bindings.values())
    signature = signatures[binding.alias]
    selected = [signature]
    if payload := payloads.get(binding.alias):
        selected.append(payload)
    blocks = _payload_notes([binding], payloads)
    blocks.extend(FunctionSignature.render_type_definitions(selected, conflicts))
    blocks.append(
        signature.render(
            "...",
            name=binding.alias,
            is_async=not _direct_definition(binding).sequential,
            conflicting_type_names=conflicts,
        )
    )
    result: dict[str, Any] = {
        "path": binding.alias,
        "kind": "method",
        "types": "\n\n".join(blocks),
    }
    if binding.tool.tool_def.description:
        result["description"] = binding.tool.tool_def.description
    return result


def _describe_connector(
    connector: str,
    group: Sequence[_BoundTool],
    bindings: dict[str, _BoundTool],
) -> dict[str, Any]:
    signatures, payloads, conflicts = _signatures_for(bindings.values())
    group = sorted(group, key=lambda binding: binding.alias)
    group_signatures = [signatures[binding.alias] for binding in group]
    selected = group_signatures + [payloads[binding.alias] for binding in group if binding.alias in payloads]
    blocks = _payload_notes(group, payloads)
    blocks.extend(FunctionSignature.render_type_definitions(selected, conflicts))
    blocks.extend(
        signature.render(
            "...",
            name=binding.alias,
            is_async=not _direct_definition(binding).sequential,
            conflicting_type_names=conflicts,
        )
        for binding, signature in zip(group, group_signatures, strict=True)
    )
    result: dict[str, Any] = {
        "path": connector,
        "kind": "connector",
        "types": "\n\n".join(blocks),
    }
    if description := _connector_description(group):
        result["description"] = description
    return result


def _signatures_for(
    bindings: Iterable[_BoundTool],
) -> tuple[dict[str, FunctionSignature], dict[str, FunctionSignature], frozenset[str]]:
    signatures: dict[str, FunctionSignature] = {}
    payloads: dict[str, FunctionSignature] = {}
    for binding in bindings:
        signatures[binding.alias] = _direct_definition(binding).function_signature
        metadata = binding.tool.tool_def.metadata or {}
        schema = metadata.get("bifrost_structured_output_schema")
        if isinstance(schema, dict):
            payloads[binding.alias] = PydanticToolDefinition(
                name=f"{binding.alias}_structured_content",
                parameters_json_schema={"type": "object", "properties": {}},
                return_schema=schema,
            ).function_signature
    all_signatures = [*signatures.values(), *payloads.values()]
    return signatures, payloads, FunctionSignature.get_conflicting_type_names(all_signatures)


def _payload_notes(
    bindings: Sequence[_BoundTool], payloads: dict[str, FunctionSignature]
) -> list[str]:
    return [
        f"# {binding.alias}: the following payload type describes successful "
        "result['structured_content']; it does not describe the surrounding result "
        "or an execution failure."
        for binding in bindings
        if binding.alias in payloads
    ]


def _decode_business_result(value: Any, result_transport: str | None) -> Any:
    """Decode Bifrost JSON-text adapters without changing native text results."""
    if (
        result_transport != _JSON_OR_TEXT_TRANSPORT
        or not isinstance(value, str)
        or value.startswith("Error:")
    ):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
