"""Exercise neutral contracts through the real S3 HTTP/signing implementation."""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from src.core.s3_transport import S3Transport
from src.core.storage.contracts import ObjectVersion, StorageError
from src.core.storage.s3 import S3ObjectStore


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"ab"
        yield b"cd"

    async def aclose(self) -> None:
        self.closed = True


def _store(client: httpx.AsyncClient) -> S3ObjectStore:
    return S3ObjectStore("bucket", S3Transport(
        endpoint_url="https://storage.example", access_key_id="fixture-access",
        secret_access_key="fixture-secret", region="us-east-1", client=client,
    ))


@pytest.mark.asyncio
async def test_conditional_read_preserves_opaque_version_and_closes_partial_body():
    stream = TrackedStream()

    def handle(request):
        assert request.method == "GET"
        assert request.headers["if-match"] == '"opaque-not-a-content-hash"'
        assert "AWS4-HMAC-SHA256" in request.headers["authorization"]
        return httpx.Response(200, headers={
            "etag": '"opaque-not-a-content-hash"', "content-length": "4",
            "content-type": "text/plain", "x-amz-meta-label": "fixture",
        }, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        async with _store(client) as store:
            async with store.open_read("some/key", expected_version=ObjectVersion('opaque-not-a-content-hash')) as read:
                assert read.info.version == 'opaque-not-a-content-hash'
                assert read.info.http_etag == '"opaque-not-a-content-hash"'
                assert read.info.metadata == {"label": "fixture"}
                assert read.info.size == 4
                assert await read.body.read(1) == b"a"
            assert stream.closed
        assert not client.is_closed  # injected HTTP client remains caller-owned


@pytest.mark.asyncio
async def test_cancellation_closes_the_owned_read_body():
    stream = TrackedStream()
    ready, blocked = asyncio.Event(), asyncio.Event()

    def handle(request):
        return httpx.Response(200, headers={"etag": '"v1"', "content-length": "4"}, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        async def consume():
            async with _store(client).open_read("key") as read:
                await read.body.read(1)
                ready.set()
                await blocked.wait()

        task = asyncio.create_task(consume())
        await ready.wait()
        task.cancel()
        await asyncio.wait({task})
        with pytest.raises(asyncio.CancelledError):
            task.result()
        assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("status,kind", [(404, "not_found"), (403, "permission_denied"), (412, "precondition_failed")])
async def test_native_errors_become_neutral_failures_without_provider_payloads(status, kind):
    def handle(request):
        return httpx.Response(status, content=b"<Error><Code>FixtureCode</Code><Message>private upstream detail</Message></Error>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(StorageError) as caught:
            async with _store(client).open_read("key"):
                pytest.fail("Failed reads must not expose a body")
    assert caught.value.kind == kind
    assert caught.value.status_code == status
    assert caught.value.provider_code == "FixtureCode"
    assert "private upstream detail" not in str(caught.value)


@pytest.mark.asyncio
async def test_invalid_metadata_also_closes_the_body():
    stream = TrackedStream()

    def handle(request):
        return httpx.Response(200, headers={"content-length": "4"}, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(StorageError, match="backend_error"):
            async with _store(client).open_read("key"):
                pytest.fail("Objects without version witnesses must refuse")
    assert stream.closed


@pytest.mark.asyncio
async def test_consumer_errors_are_not_reclassified_as_storage_failures():
    stream = TrackedStream()
    caller_error = httpx.ConnectError("A different HTTP operation failed")

    def handle(request):
        return httpx.Response(200, headers={"etag": '"v1"', "content-length": "4"}, stream=stream)

    caught_error = None
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        try:
            async with _store(client).open_read("key"):
                raise caller_error
        except httpx.ConnectError as error:
            caught_error = error
    assert caught_error is caller_error
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_checksum_failure_during_body_consumption_is_neutral(streaming):
    stream = TrackedStream()

    def handle(request):
        return httpx.Response(200, headers={
            "etag": '"v1"', "content-length": "4",
            "x-amz-checksum-sha256": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        }, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(StorageError) as caught:
            async with _store(client).open_read("key") as read:
                if streaming:
                    async for _ in read.body.iter_chunks():
                        pass
                else:
                    await read.body.read()
    assert caught.value.provider_code == "ChecksumMismatch"
    assert stream.closed


@pytest.mark.asyncio
async def test_probe_and_stat_use_the_bound_namespace():
    paths = []

    def handle(request):
        assert request.method == "HEAD"
        paths.append(request.url.path)
        return httpx.Response(200, headers={"etag": '"v1"', "content-length": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        store = _store(client)
        await store.probe()
        info = await store.stat("empty")
    assert paths == ["/bucket", "/bucket/empty"]
    assert info.size == 0
    assert info.version == 'v1'
