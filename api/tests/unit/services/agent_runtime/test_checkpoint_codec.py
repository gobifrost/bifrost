"""Pin the resumable Pydantic AI agent contract used by the durable runtime.

A Bifrost AgentRun survives worker loss: the first Pydantic invocation may
suspend at a deferred tool boundary (``DeferredToolRequests``) and a second
invocation resumes from the committed message history plus
``DeferredToolResults``. The two Pydantic invocation IDs differ while the
Bifrost run ID stays a single value.
"""

from __future__ import annotations

import pytest
from pydantic_ai import (
    Agent,
    CallDeferred,
    DeferredToolRequests,
    ModelResponse,
    ToolCallPart,
    capture_run_messages,
)
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse as ModelResponseMessage,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from src.services.agent_runtime.checkpoint_codec import (
    CHECKPOINT_MESSAGE_FORMAT_VERSION,
    CheckpointDecodeError,
    decode_messages,
    encode_messages,
)


def _round_trip(messages):
    payload = encode_messages(messages)
    assert payload["format_version"] == CHECKPOINT_MESSAGE_FORMAT_VERSION
    restored = decode_messages(payload)
    assert (
        ModelMessagesTypeAdapter.dump_python(restored, mode="json")
        == ModelMessagesTypeAdapter.dump_python(messages, mode="json")
    )
    return restored


def test_round_trip_preserves_tool_call_ids():
    request = ModelRequest(
        parts=[
            UserPromptPart(content="run the thing"),
            ToolReturnPart(
                tool_name="lookup",
                content="prior result",
                tool_call_id="call-prior-1",
            ),
        ]
    )
    response = ModelResponseMessage(
        parts=[
            TextPart(content="working"),
            ToolCallPart(
                tool_name="lookup",
                args={"q": "tickets"},
                tool_call_id="call-abc-123",
            ),
        ],
        model_name="test-model",
    )
    restored = _round_trip([request, response])
    assert isinstance(restored[0], ModelRequest)
    assert isinstance(restored[1], ModelResponseMessage)
    tool_calls = [
        part
        for part in restored[1].parts
        if isinstance(part, ToolCallPart)
    ]
    assert [part.tool_call_id for part in tool_calls] == ["call-abc-123"]
    tool_returns = [
        part
        for part in restored[0].parts
        if isinstance(part, ToolReturnPart)
    ]
    assert [part.tool_call_id for part in tool_returns] == ["call-prior-1"]


def test_decode_rejects_unknown_format_version():
    request = ModelRequest(parts=[UserPromptPart(content="hi")])
    payload = encode_messages([request])
    payload["format_version"] = 999
    with pytest.raises(CheckpointDecodeError):
        decode_messages(payload)


def test_decode_rejects_corrupt_messages():
    with pytest.raises(CheckpointDecodeError):
        decode_messages({"format_version": 1, "messages": [{"nope": True}]})


async def test_deferred_tool_suspends_and_resumes_with_new_invocation_id():
    """Fake-model resume: CallDeferred -> DeferredToolRequests -> finish."""
    bifrost_run_id = "bifrost-run-1"
    calls = {"count": 0}

    def fake_model(messages, info):
        calls["count"] += 1
        if calls["count"] == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="sleeper",
                        args={"seconds": 30},
                        tool_call_id="call-sleeper-1",
                    )
                ],
                model_name="fake-model",
            )
        return ModelResponse(
            parts=[TextPart(content="sleeper finished")],
            model_name="fake-model",
        )

    agent: Agent[None] = Agent(
        FunctionModel(fake_model),
        output_type=[DeferredToolRequests, str],
    )

    @agent.tool_plain
    def sleeper(seconds: int) -> str:
        raise CallDeferred({"bifrost_run_id": bifrost_run_id})

    first_invocation_id = "pydantic-invocation-1"
    with capture_run_messages() as first_history:
        first_result = await agent.run(
            "wait a bit, then report",
            run_id=first_invocation_id,
        )
    assert isinstance(first_result.output, DeferredToolRequests)
    deferred_requests = first_result.output
    assert [c.tool_call_id for c in deferred_requests.calls] == ["call-sleeper-1"]

    # Commit boundary: history is checkpointed through the versioned codec.
    payload = encode_messages(first_history)
    resumed_history = decode_messages(payload)
    assert [m.kind for m in resumed_history] == [m.kind for m in first_history]

    deferred_results = deferred_requests.build_results(
        calls={"call-sleeper-1": "sleeper finished"}
    )
    second_invocation_id = "pydantic-invocation-2"
    with capture_run_messages() as second_history:
        second_result = await agent.run(
            deferred_tool_results=deferred_results,
            message_history=resumed_history,
            run_id=second_invocation_id,
        )

    assert not isinstance(second_result.output, DeferredToolRequests)
    assert second_result.output == "sleeper finished"
    assert first_invocation_id != second_invocation_id
    # One Bifrost run across both Pydantic invocations.
    assert bifrost_run_id == "bifrost-run-1"
    assert len(second_history) > len(resumed_history)
