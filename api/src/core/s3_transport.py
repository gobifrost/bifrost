"""Narrow S3 protocol transport signed with AWS CRT.

The application only needs the operations implemented here.  Keeping the wire
mapping local makes S3's unusual XML and streaming semantics explicit without
loading the general AWS SDK in API processes.
"""

from __future__ import annotations

import base64
import asyncio
import hashlib
import os
import random
import ssl
import time
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote, unquote_plus, urlencode

import httpx
from awscrt import auth, checksums, http


_ssl_context: tuple[int, ssl.SSLContext] | None = None
_default_credentials_provider: tuple[int, auth.AwsCredentialsProvider] | None = None
_MAX_ATTEMPTS = 5
_RETRYABLE_S3_CODES = frozenset({"InternalError", "SlowDown", "RequestTimeout", "BadDigest"})
# HTTPX adds Connection before signing. Spaces' ingress removes that hop-by-hop
# header, so including it in SignedHeaders makes otherwise valid requests fail.
_UNSIGNED_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "proxy-connection", "te", "trailer", "transfer-encoding", "upgrade",
    "expect", "user-agent", "x-amzn-trace-id",
})


def _retry_delay(attempt: int, random_value: Callable[[], float]) -> float:
    """Match botocore's full-jitter exponential retry delay (capped at 20s)."""
    return random_value() * min(2**attempt, 20)


def _is_retryable_s3_error(status_code: int, error: S3Error) -> bool:
    return status_code == 429 or status_code >= 500 or error.code in _RETRYABLE_S3_CODES


def _process_ssl_context() -> ssl.SSLContext:
    """Return a PID-owned TLS context; inherited OpenSSL state is never reused."""
    global _ssl_context
    pid = os.getpid()
    if _ssl_context is None or _ssl_context[0] != pid:
        _ssl_context = (pid, ssl.create_default_context())
    return _ssl_context[1]


class S3Error(RuntimeError):
    """An S3 service failure with the small ``ClientError`` compatibility shape."""

    def __init__(self, code: str, status_code: int, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.status_code = status_code
        self.message = message or code
        self.response = {
            "Error": {"Code": code, "Message": self.message},
            "ResponseMetadata": {"HTTPStatusCode": status_code},
        }


class S3NoSuchKey(S3Error):
    """The requested S3 object does not exist."""


class S3PartialCredentialsError(ValueError):
    """Only one half of explicit static S3 credentials was configured."""


def _credentials_provider(
    access_key_id: str | None,
    secret_access_key: str | None,
    session_token: str | None,
) -> auth.AwsCredentialsProvider:
    """Resolve static credentials or this process's refresh-capable default chain."""
    if bool(access_key_id) != bool(secret_access_key):
        raise S3PartialCredentialsError(
            "S3 access_key_id and secret_access_key must be configured together"
        )
    if access_key_id is not None:
        return auth.AwsCredentialsProvider.new_static(
            access_key_id, secret_access_key, session_token
        )

    global _default_credentials_provider
    pid = os.getpid()
    if _default_credentials_provider is None or _default_credentials_provider[0] != pid:
        _default_credentials_provider = (
            pid,
            auth.AwsCredentialsProvider.new_default_chain(),
        )
    return _default_credentials_provider[1]


def _xml_local(element: ET.Element, name: str) -> ET.Element | None:
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1] == name:
            return child
    return None


def _xml_text(element: ET.Element, name: str, default: str = "") -> str:
    child = _xml_local(element, name)
    return child.text if child is not None and child.text is not None else default


def _error_from_payload(status_code: int, payload: bytes) -> S3Error:
    code = str(status_code)
    message = "S3 request failed"
    try:
        root = ET.fromstring(payload)
        if root.tag.rsplit("}", 1)[-1] == "Error":
            code = _xml_text(root, "Code", code)
            message = _xml_text(root, "Message", message)
    except ET.ParseError:
        if payload:
            message = payload.decode("utf-8", "replace")[:512]
    error_type = S3NoSuchKey if code in {"NoSuchKey", "404", "NotFound"} else S3Error
    return error_type(code, status_code, message)


