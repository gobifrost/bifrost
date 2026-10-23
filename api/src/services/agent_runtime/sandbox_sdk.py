"""A catalog-private SDK surface for native CodeMode programs.

The SDK is deliberately only an adapter around the assembled Bifrost toolset.
It never owns an executor or an authorization decision: ``bifrost_call``
validates and dispatches the original live ``ToolsetTool`` through its own
toolset.  This keeps application audit, authorization, cancellation, and
result handling on the ordinary Bifrost path while keeping the business
catalog out of the model-visible tool list.
"""

from __future__ import annotations

import json
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

SANDBOX_SDK_TOOL_NAMES = frozenset(
    {"bifrost_search", "bifrost_describe", "bifrost_call"}
)
"""The complete model-facing callable catalog exposed inside ``run_code``."""

MAX_SANDBOX_SDK_BUSINESS_CALLS = 16
"""Maximum real Bifrost callbacks allowed from one outer ``run_code`` call."""

RECOMMENDED_SANDBOX_SDK_CODE_MODE_MAX_TOOL_CALLS = 64
"""Allow discovery and validation calls without reducing the business-call cap."""

SANDBOX_SDK_INSTRUCTIONS = """Use the Bifrost sandbox SDK rather than guessing a business tool.
Call bifrost_search(query) to get a small ranked list of names and descriptions. Call
bifrost_describe(name) before invoking a name to obtain its exact input schema and its return
transport contract. Call bifrost_call(name, arguments) with arguments matching that schema.

Every SDK function returns an envelope with ok, data, and error. bifrost_call decodes structured
Bifrost JSON text into data. Plain text remains data. An Error: receipt or JSON payload containing
error or is_error has ok=false; inspect error and preserve useful uncertainty in your compact
result. Search and describe do not execute a business callback. A single run_code program may make
at most 16 real business callbacks, so combine dependent reads and project only task-relevant data.
"""
"""Stable transport guidance for the fixed sandbox functions."""

_ARGS_VALIDATOR = SchemaValidator(schema=core_schema.dict_schema())
_CONTROL_TOOL_NAMES = frozenset({"load_capability", "delegate_task", "run_code"})
_SEARCH_TOOL_NAME = "search_tools"
_SEARCH_RESULT_LIMIT = 8
_SEARCH_SNIPPET_CHARS = 240
_SEARCH_TERMS = re.compile(r"[a-z0-9]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SDK_ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "data": {},
        "error": {},
    },
    "required": ["ok", "data", "error"],
    "additionalProperties": False,
}


def is_sandbox_sdk_tool(
    ctx: RunContext[object], tool_def: PydanticToolDefinition
) -> bool:
    """Select only fixed SDK functions for CodeMode's public tool selector."""
    del ctx
    return tool_def.name in SANDBOX_SDK_TOOL_NAMES


