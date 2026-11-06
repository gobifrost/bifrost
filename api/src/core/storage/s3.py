"""S3 wire behavior stays behind the object-store contract."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from types import TracebackType
from typing import Any, Literal, Self, cast
from urllib.parse import urlsplit

import httpx

from src.config import Settings
from src.core.s3_transport import S3Body, S3Error, S3PartialCredentialsError, S3Transport, SyncS3Transport
from src.core.storage.contracts import (
    ContentProperties, CopyOptions, ErrorKind, ObjectInfo, ObjectPage, ObjectRead,
    ObjectSummary, ObjectVersion, PageCursor, SignedRequest, StorageConfigurationError, StorageError,
    StreamWriteResult, WriteOptions, WriteResult,
)


def _service_error(error: S3Error) -> StorageError:
    kinds: dict[int, ErrorKind] = {
        404: "not_found", 401: "permission_denied", 403: "permission_denied",
        412: "precondition_failed", 409: "conflict", 429: "unavailable",
    }
    kind = kinds.get(error.status_code, "unavailable" if error.status_code >= 500 else "backend_error")
    if error.code == "NoSuchBucket":
        kind = "namespace_not_found"
    return StorageError(kind, provider_code=error.code, status_code=error.status_code)


@asynccontextmanager
async def _errors() -> AsyncIterator[None]:
    try:
        yield
    except S3Error as error:
        raise _service_error(error) from error
    except httpx.HTTPError as error:
        raise StorageError("unavailable", provider_code=type(error).__name__) from error
    except S3PartialCredentialsError as error:
        raise StorageConfigurationError("Object storage credentials are incomplete") from error


def _version(etag: str) -> ObjectVersion:
    # Preserve the established workspace witness encoding, in the provider.
    value = etag[1:-1] if etag.startswith('"') and etag.endswith('"') else etag
    if not value:
        raise StorageError("backend_error", provider_code="MissingObjectVersion")
    return ObjectVersion(value)


def _condition(version: ObjectVersion | None) -> str | None:
    return f'"{version}"' if version is not None else None


def _info(key: str, response: dict[str, Any]) -> ObjectInfo:
    version, size = response.get("ETag"), response.get("ContentLength")
    if not isinstance(version, str) or not isinstance(size, int) or size < 0:
        raise StorageError("backend_error", provider_code="InvalidObjectMetadata")
    return ObjectInfo(
        key=key, size=size, version=_version(version),
        modified_at=cast(datetime | None, response.get("LastModified")),
        properties=ContentProperties(
            content_type=response.get("ContentType"), content_disposition=response.get("ContentDisposition"),
            content_encoding=response.get("ContentEncoding"), content_language=response.get("ContentLanguage"),
            cache_control=response.get("CacheControl"), http_expires=response.get("Expires"),
        ),
        metadata=cast(dict[str, str], response.get("Metadata", {})), http_etag=version,
    )


def _properties(properties: ContentProperties) -> dict[str, str]:
    values = {
        "ContentType": properties.content_type, "ContentDisposition": properties.content_disposition,
        "ContentEncoding": properties.content_encoding, "ContentLanguage": properties.content_language,
        "CacheControl": properties.cache_control, "Expires": properties.http_expires,
    }
    return {name: value for name, value in values.items() if value is not None}


class _S3ObjectBody:
    def __init__(self, body: S3Body):
        self._body = body

    async def read(self, amt: int | None = None) -> bytes:
        async with _errors():
            return await self._body.read(amt)

    async def iter_chunks(self, chunk_size: int = 8 * 1024 * 1024) -> AsyncIterator[bytes]:
        async with _errors():
            async for chunk in self._body.iter_chunks(chunk_size):
                yield chunk

    async def close(self) -> None:
        async with _errors():
            await self._body.close()


class S3ObjectStore:
    def __init__(
        self, namespace: str, transport: S3Transport, *, signer: S3Transport | None = None,
        public_prefix: str | None = None, copy_mode: Literal["server", "stream"] = "server",
    ):
        if not namespace:
            raise StorageConfigurationError("An object storage namespace is required")
        self._namespace = namespace
        self._transport = transport
        self._signer = signer if signer is not None else transport
        self._public_prefix = public_prefix
        self._copy_mode: Literal["server", "stream"] = copy_mode

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        def create_transport(endpoint: str | None) -> S3Transport:
            return S3Transport(
                endpoint_url=endpoint, access_key_id=settings.s3_access_key,
                secret_access_key=settings.s3_secret_key, region=settings.s3_region,
            )

        transport = create_transport(settings.s3_endpoint_url)
        public = settings.s3_public_endpoint_url
        relative = public is not None and public.startswith("/")
        signer = create_transport(public) if public and not relative else transport
        return cls(
            settings.s3_bucket or "", transport, signer=signer, public_prefix=public if relative else None,
            copy_mode=settings.s3_multipart_copy_mode,
        )

    async def __aenter__(self) -> Self:
        await self._transport.__aenter__()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._transport.__aexit__(exc_type, exc, traceback)
        if self._signer is not self._transport:
            await self._signer.__aexit__(exc_type, exc, traceback)

    def serialize_version(self, version: ObjectVersion) -> str:
        # Existing move markers and Git receipts store raw, quoted S3 ETags.
        return f'"{version}"'

    def restore_version(self, value: str) -> ObjectVersion:
        return _version(value)

    async def probe(self) -> None:
        async with _errors():
            await self._transport.head_bucket(Bucket=self._namespace)

    async def stat(self, key: str) -> ObjectInfo:
        async with _errors():
            return _info(key, await self._transport.head_object(Bucket=self._namespace, Key=key))

    @asynccontextmanager
    async def open_read(
        self, key: str, *, expected_version: ObjectVersion | None = None,
    ) -> AsyncIterator[ObjectRead]:
        async with _errors():
            response = await self._transport.get_object(
                Bucket=self._namespace, Key=key, IfMatch=_condition(expected_version),
            )
        body = _S3ObjectBody(cast(S3Body, response["Body"]))
        try:
            yield ObjectRead(_info(key, response), body)
        finally:
            await body.close()

    async def write(self, key: str, content: bytes, *, options: WriteOptions | None = None) -> WriteResult:
        options = options if options is not None else WriteOptions()
        properties = options.properties
        async with _errors():
            response = await self._transport.put_object(
                Bucket=self._namespace, Key=key, Body=content,
                Metadata=options.metadata, ContentType=properties.content_type,
                ContentDisposition=properties.content_disposition,
                ContentEncoding=properties.content_encoding, ContentLanguage=properties.content_language,
                CacheControl=properties.cache_control, Expires=properties.http_expires,
                IfNoneMatch="*" if options.create_only else None,
                IfMatch=_condition(options.expected_version),
            )
        etag = response.get("ETag")
        if not isinstance(etag, str) or not etag:
            raise StorageError("backend_error", provider_code="MissingWriteVersion")
        return WriteResult(_version(etag))

    async def delete(self, key: str, *, expected_version: ObjectVersion | None = None) -> None:
        async with _errors():
            if expected_version is None:
                await self._transport.delete_object(Bucket=self._namespace, Key=key)
            else:
                await self._transport.delete_object(Bucket=self._namespace, Key=key, IfMatch=_condition(expected_version))

    async def delete_many(self, keys: Sequence[str]) -> None:
        async with _errors():
            for start in range(0, len(keys), 1000):
                response = await self._transport.delete_objects(
                    Bucket=self._namespace,
                    Delete={"Objects": [{"Key": key} for key in keys[start:start + 1000]], "Quiet": True},
                )
                if response.get("Errors"):
                    # HTTP success may contain per-object failures. Do not report cleanup success.
                    raise StorageError("backend_error", provider_code="PartialDeleteFailure")

    async def write_stream(
        self, key: str, content: AsyncIterator[bytes], *, options: WriteOptions | None = None,
    ) -> StreamWriteResult:
        from src.core.storage.s3_transfers import write_stream

        async with _errors():
            return await write_stream(self._transport, self._namespace, key, content, options or WriteOptions())

    async def copy(
        self, source: ObjectInfo, destination_key: str, *, options: CopyOptions | None = None,
    ) -> WriteResult:
        from src.core.storage.s3_transfers import copy

        async with _errors():
            return await copy(
                self._transport, self._namespace, source, destination_key,
                options or CopyOptions(), copy_mode=self._copy_mode,
            )

    async def list_page(
        self, prefix: str = "", *, delimiter: str | None = None,
        cursor: PageCursor | None = None, limit: int = 1000,
    ) -> ObjectPage:
        if not 1 <= limit <= 1000:
            raise ValueError("S3 page size must be between 1 and 1000")
        async with _errors():
            response = await self._transport.list_objects_v2(
                Bucket=self._namespace, Prefix=prefix, Delimiter=delimiter,
                ContinuationToken=cursor, MaxKeys=limit,
            )
        token = response.get("NextContinuationToken") if response.get("IsTruncated") else None
        if response.get("IsTruncated") and (not isinstance(token, str) or not token):
            raise StorageError("backend_error", provider_code="MissingPageCursor")
        objects = tuple(ObjectSummary(
            key=item["Key"], size=item["Size"], version=_version(item["ETag"]),
            modified_at=item.get("LastModified"),
        ) for item in response.get("Contents", []))
        return ObjectPage(
            objects, tuple(item["Prefix"] for item in response.get("CommonPrefixes", [])),
            PageCursor(token) if isinstance(token, str) else None,
        )

    async def signed_request(
        self, key: str, method: Literal["GET", "PUT"], *, expires_in: int = 3600,
        content_type: str | None = None, response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> SignedRequest:
        if not 1 <= expires_in <= 604800:
            raise ValueError("S3 signed request expiry must be between 1 and 604800 seconds")
        if method not in ("GET", "PUT"):
            raise ValueError("Signed access supports GET and PUT")
        if method == "PUT" and (response_content_type is not None or response_content_disposition is not None):
            raise ValueError("Response overrides apply only to signed downloads")
        if method == "GET" and content_type is not None:
            raise ValueError("Content type is an upload request header; use a download response override")
        params = {"Bucket": self._namespace, "Key": key}
        if content_type is not None:
            params["ContentType"] = content_type
        if response_content_type is not None:
            params["ResponseContentType"] = response_content_type
        if response_content_disposition is not None:
            params["ResponseContentDisposition"] = response_content_disposition
        async with _errors():
            url = await self._signer.generate_presigned_url(
                "get_object" if method == "GET" else "put_object", Params=params, ExpiresIn=expires_in,
            )
        if self._public_prefix is not None:
            # Existing local/browser proxy changes Host to the private S3 endpoint.
            parts = urlsplit(url)
            url = f"{self._public_prefix.rstrip('/')}{parts.path}?{parts.query}"
        return SignedRequest(method, url, {"Content-Type": content_type} if content_type is not None else {}, expires_in)


class SyncS3ObjectStore:
    def __init__(self, namespace: str, transport: SyncS3Transport):
        if not namespace:
            raise StorageConfigurationError("An object storage namespace is required")
        self._namespace, self._transport = namespace, transport

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(settings.s3_bucket or "", SyncS3Transport(
            endpoint_url=settings.s3_endpoint_url, access_key_id=settings.s3_access_key,
            secret_access_key=settings.s3_secret_key, region=settings.s3_region,
        ))

    def read(self, key: str) -> bytes:
        try:
            response = self._transport.get_object(Bucket=self._namespace, Key=key)
            body = response["Body"]
            try:
                return body.read()
            finally:
                body.close()
        except S3Error as error:
            raise _service_error(error) from error
        except httpx.HTTPError as error:
            raise StorageError("unavailable", provider_code=type(error).__name__) from error
        except S3PartialCredentialsError as error:
            raise StorageConfigurationError("Object storage credentials are incomplete") from error

    def close(self) -> None:
        self._transport.close()
