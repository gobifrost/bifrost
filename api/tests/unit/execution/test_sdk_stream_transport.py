"""Engine-local streaming transport: wire, lifecycle, and isolation tests.

Exercises ``bifrost._stream_transport`` (child) against
``src.services.execution.sdk_stream_dispatch.serve_stream_channel``
(parent) over real pipe pairs with a fake operation registry injected
through the pump seam. Production ``STREAM_OPS`` stays empty (asserted
here); no AI logic, HTTP route, or fallback is involved.

Covers the handoff contract: incremental per-credit delivery, wire
bounds, out-of-order/malformed frames, EOF, idle timeout, child early
close and task cancellation, cancel racing a blocked source read, a
second stream after graceful cancellation, provider errors as one
terminal error, the outer terminal distinct from a business ``done``
event, chunked large events, and channel independence.
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
from typing import Any

import pytest

from bifrost._stream_transport import (
    MAX_FRAME_BYTES,
    TRANSPORT_VERSION,
    ChildStreamTransport,
    StreamTransportClosed,
    StreamTransportError,
    clear as clear_transport,
    decode_frame,
    encode_frame,
    get as get_transport,
    install as install_transport,
)
from bifrost.client import BifrostAPIError
from src.services.execution.sdk_stream_dispatch import (
    STREAM_OPS,
    StreamSourceError,
    serve_stream_channel,
)


def _pipes() -> tuple[Any, Any, Any, Any]:
    """(child_send, child_recv, parent_recv, parent_send) for one channel.

    ``multiprocessing.Pipe(duplex=False)`` returns ``(read_end,
    write_end)`` — the first connection reads, the second writes.
    """
    parent_recv, child_send = multiprocessing.Pipe(duplex=False)
    child_recv, parent_send = multiprocessing.Pipe(duplex=False)
    return child_send, child_recv, parent_recv, parent_send


def _list_registry(
    events: list[dict[str, Any]],
    *,
    seen: list[Any] | None = None,
    gate: asyncio.Event | None = None,
    fail_after: int | None = None,
    fail_status: int = 502,
    post_done: list[bool] | None = None,
) -> dict[str, Any]:
    """Fake registry with one ``test.stream`` source yielding ``events``."""

    async def _source(params: dict[str, Any], principal: Any):
        if seen is not None:
            seen.append((params, principal))
        for i, event in enumerate(events):
            if gate is not None and i == 1:
                await gate.wait()
            if fail_after is not None and i == fail_after:
                raise StreamSourceError(fail_status, "boom")
            yield event
        if post_done is not None:
            post_done.append(True)

    return {"test.stream": _source}


async def _drain(stream: Any) -> list[dict[str, Any]]:
    """Collect one stream to its outer terminal."""
    out = []
    async for event in stream:
        out.append(event)
    return out


@pytest.mark.asyncio
class TestProductionRegistryEmpty:
    async def test_stream_ops_empty_until_ai_binding(self):
        assert STREAM_OPS == {}

    async def test_unknown_op_is_terminal_error_and_channel_reusable(self):
        child_send, child_recv, parent_recv, parent_send = _pipes()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry=_list_registry([{"ok": True}]),
            )
        )
        try:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async("nope.unknown")
            with pytest.raises(BifrostAPIError, match="not allowed"):
                await stream.__anext__()
            # The channel stays usable for a later stream.
            stream2 = await child.open_stream_async("test.stream")
            assert await _drain(stream2) == [{"ok": True}]
        finally:
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()


@pytest.mark.asyncio
class TestIncrementalDelivery:
    async def test_large_open_is_chunked_and_reassembled(self):
        async def _source(params: dict[str, Any], principal: Any):
            yield {"length": len(params["blob"])}

        child_send, child_recv, parent_recv, parent_send = _pipes()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry={"test.stream": _source},
            )
        )
        try:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async(
                "test.stream", {"blob": "x" * (MAX_FRAME_BYTES * 2)}
            )
            assert await stream.__anext__() == {"length": MAX_FRAME_BYTES * 2}
            with pytest.raises(StopAsyncIteration):
                await stream.__anext__()
        finally:
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()

    async def test_events_arrive_incrementally_one_credit_at_a_time(self):
        gate = asyncio.Event()
        advances: list[int] = []

        async def _source(params: dict[str, Any], principal: Any):
            for i in (0, 1, 2):
                advances.append(i)
                if i == 1:
                    await gate.wait()
                yield {"i": i}

        child_send, child_recv, parent_recv, parent_send = _pipes()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry={"test.stream": _source},
            )
        )
        try:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async("test.stream")
            # The source must not advance before the first credit.
            await asyncio.sleep(0.2)
            assert advances == []
            assert await stream.__anext__() == {"i": 0}
            assert advances == [0]
            # The second advance blocks on the gate: no accumulation.
            second = asyncio.create_task(stream.__anext__())
            await asyncio.sleep(0.3)
            assert not second.done()
            assert advances == [0, 1]
            gate.set()
            assert await second == {"i": 1}
            assert await stream.__anext__() == {"i": 2}
            with pytest.raises(StopAsyncIteration):
                await stream.__anext__()
        finally:
            gate.set()
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()

    async def test_parent_receives_params_and_principal(self):
        seen: list[Any] = []
        child_send, child_recv, parent_recv, parent_send = _pipes()
        sentinel = object()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=sentinel,
                registry=_list_registry([{"a": 1}], seen=seen),
            )
        )
        try:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async("test.stream", {"n": 2})
            assert await _drain(stream) == [{"a": 1}]
            assert seen == [({"n": 2}, sentinel)]
        finally:
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()

    async def test_one_active_stream_per_child(self):
        child_send, child_recv, parent_recv, parent_send = _pipes()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry=_list_registry([{"a": 1}]),
            )
        )
        try:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async("test.stream")
            with pytest.raises(StreamTransportError, match="already active"):
                await child.open_stream_async("test.stream")
            assert await _drain(stream) == [{"a": 1}]
        finally:
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()


@pytest.mark.asyncio
class TestTerminalFrames:
    async def test_provider_error_is_one_terminal_after_delivered_events(self):
        child_send, child_recv, parent_recv, parent_send = _pipes()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry=_list_registry(
                    [{"i": 0}, {"i": 1}, {"i": 2}], fail_after=2
                ),
            )
        )
        try:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async("test.stream")
            assert await stream.__anext__() == {"i": 0}
            assert await stream.__anext__() == {"i": 1}
            with pytest.raises(BifrostAPIError, match="boom"):
                await stream.__anext__()
            # The stream finished: a later stream on the same child works.
            stream2 = await child.open_stream_async("test.stream")
            assert await stream2.__anext__() == {"i": 0}
            await stream2.aclose()
        finally:
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()

    async def test_outer_end_is_distinct_from_business_done(self):
        post_done: list[bool] = []
        child_send, child_recv, parent_recv, parent_send = _pipes()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry=_list_registry(
                    [{"content": "hi"}, {"done": True}], post_done=post_done
                ),
            )
        )
        try:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async("test.stream")
            assert await stream.__anext__() == {"content": "hi"}
            # The business done payload arrives as data, not termination.
            assert await stream.__anext__() == {"done": True}
            with pytest.raises(StopAsyncIteration):
                await stream.__anext__()
            # The source ran its post-done work before the outer terminal.
            assert post_done == [True]
        finally:
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()

    async def test_large_event_arrives_chunked(self):
        big = "x" * (3 * 48768)
        child_send, child_recv, parent_recv, parent_send = _pipes()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry=_list_registry([{"blob": big}]),
            )
        )
        try:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async("test.stream")
            assert await stream.__anext__() == {"blob": big}
            with pytest.raises(StopAsyncIteration):
                await stream.__anext__()
        finally:
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()


@pytest.mark.asyncio
class TestCancellation:
    async def test_early_aclose_then_second_stream(self):
        closed: list[bool] = []

        async def _source(params: dict[str, Any], principal: Any):
            try:
                i = 0
                while True:
                    yield {"i": i}
                    i += 1
            finally:
                closed.append(True)

        child_send, child_recv, parent_recv, parent_send = _pipes()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry={"test.stream": _source},
            )
        )
        try:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async("test.stream")
            assert await stream.__anext__() == {"i": 0}
            await stream.aclose()
            assert closed == [True]
            stream2 = await child.open_stream_async("test.stream")
            assert await stream2.__anext__() == {"i": 0}
            await stream2.aclose()
            assert closed == [True, True]
        finally:
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()

    async def test_cancel_races_blocked_source_read(self):
        entered = asyncio.Event()
        closed: list[bool] = []

        async def _source(params: dict[str, Any], principal: Any):
            yield {"i": 0}
            entered.set()
            try:
                await asyncio.sleep(60)
                yield {"i": 1}  # pragma: no cover - cancelled first
            finally:
                closed.append(True)

        child_send, child_recv, parent_recv, parent_send = _pipes()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry={"test.stream": _source},
            )
        )
        try:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async("test.stream")
            assert await stream.__anext__() == {"i": 0}
            pending = asyncio.create_task(stream.__anext__())
            await asyncio.wait_for(entered.wait(), timeout=5)
            await asyncio.sleep(0.3)
            assert not pending.done()
            # Cancel from another task while the source read is blocked.
            await stream.aclose()
            with pytest.raises(StopAsyncIteration):
                await pending
            assert closed == [True]
            stream2 = await child.open_stream_async("test.stream")
            assert await stream2.__anext__() == {"i": 0}
            await stream2.aclose()
        finally:
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()

    async def test_task_cancellation_then_second_stream(self):
        entered = asyncio.Event()
        closed: list[bool] = []

        async def _source(params: dict[str, Any], principal: Any):
            yield {"i": 0}
            entered.set()
            try:
                await asyncio.sleep(60)
                yield {"i": 1}  # pragma: no cover - cancelled first
            finally:
                closed.append(True)

        child_send, child_recv, parent_recv, parent_send = _pipes()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry={"test.stream": _source},
            )
        )
        try:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async("test.stream")
            assert await stream.__anext__() == {"i": 0}
            pending = asyncio.create_task(stream.__anext__())
            await asyncio.wait_for(entered.wait(), timeout=5)
            await asyncio.sleep(0.3)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert closed == [True]
            stream2 = await child.open_stream_async("test.stream")
            assert await stream2.__anext__() == {"i": 0}
            await stream2.aclose()
        finally:
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()


@pytest.mark.asyncio
class TestTimeoutsAndEof:
    async def test_idle_timeout_between_credits_keeps_channel(self):
        async def _source(params: dict[str, Any], principal: Any):
            yield {"i": 0}
            await asyncio.sleep(60)
            yield {"i": 1}  # pragma: no cover - idle timeout first

        child_send, child_recv, parent_recv, parent_send = _pipes()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry={"test.stream": _source},
                idle_timeout=0.3,
            )
        )
        try:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async("test.stream")
            assert await stream.__anext__() == {"i": 0}
            # No further credit within the idle bound: one terminal error.
            with pytest.raises(BifrostAPIError, match="idle timeout"):
                await stream.__anext__()
            stream2 = await child.open_stream_async("test.stream")
            assert await stream2.__anext__() == {"i": 0}
            await stream2.aclose()
        finally:
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()

    async def test_child_eof_closes_source_and_pump(self):
        closed: list[bool] = []

        async def _source(params: dict[str, Any], principal: Any):
            try:
                yield {"i": 0}
                await asyncio.sleep(60)
                yield {"i": 1}  # pragma: no cover - EOF first
            finally:
                closed.append(True)

        child_send, child_recv, parent_recv, parent_send = _pipes()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry={"test.stream": _source},
            )
        )
        # Raw child: open one stream, then vanish mid-read.
        child_send.send_bytes(
            encode_frame(
                {
                    "v": TRANSPORT_VERSION,
                    "id": "gone-1",
                    "type": "open",
                    "op": "test.stream",
                    "params": {},
                }
            )
        )
        child_send.send_bytes(
            encode_frame({"v": TRANSPORT_VERSION, "id": "gone-1", "type": "pull"})
        )
        raw = await asyncio.to_thread(child_recv.recv_bytes, MAX_FRAME_BYTES + 1)
        assert decode_frame(raw)["type"] == "event"
        child_send.close()
        child_recv.close()
        assert await asyncio.wait_for(pump, timeout=10) == "eof"
        assert closed == [True]
        parent_recv.close()
        parent_send.close()

    async def test_parent_shutdown_closes_source(self):
        closed: list[bool] = []

        async def _source(params: dict[str, Any], principal: Any):
            try:
                yield {"i": 0}
                await asyncio.sleep(60)
                yield {"i": 1}  # pragma: no cover - shutdown first
            finally:
                closed.append(True)

        child_send, child_recv, parent_recv, parent_send = _pipes()
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry={"test.stream": _source},
            )
        )
        try:
            child = ChildStreamTransport(child_send, child_recv)
            stream = await child.open_stream_async("test.stream")
            assert await stream.__anext__() == {"i": 0}
            pending = asyncio.create_task(stream.__anext__())
            await asyncio.sleep(0.3)
            pump.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pump
            assert closed == [True]
            with pytest.raises(StreamTransportClosed):
                await pending
        finally:
            child_send.close()
            child_recv.close()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()


@pytest.mark.asyncio
class TestWireContract:
    async def _run_raw(
        self, frames: list[bytes], registry: dict[str, Any] | None = None
    ) -> str:
        parent_recv, child_send = multiprocessing.Pipe(duplex=False)
        child_recv, parent_send = multiprocessing.Pipe(duplex=False)
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry=registry or {},
            )
        )
        try:
            for frame in frames:
                await asyncio.to_thread(child_send.send_bytes, frame)
            return await asyncio.wait_for(pump, timeout=10)
        finally:
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()

    async def test_oversized_frame_closes(self):
        reason = await self._run_raw([b"x" * (MAX_FRAME_BYTES + 1)])
        assert reason == "oversized"

    async def test_chunked_open_rejects_out_of_order_part(self):
        reason = await self._run_raw(
            [
                encode_frame(
                    {
                        "v": TRANSPORT_VERSION,
                        "id": "large-open",
                        "type": "open_chunked",
                        "total": 100000,
                        "parts": 3,
                    }
                ),
                encode_frame(
                    {
                        "v": TRANSPORT_VERSION,
                        "id": "large-open",
                        "type": "open_chunk",
                        "part": 1,
                        "data": "eA==",
                    }
                ),
            ]
        )
        assert reason == "malformed"

    async def test_bad_version_closes(self):
        reason = await self._run_raw(
            [
                encode_frame(
                    {
                        "v": 999,
                        "id": "a",
                        "type": "open",
                        "op": "test.stream",
                        "params": {},
                    }
                )
            ]
        )
        assert reason == "malformed"

    async def test_pull_with_no_active_stream_closes(self):
        reason = await self._run_raw(
            [encode_frame({"v": TRANSPORT_VERSION, "id": "a", "type": "pull"})]
        )
        assert reason == "malformed"

    async def test_second_open_while_active_closes(self):
        async def _source(params: dict[str, Any], principal: Any):
            yield {"i": 0}
            await asyncio.sleep(60)
            yield {"i": 1}  # pragma: no cover - protocol violation first

        reason = await self._run_raw(
            [
                encode_frame(
                    {
                        "v": TRANSPORT_VERSION,
                        "id": "a",
                        "type": "open",
                        "op": "test.stream",
                        "params": {},
                    }
                ),
                encode_frame(
                    {
                        "v": TRANSPORT_VERSION,
                        "id": "b",
                        "type": "open",
                        "op": "test.stream",
                        "params": {},
                    }
                ),
            ],
            {"test.stream": _source},
        )
        assert reason == "malformed"

    async def test_wrong_id_while_active_closes(self):
        async def _source(params: dict[str, Any], principal: Any):
            yield {"i": 0}
            await asyncio.sleep(60)
            yield {"i": 1}  # pragma: no cover - protocol violation first

        reason = await self._run_raw(
            [
                encode_frame(
                    {
                        "v": TRANSPORT_VERSION,
                        "id": "a",
                        "type": "open",
                        "op": "test.stream",
                        "params": {},
                    }
                ),
                encode_frame({"v": TRANSPORT_VERSION, "id": "nope", "type": "pull"}),
            ],
            {"test.stream": _source},
        )
        assert reason == "malformed"

    async def test_unparseable_frame_closes(self):
        reason = await self._run_raw([b"not json"])
        assert reason == "malformed"

    async def test_stale_credit_after_rejected_open_is_ignored(self):
        """A pull in flight past an open rejection must not break the channel."""

        async def _source(params: dict[str, Any], principal: Any):
            yield {"i": 0}

        parent_recv, child_send = multiprocessing.Pipe(duplex=False)
        child_recv, parent_send = multiprocessing.Pipe(duplex=False)
        pump = asyncio.create_task(
            serve_stream_channel(
                recv_conn=parent_recv,
                send_conn=parent_send,
                principal=object(),
                registry={"test.stream": _source},
            )
        )
        try:
            child_send.send_bytes(
                encode_frame(
                    {
                        "v": TRANSPORT_VERSION,
                        "id": "u1",
                        "type": "open",
                        "op": "nope.unknown",
                        "params": {},
                    }
                )
            )
            raw = await asyncio.to_thread(child_recv.recv_bytes, MAX_FRAME_BYTES + 1)
            assert decode_frame(raw)["type"] == "error"
            # The rejected open's credit arrives late: ignored, not fatal.
            child_send.send_bytes(
                encode_frame({"v": TRANSPORT_VERSION, "id": "u1", "type": "pull"})
            )
            child_send.send_bytes(
                encode_frame(
                    {
                        "v": TRANSPORT_VERSION,
                        "id": "s1",
                        "type": "open",
                        "op": "test.stream",
                        "params": {},
                    }
                )
            )
            child_send.send_bytes(
                encode_frame({"v": TRANSPORT_VERSION, "id": "s1", "type": "pull"})
            )
            first = decode_frame(
                await asyncio.to_thread(child_recv.recv_bytes, MAX_FRAME_BYTES + 1)
            )
            assert first["type"] == "event"
            assert first["event"] == {"i": 0}
            child_send.send_bytes(
                encode_frame({"v": TRANSPORT_VERSION, "id": "s1", "type": "pull"})
            )
            last = decode_frame(
                await asyncio.to_thread(child_recv.recv_bytes, MAX_FRAME_BYTES + 1)
            )
            assert last["type"] == "end"
            assert not pump.done()
        finally:
            child_send.close()
            child_recv.close()
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            parent_recv.close()
            parent_send.close()


@pytest.mark.asyncio
class TestChildValidation:
    async def test_child_rejects_out_of_order_event(self):
        child_send, child_recv, parent_recv, parent_send = _pipes()
        child = ChildStreamTransport(child_send, child_recv)
        stream_task: asyncio.Task[Any] = asyncio.create_task(
            child.open_stream_async("test.stream")
        )
        try:
            raw = await asyncio.to_thread(parent_recv.recv_bytes, MAX_FRAME_BYTES + 1)
            request = decode_frame(raw)
            stream_id = request["id"]
            parent_send.send_bytes(
                encode_frame(
                    {
                        "v": TRANSPORT_VERSION,
                        "id": stream_id,
                        "type": "event",
                        "seq": 5,
                        "event": {"i": 0},
                    }
                )
            )
            stream = await asyncio.wait_for(stream_task, timeout=5)
            with pytest.raises(StreamTransportError, match="out of order"):
                await stream.__anext__()
            # Only this channel broke; a later open fails closed loudly.
            with pytest.raises(StreamTransportError):
                await child.open_stream_async("test.stream")
        finally:
            stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream_task
            child_send.close()
            child_recv.close()
            parent_recv.close()
            parent_send.close()

    async def test_child_rejects_bad_chunk_header(self):
        child_send, child_recv, parent_recv, parent_send = _pipes()
        child = ChildStreamTransport(child_send, child_recv)
        stream_task: asyncio.Task[Any] = asyncio.create_task(
            child.open_stream_async("test.stream")
        )
        try:
            raw = await asyncio.to_thread(parent_recv.recv_bytes, MAX_FRAME_BYTES + 1)
            request = decode_frame(raw)
            stream_id = request["id"]
            # Answer the open with a chunked header whose part math is
            # inconsistent; the pull credit sits in the pipe already.
            parent_send.send_bytes(
                encode_frame(
                    {
                        "v": TRANSPORT_VERSION,
                        "id": stream_id,
                        "type": "event",
                        "seq": 0,
                        "chunked": True,
                        "total": 10,
                        "parts": 999,
                    }
                )
            )
            stream = await asyncio.wait_for(stream_task, timeout=5)
            with pytest.raises(StreamTransportError, match="chunk"):
                await stream.__anext__()
        finally:
            stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream_task
            child_send.close()
            child_recv.close()
            parent_recv.close()
            parent_send.close()

    async def test_transport_install_lifecycle(self):
        assert get_transport() is None
        child_send, child_recv, _, _ = _pipes()
        try:
            installed = install_transport(child_send, child_recv)
            assert get_transport() is installed
        finally:
            clear_transport()
            child_send.close()
            child_recv.close()
        assert get_transport() is None


@pytest.mark.asyncio
class TestChannelIndependence:
    async def test_stream_pumps_are_independent(self):
        async def _source(params: dict[str, Any], principal: Any):
            for i in range(3):
                yield {"i": i}

        registry = {"test.stream": _source}
        a_send, a_recv, a_precv, a_psend = _pipes()
        b_send, b_recv, b_precv, b_psend = _pipes()
        pump_a = asyncio.create_task(
            serve_stream_channel(
                recv_conn=a_precv, send_conn=a_psend, principal=object(), registry=registry
            )
        )
        pump_b = asyncio.create_task(
            serve_stream_channel(
                recv_conn=b_precv, send_conn=b_psend, principal=object(), registry=registry
            )
        )
        try:
            child_a = ChildStreamTransport(a_send, a_recv)
            child_b = ChildStreamTransport(b_send, b_recv)
            stream_a = await child_a.open_stream_async("test.stream")
            stream_b = await child_b.open_stream_async("test.stream")
            assert await stream_a.__anext__() == {"i": 0}
            assert await stream_b.__anext__() == {"i": 0}
            # Break channel A with garbage; channel B completes untouched and
            # its pump is still serving.
            a_send.send_bytes(b"not json")
            assert await asyncio.wait_for(pump_a, timeout=10) == "malformed"
            assert await _drain(stream_b) == [{"i": 1}, {"i": 2}]
            assert not pump_b.done()
        finally:
            a_send.close()
            a_recv.close()
            b_send.close()
            b_recv.close()
            for pump in (pump_a, pump_b):
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            a_precv.close()
            a_psend.close()
            b_precv.close()
            b_psend.close()


@pytest.mark.asyncio
class TestPoolWiring:
    async def test_close_stream_channel_leaves_sdk_and_import(self):
        from unittest.mock import MagicMock

        from src.services.execution.process_pool import ProcessHandle

        handle = ProcessHandle(
            id="stream-close-test",
            process=MagicMock(),
            pid=12345,
            state=MagicMock(),
            work_queue=MagicMock(),
            result_queue=MagicMock(),
            started_at=MagicMock(),
            sdk_req=MagicMock(),
            sdk_resp=MagicMock(),
            imp_req=MagicMock(),
            imp_resp=MagicMock(),
            stream_req=MagicMock(),
            stream_resp=MagicMock(),
        )
        from src.services.execution.process_pool import ProcessPoolManager

        pool = ProcessPoolManager.__new__(ProcessPoolManager)
        pool._close_stream_channel(handle)
        assert handle.stream_req is None
        assert handle.stream_resp is None
        assert handle.sdk_req is not None
        assert handle.sdk_resp is not None
        assert handle.imp_req is not None
        assert handle.imp_resp is not None
        # Idempotent: second close is a no-op.
        pool._close_stream_channel(handle)
