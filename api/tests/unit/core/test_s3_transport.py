"""Contracts for the narrow HTTP/CRT S3 transport."""

from __future__ import annotations

import base64
import gzip
import asyncio
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs

import httpx
import pytest
from awscrt import checksums

from src.core import s3_transport
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


def test_default_credentials_provider_is_reused_per_process(monkeypatch) -> None:
    first, second = object(), object()
    factory = Mock(side_effect=[first, second])
    monkeypatch.setattr(s3_transport, "_default_credentials_provider", None)
    monkeypatch.setattr(s3_transport.os, "getpid", Mock(side_effect=[101, 101, 202]))
    monkeypatch.setattr(
        s3_transport.auth,
        "AwsCredentialsProvider",
        SimpleNamespace(new_default_chain=factory),
    )

    assert s3_transport._credentials_provider(None, None, None) is first
    assert s3_transport._credentials_provider(None, None, None) is first
    assert s3_transport._credentials_provider(None, None, None) is second
    assert factory.call_count == 2


@pytest.mark.parametrize("access_key_id,secret_access_key", [("access", None), (None, "secret")])
def test_partial_static_credentials_are_rejected(access_key_id, secret_access_key) -> None:
    with pytest.raises(s3_transport.S3PartialCredentialsError):
        s3_transport._credentials_provider(access_key_id, secret_access_key, None)


def test_transport_rejects_file_like_body() -> None:
    with pytest.raises(TypeError, match="str, bytes, or bytearray"):
        s3_transport._to_bytes(BytesIO(b"archive"))


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
        await asyncio.gather(task)
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
async def test_request_signature_excludes_headers_removed_by_proxies() -> None:
    """Spaces cannot verify a signed Connection header its ingress removes."""
    def handler(request: httpx.Request) -> httpx.Response:
        signed = request.headers["authorization"].partition("SignedHeaders=")[2].split(",")[0].split(";")
        assert "connection" not in signed
        assert "user-agent" not in signed
        assert {"host", "x-amz-date", "x-amz-content-sha256", "content-md5"} <= set(signed)
        return httpx.Response(200, content=b"<DeleteResult/>", request=request)

    async with transport_for(httpx.MockTransport(handler)) as client:
        await client.delete_objects(Bucket="bucket", Delete={"Objects": [{"Key": "owned-fixture"}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 307, 308, 400])
async def test_region_redirect_rebuilds_async_default_endpoint(status: int) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(
                status, headers={"x-amz-bucket-region": "eu-west-1"},
                content=b"<Error><Code>AuthorizationHeaderMalformed</Code></Error>", request=request,
            )
        assert "/eu-west-1/s3/aws4_request" in request.headers["authorization"]
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


@pytest.mark.asyncio
@pytest.mark.parametrize("encoded", ["report+%281%29%2B.pdf", "report%20%281%29%2B.pdf"])
async def test_list_objects_v2_decodes_spaces_without_losing_literal_pluses(encoded: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                "<ListBucketResult><EncodingType>url</EncodingType>"
                f"<Prefix>{encoded}</Prefix><Contents><Key>{encoded}</Key><Size>1</Size></Contents>"
                f"<CommonPrefixes><Prefix>{encoded}/</Prefix></CommonPrefixes></ListBucketResult>"
            ),
            request=request,
        )

    async with transport_for(httpx.MockTransport(handler)) as client:
        result = await client.list_objects_v2(Bucket="bucket", Prefix="report (")

    assert result["Contents"][0]["Key"] == "report (1)+.pdf"
    assert result["Prefix"] == "report (1)+.pdf"
    assert result["CommonPrefixes"] == [{"Prefix": "report (1)+.pdf/"}]


