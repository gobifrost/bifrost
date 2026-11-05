#!/usr/bin/env python3
"""Run the credential-free native CodeMode proof against BifrostToolset.

Run this script in the API image. It makes no network requests and uses no
credentials, mounts, OS handler, or workflow execution route.
"""

from __future__ import annotations

import asyncio
import json
import sys
from importlib.metadata import version
from time import perf_counter
from typing import Any

import pydantic_core
import psutil
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.services.agent_runtime.code_mode import build_code_mode_capabilities
from src.services.agent_runtime.toolset import BifrostToolset, MAX_MODEL_TOOL_RESULT_CHARS
from src.services.llm import ToolDefinition

CHUNK = "x" * (MAX_MODEL_TOOL_RESULT_CHARS + 1)


def _tool_result_bytes(messages: list[ModelMessage]) -> int:
    """Count the exact serialized tool-return content given to the FunctionModel."""
    return sum(
        len(
            (
                part.content
                if isinstance(part.content, str)
                else pydantic_core.to_json(part.content, fallback=str).decode()
            ).encode()
        )
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    )


async def _run(*, code_mode: bool) -> dict[str, Any]:
    calls: list[int] = []
    seen: list[list[ModelMessage]] = []
    process = psutil.Process()
    max_child_count = 0
    max_child_rss_bytes = 0

    async def execute(name: str, arguments: dict[str, Any], tool_call_id: str) -> Any:
        nonlocal max_child_count, max_child_rss_bytes
        assert name == "read_chunk"
        assert tool_call_id
        children = process.children(recursive=True)
        max_child_count = max(max_child_count, len(children))
        for child in children:
            try:
                max_child_rss_bytes = max(max_child_rss_bytes, child.memory_info().rss)
            except psutil.Error:
                continue
        calls.append(arguments["index"])
        return {"index": arguments["index"], "payload": CHUNK}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        del info
        seen.append(messages)
        if len(seen) == 1 and code_mode:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_code",
                        {
                            "code": (
                                "chunks = []\n"
                                "for index in range(3):\n"
                                "    chunks.append(read_chunk(index=index))\n"
                                "combined = ''.join(item['payload'] for item in chunks)\n"
                                "{'combined_length': len(combined)}"
                            )
                        },
                    )
                ]
            )
        if len(seen) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart("read_chunk", {"index": index}) for index in range(3)
                ]
            )
        return ModelResponse(parts=[TextPart("complete")])

    definition = ToolDefinition(
        name="read_chunk",
        description="Return one payload chunk.",
        parameters={
            "type": "object",
            "properties": {"index": {"type": "integer"}},
            "required": ["index"],
        },
    )
    runtime = Agent(
        FunctionModel(respond),
        toolsets=[BifrostToolset([definition], execute)],
        capabilities=build_code_mode_capabilities(tools=["read_chunk"]) if code_mode else (),
    )
    started = perf_counter()
    result = await runtime.run("Aggregate three payload chunks.")
    elapsed_ms = round((perf_counter() - started) * 1_000, 2)
    contents = _tool_return_contents(seen[-1])
    children_after_run_count = len(process.children(recursive=True))
    return {
        "model_requests": len(seen),
        "model_visible_tool_result_bytes": _tool_result_bytes(seen[-1]),
        "nested_call_count": len(calls),
        "calls": calls,
        "max_child_count": max_child_count,
        "max_child_rss_bytes": max_child_rss_bytes,
        "children_after_run_count": children_after_run_count,
        "full_aggregate_correct": (
            {"combined_length": len(CHUNK) * 3} in contents if code_mode else None
        ),
        "final_output": result.output,
        "elapsed_ms": elapsed_ms,
    }


def _tool_return_contents(messages: list[ModelMessage]) -> list[Any]:
    return [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


async def main() -> int:
    direct = await _run(code_mode=False)
    sandboxed = await _run(code_mode=True)
    output = {
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "pydantic_ai_harness_version": version("pydantic-ai-harness"),
        "pydantic_monty_version": version("pydantic-monty"),
        "direct": direct,
        "code_mode": sandboxed,
        "passed": (
            direct["calls"] == [0, 1, 2]
            and sandboxed["calls"] == [0, 1, 2]
            and sandboxed["full_aggregate_correct"]
            and sandboxed["children_after_run_count"] == 0
            and sandboxed["model_visible_tool_result_bytes"]
            <= MAX_MODEL_TOOL_RESULT_CHARS
        ),
    }
    print(json.dumps(output, sort_keys=True))
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
