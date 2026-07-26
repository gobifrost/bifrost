"""Unit tests for the builder's internal agent loop.

The LLM is replaced by a scripted fake implementing the same ``complete``
signature as :class:`BaseLLMClient`, so these tests assert what the loop does
with a given sequence of model responses — workspace mutations, accounting, and
how failures are surfaced back to the model.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from src.services.builder.agent_runtime import (
    BUILDER_SYSTEM_PROMPT,
    BUILDER_TOOLS,
    InternalLoopRuntime,
)
from src.services.builder.fs_tools import WorkspaceLimits, WorkspaceRoot
from src.services.llm.base import (
    BaseLLMClient,
    LLMConfig,
    LLMMessage,
    LLMResponse,
    LLMStreamChunk,
    ToolCallRequest,
    ToolDefinition,
)


class ScriptedLLMClient(BaseLLMClient):
    """Returns a pre-set list of responses, one per ``complete`` call."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(
            LLMConfig(provider="openai", model="test-model", api_key="unused")
        )
        self._responses = list(responses)
        self.calls: list[list[LLMMessage]] = []

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        # Copy: the loop keeps mutating the same list after we return.
        self.calls.append(list(messages))
        if not self._responses:
            raise AssertionError("scripted client ran out of responses")
        return self._responses.pop(0)

    def stream(
        self,
        messages: list[LLMMessage],
        tools: list[ToolDefinition] | None = None,
        *,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        raise NotImplementedError("the builder loop is non-streaming")

    @property
    def provider_name(self) -> str:
        return "scripted"


def _call(call_id: str, name: str, **arguments: object) -> ToolCallRequest:
    return ToolCallRequest(id=call_id, name=name, arguments=dict(arguments))


def _tool_turn(*calls: ToolCallRequest, content: str | None = None) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=list(calls),
        input_tokens=10,
        output_tokens=5,
    )


def _final(text: str) -> LLMResponse:
    return LLMResponse(content=text, input_tokens=10, output_tokens=5)


@pytest.fixture
def workspace(tmp_path: Path) -> WorkspaceRoot:
    """A workspace holding a minimal valid Solution descriptor."""
    (tmp_path / "bifrost.solution.yaml").write_text(
        "slug: demo\nname: Demo Solution\n"
    )
    return WorkspaceRoot(tmp_path, WorkspaceLimits())


def _tool_results(messages: list[LLMMessage]) -> list[str]:
    return [m.content or "" for m in messages if m.role == "tool"]


@pytest.mark.asyncio
async def test_full_loop_lists_writes_patches_validates_then_finishes(
    workspace: WorkspaceRoot,
) -> None:
    client = ScriptedLLMClient(
        [
            _tool_turn(_call("c1", "list_files")),
            _tool_turn(
                _call(
                    "c2",
                    "write_file",
                    path="workflows/greet.py",
                    content="def greet():\n    return 'hello'\n",
                )
            ),
            _tool_turn(
                _call(
                    "c3",
                    "apply_patch",
                    path="workflows/greet.py",
                    old_string="'hello'",
                    new_string="'hello world'",
                )
            ),
            _tool_turn(_call("c4", "validate_solution")),
            _final("Added a greeting workflow."),
        ]
    )
    runtime = InternalLoopRuntime(client)

    result = await runtime.run_turn(
        BUILDER_SYSTEM_PROMPT, "Add a greeting workflow.", workspace
    )

    assert (workspace.root / "workflows" / "greet.py").read_text() == (
        "def greet():\n    return 'hello world'\n"
    )
    assert result.final_text == "Added a greeting workflow."
    assert result.stopped_reason == "done"
    assert result.tool_call_count == 4
    assert result.iterations == 5
    assert result.input_tokens == 50
    assert result.output_tokens == 25

    results = _tool_results(client.calls[-1])
    assert "bifrost.solution.yaml" in results[0]
    assert results[1] == "Wrote workflows/greet.py."
    assert results[2] == "Patched workflows/greet.py (1 replacement)."
    assert results[3].startswith("Valid: Solution 'demo' (Demo Solution)")

    # Every call is recorded, in execution order, and each one succeeded.
    assert [record.name for record in result.tool_calls] == [
        "list_files",
        "write_file",
        "apply_patch",
        "validate_solution",
    ]
    assert all(record.ok for record in result.tool_calls)
    assert [record.result for record in result.tool_calls] == results


