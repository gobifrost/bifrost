"""Adapter from Bifrost's stored JSON tool contracts to Pydantic AI toolsets."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic_ai import CallDeferred, RunContext
from pydantic_ai.tools import ToolDefinition as PydanticToolDefinition
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_core import SchemaValidator, core_schema

from src.services.agent_runtime.errors import AgentRunCancelled
from src.services.llm.base import ToolDefinition
from src.services.tool_schema import validate_arguments_against_schema

ToolExecutor = Callable[[str, dict[str, Any], str], Awaitable[Any]]

SYNTHETIC_TOOLSET_ID = "bifrost-synthetic"
"""Toolset identity for evaluation-synthetic runs (Studio v1, never production)."""


@dataclass(frozen=True)
class ToolEvent:
    """Observable tool lifecycle event emitted without exposing model internals."""

    type: Literal["tool_call", "tool_result", "tool_error"]
    tool_name: str
    arguments: dict[str, Any]
    result: Any | None = None
    error: str | None = None
    duration_ms: int | None = None
    tool_call_id: str | None = None


ToolEventHandler = Callable[[ToolEvent], Awaitable[None]]

_ARGS_VALIDATOR = SchemaValidator(schema=core_schema.dict_schema())

MAX_MODEL_TOOL_RESULT_CHARS = 32_000
"""Maximum serialized characters one fresh tool result may add to context."""

_TOOL_RESULT_HEAD_CHARS = 16_000
_TOOL_RESULT_TAIL_CHARS = 8_000


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

        started = time.monotonic()
        if self._event_handler:
            await self._event_handler(
                ToolEvent(
                    type="tool_call", tool_name=name, arguments=tool_args,
                    tool_call_id=ctx.tool_call_id,
                )
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
                            tool_call_id=ctx.tool_call_id,
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
                            tool_call_id=ctx.tool_call_id,
                            error=message,
                            duration_ms=int((time.monotonic() - started) * 1_000),
                        )
                    )
                return message
        try:
            result = await self._executor(name, tool_args, ctx.tool_call_id or "")
        except CallDeferred:
            # A parked child/timer is control flow, not a failed tool result.
            # Its eventual return is recorded when the durable wait resolves.
            raise
        except Exception as exc:
            if self._event_handler:
                await self._event_handler(
                    ToolEvent(
                        type="tool_error",
                        tool_name=name,
                        arguments=tool_args,
                        tool_call_id=ctx.tool_call_id,
                        error=str(exc),
                        duration_ms=int((time.monotonic() - started) * 1_000),
                    )
                )
            if isinstance(exc, AgentRunCancelled):
                raise
            return f"Error: {exc}"

        if self._event_handler:
            await self._event_handler(
                ToolEvent(
                    type="tool_result",
                    tool_name=name,
                    arguments=tool_args,
                    tool_call_id=ctx.tool_call_id,
                    result=result,
                    duration_ms=int((time.monotonic() - started) * 1_000),
                )
            )
        return bound_tool_result_for_model(result)


def build_simulator_toolset(
    definitions: Sequence[ToolDefinition],
    simulator_call: Callable[[str, dict[str, Any]], Any],
    *,
    event_handler: ToolEventHandler | None = None,
) -> BifrostToolset:
    """Documented test-run entry point: simulator-backed engine toolset.

    Only the Evaluation service calls this. Every non-engine tool the model
    invokes is routed to the case simulator; the executor has no path to
    real workflow, MCP, or system-tool dispatch.
    """

    async def _executor(name: str, tool_args: dict[str, Any], tool_call_id: str) -> Any:
        del tool_call_id
        return simulator_call(name, tool_args)

    return BifrostToolset(
        definitions,
        _executor,
        event_handler=event_handler,
        toolset_id=SYNTHETIC_TOOLSET_ID,
    )
