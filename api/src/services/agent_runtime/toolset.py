"""Adapter from Bifrost's stored JSON tool contracts to Pydantic AI toolsets."""

import json
import keyword
import re
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition as PydanticToolDefinition
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai_harness.code_mode import CodeMode
from pydantic_core import SchemaValidator, core_schema

from src.services.agent_runtime.errors import AgentRunCancelled
from src.services.llm.base import ToolDefinition
from src.services.tool_schema import validate_arguments_against_schema

ToolExecutor = Callable[[str, dict[str, Any], str], Awaitable[Any]]


@dataclass(frozen=True)
class ToolEvent:
    """Observable tool lifecycle event emitted without exposing model internals."""

    type: Literal["tool_call", "tool_result", "tool_error"]
    tool_name: str
    arguments: dict[str, Any]
    result: Any | None = None
    error: str | None = None
    duration_ms: int | None = None


ToolEventHandler = Callable[[ToolEvent], Awaitable[None]]

_ARGS_VALIDATOR = SchemaValidator(schema=core_schema.dict_schema())

MAX_MODEL_TOOL_RESULT_CHARS = 32_000
"""Maximum serialized characters one fresh tool result may add to context."""

_TOOL_RESULT_HEAD_CHARS = 16_000
_TOOL_RESULT_TAIL_CHARS = 8_000
_INVALID_PYTHON_IDENTIFIER = re.compile(r"[^0-9A-Za-z_]")
_RESERVED_CODE_MODE_NAMES = frozenset({"run_code", "search_tools"})
_MAX_CODE_MODE_NAME_LENGTH = 64
_CODE_MODE_DIRECT_RETURN_TYPES = frozenset(
    {"object", "array", "number", "integer", "boolean", "null"}
)
_RETURN_SCHEMA_COMBINATORS = frozenset({"allOf", "anyOf", "oneOf", "not"})


@dataclass
class CodeModeToolResultScope:
    """State shared by one outer CodeMode call and its nested dispatches."""

    cancellation: AgentRunCancelled | None = None
    outer_tool_call_id: str | None = None


_CODE_MODE_TOOL_RESULT_SCOPE: ContextVar[CodeModeToolResultScope | None] = ContextVar(
    "code_mode_tool_result_scope", default=None
)


def get_code_mode_tool_result_scope() -> CodeModeToolResultScope | None:
    """Return the current outer CodeMode scope, if this is a nested dispatch."""
    return _CODE_MODE_TOOL_RESULT_SCOPE.get()


@contextmanager
def unbounded_model_tool_results() -> Iterator[CodeModeToolResultScope]:
    """Keep nested values intact while an outer CodeMode result remains bounded."""
    scope = CodeModeToolResultScope()
    token = _CODE_MODE_TOOL_RESULT_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _CODE_MODE_TOOL_RESULT_SCOPE.reset(token)


def bound_tool_result_for_model(value: Any) -> Any:
    """Bound a tool result without adding a model-visible recovery protocol.

    The complete result remains available to Bifrost's execution/event storage;
    only the copy entering LLM history is shortened. Keeping both ends preserves
    headers and terminal errors while the marker makes the loss explicit.
    """

    if isinstance(value, str):
        serialized = value
    else:
        import pydantic_core

        serialized = pydantic_core.to_json(value, fallback=str).decode()
    if len(serialized) <= MAX_MODEL_TOOL_RESULT_CHARS:
        return value
    removed = len(serialized) - _TOOL_RESULT_HEAD_CHARS - _TOOL_RESULT_TAIL_CHARS
    marker = (
        f"\n\n[tool result truncated: {removed} of {len(serialized)} characters omitted "
        "from model context]\n\n"
    )
    return (
        serialized[:_TOOL_RESULT_HEAD_CHARS]
        + marker
        + serialized[-_TOOL_RESULT_TAIL_CHARS:]
    )


def code_mode_safe_name_map(names: Sequence[str]) -> dict[str, str]:
    """Map each tool's Python-safe presentation name back to its source name."""
    candidates = {name: _code_mode_safe_name(name) for name in names}
    grouped: dict[str, list[str]] = {}
    for name, candidate in candidates.items():
        grouped.setdefault(candidate, []).append(name)

    reserved = set(candidates.values())
    used: set[str] = set()
    name_map: dict[str, str] = {}
    for name in names:
        candidate = candidates[name]
        renamed = candidate
        if len(grouped[candidate]) > 1:
            renamed = _code_mode_name_with_suffix(candidate, sha256(name.encode()).hexdigest()[:10])
        suffix = 2
        while renamed in used or (renamed in reserved and renamed != candidate):
            renamed = _code_mode_name_with_suffix(candidate, f"{sha256(name.encode()).hexdigest()[:10]}_{suffix}")
            suffix += 1
        used.add(renamed)
        if renamed != name:
            name_map[renamed] = name
    return name_map


def _code_mode_name_with_suffix(name: str, suffix: str) -> str:
    return f"{name[:_MAX_CODE_MODE_NAME_LENGTH - len(suffix) - 1]}_{suffix}"


def _code_mode_safe_name(name: str) -> str:
    normalized = _INVALID_PYTHON_IDENTIFIER.sub("_", name)
    if not normalized:
        normalized = "tool"
    if normalized[0].isdigit() or keyword.iskeyword(normalized):
        normalized = f"bifrost_{normalized}"
    if normalized in _RESERVED_CODE_MODE_NAMES:
        normalized = f"bifrost_{normalized}"
    if len(normalized) > _MAX_CODE_MODE_NAME_LENGTH:
        normalized = _code_mode_name_with_suffix(normalized, sha256(name.encode()).hexdigest()[:10])
    return normalized


