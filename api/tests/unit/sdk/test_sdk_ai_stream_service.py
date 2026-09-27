"""Focused unit tests for the shared SDK AI streaming service.

Covers ``shared.sdk_ai.stream_sdk_ai`` and the thin HTTP SSE adapter in
``api/src/routers/cli.py::cli_ai_stream``:

- event order (deltas, done payload, provider error break) and usage
  recorded once on completion only;
- profile/model/max-tokens forwarding and shared input-file decoding;
- error mapping (provider error chunk, ValueError invalid input,
  provider auth text, sanitized generic failure);
- best-effort usage (redis/execution-id failures never fail the stream);
- the DB connection release boundary (session released after profile
  lookup, before provider chunks);
- cancellation (provider stream closed, no partial usage, no error
  event);
- scope stays a pre-stream HTTP error (403/422, never an SSE event);
- HTTP SSE serialization end to end (``data:`` lines + ``[DONE]``).
"""

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest


def _principal(**kwargs):
    return SimpleNamespace(
        user_id=kwargs.get("user_id", uuid4()),
        organization_id=kwargs.get("organization_id", uuid4()),
        is_superuser=kwargs.get("is_superuser", False),
        email=kwargs.get("email", "ai-stream-test@example.com"),
    )


def _delta(content):
    return SimpleNamespace(type="delta", content=content)


def _done(input_tokens=3, output_tokens=5):
    return SimpleNamespace(
        type="done",
        content=None,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=0,
        cache_write_tokens=0,
        provider_cost=None,
    )


def _provider_error(message):
    return SimpleNamespace(type="error", error=message)


class _TrackedStream:
    """Async-iterable provider stream that records ``aclose``."""

    def __init__(self, gen):
        self._gen = gen
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._gen.__anext__()

    async def aclose(self):
        self.closed = True
        await self._gen.aclose()

    async def athrow(self, *args):
        return await self._gen.athrow(*args)


class _FakeClient:
    def __init__(self, chunks=(), exc=None, order=None):
        self.provider_name = "openai"
        self.model_name = "gpt-4o"
        self.chunks = list(chunks)
        self.exc = exc
        self.order = order if order is not None else []
        self.seen = None
        self.tracked = None

    def stream(self, *, messages=None, max_tokens=None, model=None):
        self.seen = {"messages": messages, "max_tokens": max_tokens, "model": model}
        self.tracked = _TrackedStream(self._inner())
        return self.tracked

    async def _inner(self):
        self.order.append("stream")
        if self.exc is not None:
            raise self.exc
        for chunk in self.chunks:
            yield chunk


def _patch_llm(client):
    return patch("src.services.llm.get_llm_client", new=AsyncMock(return_value=client))


def _patch_usage(record=None):
    record = record if record is not None else AsyncMock()
    return (
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=AsyncMock())),
        patch("src.services.ai_usage_service.record_ai_usage", new=record),
        record,
    )


async def _collect(gen):
    return [event async for event in gen]


@pytest.mark.asyncio
async def test_stream_event_order_and_done_usage():
    from shared import sdk_ai

    client = _FakeClient(chunks=[_delta("a"), _delta("b"), _done(3, 5)])
    session = AsyncMock()
    principal = _principal()
    org_id = uuid4()
    execution_id = str(uuid4())
    redis_patch, record_patch, record = _patch_usage()

    with _patch_llm(client), redis_patch, record_patch:
        events = await _collect(
            sdk_ai.stream_sdk_ai(
                session,
                principal,
                messages=[{"role": "user", "content": "hi"}],
                execution_id=execution_id,
                resolved_org_id=str(org_id),
            )
        )

    assert events == [
        {"content": "a"},
        {"content": "b"},
        {"done": True, "input_tokens": 3, "output_tokens": 5},
    ]
    assert record.await_count == 1
    kwargs = record.await_args.kwargs
    assert kwargs["provider"] == "openai"
    assert kwargs["model"] == "gpt-4o"
    assert kwargs["input_tokens"] == 3
    assert kwargs["output_tokens"] == 5
    assert kwargs["execution_id"] == UUID(execution_id)
    assert kwargs["organization_id"] == org_id
    assert kwargs["user_id"] == principal.user_id


