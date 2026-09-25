"""Engine-local transport for ``ai.stream``.

Covers the parent ``ai.stream`` stream source
(``sdk_stream_dispatch.ai_stream_source``), the child ``bifrost.ai.stream``
binding, and the two together over real pipe pairs — without a forked
child:

- the source calls the same ``shared.sdk_ai.stream_sdk_ai`` generator
  the HTTP handler consumes, with the same ``CLIAICompleteRequest`` DTO
  validation, so chunk sequences and error mapping are identical;
- the token-equivalent caller comes from ``_table_user_for_principal``;
  usage ``execution_id`` comes from the parent principal even when the
  child frame supplies another one; the requested ``org_id`` rides as an
  untrusted scope resolved before the generator starts (403/422 stay
  terminal stream errors); the public call takes no profile, so
  ``profile=None`` always;
- only the business ``done`` payload is buffered: earlier deltas stream
  incrementally, then the shared generator is exhausted (recording
  usage), its transaction committed, and ``done`` yielded last — a
  caller that stops on ``done`` never loses usage;
- early child close shuts the provider stream with no partial usage, and
  the channel stays reusable for a later stream;
- large ``input_files`` ride the chunked open frames;
- an ordinary unary call (``config.get``) works while a stream is
  active on the independent descriptors;
- the SDK facade keeps knowledge composition and input-file encoding on
  the child, maps events to ``AIStreamChunk`` exactly like the HTTP
  branch (a provider error event becomes one empty non-done chunk, then
  EOF), and never falls back to HTTP.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import multiprocessing
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost._stream_transport import OP_AI_STREAM


@pytest_asyncio.fixture
async def db_session(async_engine):
    """Exercise real flushes while rolling back seeded rows after each test."""
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            try:
                yield session
            finally:
                await session.rollback()
                await transaction.rollback()


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    yield db_session


def _context_data(**kwargs):
    data = {
        "organization": kwargs.get("organization"),
        "is_platform_admin": kwargs.get("is_platform_admin", False),
        "execution_id": kwargs.get("execution_id", str(uuid4())),
    }
    if kwargs.get("solution_id") is not None:
        data["solution_id"] = kwargs["solution_id"]
    if kwargs.get("service") is not None:
        data["service"] = kwargs["service"]
    return data


def _workflow_principal(**kwargs):
    from src.services.execution.sdk_local_dispatch import principal_from_context

    return principal_from_context(_context_data(**kwargs))


def _service_principal(org_id, **kwargs):
    service_id = str(uuid4())
    service = {"service_id": service_id, "attempt_id": kwargs.get("attempt_id")}
    return _workflow_principal(
        organization={"id": str(org_id)}, service=service, **kwargs
    )


def _params(**fields):
    params: dict[str, Any] = {
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": None,
        "org_id": None,
        "model": None,
        "execution_id": None,
        "input_files": [],
    }
    params.update(fields)
    return params


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
    def __init__(self, chunks=(), exc=None):
        self.provider_name = "openai"
        self.model_name = "gpt-4o"
        self.chunks = list(chunks)
        self.exc = exc
        self.seen = None
        self.tracked = None

    def stream(self, *, messages=None, max_tokens=None, model=None):
        self.seen = {"messages": messages, "max_tokens": max_tokens, "model": model}
        self.tracked = _TrackedStream(self._inner())
        return self.tracked

    async def _inner(self):
        if self.exc is not None:
            raise self.exc
        for chunk in self.chunks:
            yield chunk


def _patch_provider(client):
    import src.services.llm as llm_pkg

    return (
        patch.object(llm_pkg, "get_llm_client", new=AsyncMock(return_value=client)),
        patch("src.core.cache.get_shared_redis", new=AsyncMock(return_value=AsyncMock())),
        patch("src.services.ai_usage_service.record_ai_usage", new=AsyncMock()),
    )


def _source_factory(db_session):
    return lambda: _db_factory(db_session)


async def _collect_source(principal, params, db_session):
    from src.services.execution.sdk_stream_dispatch import ai_stream_source

    return [
        event
        async for event in ai_stream_source(
            params, principal, session_factory=_source_factory(db_session)
        )
    ]


# =============================================================================
# Source: chunk parity with the shared service / HTTP path
# =============================================================================


@pytest.mark.asyncio
async def test_source_event_order_and_done_usage(db_session):
    """Deltas, done payload, and one usage write — like the HTTP stream."""
    from src.core.constants import SYSTEM_USER_UUID

    org_id = uuid4()
    parent_exec = str(uuid4())
    principal = _workflow_principal(
        organization={"id": str(org_id)}, execution_id=parent_exec
    )
    client = _FakeClient(chunks=[_delta("a"), _delta("b"), _done(3, 5)])
    get_client, redis_patch, record_patch = _patch_provider(client)
    record = record_patch.new
    with get_client, redis_patch, record_patch:
        events = await _collect_source(
            principal, _params(org_id=str(org_id)), db_session
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
    assert str(kwargs["execution_id"]) == parent_exec
    assert kwargs["organization_id"] == org_id
    assert kwargs["user_id"] == SYSTEM_USER_UUID


@pytest.mark.asyncio
async def test_source_forwards_model_max_tokens_and_forces_profile_none(db_session):
    """A forged frame profile is ignored; model/max-tokens ride through."""
    principal = _workflow_principal(execution_id=str(uuid4()))
    client = _FakeClient(chunks=[_done()])
    get_client, redis_patch, record_patch = _patch_provider(client)
    with get_client, redis_patch, record_patch:
        events = await _collect_source(
            principal,
            _params(max_tokens=42, model="gpt-4o", profile="Reasoning"),
            db_session,
        )
    assert events == [{"done": True, "input_tokens": 3, "output_tokens": 5}]
    get_client.new.assert_awaited_once()
    assert get_client.new.await_args.kwargs == {"profile_name": None}
    assert client.seen["max_tokens"] == 42
    assert client.seen["model"] == "gpt-4o"


@pytest.mark.asyncio
async def test_source_missing_messages_is_422(db_session):
    from src.services.execution.sdk_stream_dispatch import StreamSourceError

    principal = _workflow_principal(execution_id=str(uuid4()))
    params = _params()
    del params["messages"]
    with pytest.raises(StreamSourceError) as exc_info:
        await _collect_source(principal, params, db_session)
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_source_invalid_scope_is_terminal_422(db_session):
    from src.services.execution.sdk_stream_dispatch import StreamSourceError

    principal = _workflow_principal(execution_id=str(uuid4()))
    with pytest.raises(StreamSourceError) as exc_info:
        await _collect_source(
            principal, _params(org_id="not-a-uuid"), db_session
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_source_denied_scope_is_terminal_403(db_session):
    """A service child cannot stream under another org's scope."""
    from src.services.execution.sdk_stream_dispatch import StreamSourceError

    own_org = uuid4()
    other_org = uuid4()
    principal = _service_principal(own_org, execution_id=str(uuid4()))
    with pytest.raises(StreamSourceError) as exc_info:
        await _collect_source(
            principal, _params(org_id=str(other_org)), db_session
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_source_ignores_child_execution_id(db_session):
    """Usage attribution uses the parent principal, never frame claims."""
    from src.core.constants import SYSTEM_USER_UUID

    parent_exec = str(uuid4())
    principal = _workflow_principal(execution_id=parent_exec)
    client = _FakeClient(chunks=[_done()])
    get_client, redis_patch, record_patch = _patch_provider(client)
    record = record_patch.new
    with get_client, redis_patch, record_patch:
        events = await _collect_source(
            principal,
            _params(execution_id=str(uuid4()), actor_email="forged@example.com"),
            db_session,
        )
    assert events == [{"done": True, "input_tokens": 3, "output_tokens": 5}]
    assert record.await_count == 1
    kwargs = record.await_args.kwargs
    assert kwargs["user_id"] == SYSTEM_USER_UUID
    assert str(kwargs["execution_id"]) == parent_exec


@pytest.mark.asyncio
async def test_source_provider_error_event_streams_without_usage(db_session):
    """A provider error chunk streams through; no usage is recorded."""
    principal = _workflow_principal(execution_id=str(uuid4()))
    client = _FakeClient(chunks=[_delta("a"), _provider_error("boom")])
    get_client, redis_patch, record_patch = _patch_provider(client)
    record = record_patch.new
    with get_client, redis_patch, record_patch:
        events = await _collect_source(principal, _params(), db_session)
    assert events == [{"content": "a"}, {"error": "boom"}]
    assert client.tracked.closed is True
    assert record.await_count == 0


@pytest.mark.asyncio
async def test_source_done_buffered_until_usage_recorded(db_session):
    """Only ``done`` waits: deltas stream first, usage lands before ``done``."""
    order: list[Any] = []
    client = _FakeClient(chunks=[_delta("a"), _delta("b"), _done(1, 2)])
    import src.services.llm as llm_pkg

    async def _record(**kwargs):
        order.append("usage")

    with (
        patch.object(
            llm_pkg, "get_llm_client", new=AsyncMock(return_value=client)
        ),
        patch(
            "src.core.cache.get_shared_redis",
            new=AsyncMock(return_value=AsyncMock()),
        ),
        patch("src.services.ai_usage_service.record_ai_usage", new=_record),
    ):
        from src.services.execution.sdk_stream_dispatch import ai_stream_source

        gen = ai_stream_source(
            _params(),
            _workflow_principal(execution_id=str(uuid4())),
            session_factory=_source_factory(db_session),
        )
        async for event in gen:
            if event.get("done") is True:
                order.append("done")
            else:
                order.append(("delta", event.get("content")))
    assert order == [("delta", "a"), ("delta", "b"), "usage", "done"]


@pytest.mark.asyncio
async def test_source_first_chunk_before_provider_completes(db_session):
    """Incremental delivery: the first delta arrives while chunks pend."""
    gate = asyncio.Event()
    gate.clear()

    async def _gated():
        yield _delta("first")
        await gate.wait()
        yield _delta("second")
        yield _done()

    client = _FakeClient()
    tracked = _TrackedStream(_gated())
    client.stream = lambda **_: setattr(client, "tracked", tracked) or tracked
    import src.services.llm as llm_pkg

    with (
        patch.object(
            llm_pkg, "get_llm_client", new=AsyncMock(return_value=client)
        ),
        patch(
            "src.core.cache.get_shared_redis",
            new=AsyncMock(return_value=AsyncMock()),
        ),
        patch(
            "src.services.ai_usage_service.record_ai_usage", new=AsyncMock()
        ),
    ):
        from src.services.execution.sdk_stream_dispatch import ai_stream_source

        gen = ai_stream_source(
            _params(),
            _workflow_principal(execution_id=str(uuid4())),
            session_factory=_source_factory(db_session),
        )
        assert await gen.__anext__() == {"content": "first"}
        assert not gate.is_set()
        gate.set()
        rest = [event async for event in gen]
    assert rest == [
        {"content": "second"},
        {"done": True, "input_tokens": 3, "output_tokens": 5},
    ]


@pytest.mark.asyncio
async def test_source_cancel_closes_provider_without_usage(db_session):
    """Early close shuts the provider stream; no partial usage lands."""
    principal = _workflow_principal(execution_id=str(uuid4()))
    client = _FakeClient(chunks=[_delta("a"), _delta("b"), _done()])
    get_client, redis_patch, record_patch = _patch_provider(client)
    record = record_patch.new
    with get_client, redis_patch, record_patch:
        from src.services.execution.sdk_stream_dispatch import ai_stream_source

        gen = ai_stream_source(
            _params(), principal, session_factory=_source_factory(db_session)
        )
        assert await gen.__anext__() == {"content": "a"}
        await gen.aclose()
        assert client.tracked.closed is True
        assert record.await_count == 0
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()


@pytest.mark.asyncio
async def test_source_input_files_decode_on_parent(db_session):
    principal = _workflow_principal(execution_id=str(uuid4()))
    client = _FakeClient(chunks=[_done()])
    get_client, redis_patch, record_patch = _patch_provider(client)
    payload = base64.b64encode(b"%PDF-data").decode()
    with get_client, redis_patch, record_patch:
        events = await _collect_source(
            principal,
            _params(
                messages=[
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "ok"},
                    {"role": "user", "content": "summarize this"},
                ],
                input_files=[
                    {
                        "filename": "doc.pdf",
                        "content_type": "application/pdf",
                        "data_base64": payload,
                    }
                ],
            ),
            db_session,
        )
    assert events == [{"done": True, "input_tokens": 3, "output_tokens": 5}]
    sent = client.seen["messages"]
    assert sent[-1].role == "user"
    assert len(sent[-1].input_files) == 1
    assert sent[-1].input_files[0].data == b"%PDF-data"