@pytest.mark.asyncio
async def test_first_message_is_system_prompt_then_history_then_user(
    workspace: WorkspaceRoot,
) -> None:
    client = ScriptedLLMClient([_final("done")])
    runtime = InternalLoopRuntime(client)

    await runtime.run_turn(
        BUILDER_SYSTEM_PROMPT,
        "Now add a table.",
        workspace,
        history=[
            {"role": "user", "content": "Add a workflow."},
            {"role": "assistant", "content": "Added it."},
        ],
    )

    sent = client.calls[0]
    assert [m.role for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[0].content == BUILDER_SYSTEM_PROMPT
    assert sent[-1].content == "Now add a table."


@pytest.mark.asyncio
async def test_tools_are_offered_to_the_model(workspace: WorkspaceRoot) -> None:
    names = {tool.name for tool in BUILDER_TOOLS}
    assert names == {
        "list_files",
        "read_file",
        "search_text",
        "write_file",
        "apply_patch",
        "delete_file",
        "make_directory",
        "validate_solution",
    }
    # No build/shell/network tool ever reaches the model (spec: Model tools).
    assert "request_build" not in names


@pytest.mark.asyncio
async def test_max_iterations_stops_the_loop(workspace: WorkspaceRoot) -> None:
    # A model that never stops calling tools.
    client = ScriptedLLMClient([_tool_turn(_call("c", "list_files")) for _ in range(10)])
    runtime = InternalLoopRuntime(client)

    result = await runtime.run_turn(
        BUILDER_SYSTEM_PROMPT, "Loop forever.", workspace, max_iterations=3
    )

    assert result.stopped_reason == "max_iterations"
    assert result.iterations == 3
    assert result.tool_call_count == 3


@pytest.mark.asyncio
async def test_workspace_violation_is_a_tool_error_and_the_loop_continues(
    workspace: WorkspaceRoot,
) -> None:
    client = ScriptedLLMClient(
        [
            _tool_turn(_call("c1", "read_file", path="../../etc/passwd")),
            _tool_turn(_call("c2", "read_file", path="bifrost.solution.yaml")),
            _final("Recovered."),
        ]
    )
    runtime = InternalLoopRuntime(client)

    result = await runtime.run_turn(BUILDER_SYSTEM_PROMPT, "Read something.", workspace)

    results = _tool_results(client.calls[-1])
    assert results[0] == "Error: path contains '..'"
    assert "slug: demo" in results[1]
    assert result.stopped_reason == "done"
    assert result.tool_call_count == 2

    # The refused call is recorded as failed; the recovery alongside it is not.
    refused, recovered = result.tool_calls
    assert (refused.name, refused.ok) == ("read_file", False)
    assert refused.result == "Error: path contains '..'"
    assert refused.arguments == {"path": "../../etc/passwd"}
    assert (recovered.name, recovered.ok) == ("read_file", True)


@pytest.mark.asyncio
async def test_unknown_tool_name_is_a_tool_error_and_the_loop_continues(
    workspace: WorkspaceRoot,
) -> None:
    client = ScriptedLLMClient(
        [
            _tool_turn(_call("c1", "run_shell", command="rm -rf /")),
            _final("Understood, no shell."),
        ]
    )
    runtime = InternalLoopRuntime(client)

    result = await runtime.run_turn(BUILDER_SYSTEM_PROMPT, "Run a shell.", workspace)

    assert _tool_results(client.calls[-1]) == ["Error: unknown tool 'run_shell'."]
    assert result.stopped_reason == "done"
    assert result.final_text == "Understood, no shell."


@pytest.mark.asyncio
async def test_bad_tool_arguments_are_a_tool_error(workspace: WorkspaceRoot) -> None:
    client = ScriptedLLMClient(
        [
            _tool_turn(_call("c1", "write_file", path="a.txt")),
            _final("Fixed."),
        ]
    )
    runtime = InternalLoopRuntime(client)

    await runtime.run_turn(BUILDER_SYSTEM_PROMPT, "Write a file.", workspace)

    assert _tool_results(client.calls[-1]) == [
        "Error: invalid arguments for write_file: 'content' must be a string"
    ]


@pytest.mark.asyncio
async def test_apply_patch_requires_a_unique_match_unless_replace_all(
    workspace: WorkspaceRoot,
) -> None:
    workspace.write_file("dup.txt", b"x\nx\n")
    client = ScriptedLLMClient(
        [
            _tool_turn(_call("c1", "apply_patch", path="dup.txt", old_string="x", new_string="y")),
            _tool_turn(
                _call(
                    "c2",
                    "apply_patch",
                    path="dup.txt",
                    old_string="x",
                    new_string="y",
                    replace_all=True,
                )
            ),
            _final("Done."),
        ]
    )
    runtime = InternalLoopRuntime(client)

    await runtime.run_turn(BUILDER_SYSTEM_PROMPT, "Patch it.", workspace)

    results = _tool_results(client.calls[-1])
    assert results[0] == (
        "Error: old_string matches 2 times; make it unique or pass replace_all"
    )
    assert results[1] == "Patched dup.txt (2 replacements)."
    assert (workspace.root / "dup.txt").read_text() == "y\ny\n"


@pytest.mark.asyncio
async def test_validate_solution_reports_a_missing_descriptor(tmp_path: Path) -> None:
    empty = WorkspaceRoot(tmp_path, WorkspaceLimits())
    client = ScriptedLLMClient(
        [_tool_turn(_call("c1", "validate_solution")), _final("Needs a descriptor.")]
    )
    runtime = InternalLoopRuntime(client)

    await runtime.run_turn(BUILDER_SYSTEM_PROMPT, "Validate.", empty)

    assert _tool_results(client.calls[-1]) == [
        "Invalid: workspace has no bifrost.solution.yaml at its root."
    ]


@pytest.mark.asyncio
async def test_validate_solution_reports_a_malformed_descriptor(
    tmp_path: Path,
) -> None:
    (tmp_path / "bifrost.solution.yaml").write_text("name: No Slug Here\n")
    root = WorkspaceRoot(tmp_path, WorkspaceLimits())
    client = ScriptedLLMClient(
        [_tool_turn(_call("c1", "validate_solution")), _final("Fixing.")]
    )
    runtime = InternalLoopRuntime(client)

    await runtime.run_turn(BUILDER_SYSTEM_PROMPT, "Validate.", root)

    result = _tool_results(client.calls[-1])[0]
    assert result.startswith("Invalid: bifrost.solution.yaml failed validation:")
    assert "slug" in result


@pytest.mark.asyncio
async def test_tool_results_are_capped(tmp_path: Path) -> None:
    """A result larger than the cap is truncated before it enters the context."""
    root = WorkspaceRoot(tmp_path, WorkspaceLimits(max_read_bytes=512 * 1024))
    root.write_file("big.txt", b"a" * (200 * 1024))
    client = ScriptedLLMClient(
        [_tool_turn(_call("c1", "read_file", path="big.txt")), _final("Read it.")]
    )
    runtime = InternalLoopRuntime(client)

    await runtime.run_turn(BUILDER_SYSTEM_PROMPT, "Read it.", root)

    assert len(_tool_results(client.calls[-1])[0]) == 64 * 1024


@pytest.mark.asyncio
async def test_parallel_tool_calls_in_one_iteration_all_execute(
    workspace: WorkspaceRoot,
) -> None:
    client = ScriptedLLMClient(
        [
            _tool_turn(
                _call("c1", "write_file", path="a.txt", content="A"),
                _call("c2", "write_file", path="b.txt", content="B"),
            ),
            _final("Wrote both."),
        ]
    )
    runtime = InternalLoopRuntime(client)

    result = await runtime.run_turn(BUILDER_SYSTEM_PROMPT, "Write two files.", workspace)

    assert (workspace.root / "a.txt").read_text() == "A"
    assert (workspace.root / "b.txt").read_text() == "B"
    assert result.tool_call_count == 2
    assert result.iterations == 2
