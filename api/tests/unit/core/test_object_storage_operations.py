"""Neutral write, pagination and signed-request contracts over native S3 wire IO."""
from unittest.mock import AsyncMock
from types import SimpleNamespace

import httpx
import pytest

from src.core.s3_transport import S3Transport
from src.core.storage import object_pages
from src.core.storage.contracts import ContentProperties, ObjectPage, ObjectVersion, PageCursor, StorageError, WriteOptions
from src.core.storage.s3 import S3ObjectStore


def _transport(client, endpoint="https://private.example"):
    return S3Transport(endpoint_url=endpoint, access_key_id="fixture", secret_access_key="fixture", region="us-east-1", client=client)


@pytest.mark.asyncio
@pytest.mark.parametrize("create_only,expected,header,value", [
    (True, None, "if-none-match", "*"),
    (False, ObjectVersion("prior"), "if-match", '"prior"'),
])
async def test_write_binds_conditions_and_properties_to_one_request(create_only, expected, header, value):
    def handle(request):
        assert request.method == "PUT" and request.content == b"payload"
        assert request.headers[header] == value
        assert request.headers["x-amz-meta-label"] == "fixture"
        assert request.headers["content-type"] == "text/plain"
        assert request.headers["cache-control"] == "private, max-age=60"
        assert request.headers["expires"] == "Wed, 21 Oct 2037 07:28:00 GMT"
        return httpx.Response(200, headers={"etag": '"committed"'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await S3ObjectStore("bucket", _transport(client)).write("key", b"payload", options=WriteOptions(
            properties=ContentProperties(content_type="text/plain", cache_control="private, max-age=60", http_expires="Wed, 21 Oct 2037 07:28:00 GMT"),
            metadata={"label": "fixture"}, create_only=create_only, expected_version=expected,
        ))
    assert result.version == "committed"


def test_incompatible_write_conditions_refuse_before_io():
    with pytest.raises(ValueError):
        WriteOptions(create_only=True, expected_version=ObjectVersion("prior"))


@pytest.mark.asyncio
async def test_missing_namespace_is_not_an_object_miss():
    def handle(request):
        return httpx.Response(404, content=b"<Error><Code>NoSuchBucket</Code></Error>")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(StorageError) as caught:
            async with S3ObjectStore("missing", _transport(client)).open_read("key"):
                pytest.fail("Missing namespaces must refuse")
    assert caught.value.kind == "namespace_not_found"


@pytest.mark.asyncio
async def test_listing_preserves_cursor_and_decodes_only_object_names():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=b'''<ListBucketResult><EncodingType>url</EncodingType>
        <Contents><Key>folder/a%2Bb.py</Key><Size>2</Size><ETag>"opaque"</ETag></Contents>
        <CommonPrefixes><Prefix>folder/sub/</Prefix></CommonPrefixes><IsTruncated>true</IsTruncated>
        <NextContinuationToken>next+/%2F</NextContinuationToken></ListBucketResult>''')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        page = await S3ObjectStore("bucket", _transport(client)).list_page("folder/", delimiter="/", cursor=PageCursor("prior+/%2F"), limit=7)
    assert requests[0].url.params["continuation-token"] == "prior+/%2F"
    assert requests[0].url.params["max-keys"] == "7"
    assert page.next_cursor == "next+/%2F"
    assert page.objects[0].key == "folder/a+b.py" and page.objects[0].version == "opaque"
    assert page.prefixes == ("folder/sub/",)


@pytest.mark.asyncio
async def test_truncated_listing_without_cursor_refuses():
    def handle(request):
        return httpx.Response(200, content=b"<ListBucketResult><IsTruncated>true</IsTruncated></ListBucketResult>")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(StorageError, match="backend_error"):
            await S3ObjectStore("bucket", _transport(client)).list_page()


@pytest.mark.asyncio
async def test_repeated_cursor_never_loops_or_silently_truncates():
    store = AsyncMock()
    store.list_page.return_value = ObjectPage((), (), PageCursor("same"))
    with pytest.raises(StorageError) as caught:
        async for _ in object_pages(store):
            pass
    assert caught.value.provider_code == "RepeatedPageCursor"
    assert store.list_page.await_count == 2


@pytest.mark.asyncio
async def test_public_signer_returns_complete_request_without_changing_private_io():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, headers={"etag": '"v1"', "content-length": "0"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        store = S3ObjectStore("bucket", _transport(client), signer=_transport(client, "https://public.example"))
        await store.stat("key")
        upload = await store.signed_request("key", "PUT", content_type="text/plain", expires_in=90)
        download = await store.signed_request("key", "GET", response_content_disposition='attachment; filename="x.txt"')
    assert requests[0].url.host == "private.example"
    assert httpx.URL(upload.url).host == "public.example"
    assert upload.method == "PUT" and upload.headers == {"Content-Type": "text/plain"}
    assert upload.expires_in == 90 and "X-Amz-Signature" in httpx.URL(upload.url).params
    assert httpx.URL(download.url).params["response-content-disposition"] == 'attachment; filename="x.txt"'
    assert upload.url not in repr(upload)


@pytest.mark.asyncio
async def test_origin_relative_proxy_prefix_preserves_the_internal_signature():
    settings = SimpleNamespace(
        s3_bucket="bucket", s3_endpoint_url="http://internal.example:8333",
        s3_public_endpoint_url="/s3", s3_access_key="fixture", s3_secret_key="fixture", s3_region="us-east-1", s3_multipart_copy_mode="server",
    )
    async with S3ObjectStore.from_settings(settings) as store:
        signed = await store.signed_request("folder/a+b.txt", "PUT", content_type="text/plain")
    assert signed.url.startswith("/s3/bucket/folder/a%2Bb.txt?")
    assert "X-Amz-Signature" in httpx.URL(signed.url).params
    assert signed.headers == {"Content-Type": "text/plain"}


@pytest.mark.asyncio
@pytest.mark.parametrize("length", [0, 12])
async def test_signed_upload_binds_the_exact_content_length(length):
    from urllib.parse import parse_qs, urlsplit

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        signed = await S3ObjectStore("bucket", _transport(client)).signed_request("upload.txt", "PUT", content_type="text/plain", content_length=length)
        headers = parse_qs(urlsplit(signed.url).query)["X-Amz-SignedHeaders"][0].split(";")
        assert "content-length" in headers
        assert signed.headers["Content-Length"] == str(length)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,length", [("GET", 12), ("PUT", -1), ("PUT", True)])
async def test_signed_content_length_rejects_invalid_options(method, length):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        with pytest.raises(ValueError):
            await S3ObjectStore("bucket", _transport(client)).signed_request("key", method, content_length=length)


@pytest.mark.asyncio
async def test_bulk_delete_batches_native_requests_and_does_not_hide_partial_failure():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=b"<DeleteResult/>")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        await S3ObjectStore("bucket", _transport(client)).delete_many([f"key-{i}" for i in range(1001)])
    import xml.etree.ElementTree as ET
    assert [len(ET.fromstring(request.content).findall("Object")) for request in requests] == [1000, 1]

    def failed(request):
        return httpx.Response(200, content=b"<DeleteResult><Error><Key>key</Key><Code>AccessDenied</Code></Error></DeleteResult>")
    async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as client:
        with pytest.raises(StorageError) as caught:
            await S3ObjectStore("bucket", _transport(client)).delete_many(["key"])
    assert caught.value.provider_code == "PartialDeleteFailure"


@pytest.mark.asyncio
async def test_persisted_s3_witness_round_trips_and_pins_the_original_version():
    seen = []
    def handle(request):
        seen.append(request)
        assert request.headers["if-match"] == '\"legacy-opaque-v1\"'
        return httpx.Response(412, content=b"<Error><Code>PreconditionFailed</Code></Error>")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        store = S3ObjectStore("bucket", _transport(client))
        version = store.restore_version('"legacy-opaque-v1"')
        assert version == "legacy-opaque-v1"
        assert store.serialize_version(version) == '"legacy-opaque-v1"'
        with pytest.raises(StorageError) as caught:
            await store.write("pointer", b"stale update", options=WriteOptions(expected_version=version))
    assert caught.value.kind == "precondition_failed" and len(seen) == 1