@pytest.mark.asyncio
async def test_stream_forwards_profile_model_max_tokens():
    from shared import sdk_ai

    client = _FakeClient(chunks=[_done()])
    session = AsyncMock()
    get_client = AsyncMock(return_value=client)
    redis_patch, record_patch, _ = _patch_usage()

    with (
        patch("src.services.llm.get_llm_client", new=get_client),
        redis_patch,
        record_patch,
    ):
        await _collect(
            sdk_ai.stream_sdk_ai(
                session,
                _principal(),
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=42,
                model="gpt-4o",
                profile="Reasoning",
            )
        )

    get_client.assert_awaited_once_with(session, profile_name="Reasoning")
    assert client.seen["max_tokens"] == 42
    assert client.seen["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_stream_releases_connection_before_chunks():
    from shared import sdk_ai

    order: list[str] = []

    async def _close():
        order.append("close")

    session = AsyncMock()
    session.close.side_effect = _close
    client = _FakeClient(chunks=[_delta("a"), _done()], order=order)
    redis_patch, record_patch, _ = _patch_usage()

    with _patch_llm(client), redis_patch, record_patch:
        await _collect(
            sdk_ai.stream_sdk_ai(
                session, _principal(), messages=[{"role": "user", "content": "hi"}]
            )
        )

    assert order == ["close", "stream"]
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_input_files_attach_to_last_user_message():
    from shared import sdk_ai

    client = _FakeClient(chunks=[_done()])
    session = AsyncMock()
    payload = base64.b64encode(b"%PDF-data").decode()
    redis_patch, record_patch, _ = _patch_usage()

    with _patch_llm(client), redis_patch, record_patch:
        await _collect(
            sdk_ai.stream_sdk_ai(
                session,
                _principal(),
                messages=[
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "ok"},
                    {"role": "user", "content": "summarize this"},
                ],
                input_files=[
                    SimpleNamespace(
                        filename="doc.pdf",
                        content_type="application/pdf",
                        data_base64=payload,
                    )
                ],
            )
        )

    sent = client.seen["messages"]
    assert sent[-1].role == "user"
    assert len(sent[-1].input_files) == 1
    assert sent[-1].input_files[0].data == b"%PDF-data"
    assert sent[0].input_files == []


@pytest.mark.asyncio
async def test_stream_missing_user_message_yields_unavailable():
    from shared import sdk_ai

    client = _FakeClient(chunks=[_done()])
    session = AsyncMock()
    redis_patch, record_patch, record = _patch_usage()

    with _patch_llm(client), redis_patch, record_patch:
        events = await _collect(
            sdk_ai.stream_sdk_ai(
                session,
                _principal(),
                messages=[{"role": "system", "content": "sys"}],
                input_files=[
                    SimpleNamespace(
                        filename="a.txt",
                        content_type="text/plain",
                        data_base64=base64.b64encode(b"x").decode(),
                    )
                ],
            )
        )

    assert events == [
        {"error": "AI stream is unavailable. See server logs for details."}
    ]
    assert record.await_count == 0


@pytest.mark.asyncio
async def test_stream_bad_base64_yields_unavailable():
    from shared import sdk_ai

    client = _FakeClient(chunks=[_done()])
    session = AsyncMock()
    redis_patch, record_patch, record = _patch_usage()

    with _patch_llm(client), redis_patch, record_patch:
        events = await _collect(
            sdk_ai.stream_sdk_ai(
                session,
                _principal(),
                messages=[{"role": "user", "content": "hi"}],
                input_files=[
                    SimpleNamespace(
                        filename="a.txt",
                        content_type="text/plain",
                        data_base64="!!!not-base64!!!",
                    )
                ],
            )
        )

    assert events == [
        {"error": "AI stream is unavailable. See server logs for details."}
    ]
    assert record.await_count == 0


