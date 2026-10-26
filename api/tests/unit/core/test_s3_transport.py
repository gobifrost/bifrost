"""Contracts for the narrow HTTP/CRT S3 transport."""

from __future__ import annotations

import base64
import gzip
import asyncio
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest
from awscrt import checksums

from src.core.s3_transport import S3Error, S3NoSuchKey, S3Transport, SyncS3Transport


class RawStream(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class RawSyncStream(httpx.SyncByteStream):
    def __init__(self, content: bytes) -> None:
        self.content = content

    def __iter__(self):
        yield self.content

    def close(self) -> None:
        return None


class DirectSyncClient:
    """A synchronous fixture that returns the supplied response unconsumed."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self._requests = httpx.Client()

    def build_request(self, *args, **kwargs):
        return self._requests.build_request(*args, **kwargs)

    def send(self, request, *, stream: bool):
        assert stream is True
        return self.handler(request)

    def close(self) -> None:
        self._requests.close()


def transport_for(handler: httpx.MockTransport, **kwargs) -> S3Transport:
    return S3Transport(
        endpoint_url="https://s3.example.test",
        access_key_id="AKIDEXAMPLE",
        secret_access_key="secret",
        region="us-east-1",
        client=httpx.AsyncClient(transport=handler),
        now=lambda: datetime(2026, 10, 25, tzinfo=UTC),
        **kwargs,
    )


def regional_transport_for(handler: httpx.MockTransport, **kwargs) -> S3Transport:
    return S3Transport(
        endpoint_url=None,
        access_key_id="AKIDEXAMPLE",
        secret_access_key="secret",
        region="us-west-1",
        client=httpx.AsyncClient(transport=handler),
        now=lambda: datetime(2026, 10, 25, tzinfo=UTC),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_get_object_preserves_raw_encoded_bytes_and_closes_when_consumed() -> None:
    raw = gzip.compress(b"stored bytes")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip", "Content-Length": str(len(raw))},
            stream=RawStream(raw),
            request=request,
        )

    client = transport_for(httpx.MockTransport(handler))
    response = await client.get_object(Bucket="bucket", Key="café/%2F//file")
    async with response["Body"] as body:
        assert await body.read() == raw

    assert seen[0].url.raw_path == b"/bucket/caf%C3%A9/%252F//file"
    assert seen[0].headers["x-amz-checksum-mode"] == "ENABLED"
    await client.aclose()


@pytest.mark.asyncio
async def test_body_keeps_stream_open_between_bounded_reads_and_closes_on_cancel() -> None:
    stream = RawStream(b"first", b"second")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream, request=request)

    client = transport_for(httpx.MockTransport(handler))
    response = await client.get_object(Bucket="bucket", Key="object")
    body = response["Body"]
    assert await body.read(3) == b"fir"
    assert stream.closed is False
    assert await body.read(4) == b"stse"
    assert await body.read() == b"cond"
    assert stream.closed is True
    await client.aclose()


@pytest.mark.asyncio
async def test_cancelled_body_read_closes_the_response() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class BlockingStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            started.set()
            await release.wait()
            yield b"never"

        async def aclose(self) -> None:
            self.closed = True

    stream = BlockingStream()
    client = transport_for(httpx.MockTransport(lambda request: httpx.Response(200, stream=stream, request=request)))
    body = (await client.get_object(Bucket="bucket", Key="object"))["Body"]
    task = asyncio.create_task(body.read())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stream.closed is True
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("x-amz-checksum-crc32", lambda data: checksums.crc32(data).to_bytes(4, "big")),
        ("x-amz-checksum-crc64nvme", lambda data: checksums.crc64nvme(data).to_bytes(8, "big")),
    ],
)
async def test_streaming_body_rejects_crc_checksum_mismatch(header, expected) -> None:
    correct = b"correct object"
    stream = RawStream(b"corrupt object")
    expected_value = base64.b64encode(expected(correct)).decode()
    client = transport_for(
        httpx.MockTransport(
            lambda request: httpx.Response(200, headers={header: expected_value}, stream=stream, request=request)
        )
    )

    body = (await client.get_object(Bucket="bucket", Key="object"))["Body"]
    with pytest.raises(S3Error, match="checksum"):
        await body.read()
    assert stream.closed is True
    await client.aclose()


@pytest.mark.asyncio
async def test_s3_xml_error_is_structured_and_missing_key_has_dedicated_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            content=b"<Error><Code>NoSuchKey</Code><Message>missing</Message></Error>",
            request=request,
        )

    client = transport_for(httpx.MockTransport(handler))
    with pytest.raises(S3NoSuchKey) as raised:
        await client.head_object(Bucket="bucket", Key="gone")

    assert raised.value.code == "NoSuchKey"
    assert raised.value.status_code == 404
    assert raised.value.response["Error"]["Message"] == "missing"
    await client.aclose()


@pytest.mark.asyncio
async def test_region_redirect_rebuilds_async_default_endpoint() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(301, headers={"x-amz-bucket-region": "eu-west-1"}, request=request)
        return httpx.Response(200, request=request)

    client = regional_transport_for(httpx.MockTransport(handler))
    await client.head_bucket(Bucket="bucket")
    assert seen == [
        "https://s3.us-west-1.amazonaws.com/bucket",
        "https://s3.eu-west-1.amazonaws.com/bucket",
    ]
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "code"), [(429, "SlowDown"), (400, "BadDigest")])
async def test_async_retry_exhaustion_uses_five_attempts_and_full_jitter(status, code) -> None:
    seen: list[httpx.Request] = []
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, content=f"<Error><Code>{code}</Code></Error>".encode(), request=request)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    client = transport_for(httpx.MockTransport(handler), sleep=sleep, random_value=lambda: 0.5)
    with pytest.raises(S3Error) as raised:
        await client.head_bucket(Bucket="bucket")
    assert raised.value.code == code
    assert len(seen) == 5
    assert delays == [0.5, 1.0, 2.0, 4.0]
    await client.aclose()


def test_sync_region_redirect_rebuilds_default_endpoint() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(301, headers={"x-amz-bucket-region": "eu-west-1"}, request=request)
        return httpx.Response(200, stream=RawSyncStream(b"object"), request=request)

    client = SyncS3Transport(
        endpoint_url=None,
        access_key_id="AKIDEXAMPLE",
        secret_access_key="secret",
        region="us-west-1",
        client=DirectSyncClient(handler),
    )
    assert client.get_object(Bucket="bucket", Key="object")["Body"].read() == b"object"
    assert seen == [
        "https://s3.us-west-1.amazonaws.com/bucket/object",
        "https://s3.eu-west-1.amazonaws.com/bucket/object",
    ]
    client.close()


def test_sync_retry_exhaustion_uses_five_attempts_and_full_jitter() -> None:
    seen: list[httpx.Request] = []
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, stream=RawSyncStream(b"<Error><Code>SlowDown</Code></Error>"), request=request)

    client = SyncS3Transport(
        endpoint_url="https://s3.example.test",
        access_key_id="AKIDEXAMPLE",
        secret_access_key="secret",
        region="us-east-1",
        client=DirectSyncClient(handler),
        sleep=delays.append,
        random_value=lambda: 0.5,
    )
    with pytest.raises(S3Error) as raised:
        client.get_object(Bucket="bucket", Key="object")
    assert raised.value.code == "SlowDown"
    assert len(seen) == 5
    assert delays == [0.5, 1.0, 2.0, 4.0]
    client.close()


@pytest.mark.asyncio
async def test_copy_object_rejects_embedded_200_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<Error><Code>InternalError</Code><Message>copy failed</Message></Error>",
            request=request,
        )

    client = transport_for(httpx.MockTransport(handler))
    with pytest.raises(S3Error, match="copy failed"):
        await client.copy_object(
            Bucket="bucket", Key="destination", CopySource={"Bucket": "bucket", "Key": "source"}
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_complete_multipart_upload_sends_destination_guard() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"<CompleteMultipartUploadResult/>", request=request)

    client = transport_for(httpx.MockTransport(handler))
    await client.complete_multipart_upload(
        Bucket="bucket",
        Key="destination",
        UploadId="upload",
        MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": '"etag"'}]},
        IfNoneMatch="*",
    )

    assert seen[0].headers["if-none-match"] == "*"
    await client.aclose()


@pytest.mark.asyncio
async def test_presign_uses_only_browser_stable_headers() -> None:
    client = transport_for(httpx.MockTransport(lambda request: httpx.Response(500, request=request)))
    url = await client.generate_presigned_url(
        "put_object",
        Params={"Bucket": "bucket", "Key": "object", "ContentType": "text/plain"},
        ExpiresIn=60,
    )

    signed_headers = parse_qs(httpx.URL(url).query.decode())["X-Amz-SignedHeaders"][0]
    assert signed_headers == "content-type;host"
    await client.aclose()


@pytest.mark.asyncio
async def test_presigned_urls_are_usable_by_ordinary_http_clients() -> None:
    seen: list[httpx.Request] = []

    def fixture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert "authorization" not in request.headers
        assert request.url.params["X-Amz-Algorithm"] == "AWS4-HMAC-SHA256"
        return httpx.Response(200, request=request)

    transport = httpx.MockTransport(fixture)
    signer = transport_for(transport)
    put_url = await signer.generate_presigned_url(
        "put_object", Params={"Bucket": "bucket", "Key": "put", "ContentType": "text/plain"}
    )
    get_url = await signer.generate_presigned_url("get_object", Params={"Bucket": "bucket", "Key": "get"})
    async with httpx.AsyncClient(transport=transport) as browser:
        assert (await browser.put(put_url, content=b"browser upload", headers={"content-type": "text/plain"})).status_code == 200
        assert (await browser.get(get_url)).status_code == 200
    assert [request.method for request in seen] == ["PUT", "GET"]
    await signer.aclose()
