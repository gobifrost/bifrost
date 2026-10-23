"""Adapter from Bifrost's stored JSON tool contracts to Pydantic AI toolsets."""

from collections.abc import Awaitable, Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition as PydanticToolDefinition
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
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


class BifrostToolset(AbstractToolset[object]):
    """Expose already-resolved Bifrost tools without regenerating their schemas."""

    def __init__(
        self,
        definitions: Sequence[ToolDefinition],
        executor: ToolExecutor,
        *,
        event_handler: ToolEventHandler | None = None,
        toolset_id: str = "bifrost",
    ) -> None:
        self._definitions = tuple(definitions)
        self._executor = executor
        self._event_handler = event_handler
        self._id = toolset_id

    @property
    def id(self) -> str:
        return self._id

    async def get_tools(self, ctx: RunContext[object]) -> dict[str, ToolsetTool[object]]:
        del ctx
        return {
            definition.name: ToolsetTool(
                toolset=self,
                tool_def=PydanticToolDefinition(
                    name=definition.name,
                    description=definition.description,
                    parameters_json_schema=definition.parameters,
                    return_schema=definition.return_schema,
                    include_return_schema=definition.return_schema is not None,
                    sequential=True,
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
            return result
        return bound_tool_result_for_model(result)