class BifrostToolset(AbstractToolset[object]):
    """Expose already-resolved Bifrost tools without regenerating their schemas."""

    def __init__(
        self,
        definitions: Sequence[ToolDefinition],
        executor: ToolExecutor,
        *,
        event_handler: ToolEventHandler | None = None,
        toolset_id: str = "bifrost",
        tool_metadata: Mapping[str, dict[str, Any]] | None = None,
    ) -> None:
        self._definitions = tuple(definitions)
        self._executor = executor
        self._event_handler = event_handler
        self._id = toolset_id
        self._tool_metadata = {
            name: dict(metadata) for name, metadata in (tool_metadata or {}).items()
        }

    @property
    def id(self) -> str:
        return self._id

    async def get_tools(self, ctx: RunContext[object]) -> dict[str, ToolsetTool[object]]:
        code_mode_visible = any(
            isinstance(capability, CodeMode) for capability in ctx.capabilities.values()
        )
        return {
            definition.name: ToolsetTool(
                toolset=self,
                tool_def=PydanticToolDefinition(
                    name=definition.name,
                    description=_code_mode_description(definition, code_mode_visible),
                    parameters_json_schema=definition.parameters,
                    return_schema=_code_mode_return_schema(
                        definition.return_schema, code_mode_visible
                    ),
                    include_return_schema=_code_mode_return_schema(
                        definition.return_schema, code_mode_visible
                    ) is not None,
                    sequential=True,
                    metadata=self._tool_metadata.get(definition.name, {}),
                ),
                max_retries=0,
                args_validator=_ARGS_VALIDATOR,
            )
            for definition in self._definitions
        }

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[object],
        tool: ToolsetTool[object],
    ) -> Any:
        import time

        code_mode_scope = _CODE_MODE_TOOL_RESULT_SCOPE.get()
        if code_mode_scope and code_mode_scope.cancellation:
            raise code_mode_scope.cancellation
        started = time.monotonic()
        if self._event_handler:
            await self._event_handler(
                ToolEvent(type="tool_call", tool_name=name, arguments=tool_args)
            )
        schema = next(
            (definition.parameters for definition in self._definitions if definition.name == name),
            None,
        )
        if schema is not None:
            issues, schema_error = validate_arguments_against_schema(schema, tool_args)
            if schema_error:
                message = f"Error: The live tool schema is invalid and cannot be executed: {schema_error}"
                if self._event_handler:
                    await self._event_handler(
                        ToolEvent(
                            type="tool_error",
                            tool_name=name,
                            arguments=tool_args,
                            error=message,
                            duration_ms=int((time.monotonic() - started) * 1_000),
                        )
                    )
                return message
            if issues:
                message = (
                    "Error: Arguments do not match the live tool schema. "
                    f"Issues: {issues}"
                )
                if self._event_handler:
                    await self._event_handler(
                        ToolEvent(
                            type="tool_error",
                            tool_name=name,
                            arguments=tool_args,
                            error=message,
                            duration_ms=int((time.monotonic() - started) * 1_000),
                        )
                    )
                return message
        try:
            result = await self._executor(name, tool_args, ctx.tool_call_id or "")
        except Exception as exc:
            if self._event_handler:
                await self._event_handler(
                    ToolEvent(
                        type="tool_error",
                        tool_name=name,
                        arguments=tool_args,
                        error=str(exc),
                        duration_ms=int((time.monotonic() - started) * 1_000),
                    )
                )
            if isinstance(exc, AgentRunCancelled):
                if code_mode_scope:
                    code_mode_scope.cancellation = exc
                raise
            return f"Error: {exc}"

        if self._event_handler:
            await self._event_handler(
                ToolEvent(
                    type="tool_result",
                    tool_name=name,
                    arguments=tool_args,
                    result=result,
                    duration_ms=int((time.monotonic() - started) * 1_000),
                )
            )
        if code_mode_scope:
            return _decode_code_mode_result(result)
        return bound_tool_result_for_model(result)


def _code_mode_return_schema(
    return_schema: dict[str, Any] | None,
    code_mode_visible: bool,
) -> dict[str, Any] | None:
    """Keep only return schemas unchanged by Bifrost's JSON-or-text transport."""
    if not code_mode_visible:
        return return_schema
    if not isinstance(return_schema, dict):
        return None
    source_type = return_schema.get("type")
    if (
        not isinstance(source_type, str)
        or source_type not in _CODE_MODE_DIRECT_RETURN_TYPES
        or "$ref" in return_schema
        or _RETURN_SCHEMA_COMBINATORS & return_schema.keys()
    ):
        return None
    return return_schema


def _code_mode_description(definition: ToolDefinition, code_mode_visible: bool) -> str:
    """Keep structured-content payload documentation without inventing an envelope."""
    if not code_mode_visible or definition.structured_output_schema is None:
        return definition.description
    return (
        f"{definition.description}\n\n"
        "Successful structured_content payload schema (not the response envelope or errors):\n"
        f"{json.dumps(definition.structured_output_schema, sort_keys=True)}"
    )


def _decode_code_mode_result(value: Any) -> Any:
    """Decode Bifrost's JSON-or-text transport only for nested CodeMode calls."""
    if not isinstance(value, str) or value.startswith("Error:"):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