def _embedded_error(payload: bytes) -> S3Error | None:
    """Return an S3 error embedded in a nominally successful XML response."""
    if not payload:
        return None
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None
    if root.tag.rsplit("}", 1)[-1] == "Error":
        return _error_from_payload(200, payload)
    return None


def _encode_key(key: str) -> str:
    # S3 canonical paths preserve slashes and must treat literal '%' as data.
    return quote(key, safe="/-_.~")


def _endpoint_url(
    endpoint_url: str | None, bucket: str, key: str = "", *, region: str = "us-east-1"
) -> str:
    base = (
        endpoint_url
        or ("https://s3.amazonaws.com" if region == "us-east-1" else f"https://s3.{region}.amazonaws.com")
    ).rstrip("/")
    path = f"{_encode_key(bucket)}/{_encode_key(key)}" if key else _encode_key(bucket)
    return f"{base}/{path}"


def _query_url(url: str, values: Iterable[tuple[str, str]]) -> str:
    query = urlencode(list(values), quote_via=quote, safe="~-._")
    return f"{url}?{query}" if query else url


def _to_bytes(body: Any) -> bytes:
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    if isinstance(body, bytearray):
        return bytes(body)
    if isinstance(body, str):
        return body.encode()
    raise TypeError("S3 Body must be str, bytes, or bytearray")


def _sign_request(
    request: httpx.Request,
    *,
    access_key_id: str | None,
    secret_access_key: str | None,
    session_token: str | None,
    region: str,
    presign: bool = False,
    expires: int | None = None,
    now: datetime | None = None,
) -> httpx.Request:
    provider = _credentials_provider(access_key_id, secret_access_key, session_token)
    signature_type = (
        auth.AwsSignatureType.HTTP_REQUEST_QUERY_PARAMS
        if presign
        else auth.AwsSignatureType.HTTP_REQUEST_HEADERS
    )
    crt_request = http.HttpRequest(
        method=request.method,
        path=request.url.raw_path.decode("ascii"),
        headers=http.HttpHeaders(list(request.headers.multi_items())),
        body_stream=BytesIO(request.content),
    )
    config = auth.AwsSigningConfig(
        credentials_provider=provider,
        region=region,
        service="s3",
        date=now,
        signature_type=signature_type,
        should_sign_header=lambda name: name.lower() not in _UNSIGNED_HEADERS,
        use_double_uri_encode=False,
        should_normalize_uri_path=False,
        signed_body_value=auth.AwsSignedBodyValue.UNSIGNED_PAYLOAD if presign else None,
        signed_body_header_type=(
            auth.AwsSignedBodyHeaderType.NONE
            if presign
            else auth.AwsSignedBodyHeaderType.X_AMZ_CONTENT_SHA_256
        ),
        expiration_in_seconds=expires,
    )
    auth.aws_sign_request(crt_request, config).result()
    return httpx.Request(
        request.method,
        request.url.copy_with(raw_path=crt_request.path.encode("ascii")),
        headers=list(crt_request.headers),
        content=request.content,
    )


class _ResponseChecksum:
    """Incremental checksum encoded as S3 sends it in a response header."""

    def __init__(self, algorithm: str, expected: str) -> None:
        self.algorithm = algorithm
        self.expected = expected
        self._hash = hashlib.new(algorithm) if algorithm in {"sha1", "sha256"} else None
        self._crc = 0

    def update(self, chunk: bytes) -> None:
        if self._hash is not None:
            self._hash.update(chunk)
            return
        if self.algorithm == "crc32":
            self._crc = checksums.crc32(chunk, self._crc)
        elif self.algorithm == "crc32c":
            self._crc = checksums.crc32c(chunk, self._crc)
        elif self.algorithm == "crc64nvme":
            self._crc = checksums.crc64nvme(chunk, self._crc)

    def matches(self) -> bool:
        if self._hash is not None:
            actual = base64.b64encode(self._hash.digest()).decode()
        else:
            width = 8 if self.algorithm == "crc64nvme" else 4
            actual = base64.b64encode(self._crc.to_bytes(width, "big")).decode()
        return actual == self.expected