@dataclass
class BifrostSandboxSDK(AbstractCapability[object]):
    """Expose a bounded SDK while retaining the assembled toolset as authority."""

    max_business_calls: int = MAX_SANDBOX_SDK_BUSINESS_CALLS

    def get_instructions(self) -> str:
        """Document the fixed SDK transport without publishing the business catalog."""
        return SANDBOX_SDK_INSTRUCTIONS

    def get_ordering(self) -> CapabilityOrdering:
        """Let CodeMode see the fixed SDK functions, never the raw business tools."""
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
    """Hide business definitions while forwarding valid calls to their source toolset."""

    max_business_calls: int
    async def get_tools(self, ctx: RunContext[object]) -> dict[str, ToolsetTool[object]]:
        live_tools = await self.wrapped.get_tools(ctx)
        result = {
            name: tool
            for name, tool in live_tools.items()
            if _is_native_control(name, tool)
        }
        result.update(self._sdk_tools())
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
        if name == "bifrost_call":
            return await self._call(ctx, tool_args)
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

        matches: list[tuple[int, str, ToolsetTool[object]]] = []
        for name, tool in (await self._business_tools(ctx)).items():
            description = tool.tool_def.description or ""
            name_words = _search_terms(name)
            text_words = _search_terms(description)
            overlap = len(terms & (name_words | text_words))
            if overlap:
                score = overlap * 10 + len(terms & name_words) * 10
                matches.append((score, name, tool))
        matches.sort(key=lambda match: (-match[0], match[1]))
        return _success(
            [
                {
                    "name": name,
                    "description": _snippet(tool.tool_def.description),
                }
                for _, name, tool in matches[:_SEARCH_RESULT_LIMIT]
            ]
        )

    async def _describe(
        self, ctx: RunContext[object], tool_args: dict[str, Any]
    ) -> dict[str, Any]:
        name = tool_args.get("name")
        if not isinstance(name, str):
            return _error("Error: bifrost_describe requires a string name.")
        tool = (await self._business_tools(ctx)).get(name)
        if tool is None:
            return _unknown_tool(name)
        return _success(
            {
                "name": name,
                "description": tool.tool_def.description,
                "input_schema": tool.tool_def.parameters_json_schema,
                "source_return_schema": tool.tool_def.return_schema,
                "return_transport": (
                    "The source adapter's transport is described by source_return_schema. "
                    "bifrost_call itself returns {ok, data, error}: JSON text from the source "
                    "adapter is decoded into data, while non-JSON text remains data. Error: "
                    "receipts and error or is_error payloads set ok to false."
                ),
            }
        )

    async def _call(self, ctx: RunContext[object], tool_args: dict[str, Any]) -> dict[str, Any]:
        name = tool_args.get("name")
        arguments = tool_args.get("arguments")
        if not isinstance(name, str):
            return _error("Error: bifrost_call requires a string name.")
        if not isinstance(arguments, dict):
            return _error("Error: bifrost_call requires an object arguments value.")

        tool = (await self._business_tools(ctx)).get(name)
        if tool is None:
            return _unknown_tool(name)

        issues, schema_error = validate_arguments_against_schema(
            tool.tool_def.parameters_json_schema, arguments
        )
        if schema_error:
            return _error(
                "Error: The live tool schema is invalid and cannot be executed: "
                f"{schema_error}"
            )
        if issues:
            return _error(
                "Error: Arguments do not match the live tool schema. "
                f"Issues: {issues}"
            )

        scope = get_code_mode_tool_result_scope()
        if scope is None:
            return _error("Error: bifrost_call is only available inside run_code.")
        if scope.cancellation:
            raise scope.cancellation
        call_count = scope.business_calls
        if call_count >= self.max_business_calls:
            return _error(
                f"Error: Bifrost sandbox business call limit of {self.max_business_calls} reached."
            )
        scope.business_calls = call_count + 1

        # The original source toolset remains the only executor. Give it a real
        # child dispatch identity so Bifrost can record it below the outer code call.
        child_ctx = replace(
            ctx,
            tool_name=name,
            tool_call_id=f"bifrost_sdk_{uuid4().hex}",
        )
        try:
            value = await self.wrapped.call_tool(name, arguments, child_ctx, tool)
        except AgentRunCancelled as exc:
            scope.cancellation = exc
            raise
        except Exception as exc:
            return _error(f"Error: {exc}")
        return _normalize_result(value)

    async def _business_tools(
        self, ctx: RunContext[object]
    ) -> dict[str, ToolsetTool[object]]:
        return {
            name: tool
            for name, tool in (await self.wrapped.get_tools(ctx)).items()
            if (
                not _is_native_control(name, tool)
                and name != _SEARCH_TOOL_NAME
                and tool.tool_def.tool_kind != "tool-search"
                and name not in SANDBOX_SDK_TOOL_NAMES
            )
        }


def _sdk_definitions() -> Sequence[PydanticToolDefinition]:
    return (
        PydanticToolDefinition(
            name="bifrost_search",
            description="Search approved Bifrost business tools by a short query.",
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
            description=(
                "Describe one approved Bifrost business tool's exact input and return transport."
            ),
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
        PydanticToolDefinition(
            name="bifrost_call",
            description="Call one described Bifrost business tool with schema-valid arguments.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "arguments": {"type": "object", "additionalProperties": True},
                },
                "required": ["name", "arguments"],
                "additionalProperties": False,
            },
            return_schema=_SDK_ENVELOPE_SCHEMA,
            include_return_schema=True,
            sequential=True,
        ),
    )


def _is_native_control(name: str, tool: ToolsetTool[object]) -> bool:
    """Retain framework controls while explicitly removing ToolSearch."""
    if name == _SEARCH_TOOL_NAME or tool.tool_def.tool_kind == "tool-search":
        return False
    return name in _CONTROL_TOOL_NAMES or tool.tool_def.tool_kind is not None


def _snippet(description: str | None) -> str:
    if not description:
        return ""
    collapsed = " ".join(description.split())
    return collapsed[:_SEARCH_SNIPPET_CHARS]


def _search_terms(value: str) -> set[str]:
    """Match ordinary words in snake_case and camelCase callable names."""
    normalized = _CAMEL_BOUNDARY.sub(" ", value).replace("_", " ")
    return set(_SEARCH_TERMS.findall(normalized.casefold()))


def _success(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def _error(error: Any, *, data: Any = None) -> dict[str, Any]:
    return {"ok": False, "data": data, "error": error}


def _unknown_tool(name: str) -> dict[str, Any]:
    return _error(f"Unknown Bifrost tool: {name}")


def _normalize_result(value: Any) -> dict[str, Any]:
    """Decode only the adapter transport; business payload fields stay untouched."""
    parsed = value
    if isinstance(value, str):
        if value.startswith("Error:"):
            return _error(value, data=value)
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return _success(value)
    if isinstance(parsed, dict) and (
        parsed.get("is_error") is True or parsed.get("error") is not None
    ):
        return _error(parsed.get("error", parsed), data=parsed)
    return _success(parsed)
