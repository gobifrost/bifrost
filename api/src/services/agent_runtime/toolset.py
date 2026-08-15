"""Adapter from Bifrost's stored JSON tool contracts to Pydantic AI toolsets."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic_ai import RunContext
from pydantic_ai.tools import ToolDefinition as PydanticToolDefinition
from pydantic_ai.toolsets import AbstractToolset
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_core import SchemaValidator, core_schema

from src.services.agent_runtime.errors import AgentRunCancelled
from src.services.llm.base import ToolDefinition

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

_ARGS_VALIDATOR = SchemaValidator(schema=core_schema.any_schema())


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
        del tool
        import time

        started = time.monotonic()
        if self._event_handler:
            await self._event_handler(
                ToolEvent(type="tool_call", tool_name=name, arguments=tool_args)
            )
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
        return result
