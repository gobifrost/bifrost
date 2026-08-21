from types import SimpleNamespace
from uuid import uuid4

from src.services.agent_runtime.history import build_runtime_message_history
from src.services.llm.base import LLMInputFile


def _message(role: str, sequence: int, **values):
    return SimpleNamespace(
        id=values.pop("id", uuid4()),
        role=role,
        content=values.pop("content", None),
        sequence=sequence,
        tool_calls=values.pop("tool_calls", None),
        tool_call_id=values.pop("tool_call_id", None),
        tool_name=values.pop("tool_name", None),
        tool_input=values.pop("tool_input", None),
        **values,
    )


def test_runtime_history_replays_attachments_and_repairs_interleaved_tools():
    user_id = uuid4()
    records = [
        _message(
            "assistant",
            1,
            tool_calls=[{"id": "call-1", "name": "read_file", "arguments": {}}],
        ),
        _message("user", 2, id=user_id, content="Please continue"),
        _message(
            "tool",
            3,
            content="contents",
            tool_call_id="call-1",
            tool_name="read_file",
        ),
    ]
    attachment = LLMInputFile(
        filename="diagram.png",
        media_type="image/png",
        data=b"png",
    )

    history = build_runtime_message_history(
        system_prompt="System",
        persisted_messages=records,
        user_inputs={user_id: ("Please continue", [attachment])},
    )

    assert [message.role for message in history] == [
        "system",
        "assistant",
        "tool",
        "user",
    ]
    assert history[-1].input_files == [attachment]


def test_runtime_history_remaps_reused_ids_and_repairs_dangling_calls():
    records = [
        _message(
            "assistant",
            1,
            tool_calls=[{"id": "same", "name": "first", "arguments": {}}],
        ),
        _message(
            "tool",
            2,
            content="first result",
            tool_call_id="same",
            tool_name="first",
        ),
        _message(
            "tool_call",
            3,
            tool_call_id="same",
            tool_name="second",
            tool_input={"path": "x"},
        ),
    ]

    history = build_runtime_message_history(
        system_prompt="System",
        persisted_messages=records,
    )

    assert history[1].tool_calls is not None
    assert [call.id for call in history[1].tool_calls] == ["same", "same_t2"]
    assert history[-1].role == "tool"
    assert history[-1].tool_call_id == "same_t2"
    assert history[-1].content == "[Tool execution was interrupted]"