@pytest.mark.asyncio
async def test_stream_provider_error_chunk_breaks_without_usage():
    from shared import sdk_ai

    client = _FakeClient(chunks=[_delta("a"), _provider_error("boom")])
    session = AsyncMock()
    redis_patch, record_patch, record = _patch_usage()

    with _patch_llm(client), redis_patch, record_patch:
        events = await _collect(
            sdk_ai.stream_sdk_ai(
                session, _principal(), messages=[{"role": "user", "content": "hi"}]
            )
        )

    assert events == [{"content": "a"}, {"error": "boom"}]
    assert client.tracked.closed is True
    assert record.await_count == 0


@pytest.mark.asyncio
async def test_stream_provider_auth_error_keeps_provider_text():
    from shared import sdk_ai

    auth_error = type("AuthenticationError", (Exception,), {"__module__": "openai"})(
        "bad key"
    )
    client = _FakeClient(exc=auth_error)
    session = AsyncMock()
    redis_patch, record_patch, record = _patch_usage()

    with _patch_llm(client), redis_patch, record_patch:
        events = await _collect(
            sdk_ai.stream_sdk_ai(
                session, _principal(), messages=[{"role": "user", "content": "hi"}]
            )
        )

    assert len(events) == 1
    assert "OpenAI API key is invalid or expired" in events[0]["error"]
    assert record.await_count == 0


@pytest.mark.asyncio
async def test_stream_generic_error_is_sanitized():
    from shared import sdk_ai

    client = _FakeClient(exc=RuntimeError("secret boom"))
    session = AsyncMock()
    redis_patch, record_patch, record = _patch_usage()

    with _patch_llm(client), redis_patch, record_patch:
        events = await _collect(
            sdk_ai.stream_sdk_ai(
                session, _principal(), messages=[{"role": "user", "content": "hi"}]
            )
        )

    assert events == [{"error": "AI stream failed. See server logs for details."}]
    assert record.await_count == 0


@pytest.mark.asyncio
async def test_stream_usage_redis_failure_never_fails_stream():
    from shared import sdk_ai

    client = _FakeClient(chunks=[_delta("a"), _done(1, 2)])
    session = AsyncMock()
    record = AsyncMock()

    with (
        _patch_llm(client),
        patch(
            "src.core.cache.get_shared_redis",
            new=AsyncMock(side_effect=RuntimeError("redis down")),
        ),
        patch("src.services.ai_usage_service.record_ai_usage", new=record),
    ):
        events = await _collect(
            sdk_ai.stream_sdk_ai(
                session, _principal(), messages=[{"role": "user", "content": "hi"}]
            )
        )

    assert events[-1] == {"done": True, "input_tokens": 1, "output_tokens": 2}
    assert record.await_count == 0


@pytest.mark.asyncio
async def test_stream_malformed_execution_id_never_fails_stream():
    from shared import sdk_ai

    client = _FakeClient(chunks=[_done()])
    session = AsyncMock()
    redis_patch, record_patch, record = _patch_usage()

    with _patch_llm(client), redis_patch, record_patch:
        events = await _collect(
            sdk_ai.stream_sdk_ai(
                session,
                _principal(),
                messages=[{"role": "user", "content": "hi"}],
                execution_id="not-a-uuid",
            )
        )

    assert events == [{"done": True, "input_tokens": 3, "output_tokens": 5}]
    assert record.await_count == 0


@pytest.mark.asyncio
async def test_stream_cancellation_closes_provider_without_usage():
    from shared import sdk_ai

    client = _FakeClient(chunks=[_delta("a"), _delta("b"), _done()])
    session = AsyncMock()
    redis_patch, record_patch, record = _patch_usage()

    with _patch_llm(client), redis_patch, record_patch:
        gen = sdk_ai.stream_sdk_ai(
            session, _principal(), messages=[{"role": "user", "content": "hi"}]
        )
        assert await gen.__anext__() == {"content": "a"}
        await gen.aclose()
        assert client.tracked.closed is True
        assert record.await_count == 0
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()


@pytest.mark.asyncio
async def test_stream_cancelled_error_propagates_without_error_event():
    from shared import sdk_ai

    client = _FakeClient(chunks=[_delta("a"), _delta("b"), _done()])
    session = AsyncMock()
    redis_patch, record_patch, record = _patch_usage()

    with _patch_llm(client), redis_patch, record_patch:
        gen = sdk_ai.stream_sdk_ai(
            session, _principal(), messages=[{"role": "user", "content": "hi"}]
        )
        assert await gen.__anext__() == {"content": "a"}
        with pytest.raises(asyncio.CancelledError):
            await gen.athrow(asyncio.CancelledError())
        assert client.tracked.closed is True
        assert record.await_count == 0


