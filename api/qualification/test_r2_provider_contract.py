# Provenance: R2 qualification owner report r2-provider-offline-qualification-20261106-06.
# Supplied SHA256: 215d8a88894e0bdcac90e40c4e8539976b9fe9062789540b5ed5a212affe057c.
# Integration changes: formatting and conventional receiver naming; assertions unchanged.
"""Typed-provider wire qualification with synthetic credentials and network none.

These assertions exercise real committed provider/transport code. Mock HTTP
responses prove client contracts only; they are never real R2 acceptance.
"""

import asyncio
import hashlib
import json
from pathlib import Path
import ssl
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, quote

import httpx
from src.config import Settings
from src.core import s3_transport as native
from src.core.storage import object_store, object_pages
from src.core.storage.contracts import (
    ContentProperties,
    CopyConditionFailed,
    CopyOptions,
    ObjectInfo,
    StorageError,
    WriteOptions,
)

ENDPOINT = "https://r2-qualification.invalid"
BUCKET = "synthetic-provider-qualification"
PREFIX = "synthetic-typed/"
WIRE = []


def error(request, status, code):
    return httpx.Response(
        status, request=request, content=f"<Error><Code>{code}</Code></Error>".encode()
    )


class SyntheticResponseStream(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data

    async def __aiter__(self):
        for offset in range(0, len(self.data), 65536):
            yield self.data[offset : offset + 65536]


class TypedProvider(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.seen = []
        self.settings = Settings(
            _env_file=None,
            environment="testing",
            s3_endpoint_url=ENDPOINT,
            s3_bucket=BUCKET,
            s3_access_key="SYNTHETICQUALKEY",
            s3_secret_key="synthetic-qualification-only",
            s3_region="auto",
            s3_multipart_copy_mode="stream",
        )
        self.store = object_store(self.settings)

        def unexpected(request):
            raise AssertionError(
                "Unexpected synthetic wire operation: "
                + request.method
                + " "
                + request.url.path
            )

        self.handler = unexpected

        async def send(client, request):
            self.assertTrue(
                request.url.scheme == "https"
                and request.url.host == "r2-qualification.invalid"
            )
            self.assertTrue(
                request.url.path == "/" + BUCKET
                or request.url.path.startswith("/" + BUCKET + "/")
            )
            authorization = request.headers.get("authorization", "")
            self.assertTrue(authorization.startswith("AWS4-HMAC-SHA256 "))
            self.assertIn("/auto/s3/aws4_request", authorization)
            for header in ("if-match", "if-none-match", "range"):
                if header in request.headers:
                    self.assertIn(
                        header,
                        authorization.split("SignedHeaders=", 1)[1]
                        .split(",", 1)[0]
                        .split(";"),
                    )
            self.seen.append(request)
            response = self.handler(request)
            if asyncio.iscoroutine(response):
                response = await response
            WIRE.append(
                {
                    "test": self._testMethodName,
                    "method": request.method,
                    "path": request.url.path,
                    "query_keys": sorted(request.url.params.keys()),
                    "conditions": {
                        k: v
                        for k, v in request.headers.items()
                        if k
                        in (
                            "if-match",
                            "if-none-match",
                            "range",
                            "x-amz-copy-source-if-match",
                        )
                    },
                    "sent_bytes": len(request.content),
                    "response": response.status_code,
                    "synthetic": True,
                }
            )
            return response

        self.patcher = patch.object(httpx.AsyncClient, "_send_single_request", new=send)
        self.patcher.start()
        await self.store.__aenter__()

    async def asyncTearDown(self):
        try:
            await self.store.__aexit__(None, None, None)
            await native.close_shared_s3_client()
            self.assertIsNone(native._default_credentials_provider)
        finally:
            self.patcher.stop()
            Path("/results/typed-provider-wire.json").write_text(
                json.dumps(WIRE, indent=2) + "\n"
            )

    def source(self, size):
        return ObjectInfo(
            PREFIX + "source",
            size,
            self.store.restore_version('"source-v1"'),
            None,
            ContentProperties(
                content_type="application/octet-stream",
                cache_control="private",
                http_expires="Wed, 21 Oct 2037 07:28:00 GMT",
            ),
            {"trial": "synthetic"},
            '"source-v1"',
        )

    def multipart_handler(
        self,
        body,
        *,
        complete_status=200,
        missing_version=False,
        ignored_range=False,
        changed_second=False,
        abort_status=204,
    ):
        part_reads = []

        def handle(request):
            q = request.url.params
            if request.method == "POST" and "uploads" in q:
                return httpx.Response(
                    200,
                    request=request,
                    content=b"<InitiateMultipartUploadResult><UploadId>owned-upload</UploadId></InitiateMultipartUploadResult>",
                )
            if request.method == "GET":
                self.assertEqual(request.headers["if-match"], '"source-v1"')
                if body:
                    start, end = map(
                        int, request.headers["range"].removeprefix("bytes=").split("-")
                    )
                    part_reads.append((start, end))
                    data = body[start : end + 1]
                    return httpx.Response(
                        200 if ignored_range else 206,
                        request=request,
                        stream=SyntheticResponseStream(data),
                        headers={
                            "content-length": str(len(data)),
                            "etag": '"source-v2"'
                            if changed_second and len(part_reads) == 2
                            else '"source-v1"',
                            "content-range": f"bytes {start}-{end}/{len(body)}",
                        },
                    )
                return httpx.Response(
                    200,
                    request=request,
                    stream=SyntheticResponseStream(b""),
                    headers={"etag": '"source-v1"', "content-length": "0"},
                )
            if request.method == "PUT" and "partNumber" in q:
                self.assertNotIn("x-amz-copy-source", request.headers)
                self.assertLessEqual(len(request.content), 8 * 1024**2)
                return httpx.Response(
                    200,
                    request=request,
                    headers={"etag": '"part-' + q["partNumber"] + '"'},
                )
            if request.method == "POST" and "uploadId" in q:
                self.assertEqual(request.headers["if-none-match"], "*")
                if complete_status != 200:
                    return error(request, complete_status, "PreconditionFailed")
                payload = (
                    b"<CompleteMultipartUploadResult></CompleteMultipartUploadResult>"
                    if missing_version
                    else b'<CompleteMultipartUploadResult><ETag>"committed-2"</ETag></CompleteMultipartUploadResult>'
                )
                return httpx.Response(200, request=request, content=payload)
            if request.method == "DELETE" and "uploadId" in q:
                if abort_status != 204:
                    return error(request, abort_status, "AccessDenied")
                return httpx.Response(204, request=request)
            raise AssertionError("Unexpected MPU operation")

        self.handler = handle
        return part_reads

    async def test_actual_factory_preserves_explicit_r2_mode_and_tls(self):
        self.assertEqual(self.store._copy_mode, "stream")
        self.assertEqual(self.store._transport.region, "auto")
        context = native._process_ssl_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(len(context.get_ca_certs()), 148)
        self.assertEqual(
            hashlib.sha256(
                Path("/etc/ssl/certs/ca-certificates.crt").read_bytes()
            ).hexdigest(),
            "e9c7b63ee2623386e9b0a3ee32f499d01a8ea23dc83b8383ddff9f8e749543b7",
        )
        self.assertFalse(self.seen)

    async def test_conditional_put_uses_normal_provider_and_returns_commit_version(
        self,
    ):
        def handle(r):
            self.assertEqual(r.method, "PUT")
            self.assertEqual(r.headers["if-match"], '"prior-2"')
            return httpx.Response(200, request=r, headers={"etag": '"commit-3"'})

        self.handler = handle
        result = await self.store.write(
            PREFIX + "put",
            b"bytes",
            options=WriteOptions(
                expected_version=self.store.restore_version('"prior-2"')
            ),
        )
        self.assertEqual(self.store.serialize_version(result.version), '"commit-3"')
        self.assertEqual(len(self.seen), 1)

    async def test_create_only_conflict_is_not_retried_or_overwritten(self):
        def handle(r):
            self.assertEqual(r.headers["if-none-match"], "*")
            return error(r, 412, "PreconditionFailed")

        self.handler = handle
        with self.assertRaises(StorageError) as caught:
            await self.store.write(
                PREFIX + "create", b"bytes", options=WriteOptions(create_only=True)
            )
        self.assertEqual(caught.exception.kind, "precondition_failed")
        self.assertEqual(len(self.seen), 1)

    async def test_conditional_delete_signs_observed_version_and_preserves_stale_failure(
        self,
    ):
        def handle(r):
            self.assertEqual(r.method, "DELETE")
            self.assertEqual(r.headers["if-match"], '"observed-2"')
            return error(r, 412, "PreconditionFailed")

        self.handler = handle
        with self.assertRaises(StorageError) as caught:
            await self.store.delete(
                PREFIX + "delete",
                expected_version=self.store.restore_version('"observed-2"'),
            )
        self.assertEqual(caught.exception.kind, "precondition_failed")
        self.assertEqual(len(self.seen), 1)

    async def test_raw_multipart_version_round_trips_without_hash_interpretation(self):
        value = '"not-an-md5-7"'
        self.assertEqual(
            self.store.serialize_version(self.store.restore_version(value)), value
        )
        self.assertEqual(self.store.restore_version(value), "not-an-md5-7")

    async def test_metadata_expires_and_encoded_keys_survive_public_methods(self):
        properties = ContentProperties(
            "text/plain",
            'attachment; filename="synthetic.txt"',
            "identity",
            "en",
            "private, max-age=11",
            "Wed, 21 Oct 2037 07:28:00 GMT",
        )
        headers = {
            "content-type": properties.content_type,
            "content-disposition": properties.content_disposition,
            "content-encoding": properties.content_encoding,
            "content-language": properties.content_language,
            "cache-control": properties.cache_control,
            "expires": properties.http_expires,
            "x-amz-meta-trial": "synthetic",
        }
        key = PREFIX + "encoding/space + percent% unicode-é.txt"

        def handle(r):
            self.assertEqual(
                r.url.raw_path.split(b"?")[0],
                ("/" + BUCKET + "/" + quote(key, safe="/")).encode(),
            )
            if r.method == "PUT":
                for name, value in headers.items():
                    self.assertEqual(r.headers[name], value)
                return httpx.Response(200, request=r, headers={"etag": '"metadata"'})
            self.assertEqual(r.method, "HEAD")
            return httpx.Response(
                200,
                request=r,
                headers={**headers, "etag": '"metadata"', "content-length": "3"},
            )

        self.handler = handle
        await self.store.write(
            key,
            b"abc",
            options=WriteOptions(
                properties=properties, metadata={"trial": "synthetic"}
            ),
        )
        info = await self.store.stat(key)
        self.assertEqual(info.properties, properties)
        self.assertEqual(info.metadata, {"trial": "synthetic"})

    async def test_batch_http200_per_object_errors_refuse_success(self):
        self.handler = lambda r: httpx.Response(
            200,
            request=r,
            content=b"<DeleteResult><Error><Key>synthetic-typed/a</Key><Code>AccessDenied</Code></Error></DeleteResult>",
        )
        with self.assertRaises(StorageError) as caught:
            await self.store.delete_many([PREFIX + "a"])
        self.assertEqual(caught.exception.provider_code, "PartialDeleteFailure")

    async def test_conditioned_stream_copy_crosses_real_8MiB_boundary(self):
        body = b"a" * (8 * 1024**2) + b"b" * 1024**2
        reads = self.multipart_handler(body)
        result = await self.store.copy(
            self.source(len(body)),
            PREFIX + "destination",
            options=CopyOptions(create_only=True),
        )
        self.assertEqual(reads, [(0, 8 * 1024**2 - 1), (8 * 1024**2, len(body) - 1)])
        self.assertEqual(
            self.seen[0].headers["expires"], "Wed, 21 Oct 2037 07:28:00 GMT"
        )
        self.assertEqual(self.seen[0].headers["x-amz-meta-trial"], "synthetic")
        self.assertEqual(self.store.serialize_version(result.version), '"committed-2"')
        self.assertFalse(any(r.method == "HEAD" for r in self.seen))
        self.assertEqual(
            sum(r.method == "POST" and "uploadId" in r.url.params for r in self.seen), 1
        )

    async def test_same_size_version_change_between_parts_aborts_without_publish(self):
        body = b"x" * (9 * 1024**2)
        self.multipart_handler(body, changed_second=True)
        with self.assertRaises(CopyConditionFailed) as caught:
            await self.store.copy(
                self.source(len(body)),
                PREFIX + "destination",
                options=CopyOptions(create_only=True),
            )
        self.assertEqual(caught.exception.side, "source")
        self.assertEqual(sum(r.method == "DELETE" for r in self.seen), 1)
        self.assertFalse(
            any(r.method == "POST" and "uploadId" in r.url.params for r in self.seen)
        )

    async def test_ignored_range_aborts_instead_of_accepting_full_body(self):
        body = b"x" * 4096
        self.multipart_handler(body, ignored_range=True)
        with self.assertRaises(StorageError) as caught:
            await self.store.copy(
                self.source(len(body)),
                PREFIX + "destination",
                options=CopyOptions(create_only=True),
            )
        self.assertEqual(caught.exception.provider_code, "InvalidCopyRange")
        self.assertEqual(sum(r.method == "DELETE" for r in self.seen), 1)

    async def test_destination_completion_conflict_has_correct_side(self):
        body = b"x" * 4096
        self.multipart_handler(body, complete_status=412)
        with self.assertRaises(CopyConditionFailed) as caught:
            await self.store.copy(
                self.source(len(body)),
                PREFIX + "destination",
                options=CopyOptions(create_only=True),
            )
        self.assertEqual(caught.exception.side, "destination")
        self.assertEqual(sum(r.method == "DELETE" for r in self.seen), 1)

    async def test_missing_commit_witness_is_not_replaced_with_racy_head(self):
        body = b"x" * 4096
        self.multipart_handler(body, missing_version=True)
        with self.assertRaises(StorageError) as caught:
            await self.store.copy(
                self.source(len(body)),
                PREFIX + "destination",
                options=CopyOptions(create_only=True),
            )
        self.assertEqual(caught.exception.provider_code, "MissingCommittedVersion")
        self.assertFalse(any(r.method == "HEAD" for r in self.seen))

    async def test_cancelled_producer_aborts_owned_upload_and_propagates(self):
        async def chunks():
            yield b"x" * (8 * 1024**2)
            raise asyncio.CancelledError()

        self.multipart_handler(b"")
        with self.assertRaises(asyncio.CancelledError):
            await self.store.write_stream(
                PREFIX + "cancelled", chunks(), options=WriteOptions(create_only=True)
            )
        self.assertEqual(sum(r.method == "DELETE" for r in self.seen), 1)
        self.assertFalse(
            any(r.method == "POST" and "uploadId" in r.url.params for r in self.seen)
        )

    async def test_signed_requests_keep_required_headers_and_relative_proxy(self):
        signed = await self.store.signed_request(
            PREFIX + "signed", "PUT", expires_in=120, content_type="text/plain"
        )
        self.assertEqual(signed.method, "PUT")
        self.assertEqual(signed.headers, {"Content-Type": "text/plain"})
        self.assertIn(
            "content-type",
            parse_qs(httpx.URL(signed.url).query.decode())["X-Amz-SignedHeaders"][0],
        )
        download = await self.store.signed_request(
            PREFIX + "signed",
            "GET",
            response_content_disposition='attachment; filename="synthetic.txt"',
        )
        self.assertEqual(
            parse_qs(httpx.URL(download.url).query.decode())[
                "response-content-disposition"
            ],
            ['attachment; filename="synthetic.txt"'],
        )
        relative = object_store(
            self.settings.model_copy(update={"s3_public_endpoint_url": "/s3"})
        )
        async with relative:
            signed = await relative.signed_request(
                PREFIX + "signed", "PUT", content_type="text/plain"
            )
            self.assertTrue(signed.url.startswith("/s3/" + BUCKET + "/"))
            self.assertEqual(signed.headers, {"Content-Type": "text/plain"})
        self.assertFalse(self.seen)

    async def test_namespace_permission_and_object_absence_stay_distinct(self):
        for code, status, kind in [
            ("NoSuchKey", 404, "not_found"),
            ("NoSuchBucket", 404, "namespace_not_found"),
            ("AccessDenied", 403, "permission_denied"),
        ]:
            self.handler = lambda r, c=code, s=status: error(r, s, c)
            with self.assertRaises(StorageError) as caught:
                await self.store.stat(PREFIX + "missing")
            self.assertEqual(caught.exception.kind, kind)
        self.assertEqual(len(self.seen), 3)

    async def test_redirect_is_not_followed(self):
        self.handler = lambda r: httpx.Response(
            307, request=r, headers={"location": "https://outside.invalid/escape"}
        )
        with self.assertRaises(StorageError):
            await self.store.stat(PREFIX + "redirect")
        self.assertEqual(len(self.seen), 1)

    async def test_streamed_upload_returns_own_commit_size_and_content_hash(self):
        body = b"a" * (8 * 1024**2) + b"b" * 1024**2
        self.multipart_handler(b"")

        async def chunks():
            for offset in range(0, len(body), 700001):
                yield body[offset : offset + 700001]

        result = await self.store.write_stream(
            PREFIX + "stream", chunks(), options=WriteOptions(create_only=True)
        )
        self.assertEqual(result.size, len(body))
        self.assertEqual(result.sha256, hashlib.sha256(body).hexdigest())
        self.assertEqual(self.store.serialize_version(result.version), '"committed-2"')
        self.assertEqual(
            [len(r.content) for r in self.seen if r.method == "PUT"],
            [8 * 1024**2, 1024**2],
        )
        self.assertFalse(any(r.method in ("GET", "HEAD") for r in self.seen))

    async def test_zero_byte_conditioned_copy_pins_read_and_conditional_put(self):
        def handle(r):
            if r.method == "GET":
                self.assertEqual(r.headers["if-match"], '"source-v1"')
                self.assertNotIn("range", r.headers)
                return httpx.Response(
                    200,
                    request=r,
                    stream=SyntheticResponseStream(b""),
                    headers={"etag": '"source-v1"', "content-length": "0"},
                )
            self.assertEqual(r.method, "PUT")
            self.assertEqual(r.headers["if-none-match"], "*")
            self.assertEqual(r.content, b"")
            return httpx.Response(200, request=r, headers={"etag": '"empty-commit"'})

        self.handler = handle
        result = await self.store.copy(
            self.source(0), PREFIX + "empty", options=CopyOptions(create_only=True)
        )
        self.assertEqual(self.store.serialize_version(result.version), '"empty-commit"')
        self.assertEqual([r.method for r in self.seen], ["GET", "PUT"])

    async def test_single_copy_transmits_source_condition_and_returns_commit(self):
        def handle(r):
            self.assertEqual(r.method, "PUT")
            self.assertEqual(r.headers["x-amz-copy-source-if-match"], '"source-v1"')
            self.assertEqual(r.headers["x-amz-metadata-directive"], "REPLACE")
            self.assertEqual(r.headers["expires"], "Wed, 21 Oct 2037 07:28:00 GMT")
            return httpx.Response(
                200,
                request=r,
                content=b'<CopyObjectResult><ETag>"single-commit"</ETag></CopyObjectResult>',
            )

        self.handler = handle
        result = await self.store.copy(self.source(123), PREFIX + "single")
        self.assertEqual(
            self.store.serialize_version(result.version), '"single-commit"'
        )
        self.assertEqual(len(self.seen), 1)

    async def test_native_retry_attempts_remain_visible_and_bounded(self):
        self.handler = lambda r: error(r, 503, "SlowDown")
        with self.assertRaises(StorageError) as caught:
            await self.store.stat(PREFIX + "retry")
        self.assertEqual(caught.exception.kind, "unavailable")
        self.assertEqual(len(self.seen), 5)
        self.assertEqual(len({r.url for r in self.seen}), 1)

    async def test_typed_pagination_preserves_cursor_prefix_and_delimiter(self):
        def handle(r):
            self.assertEqual(r.method, "GET")
            self.assertEqual(r.url.params["prefix"], PREFIX)
            self.assertEqual(r.url.params["delimiter"], "/")
            self.assertEqual(r.url.params["max-keys"], "1")
            if "continuation-token" not in r.url.params:
                xml = (
                    "<ListBucketResult><IsTruncated>true</IsTruncated><NextContinuationToken>opaque+cursor=</NextContinuationToken><Contents><Key>"
                    + PREFIX
                    + 'a</Key><Size>3</Size><ETag>"v1"</ETag></Contents></ListBucketResult>'
                )
            else:
                self.assertEqual(r.url.params["continuation-token"], "opaque+cursor=")
                xml = (
                    "<ListBucketResult><IsTruncated>false</IsTruncated><CommonPrefixes><Prefix>"
                    + PREFIX
                    + "nested/</Prefix></CommonPrefixes></ListBucketResult>"
                )
            return httpx.Response(200, request=r, content=xml.encode())

        self.handler = handle
        pages = [
            p async for p in object_pages(self.store, PREFIX, delimiter="/", limit=1)
        ]
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].objects[0].key, PREFIX + "a")
        self.assertEqual(pages[0].objects[0].size, 3)
        self.assertEqual(pages[1].prefixes, (PREFIX + "nested/",))
        self.assertEqual(len(self.seen), 2)

    async def test_pinned_read_closes_real_transport_body(self):
        class TrackedStream(SyntheticResponseStream):
            closed = False

            async def aclose(self):
                self.closed = True

        stream = TrackedStream(b"one-two-three")

        def handle(r):
            self.assertEqual(r.headers["if-match"], '"source-v1"')
            return httpx.Response(
                200,
                request=r,
                stream=stream,
                headers={"etag": '"source-v1"', "content-length": "13"},
            )

        self.handler = handle
        async with self.store.open_read(
            PREFIX + "source", expected_version=self.source(13).version
        ) as read:
            self.assertEqual(read.info.size, 13)
            self.assertEqual(await read.body.read(4), b"one-")
        self.assertTrue(stream.closed)
        self.assertEqual(len(self.seen), 1)