@pytest.mark.asyncio
async def test_list_objects_v2_paginator_preserves_options_and_continues() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            content = b"""<ListBucketResult><EncodingType>url</EncodingType><Prefix>caf%C3%A9/</Prefix><IsTruncated>true</IsTruncated><NextContinuationToken>next</NextContinuationToken><KeyCount>1</KeyCount><Contents><Key>caf%C3%A9/%252F//source.bin</Key><ETag>etag-1</ETag><Size>1</Size></Contents><CommonPrefixes><Prefix>caf%C3%A9/%252F/</Prefix></CommonPrefixes></ListBucketResult>"""
        else:
            content = b"""<ListBucketResult><EncodingType>url</EncodingType><Prefix>caf%C3%A9/</Prefix><IsTruncated>false</IsTruncated><KeyCount>1</KeyCount><Contents><Key>caf%C3%A9/two</Key><ETag>etag-2</ETag><Size>2</Size></Contents></ListBucketResult>"""
        return httpx.Response(200, content=content, request=request)

    client = transport_for(httpx.MockTransport(handler))
    pages = [
        page async for page in client.get_paginator("list_objects_v2").paginate(
            Bucket="bucket", Prefix="café/", Delimiter="/", MaxKeys=2,
        )
    ]

    assert [page["Contents"][0]["Key"] for page in pages] == ["café/%2F//source.bin", "café/two"]
    assert pages[0]["Prefix"] == "café/"
    assert pages[0]["CommonPrefixes"] == [{"Prefix": "café/%2F/"}]
    assert dict(seen[0].url.params) == {"list-type": "2", "prefix": "café/", "delimiter": "/", "max-keys": "2", "encoding-type": "url"}
    assert dict(seen[1].url.params) == {
        "list-type": "2", "prefix": "café/", "delimiter": "/", "max-keys": "2", "encoding-type": "url", "continuation-token": "next",
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_list_objects_v2_paginator_rejects_a_repeated_token() -> None:
    class RepeatingListClient:
        calls = 0

        async def list_objects_v2(self, **kwargs):
            self.calls += 1
            if self.calls > 2:
                raise AssertionError("paginator requested a repeated token again")
            return {"IsTruncated": True, "NextContinuationToken": "repeat"}

    client = RepeatingListClient()
    paginator = s3_transport.S3ListObjectsV2Paginator(client)
    with pytest.raises(RuntimeError, match="repeated continuation token"):
        async for _ in paginator.paginate(Bucket="bucket", Prefix="prefix/"):
            pass
    assert client.calls == 2


@pytest.mark.parametrize("status", [301, 307, 308, 400])
def test_sync_region_redirect_rebuilds_default_endpoint(status: int) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(
                status, headers={"x-amz-bucket-region": "eu-west-1"},
                content=b"<Error><Code>AuthorizationHeaderMalformed</Code></Error>", request=request,
            )
        assert "/eu-west-1/s3/aws4_request" in request.headers["authorization"]
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
@pytest.mark.parametrize("operation", ["copy", "part-copy", "complete"])
async def test_embedded_transient_200_error_retries_then_succeeds(operation: str) -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                200,
                content=b"<Error><Code>SlowDown</Code><Message>try again</Message></Error>",
                request=request,
            )
        return httpx.Response(200, content=b"<Result><ETag>etag</ETag></Result>", request=request)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    client = transport_for(httpx.MockTransport(handler), sleep=sleep, random_value=lambda: 0.5)
    if operation == "copy":
        await client.copy_object(Bucket="bucket", Key="destination", CopySource={"Bucket": "bucket", "Key": "source"})
    elif operation == "part-copy":
        await client.upload_part_copy(
            Bucket="bucket", Key="destination", UploadId="upload", PartNumber=1,
            CopySource={"Bucket": "bucket", "Key": "source"},
        )
    else:
        await client.complete_multipart_upload(
            Bucket="bucket", Key="destination", UploadId="upload",
            MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": '"etag"'}]},
        )

    assert attempts == 2
    assert delays == [0.5]
    await client.aclose()


@pytest.mark.asyncio
async def test_embedded_transient_200_error_exhausts_retry_budget() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            content=b"<Error><Code>InternalError</Code><Message>copy failed</Message></Error>",
            request=request,
        )

    async def sleep(delay: float) -> None:
        delays.append(delay)

    client = transport_for(httpx.MockTransport(handler), sleep=sleep, random_value=lambda: 0.5)
    with pytest.raises(S3Error) as raised:
        await client.copy_object(Bucket="bucket", Key="destination", CopySource={"Bucket": "bucket", "Key": "source"})

    assert raised.value.code == "InternalError"
    assert attempts == 5
    assert delays == [0.5, 1.0, 2.0, 4.0]
    await client.aclose()


@pytest.mark.asyncio
async def test_copy_object_rejects_nonretryable_embedded_200_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<Error><Code>AccessDenied</Code><Message>copy denied</Message></Error>",
            request=request,
        )

    client = transport_for(httpx.MockTransport(handler))
    with pytest.raises(S3Error, match="copy denied"):
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