@pytest.mark.asyncio
async def test_http_stream_scope_failures_stay_http_errors():
    from fastapi import HTTPException

    from src.models.contracts.cli import CLIAICompleteRequest
    from src.routers.cli import cli_ai_stream

    caller = _principal()

    with pytest.raises(HTTPException) as exc_info:
        await cli_ai_stream(
            CLIAICompleteRequest(
                messages=[{"role": "user", "content": "hi"}], org_id="not-a-uuid"
            ),
            caller,
            AsyncMock(),
        )
    assert exc_info.value.status_code == 422

    no_provider_db = AsyncMock()
    no_provider_db.execute.return_value = SimpleNamespace(
        scalar_one_or_none=lambda: None
    )
    with pytest.raises(HTTPException) as exc_info:
        await cli_ai_stream(
            CLIAICompleteRequest(
                messages=[{"role": "user", "content": "hi"}], org_id=str(uuid4())
            ),
            caller,
            no_provider_db,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_http_stream_sse_serialization_end_to_end():
    from src.models.contracts.cli import CLIAICompleteRequest
    from src.routers.cli import cli_ai_stream

    client = _FakeClient(chunks=[_delta("hello"), _delta(" world"), _done(4, 6)])
    session = AsyncMock()
    redis_patch, record_patch, record = _patch_usage()

    with _patch_llm(client), redis_patch, record_patch:
        response = await cli_ai_stream(
            CLIAICompleteRequest(
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=16,
                model="gpt-4o",
            ),
            _principal(),
            session,
        )

        assert response.media_type == "text/event-stream"
        lines = []
        async for chunk in response.body_iterator:
            lines.extend(line for line in chunk.splitlines() if line)
    assert lines[0] == f"data: {json.dumps({'content': 'hello'})}"
    assert lines[1] == f"data: {json.dumps({'content': ' world'})}"
    assert lines[2] == (
        f"data: {json.dumps({'done': True, 'input_tokens': 4, 'output_tokens': 6})}"
    )
    assert lines[3] == "data: [DONE]"
    assert record.await_count == 1


@pytest.mark.asyncio
async def test_http_stream_ignores_requested_profile():
    """Keep the HTTP stream's historical default-profile selection."""
    from src.models.contracts.cli import CLIAICompleteRequest
    from src.routers.cli import cli_ai_stream

    client = _FakeClient(chunks=[_done()])
    session = AsyncMock()
    get_client = AsyncMock(return_value=client)
    redis_patch, record_patch, _ = _patch_usage()

    with (
        patch("src.services.llm.get_llm_client", new=get_client),
        redis_patch,
        record_patch,
    ):
        response = await cli_ai_stream(
            CLIAICompleteRequest(
                messages=[{"role": "user", "content": "hi"}],
                profile="Reasoning",
            ),
            _principal(),
            session,
        )
        async for _ in response.body_iterator:
            pass

    get_client.assert_awaited_once_with(session, profile_name=None)


@pytest.mark.asyncio
async def test_http_stream_invalid_input_is_event_not_status():
    from src.models.contracts.cli import CLIAICompleteRequest
    from src.routers.cli import cli_ai_stream

    client = _FakeClient(chunks=[_done()])
    session = AsyncMock()
    redis_patch, record_patch, record = _patch_usage()

    with _patch_llm(client), redis_patch, record_patch:
        response = await cli_ai_stream(
            CLIAICompleteRequest(
                messages=[{"role": "system", "content": "sys"}],
                input_files=[
                    {
                        "filename": "a.txt",
                        "content_type": "text/plain",
                        "data_base64": base64.b64encode(b"x").decode(),
                    }
                ],
            ),
            _principal(),
            session,
        )

        assert response.media_type == "text/event-stream"
        body = ""
        async for chunk in response.body_iterator:
            body += chunk
    assert "AI stream is unavailable" in body
    assert "[DONE]" not in body
    assert record.await_count == 0
