import json

from openai.types.chat import ChatCompletion
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk

from scripts.scheduler_fixture_server import (
    chat_completion_payload,
    chat_completion_stream_events,
    encode_sse_events,
)


def test_chat_completion_non_stream_contract_remains_openai_compatible_json() -> None:
    payload = chat_completion_payload({"model": "fixture-chat"})

    assert ChatCompletion.model_validate(payload).choices[0].message.content == "ok"
    assert payload == {
        "id": "chatcmpl-fixture",
        "object": "chat.completion",
        "created": 0,
        "model": "fixture-chat",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
    }


def test_chat_completion_stream_contract_is_openai_compatible_sse() -> None:
    events = chat_completion_stream_events({"model": "fixture-chat", "stream": True})
    encoded = encode_sse_events(events).decode()
    frames = [frame for frame in encoded.split("\n\n") if frame]

    assert frames[-1] == "data: [DONE]"
    chunks = [json.loads(frame.removeprefix("data: ")) for frame in frames[:-1]]
    validated = [ChatCompletionChunk.model_validate(chunk) for chunk in chunks]
    assert [chunk.object for chunk in validated] == [
        "chat.completion.chunk",
        "chat.completion.chunk",
        "chat.completion.chunk",
    ]
    assert chunks[0]["choices"][0] == {
        "index": 0,
        "delta": {"role": "assistant", "content": ""},
        "finish_reason": None,
    }
    assert chunks[1]["choices"][0] == {
        "index": 0,
        "delta": {"content": "ok"},
        "finish_reason": None,
    }
    assert chunks[2]["choices"][0] == {
        "index": 0,
        "delta": {},
        "finish_reason": "stop",
    }
    assert chunks[2]["usage"] == {
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "total_tokens": 2,
    }


def test_missing_usage_model_omits_usage_only_for_its_own_reply() -> None:
    assert "usage" not in chat_completion_payload({"model": "fixture-missing-usage"})
    assert chat_completion_payload({"model": "fixture-chat"})["usage"] == {
        "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
    }


def test_missing_usage_model_omits_usage_from_stream_chunks() -> None:
    events = chat_completion_stream_events({"model": "fixture-missing-usage", "stream": True})
    chunks = [event for event in events if isinstance(event, dict)]
    assert chunks
    assert all("usage" not in chunk for chunk in chunks)
    assert all(ChatCompletionChunk.model_validate(chunk).usage is None for chunk in chunks)
