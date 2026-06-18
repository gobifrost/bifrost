"""Unit tests for M6 delegation reconstruction on the GET messages path.

The "✓ consulted <agent>" badge is set live from delegation chunks; on reload
the badge must be rebuilt from the persisted delegate_to_* tool_call message so
it doesn't vanish. This covers that reconstruction.
"""

from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from src.models.orm import Message
from src.routers.chat import _reconstruct_delegation


def _msg(**kw) -> Message:
    base = dict(
        id=uuid4(),
        tool_name=None,
        tool_call_id="call_1",
        tool_result=None,
        tool_input=None,
        duration_ms=None,
    )
    base.update(kw)
    # The helper only reads duck-typed attributes; a SimpleNamespace stand-in
    # avoids constructing a full ORM row.
    return cast(Message, SimpleNamespace(**base))


def test_non_delegation_message_returns_none():
    assert _reconstruct_delegation(_msg(tool_name="halopsa_list_tickets")) is None
    assert _reconstruct_delegation(_msg(tool_name=None)) is None


def test_reconstructs_full_delegation():
    m = _msg(
        tool_name="delegate_to_weather_specialist",
        tool_call_id="call_abc",
        tool_input={"task": "weather in Paris?"},
        tool_result={"agent": "Weather Specialist", "response": "Mild, 17C."},
        duration_ms=5437,
    )
    d = _reconstruct_delegation(m)
    assert d is not None
    assert d.tool_call_id == "call_abc"
    assert d.agent_name == "Weather Specialist"
    assert d.task == "weather in Paris?"
    assert d.response == "Mild, 17C."
    assert d.error is None
    assert d.duration_ms == 5437


def test_falls_back_to_tool_name_when_agent_missing():
    m = _msg(
        tool_name="delegate_to_billing_bot",
        tool_result={"response": "done"},
    )
    d = _reconstruct_delegation(m)
    assert d is not None
    assert d.agent_name == "billing_bot"


def test_surfaces_error():
    m = _msg(
        tool_name="delegate_to_x",
        tool_result={"agent": "X", "error": "boom"},
    )
    d = _reconstruct_delegation(m)
    assert d is not None
    assert d.error == "boom"
    assert d.response is None


def test_handles_non_dict_result_and_input():
    m = _msg(tool_name="delegate_to_x", tool_result="oops", tool_input="nope")
    d = _reconstruct_delegation(m)
    assert d is not None
    assert d.task == ""
    assert d.response is None
    assert d.agent_name == "x"