def _response_checksums(headers: httpx.Headers) -> list[_ResponseChecksum]:
    """Return whole-object checksums; multipart composite values are not comparable."""
    if headers.get("x-amz-checksum-type", "").upper() == "COMPOSITE":
        return []
    algorithms = ("crc32", "crc32c", "crc64nvme", "sha1", "sha256")
    return [
        _ResponseChecksum(algorithm, value)
        for algorithm in algorithms
        if (value := headers.get(f"x-amz-checksum-{algorithm}")) is not None
    ]


def _validate_buffered_checksums(headers: httpx.Headers, content: bytes) -> None:
    values = _response_checksums(headers)
    for value in values:
        value.update(content)
    if values and not all(value.matches() for value in values):
        raise S3Error("ChecksumMismatch", 200, "S3 object checksum did not match response body")


class S3Body:
    """One-shot raw response reader with the existing body API."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self._iterator: AsyncIterator[bytes] | None = None
        # Mock transports may hand HTTPX a pre-consumed byte stream. Production
        # ``send(stream=True)`` leaves it untouched, but retaining the bytes
        # here keeps the body contract deterministic for any transport.
        self._pending = bytearray()
        self._closed = False
        self._checksums = _response_checksums(response.headers)

    async def __aenter__(self) -> S3Body:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._response.aclose()

    async def _next_chunk(self) -> bytes | None:
        if self._iterator is None:
            self._iterator = self._response.aiter_raw()
        try:
            return await anext(self._iterator)
        except StopAsyncIteration:
            if self._checksums and not all(checksum.matches() for checksum in self._checksums):
                await self.close()
                raise S3Error("ChecksumMismatch", 200, "S3 object checksum did not match response body")
            await self.close()
            return None

    def _returned(self, chunk: bytes) -> bytes:
        for checksum in self._checksums:
            checksum.update(chunk)
        return chunk

    async def read(self, amt: int | None = None) -> bytes:
        if self._closed:
            return b""
        target = -1 if amt is None or amt < 0 else amt
        if target == 0:
            return b""
        chunks: list[bytes] = []
        if self._pending:
            if target > 0 and len(self._pending) >= target:
                result = self._returned(bytes(self._pending[:target]))
                del self._pending[:target]
                return result
            chunks.append(self._returned(bytes(self._pending)))
            self._pending.clear()
        total = sum(map(len, chunks))
        try:
            while (chunk := await self._next_chunk()) is not None:
                if target > 0 and total + len(chunk) > target:
                    take = target - total
                    chunks.append(self._returned(chunk[:take]))
                    self._pending.extend(chunk[take:])
                    return b"".join(chunks)
                chunks.append(self._returned(chunk))
                total += len(chunk)
                if target > 0 and total == target:
                    return b"".join(chunks)
        except BaseException:
            await self.close()
            raise
        return b"".join(chunks)

    async def iter_chunks(self, chunk_size: int = 8 * 1024 * 1024) -> AsyncIterator[bytes]:
        while chunk := await self.read(chunk_size):
            yield chunk


class S3Transport:
    """Async path-style S3 client implementing Bifrost's used operation subset."""

    exceptions = SimpleNamespace(NoSuchKey=S3NoSuchKey)

    def __init__(
        self,
        *,
        endpoint_url: str | None,
        access_key_id: str | None,
        secret_access_key: str | None,
        region: str | None,
        session_token: str | None = None,
        client: httpx.AsyncClient | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        random_value: Callable[[], float] | None = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.session_token = session_token
        self.region = region or "us-east-1"
        self._client = client or httpx.AsyncClient(verify=_process_ssl_context(), timeout=60.0)
        self._owns_client = client is None
        self._now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep or asyncio.sleep
        self._random_value = random_value or random.random

    async def __aenter__(self) -> S3Transport:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client or not self._client.is_closed:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        *,
        bucket: str,
        key: str = "",
        query: Iterable[tuple[str, str]] = (),
        headers: Mapping[str, str] | None = None,
        body: Any = None,
        stream: bool = False,
        embedded_error: bool = False,
    ) -> httpx.Response:
        content = _to_bytes(body)
        for attempt in range(_MAX_ATTEMPTS):
            url = _query_url(_endpoint_url(self.endpoint_url, bucket, key, region=self.region), query)
            request = self._client.build_request(method, url, headers=headers, content=content)
            signed = _sign_request(
                request,
                access_key_id=self.access_key_id,
                secret_access_key=self.secret_access_key,
                session_token=self.session_token,
                region=self.region,
                now=self._now(),
            )
            try:
                response = await self._client.send(signed, stream=stream)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == _MAX_ATTEMPTS - 1:
                    raise
                await self._sleep(_retry_delay(attempt, self._random_value))
                continue
            if response.status_code in {301, 307, 308} and response.headers.get("x-amz-bucket-region"):
                self.region = response.headers["x-amz-bucket-region"]
                await response.aclose()
                continue
            if response.status_code >= 400:
                payload = await response.aread()
                error = _error_from_payload(response.status_code, payload)
                await response.aclose()
                region = response.headers.get("x-amz-bucket-region")
                if (
                    response.status_code == 400 and error.code == "AuthorizationHeaderMalformed"
                    and region and region != self.region and attempt < _MAX_ATTEMPTS - 1
                ):
                    self.region = region
                    continue
                if attempt < _MAX_ATTEMPTS - 1 and _is_retryable_s3_error(response.status_code, error):
                    await self._sleep(_retry_delay(attempt, self._random_value))
                    continue
                raise error
            if embedded_error:
                error = _embedded_error(await response.aread())
                if error is not None:
                    await response.aclose()
                    if attempt < _MAX_ATTEMPTS - 1 and _is_retryable_s3_error(response.status_code, error):
                        await self._sleep(_retry_delay(attempt, self._random_value))
                        continue
                    raise error
            return response
        raise RuntimeError("unreachable")

    @staticmethod
    def _response(response: httpx.Response) -> dict[str, Any]:
        metadata = {
            name[11:]: value
            for name, value in response.headers.items()
            if name.lower().startswith("x-amz-meta-")
        }
        result: dict[str, Any] = {
            "ResponseMetadata": {"HTTPStatusCode": response.status_code},
            "Metadata": metadata,
        }
        if "content-length" in response.headers:
            result["ContentLength"] = int(response.headers["content-length"])
        if "etag" in response.headers:
            result["ETag"] = response.headers["etag"]
        if "last-modified" in response.headers:
            result["LastModified"] = parsedate_to_datetime(response.headers["last-modified"])
        for source, target in (
            ("content-type", "ContentType"), ("content-disposition", "ContentDisposition"),
            ("content-encoding", "ContentEncoding"), ("content-language", "ContentLanguage"),
            ("cache-control", "CacheControl"), ("expires", "Expires"),
        ):
            if source in response.headers:
                result[target] = response.headers[source]
        return result

    async def head_bucket(self, *, Bucket: str) -> dict[str, Any]:
        response = await self._request("HEAD", bucket=Bucket)
        await response.aclose()
        return self._response(response)

    async def get_object(self, *, Bucket: str, Key: str, ChecksumMode: str | None = None) -> dict[str, Any]:
        response = await self._request(
            "GET",
            bucket=Bucket,
            key=Key,
            headers={"x-amz-checksum-mode": ChecksumMode or "ENABLED"},
            stream=True,
        )
        result = self._response(response)
        result["Body"] = S3Body(response)
        return result

    async def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        response = await self._request("HEAD", bucket=Bucket, key=Key)
        result = self._response(response)
        await response.aclose()
        return result

    async def put_object(self, *, Bucket: str, Key: str, Body: Any, ContentType: str | None = None, **kwargs: Any) -> dict[str, Any]:
        headers = {"content-type": ContentType} if ContentType else {}
        headers.update(_object_headers(kwargs))
        response = await self._request("PUT", bucket=Bucket, key=Key, headers=headers, body=Body)
        result = self._response(response)
        await response.aclose()
        return result

    async def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        response = await self._request("DELETE", bucket=Bucket, key=Key)
        result = self._response(response)
        await response.aclose()
        return result

    async def delete_objects(self, *, Bucket: str, Delete: Mapping[str, Any]) -> dict[str, Any]:
        root = ET.Element("Delete")
        quiet = Delete.get("Quiet")
        if quiet is not None:
            ET.SubElement(root, "Quiet").text = str(bool(quiet)).lower()
        for item in Delete.get("Objects", []):
            obj = ET.SubElement(root, "Object")
            ET.SubElement(obj, "Key").text = item["Key"]
            if item.get("VersionId"):
                ET.SubElement(obj, "VersionId").text = item["VersionId"]
        content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        response = await self._request(
            "POST", bucket=Bucket, query=(("delete", ""),), body=content,
            headers={"content-md5": base64.b64encode(hashlib.md5(content).digest()).decode()},
        )
        payload = await response.aread()
        await response.aclose()
        parsed = ET.fromstring(payload)
        return {
            "Deleted": [{"Key": _xml_text(child, "Key")} for child in parsed if child.tag.rsplit("}", 1)[-1] == "Deleted"],
            "Errors": [
                {"Key": _xml_text(child, "Key"), "Code": _xml_text(child, "Code"), "Message": _xml_text(child, "Message")}
                for child in parsed if child.tag.rsplit("}", 1)[-1] == "Error"
            ],
        }

    async def list_objects_v2(self, *, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        names = {"Prefix": "prefix", "Delimiter": "delimiter", "ContinuationToken": "continuation-token", "MaxKeys": "max-keys"}
        query = [("list-type", "2"), ("encoding-type", "url")] + [(wire, str(kwargs[name])) for name, wire in names.items() if kwargs.get(name) is not None]
        response = await self._request("GET", bucket=Bucket, query=query)
        payload = await response.aread()
        await response.aclose()
        root = ET.fromstring(payload)
        # Match the previous SDK: list responses may encode spaces as '+';
        # actual plus signs are encoded as %2B and must remain literal.
        decode = unquote_plus if _xml_text(root, "EncodingType") == "url" else lambda value: value
        contents = []
        for item in root:
            if item.tag.rsplit("}", 1)[-1] != "Contents":
                continue
            entry: dict[str, Any] = {"Key": decode(_xml_text(item, "Key")), "ETag": _xml_text(item, "ETag"), "Size": int(_xml_text(item, "Size", "0"))}
            modified = _xml_text(item, "LastModified")
            if modified:
                entry["LastModified"] = datetime.fromisoformat(modified.replace("Z", "+00:00"))
            contents.append(entry)
        result = {
            "Contents": contents,
            "CommonPrefixes": [{"Prefix": decode(_xml_text(item, "Prefix"))} for item in root if item.tag.rsplit("}", 1)[-1] == "CommonPrefixes"],
            "IsTruncated": _xml_text(root, "IsTruncated").lower() == "true",
            "NextContinuationToken": _xml_text(root, "NextContinuationToken") or None,
            "KeyCount": int(_xml_text(root, "KeyCount", str(len(contents)))),
        }
        prefix = _xml_text(root, "Prefix")
        if prefix:
            result["Prefix"] = decode(prefix)
        encoding_type = _xml_text(root, "EncodingType")
        if encoding_type:
            result["EncodingType"] = encoding_type
        return result

    def get_paginator(self, operation_name: str) -> S3ListObjectsV2Paginator:
        if operation_name != "list_objects_v2":
            raise ValueError(f"unsupported S3 paginator: {operation_name}")
        return S3ListObjectsV2Paginator(self)

    async def copy_object(self, *, Bucket: str, Key: str, CopySource: Mapping[str, str] | str, **kwargs: Any) -> dict[str, Any]:
        source = CopySource if isinstance(CopySource, str) else f"/{CopySource['Bucket']}/{_encode_key(CopySource['Key'])}"
        headers = {"x-amz-copy-source": source, **_object_headers(kwargs)}
        response = await self._request("PUT", bucket=Bucket, key=Key, headers=headers, embedded_error=True)
        payload = await response.aread()
        await response.aclose()
        return {"CopyObjectResult": {"ETag": _xml_text(ET.fromstring(payload), "ETag")}} if payload else {}

    async def create_multipart_upload(self, *, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._request("POST", bucket=Bucket, key=Key, query=(("uploads", ""),), headers=_object_headers(kwargs))
        payload = await response.aread()
        await response.aclose()
        return {"UploadId": _xml_text(ET.fromstring(payload), "UploadId")}

    async def upload_part(self, *, Bucket: str, Key: str, UploadId: str, PartNumber: int, Body: Any) -> dict[str, Any]:
        response = await self._request("PUT", bucket=Bucket, key=Key, query=(("partNumber", str(PartNumber)), ("uploadId", UploadId)), body=Body)
        result = self._response(response)
        await response.aclose()
        return result

    async def upload_part_copy(self, *, Bucket: str, Key: str, UploadId: str, PartNumber: int, CopySource: Mapping[str, str] | str, **kwargs: Any) -> dict[str, Any]:
        source = CopySource if isinstance(CopySource, str) else f"/{CopySource['Bucket']}/{_encode_key(CopySource['Key'])}"
        headers = {"x-amz-copy-source": source, **_object_headers(kwargs)}
        response = await self._request("PUT", bucket=Bucket, key=Key, query=(("partNumber", str(PartNumber)), ("uploadId", UploadId)), headers=headers, embedded_error=True)
        payload = await response.aread()
        await response.aclose()
        root = ET.fromstring(payload)
        return {"CopyPartResult": {"ETag": _xml_text(root, "ETag")}}

    async def complete_multipart_upload(self, *, Bucket: str, Key: str, UploadId: str, MultipartUpload: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        root = ET.Element("CompleteMultipartUpload")
        for part in MultipartUpload.get("Parts", []):
            element = ET.SubElement(root, "Part")
            ET.SubElement(element, "PartNumber").text = str(part["PartNumber"])
            ET.SubElement(element, "ETag").text = part["ETag"]
        response = await self._request("POST", bucket=Bucket, key=Key, query=(("uploadId", UploadId),), headers=_object_headers(kwargs), body=ET.tostring(root, encoding="utf-8"), embedded_error=True)
        await response.aclose()
        return {}

    async def abort_multipart_upload(self, *, Bucket: str, Key: str, UploadId: str) -> dict[str, Any]:
        response = await self._request("DELETE", bucket=Bucket, key=Key, query=(("uploadId", UploadId),))
        await response.aclose()
        return {}

    async def generate_presigned_url(self, ClientMethod: str, Params: Mapping[str, Any], ExpiresIn: int = 3600) -> str:
        operation = {"get_object": "GET", "put_object": "PUT"}.get(ClientMethod)
        if operation is None:
            raise ValueError(f"unsupported S3 presign operation: {ClientMethod}")
        query_names = {"ResponseContentType": "response-content-type", "ResponseContentDisposition": "response-content-disposition"}
        query = [(wire, str(Params[name])) for name, wire in query_names.items() if Params.get(name) is not None]
        headers = {"content-type": str(Params["ContentType"])} if Params.get("ContentType") is not None else {}
        request = httpx.Request(
            operation,
            _query_url(
                _endpoint_url(self.endpoint_url, Params["Bucket"], Params["Key"], region=self.region),
                query,
            ),
            headers=headers,
        )
        # ``httpx.Request`` supplies ``Content-Length: 0`` for a PUT with no
        # body. A browser uploads a real body, so binding that synthetic header
        # would make an otherwise-valid presigned URL unusable.
        if "content-length" in request.headers:
            del request.headers["content-length"]
        signed = _sign_request(request, access_key_id=self.access_key_id, secret_access_key=self.secret_access_key, session_token=self.session_token, region=self.region, presign=True, expires=ExpiresIn, now=self._now())
        return str(signed.url)


class S3ListObjectsV2Paginator:
    """Async paginator for the sole S3 listing operation used by the API."""

    def __init__(self, client: S3Transport) -> None:
        self._client = client

    async def paginate(self, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        if "PaginationConfig" in kwargs:
            raise ValueError("PaginationConfig is not used by Bifrost S3 callers")
        request = dict(kwargs)
        seen_tokens: set[str] = set()
        while True:
            page = await self._client.list_objects_v2(**request)
            token = page.get("NextContinuationToken")
            if page.get("IsTruncated") and token:
                if token in seen_tokens:
                    raise RuntimeError("S3 list_objects_v2 returned a repeated continuation token")
                seen_tokens.add(token)
            yield page
            if not page.get("IsTruncated") or not token:
                return
            request["ContinuationToken"] = token


def _object_headers(values: Mapping[str, Any]) -> dict[str, str]:
    names = {
        "ContentType": "content-type", "ContentDisposition": "content-disposition", "ContentEncoding": "content-encoding",
        "ContentLanguage": "content-language", "CacheControl": "cache-control", "Expires": "expires",
        "CopySourceIfMatch": "x-amz-copy-source-if-match", "CopySourceRange": "x-amz-copy-source-range",
        "IfNoneMatch": "if-none-match", "MetadataDirective": "x-amz-metadata-directive",
    }
    headers = {wire: str(values[name]) for name, wire in names.items() if values.get(name) is not None}
    for key, value in (values.get("Metadata") or {}).items():
        headers[f"x-amz-meta-{key}"] = str(value)
    return headers


class SyncS3Transport:
    """The sole synchronous S3 path, used by parent-only import cache fallback."""

    def __init__(
        self,
        *,
        endpoint_url: str | None,
        access_key_id: str | None,
        secret_access_key: str | None,
        region: str | None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
        random_value: Callable[[], float] | None = None,
    ) -> None:
        self.endpoint_url, self.access_key_id, self.secret_access_key = endpoint_url, access_key_id, secret_access_key
        self.region = region or "us-east-1"
        self._client = client or httpx.Client(verify=_process_ssl_context(), timeout=60.0)
        self._sleep = sleep or time.sleep
        self._random_value = random_value or random.random

    def close(self) -> None:
        self._client.close()

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        for attempt in range(_MAX_ATTEMPTS):
            request = self._client.build_request(
                "GET",
                _endpoint_url(self.endpoint_url, Bucket, Key, region=self.region),
                headers={"x-amz-checksum-mode": "ENABLED"},
            )
            signed = _sign_request(request, access_key_id=self.access_key_id, secret_access_key=self.secret_access_key, session_token=None, region=self.region, now=datetime.now(UTC))
            try:
                response = self._client.send(signed, stream=True)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == _MAX_ATTEMPTS - 1:
                    raise
                self._sleep(_retry_delay(attempt, self._random_value))
                continue
            try:
                # Production sends stream=True and takes the raw path. Some
                # in-memory HTTPX transports provide an already-buffered
                # response, whose ``content`` is the only readable surface.
                content = response.content if response.is_stream_consumed else b"".join(response.iter_raw())
                if response.status_code in {301, 307, 308} and response.headers.get("x-amz-bucket-region"):
                    self.region = response.headers["x-amz-bucket-region"]
                    continue
                if response.status_code >= 400:
                    error = _error_from_payload(response.status_code, content)
                    region = response.headers.get("x-amz-bucket-region")
                    if (
                        response.status_code == 400 and error.code == "AuthorizationHeaderMalformed"
                        and region and region != self.region and attempt < _MAX_ATTEMPTS - 1
                    ):
                        self.region = region
                        continue
                    if attempt < _MAX_ATTEMPTS - 1 and _is_retryable_s3_error(response.status_code, error):
                        self._sleep(_retry_delay(attempt, self._random_value))
                        continue
                    raise error
                _validate_buffered_checksums(response.headers, content)
                return {"Body": BytesIO(content)}
            finally:
                response.close()
        raise RuntimeError("unreachable")