@pytest.mark.asyncio
async def test_source_usage_commit_persists_row(db_session):
    """The dispatcher commits the flush-only usage write before ``done``."""
    from sqlalchemy import select

    from src.models.orm.ai_usage import AIUsage
    from src.models.orm.executions import Execution
    from src.models.orm.organizations import Organization

    org = Organization(
        id=uuid4(), name=f"O-{uuid4().hex[:6]}", created_by="ai-stream-local-test"
    )
    db_session.add(org)
    await db_session.flush()
    org_id = org.id
    execution_id = uuid4()
    db_session.add(
        Execution(
            id=execution_id,
            workflow_name="ai-stream-local-test",
            executed_by_name="ai-stream-local-test",
            organization_id=org_id,
        )
    )
    await db_session.flush()
    await db_session.commit()
    principal = _workflow_principal(
        organization={"id": str(org_id)}, execution_id=str(execution_id)
    )
    client = _FakeClient(chunks=[_delta("a"), _done(3, 5)])
    import src.services.llm as llm_pkg

    with (
        patch.object(
            llm_pkg, "get_llm_client", new=AsyncMock(return_value=client)
        ),
        patch(
            "src.core.cache.get_shared_redis",
            new=AsyncMock(return_value=AsyncMock()),
        ),
        patch(
            "src.services.model_registry.get_display_name",
            new=AsyncMock(return_value="gpt-4o"),
        ),
        patch(
            "src.services.ai_usage_service.get_cached_pricing",
            new=AsyncMock(return_value=(None, None, None, None)),
        ),
        patch(
            "src.services.ai_usage_service._notify_missing_pricing",
            new=AsyncMock(),
        ),
        patch(
            "src.services.ai_usage_service.invalidate_usage_cache",
            new=AsyncMock(),
        ),
        patch(
            "src.services.ai_usage_service._add_used_model",
            new=AsyncMock(),
        ),
    ):
        events = await _collect_source(
            principal, _params(org_id=str(org_id)), db_session
        )
    assert events[-1] == {"done": True, "input_tokens": 3, "output_tokens": 5}
    rows = (
        await db_session.execute(
            select(AIUsage).where(AIUsage.execution_id == execution_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].organization_id == org_id


# =============================================================================
# Pipes: child stream transport against the production registry
# =============================================================================


def _pipes() -> tuple[Any, Any, Any, Any]:
    parent_recv, child_send = multiprocessing.Pipe(duplex=False)
    child_recv, parent_send = multiprocessing.Pipe(duplex=False)
    return child_send, child_recv, parent_recv, parent_send


def _patch_session_factory(monkeypatch, db_session):
    monkeypatch.setattr(
        "src.core.database.get_session_factory",
        lambda: _source_factory(db_session),
    )


async def _drain(stream):
    return [event async for event in stream]


@pytest.mark.asyncio
async def test_pipe_chunk_parity_with_http(db_session, monkeypatch):
    """The wire carries exactly the shared-service payloads the SSE path sends."""
    import json as json_lib

    from bifrost._stream_transport import ChildStreamTransport
    from src.models.contracts.cli import CLIAICompleteRequest
    from src.routers.cli import cli_ai_stream
    from src.services.execution.sdk_stream_dispatch import serve_stream_channel

    _patch_session_factory(monkeypatch, db_session)
    client = _FakeClient(chunks=[_delta("hello"), _delta(" world"), _done(4, 6)])
    session = AsyncMock()
    get_client, redis_patch, record_patch = _patch_provider(client)
    child_send, child_recv, parent_recv, parent_send = _pipes()
    pump = asyncio.create_task(
        serve_stream_channel(
            recv_conn=parent_recv,
            send_conn=parent_send,
            principal=_workflow_principal(execution_id=str(uuid4())),
        )
    )
    try:
        with get_client, redis_patch, record_patch:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async(
                OP_AI_STREAM, _params(messages=[{"role": "user", "content": "hi"}])
            )
            local_events = await _drain(stream)

            http_response = await cli_ai_stream(
                CLIAICompleteRequest(
                    messages=[{"role": "user", "content": "hi"}],
                ),
                SimpleNamespace(
                    user_id=uuid4(), organization_id=uuid4(), is_superuser=False
                ),
                session,
            )
            lines = []
            async for chunk in http_response.body_iterator:
                lines.extend(line for line in chunk.splitlines() if line)
            http_events = [
                json_lib.loads(line[6:])
                for line in lines
                if line != "data: [DONE]"
            ]
        assert local_events == http_events == [
            {"content": "hello"},
            {"content": " world"},
            {"done": True, "input_tokens": 4, "output_tokens": 6},
        ]
    finally:
        child_send.close()
        child_recv.close()
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        parent_recv.close()
        parent_send.close()


@pytest.mark.asyncio
async def test_pipe_cancel_then_second_stream(db_session, monkeypatch):
    """Early close acks gracefully; a later stream on the child works."""
    from bifrost._stream_transport import ChildStreamTransport
    from src.services.execution.sdk_stream_dispatch import serve_stream_channel

    _patch_session_factory(monkeypatch, db_session)
    client = _FakeClient(chunks=[_delta("a"), _delta("b"), _done()])
    get_client, redis_patch, record_patch = _patch_provider(client)
    child_send, child_recv, parent_recv, parent_send = _pipes()
    pump = asyncio.create_task(
        serve_stream_channel(
            recv_conn=parent_recv,
            send_conn=parent_send,
            principal=_workflow_principal(execution_id=str(uuid4())),
        )
    )
    try:
        with get_client, redis_patch, record_patch:
            child = ChildStreamTransport(child_send, child_recv)
            first = await child.open_stream_async(OP_AI_STREAM, _params())
            assert await first.__anext__() == {"content": "a"}
            await first.aclose()
            assert client.tracked.closed is True
            assert record_patch.new.await_count == 0

            second = await child.open_stream_async(OP_AI_STREAM, _params())
            assert await _drain(second) == [
                {"content": "a"},
                {"content": "b"},
                {"done": True, "input_tokens": 3, "output_tokens": 5},
            ]
            assert record_patch.new.await_count == 1
    finally:
        child_send.close()
        child_recv.close()
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        parent_recv.close()
        parent_send.close()


@pytest.mark.asyncio
async def test_pipe_large_input_files_ride_chunked_open(db_session, monkeypatch):
    """A >64 KiB open (large input files) reassembles on the parent."""
    from bifrost._stream_transport import (
        MAX_FRAME_BYTES,
        ChildStreamTransport,
    )
    from src.services.execution.sdk_stream_dispatch import serve_stream_channel

    _patch_session_factory(monkeypatch, db_session)
    blob = base64.b64encode(b"y" * (MAX_FRAME_BYTES + 1024)).decode()
    params = _params(
        input_files=[
            {
                "filename": "big.bin",
                "content_type": "application/octet-stream",
                "data_base64": blob,
            }
        ]
    )
    assert len(json.dumps(params).encode()) > MAX_FRAME_BYTES
    client = _FakeClient(chunks=[_done()])
    get_client, redis_patch, record_patch = _patch_provider(client)
    child_send, child_recv, parent_recv, parent_send = _pipes()
    pump = asyncio.create_task(
        serve_stream_channel(
            recv_conn=parent_recv,
            send_conn=parent_send,
            principal=_workflow_principal(execution_id=str(uuid4())),
        )
    )
    try:
        with get_client, redis_patch, record_patch:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async(OP_AI_STREAM, params)
            assert await _drain(stream) == [
                {"done": True, "input_tokens": 3, "output_tokens": 5}
            ]
        sent = client.seen["messages"]
        assert sent[-1].input_files[0].data == b"y" * (MAX_FRAME_BYTES + 1024)
    finally:
        child_send.close()
        child_recv.close()
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        parent_recv.close()
        parent_send.close()


@pytest.mark.asyncio
async def test_pipe_scope_denied_terminal_error_leaves_channel_reusable(
    db_session, monkeypatch
):
    """A denied scope raises the HTTP-mapped error; the next stream works."""
    from bifrost._stream_transport import ChildStreamTransport
    from bifrost.client import BifrostAuthorizationError
    from src.services.execution.sdk_stream_dispatch import serve_stream_channel

    _patch_session_factory(monkeypatch, db_session)
    client = _FakeClient(chunks=[_done()])
    get_client, redis_patch, record_patch = _patch_provider(client)
    own_org = uuid4()
    principal = _service_principal(own_org, execution_id=str(uuid4()))
    child_send, child_recv, parent_recv, parent_send = _pipes()
    pump = asyncio.create_task(
        serve_stream_channel(
            recv_conn=parent_recv,
            send_conn=parent_send,
            principal=principal,
        )
    )
    try:
        with get_client, redis_patch, record_patch:
            child = ChildStreamTransport(child_send, child_recv)
            denied = await child.open_stream_async(
                OP_AI_STREAM, _params(org_id=str(uuid4()))
            )
            with pytest.raises(BifrostAuthorizationError):
                await denied.__anext__()
            retry = await child.open_stream_async(
                OP_AI_STREAM, _params(org_id=str(own_org))
            )
            assert await _drain(retry) == [
                {"done": True, "input_tokens": 3, "output_tokens": 5}
            ]
    finally:
        child_send.close()
        child_recv.close()
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        parent_recv.close()
        parent_send.close()


@pytest.mark.asyncio
async def test_pipe_config_call_while_streaming(db_session, monkeypatch):
    """A unary ``config.get`` serves while the AI stream stays open."""
    from bifrost._local_transport import ChildLocalTransport
    from bifrost._stream_transport import ChildStreamTransport
    from src.services.execution.sdk_local_dispatch import (
        SDK_CHANNEL_ALLOWED_OPS,
        serve_channel,
    )
    from src.services.execution.sdk_stream_dispatch import serve_stream_channel

    _patch_session_factory(monkeypatch, db_session)
    gate = asyncio.Event()
    gate.clear()

    async def _gated():
        yield _delta("first")
        await gate.wait()
        yield _delta("second")
        yield _done()

    client = _FakeClient()
    tracked = _TrackedStream(_gated())
    client.stream = lambda **_: setattr(client, "tracked", tracked) or tracked
    import src.services.llm as llm_pkg

    from shared.sdk_config import set_sdk_config_value

    await set_sdk_config_value(
        db_session,
        key="ai-stream-while",
        value=" reachable ",
        is_secret=False,
        org_id=None,
        actor_email="ai-stream-local-test",
    )
    sdk_req_recv, sdk_child_send = multiprocessing.Pipe(duplex=False)
    sdk_child_recv, sdk_resp_send = multiprocessing.Pipe(duplex=False)
    strm_child_send, strm_child_recv, strm_parent_recv, strm_parent_send = _pipes()
    principal = _workflow_principal(execution_id=str(uuid4()))
    stream_pump = asyncio.create_task(
        serve_stream_channel(
            recv_conn=strm_parent_recv,
            send_conn=strm_parent_send,
            principal=principal,
        )
    )
    sdk_pump = asyncio.create_task(
        serve_channel(
            recv_conn=sdk_req_recv,
            send_conn=sdk_resp_send,
            session_factory=_source_factory(db_session),
            principal=principal,
            allowed_ops=SDK_CHANNEL_ALLOWED_OPS,
        )
    )
    try:
        with (
            patch.object(
                llm_pkg, "get_llm_client", new=AsyncMock(return_value=client)
            ),
            patch(
                "src.core.cache.get_shared_redis",
                new=AsyncMock(return_value=AsyncMock()),
            ),
            patch(
                "src.services.ai_usage_service.record_ai_usage",
                new=AsyncMock(),
            ),
        ):
            stream_child = ChildStreamTransport(strm_child_send, strm_child_recv)
            sdk_child = ChildLocalTransport(sdk_child_send, sdk_child_recv)
            stream = await stream_child.open_stream_async(OP_AI_STREAM, _params())
            assert await stream.__anext__() == {"content": "first"}
            value = await sdk_child.call_config_get("ai-stream-while", None)
            assert value is not None
            assert not stream.closed
            gate.set()
            assert await _drain(stream) == [
                {"content": "second"},
                {"done": True, "input_tokens": 3, "output_tokens": 5},
            ]
    finally:
        gate.set()
        for conn in (
            sdk_child_send,
            sdk_child_recv,
            strm_child_send,
            strm_child_recv,
        ):
            with contextlib.suppress(Exception):
                conn.close()
        for pump in (stream_pump, sdk_pump):
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
        for conn in (
            sdk_req_recv,
            sdk_resp_send,
            strm_parent_recv,
            strm_parent_send,
        ):
            with contextlib.suppress(Exception):
                conn.close()


# =============================================================================
# SDK facade (no DB)
# =============================================================================


class _FakeChildStream:
    """Minimal async-iterable stream the facade consumes."""

    def __init__(self, events=None, error=None):
        self._events = list(events or [])
        self._error = error
        self.closed = False
        self.aclose_called = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._error is not None:
            self.closed = True
            raise self._error
        if not self._events:
            self.closed = True
            raise StopAsyncIteration
        return self._events.pop(0)

    async def aclose(self):
        self.aclose_called = True
        self.closed = True


def _stream_transport_for(fake_stream):
    transport = AsyncMock()
    transport.open_stream_async.return_value = fake_stream
    return transport


@pytest.mark.asyncio
async def test_facade_stream_local_chunks_match_http_shape():
    from bifrost.ai import ai as ai_facade
    from bifrost.models import AIStreamChunk

    transport = _stream_transport_for(
        _FakeChildStream(
            [
                {"content": "hello"},
                {"content": " world"},
                {"done": True, "input_tokens": 4, "output_tokens": 6},
            ]
        )
    )
    with (
        patch("bifrost._stream_transport.get", return_value=transport),
        patch(
            "bifrost.ai.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        chunks = [chunk async for chunk in ai_facade.stream("Hi")]
    assert chunks == [
        AIStreamChunk(content="hello", done=False),
        AIStreamChunk(content=" world", done=False),
        AIStreamChunk(content="", done=True, input_tokens=4, output_tokens=6),
    ]
    params = transport.open_stream_async.await_args.args[1]
    assert params["messages"] == [{"role": "user", "content": "Hi"}]
    assert params["input_files"] == []


@pytest.mark.asyncio
async def test_facade_stream_error_event_becomes_empty_chunk_then_eof():
    """Provider-error parity: one empty non-done chunk, then EOF."""
    from bifrost.ai import ai as ai_facade

    transport = _stream_transport_for(
        _FakeChildStream([{"content": "a"}, {"error": "boom"}])
    )
    with (
        patch("bifrost._stream_transport.get", return_value=transport),
        patch(
            "bifrost.ai.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        chunks = [chunk async for chunk in ai_facade.stream("Hi")]
    assert len(chunks) == 2
    assert chunks[0].content == "a" and chunks[0].done is False
    assert chunks[1].content == "" and chunks[1].done is False


@pytest.mark.asyncio
async def test_facade_stream_terminal_error_propagates_without_http():
    """Scope/validation denials raise the HTTP-mapped error loudly."""
    import httpx

    from bifrost.ai import ai as ai_facade
    from bifrost.client import BifrostAuthorizationError, raise_for_status_with_detail

    request = httpx.Request("POST", "local://sdk/ai/stream")
    response = httpx.Response(403, json={"detail": "denied"}, request=request)
    with pytest.raises(BifrostAuthorizationError) as mapped:
        raise_for_status_with_detail(response)
    terminal = mapped.value
    transport = _stream_transport_for(_FakeChildStream(error=terminal))
    with (
        patch("bifrost._stream_transport.get", return_value=transport),
        patch(
            "bifrost.ai.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        with pytest.raises(BifrostAuthorizationError):
            [chunk async for chunk in ai_facade.stream("Hi")]


@pytest.mark.asyncio
async def test_facade_stream_knowledge_and_files_composed_on_child():
    """knowledge= pre-search and file encoding stay on the child."""
    from bifrost import knowledge
    from bifrost.ai import ai as ai_facade
    from bifrost.models import AIInputFile, KnowledgeDocument

    doc = KnowledgeDocument(
        id=str(uuid4()),
        namespace="policies",
        content="Refunds within 30 days.",
        metadata={},
        score=0.9,
        organization_id=None,
        key="refund",
        created_at=None,
    )
    transport = _stream_transport_for(_FakeChildStream([{"done": True}]))
    with (
        patch("bifrost._stream_transport.get", return_value=transport),
        patch.object(
            knowledge, "search", new=AsyncMock(return_value=[doc])
        ) as search,
        patch(
            "bifrost.ai.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        chunks = [
            chunk
            async for chunk in ai_facade.stream(
                "Refund policy?",
                knowledge=["policies"],
                files=[
                    AIInputFile(
                        filename="a.txt", content_type="text/plain", data=b"x"
                    )
                ],
            )
        ]
    assert chunks[-1].done is True
    search.assert_awaited_once()
    params = transport.open_stream_async.await_args.args[1]
    assert any(
        "Refunds within 30 days" in m.get("content", "")
        for m in params["messages"]
    )
    assert params["input_files"][0]["filename"] == "a.txt"


@pytest.mark.asyncio
async def test_facade_close_cancels_parent_source_and_reuses_channel(
    db_session, monkeypatch
):
    """Closing the facade stream cancels the parent source, not just the child view."""
    from bifrost._stream_transport import ChildStreamTransport
    from bifrost.ai import ai as ai_facade
    from src.services.execution.sdk_stream_dispatch import serve_stream_channel

    _patch_session_factory(monkeypatch, db_session)
    client = _FakeClient(chunks=[_delta("a"), _delta("b"), _done()])
    get_client, redis_patch, record_patch = _patch_provider(client)
    record = record_patch.new
    child_send, child_recv, parent_recv, parent_send = _pipes()
    pump = asyncio.create_task(
        serve_stream_channel(
            recv_conn=parent_recv,
            send_conn=parent_send,
            principal=_workflow_principal(execution_id=str(uuid4())),
        )
    )
    try:
        with (
            get_client,
            redis_patch,
            record_patch,
            patch(
                "bifrost._stream_transport.get",
                return_value=ChildStreamTransport(child_send, child_recv),
            ),
            patch(
                "bifrost.ai.get_client",
                side_effect=AssertionError("HTTP fallback is forbidden"),
            ),
        ):
            first = ai_facade.stream("Hi")
            assert (await first.__anext__()).content == "a"
            await first.aclose()
            assert client.tracked.closed is True
            assert record.await_count == 0
            second = [chunk async for chunk in ai_facade.stream("Hi")]
        assert [c.content for c in second] == ["a", "b", ""]
        assert second[-1].done is True
        assert record.await_count == 1
    finally:
        child_send.close()
        child_recv.close()
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        parent_recv.close()
        parent_send.close()


@pytest.mark.asyncio
async def test_facade_stream_never_falls_back_to_http():
    from bifrost._stream_transport import StreamTransportClosed
    from bifrost.ai import ai as ai_facade

    transport = AsyncMock()
    transport.open_stream_async.side_effect = StreamTransportClosed("closed")
    with (
        patch("bifrost._stream_transport.get", return_value=transport),
        patch(
            "bifrost.ai.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        with pytest.raises(StreamTransportClosed):
            [chunk async for chunk in ai_facade.stream("Hi")]


@pytest.mark.asyncio
async def test_external_http_stream_unchanged():
    """External callers keep the SSE path with composed files attached."""
    from bifrost.ai import ai as ai_facade

    seen: dict[str, Any] = {}

    class _Lines:
        def __init__(self, lines):
            self._lines = lines

        async def __aiter__(self):
            for line in self._lines:
                yield line

    class _Response:
        is_success = True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def aiter_lines(self):
            return _Lines(
                [
                    'data: {"content": "hi"}',
                    'data: {"done": true, "input_tokens": 1, "output_tokens": 2}',
                    "data: [DONE]",
                ]
            )

    class _Client:
        def stream(self, method, url, json=None):
            seen.update(json or {})
            assert method == "POST"
            assert url == "/api/sdk/ai/stream"
            return _Response()

    with (
        patch("bifrost._stream_transport.get", return_value=None),
        patch("bifrost.ai.get_client", return_value=_Client()),
    ):
        chunks = [chunk async for chunk in ai_facade.stream("Hi")]
    assert [c.content for c in chunks] == ["hi", ""]
    assert chunks[-1].done is True
    assert chunks[-1].input_tokens == 1
    assert seen["messages"] == [{"role": "user", "content": "Hi"}]
    assert seen["input_files"] == []


@pytest.mark.asyncio
async def test_external_http_stream_preflight_error_matches_local():
    """A rejected stream request raises the same public SDK error on HTTP."""
    import httpx

    from bifrost.ai import ai as ai_facade
    from bifrost.client import BifrostAuthorizationError

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(403, json={"detail": "denied"})
        ),
        base_url="https://example.test",
    ) as client:
        with (
            patch("bifrost._stream_transport.get", return_value=None),
            patch("bifrost.ai.get_client", return_value=client),
            pytest.raises(BifrostAuthorizationError, match="denied"),
        ):
            [chunk async for chunk in ai_facade.stream("Hi")]
