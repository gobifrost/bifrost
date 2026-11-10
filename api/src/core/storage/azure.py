"""Native Azure Blob behavior behind the object-store contract."""

import base64
import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Any, Literal, Self, cast
from uuid import uuid4

from src.config import Settings
from src.core.storage.contracts import (
    ContentProperties, CopyConditionFailed, CopyOptions, ErrorKind, ObjectInfo,
    ObjectPage, ObjectRead, ObjectSummary, ObjectVersion, PageCursor,
    SignedRequest, StorageConfigurationError, StorageError, StreamWriteResult,
    WriteOptions, WriteResult,
)

STREAM_PART_SIZE = 8 * 1024**2
COPY_PART_SIZE = 256 * 1024**2


def _service_error(error: Any) -> StorageError:
    from azure.core.exceptions import ServiceRequestError, ServiceResponseError

    if isinstance(error, (ServiceRequestError, ServiceResponseError)):
        return StorageError("unavailable", provider_code=type(error).__name__)
    status = error.status_code or 500
    raw_code = getattr(error, "error_code", None)
    code = str(getattr(raw_code, "value", raw_code) or status)
    if code == "ContainerNotFound":
        return StorageError("namespace_not_found", provider_code=code, status_code=status)
    if code == "BlobAlreadyExists":
        # A conditional create (If-None-Match *) lost a create race. Azure
        # reports 409 where S3 reports 412; the contract kind stays uniform.
        return StorageError("precondition_failed", provider_code=code, status_code=status)
    kinds: dict[int, ErrorKind] = {
        404: "not_found", 401: "permission_denied", 403: "permission_denied",
        412: "precondition_failed", 409: "conflict", 429: "unavailable",
        501: "unsupported",
    }
    kind = kinds.get(status, "unavailable" if status >= 500 else "backend_error")
    if code == "BlobNotFound" and kind != "not_found":
        kind = "not_found"
    return StorageError(kind, provider_code=code, status_code=status)


@asynccontextmanager
async def _errors() -> AsyncIterator[None]:
    from azure.core.exceptions import (
        HttpResponseError, ServiceRequestError, ServiceResponseError,
    )

    try:
        yield
    except (HttpResponseError, ServiceRequestError, ServiceResponseError) as error:
        raise _service_error(error) from error


def _version(etag: str) -> ObjectVersion:
    # Azure list responses strip HTTP entity-tag quotes while GET/HEAD/write
    # responses retain them. Normalize to one opaque quoted token so a blob
    # keeps a single version across listing, stat, read and conditions.
    value = str(etag or "")
    if value.startswith('W/"') and value.endswith('"'):
        value = value[2:]
    if value and not (value.startswith('"') and value.endswith('"')):
        value = f'"{value}"'
    if not value:
        raise StorageError("backend_error", provider_code="MissingObjectVersion")
    return ObjectVersion(value)


def _conditions(options: WriteOptions | CopyOptions) -> dict[str, Any]:
    from azure.core import MatchConditions

    if options.create_only and options.expected_version is not None:
        raise ValueError("Create-only and expected-version writes are mutually exclusive")
    if options.expected_version is not None:
        return {"etag": options.expected_version, "match_condition": MatchConditions.IfNotModified}
    if options.create_only:
        return {"match_condition": MatchConditions.IfMissing}
    return {}


def _metadata(values: dict[str, str] | None) -> dict[str, str]:
    # Azure metadata names cannot contain S3's move-marker hyphens. An
    # explicit envelope preserves values and S3's lowercased header names.
    if not values:
        return {}
    normalized = {key.lower(): value for key, value in values.items()}
    if len(normalized) != len(values):
        raise ValueError("Metadata names must be unique ignoring case")
    return {
        "bifrost_metadata": base64.b64encode(
            json.dumps(normalized, ensure_ascii=True, sort_keys=True).encode()
        ).decode()
    }


def _unmetadata(values: dict[str, str]) -> dict[str, str]:
    metadata = {key.lower(): value for key, value in (values or {}).items()}
    encoded = metadata.pop("bifrost_metadata", None)
    if encoded is None:
        return metadata
    decoded = json.loads(base64.b64decode(encoded, validate=True))
    if not isinstance(decoded, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in decoded.items()
    ):
        raise StorageError("backend_error", provider_code="InvalidBlobMetadataEnvelope")
    if metadata:
        raise StorageError("backend_error", provider_code="MixedBlobMetadataEnvelope")
    return decoded


def _content_settings(properties: ContentProperties) -> Any:
    from azure.storage.blob import ContentSettings

    if properties.http_expires is not None:
        # Disclosed provider limitation: Azure Blob has no native HTTP Expires
        # object property. Fail explicitly rather than silently dropping it.
        raise StorageError("unsupported", provider_code="HttpExpiresUnsupported")
    return ContentSettings(
        content_type=properties.content_type,
        content_disposition=properties.content_disposition,
        content_encoding=properties.content_encoding,
        content_language=properties.content_language,
        cache_control=properties.cache_control,
    )


def _info(key: str, properties: Any) -> ObjectInfo:
    etag = str(properties.etag or "")
    size = int(properties.size)
    if size < 0:
        raise StorageError("backend_error", provider_code="InvalidObjectMetadata")
    settings = properties.content_settings
    return ObjectInfo(
        key=key, size=size, version=_version(etag),
        modified_at=properties.last_modified,
        properties=ContentProperties(
            content_type=getattr(settings, "content_type", None),
            content_disposition=getattr(settings, "content_disposition", None),
            content_encoding=getattr(settings, "content_encoding", None),
            content_language=getattr(settings, "content_language", None),
            cache_control=getattr(settings, "cache_control", None),
            http_expires=None,
        ),
        metadata=_unmetadata(dict(properties.metadata or {})),
        http_etag=etag or None,
    )


class _BlobIntegrity:
    """Validate the complete raw representation, like the S3 response body."""

    def __init__(self, size: int, content_md5: bytes | bytearray | str | None):
        self._size = size
        self._received = 0
        self._digest = hashlib.md5(usedforsecurity=False)
        self._expected = (
            base64.b64decode(content_md5, validate=True)
            if isinstance(content_md5, str)
            else bytes(content_md5)
            if content_md5 is not None
            else None
        )
        if self._expected is not None and len(self._expected) != 16:
            raise StorageError("backend_error", provider_code="InvalidBlobContentMD5")

    def update(self, data: bytes) -> None:
        self._received += len(data)
        self._digest.update(data)

    def finish(self) -> None:
        if self._received != self._size:
            raise StorageError("backend_error", provider_code="IncompleteBlobBody")
        if self._expected is not None and self._digest.digest() != self._expected:
            raise StorageError("backend_error", provider_code="BadBlobDigest")


class _AzureObjectBody:
    """Incremental SDK download; the owning client context must outlive it."""

    def __init__(self, stream: Any, *, size: int, content_md5: bytes | bytearray | str | None = None):
        self._chunks = stream.chunks().__aiter__()
        self._buffer = bytearray()
        self._closed = False
        self._integrity = _BlobIntegrity(size, content_md5)

    async def close(self) -> None:
        self._closed = True
        self._buffer.clear()
        close = getattr(self._chunks, "aclose", None)
        if close is not None:
            await close()

    async def read(self, amt: int | None = None) -> bytes:
        if amt is not None and amt < 0:
            amt = None
        if self._closed:
            return b""
        while amt is None or len(self._buffer) < amt:
            try:
                chunk = await anext(self._chunks)
                self._integrity.update(chunk)
                self._buffer.extend(chunk)
            except StopAsyncIteration:
                self._integrity.finish()
                break
            except Exception as error:
                from azure.core.exceptions import (
                    HttpResponseError, ServiceRequestError, ServiceResponseError,
                )

                if isinstance(error, (HttpResponseError, ServiceRequestError, ServiceResponseError)):
                    raise _service_error(error) from None
                raise
        size = len(self._buffer) if amt is None else amt
        result = bytes(self._buffer[:size])
        del self._buffer[:size]
        return result

    async def iter_chunks(self, chunk_size: int = 8 * 1024 * 1024) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        while chunk := await self.read(chunk_size):
            yield chunk


class AzureObjectStore:
    """One configured container, with provider-owned connection lifecycle."""

    def __init__(self, namespace: str, settings: Settings):
        if not namespace:
            raise StorageConfigurationError("An object storage namespace is required")
        self._namespace = namespace
        self._settings = settings
        self._credential: Any = None
        self._service: Any = None
        self._container: Any = None

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        if not settings.azure_blob_configured:
            raise StorageConfigurationError("Azure Blob storage is not configured")
        return cls(settings.azure_blob_container or "", settings)

    @staticmethod
    def _account_name(settings: Settings) -> str:
        from urllib.parse import urlparse

        if settings.azure_blob_account_name:
            return settings.azure_blob_account_name
        hostname = urlparse(settings.azure_blob_account_url or "").hostname or ""
        for suffix in (
            ".blob.core.windows.net", ".blob.core.usgovcloudapi.net",
            ".blob.core.chinacloudapi.cn",
        ):
            if hostname.endswith(suffix):
                account = hostname.removesuffix(suffix)
                if account and "." not in account:
                    return account
        raise StorageConfigurationError(
            "Azure Blob account name is required for custom endpoints and emulators"
        )

    async def __aenter__(self) -> Self:
        if self._container is not None:
            return self
        try:
            await self._ensure_client()
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        service, credential = self._service, self._credential
        self._service = self._container = self._credential = None
        try:
            if service is not None:
                await service.close()
        finally:
            close_credential = getattr(credential, "close", None)
            if close_credential is not None:
                await close_credential()

    async def _ensure_client(self) -> None:
        from azure.storage.blob.aio import BlobServiceClient

        settings = self._settings
        credential: Any = None
        if settings.azure_blob_auth == "account_key":
            from azure.core.credentials import AzureNamedKeyCredential

            key = settings.azure_blob_account_key
            if key is None or not key.get_secret_value():
                raise StorageConfigurationError("Azure Blob account key is not configured")
            credential = AzureNamedKeyCredential(self._account_name(settings), key.get_secret_value())
        elif settings.azure_blob_auth == "managed_identity":
            from azure.identity.aio import ManagedIdentityCredential

            credential = ManagedIdentityCredential(
                client_id=settings.azure_blob_managed_identity_client_id
            )
        else:
            raise StorageConfigurationError(
                f"Unsupported Azure Blob auth mode: {settings.azure_blob_auth}"
            )
        self._credential = credential
        self._service = BlobServiceClient(settings.azure_blob_account_url, credential=credential)
        self._container = self._service.get_container_client(self._namespace)

    def serialize_version(self, version: ObjectVersion) -> str:
        # Versions are already opaque quoted entity-tags; persist them verbatim.
        if not version:
            raise StorageError("backend_error", provider_code="MissingObjectVersion")
        return str(version)

    def restore_version(self, value: str) -> ObjectVersion:
        return _version(value)

    async def probe(self) -> None:
        async with _errors():
            await self._container.get_container_properties()

    async def stat(self, key: str) -> ObjectInfo:
        async with _errors():
            return _info(key, await self._container.get_blob_client(key).get_blob_properties())

    @asynccontextmanager
    async def open_read(
        self, key: str, *, expected_version: ObjectVersion | None = None,
    ) -> AsyncIterator[ObjectRead]:
        from azure.core import MatchConditions

        kwargs: dict[str, Any] = {"decompress": False}
        if expected_version is not None:
            kwargs.update({"etag": expected_version, "match_condition": MatchConditions.IfNotModified})
        async with _errors():
            stream = await self._container.get_blob_client(key).download_blob(**kwargs)
            info = _info(key, stream.properties)
        body = _AzureObjectBody(
            stream, size=info.size,
            content_md5=stream.properties.content_settings.content_md5,
        )
        try:
            yield ObjectRead(info, body)
        finally:
            await body.close()

    async def write(self, key: str, content: bytes, *, options: WriteOptions | None = None) -> WriteResult:
        opts = options if options is not None else WriteOptions()
        async with _errors():
            response = await self._container.get_blob_client(key).upload_blob(
                content, overwrite=True, metadata=_metadata(dict(opts.metadata)),
                content_settings=_content_settings(opts.properties), retry_total=0,
                **_conditions(opts),
            )
        etag = response.get("etag") if isinstance(response, dict) else getattr(response, "etag", None)
        if not isinstance(etag, str) or not etag:
            raise StorageError("backend_error", provider_code="MissingWriteVersion")
        return WriteResult(_version(etag))

    async def delete(self, key: str, *, expected_version: ObjectVersion | None = None) -> None:
        from azure.core import MatchConditions

        kwargs: dict[str, Any] = {"retry_total": 0}
        if expected_version is not None:
            kwargs.update({"etag": expected_version, "match_condition": MatchConditions.IfNotModified})
        try:
            async with _errors():
                await self._container.delete_blob(key, **kwargs)
        except StorageError as error:
            if error.kind == "not_found" and error.provider_code == "BlobNotFound":
                # Deletes are idempotent; a lost response must not fail cleanup.
                return
            raise

    async def delete_many(self, keys: Sequence[str]) -> None:
        for start in range(0, len(keys), 256):
            errors: list[str] = []
            for key in keys[start:start + 256]:
                try:
                    await self.delete(key)
                except StorageError as error:
                    errors.append(f"{key}: {error.provider_code}")
            if errors:
                # HTTP success may contain per-object failures. Do not report cleanup success.
                raise StorageError("backend_error", provider_code="PartialDeleteFailure")

    async def write_stream(
        self, key: str, content: AsyncIterator[bytes], *, options: WriteOptions | None = None,
    ) -> StreamWriteResult:
        opts = options if options is not None else WriteOptions()
        digest, total = hashlib.sha256(), 0
        buffer = bytearray()
        block_ids: list[str] = []
        blob = self._container.get_blob_client(key)
        metadata = _metadata(dict(opts.metadata))
        settings = _content_settings(opts.properties)
        conditions = _conditions(opts)

        async def stage() -> None:
            block_id = base64.b64encode(f"{uuid4().hex}:{len(block_ids):05d}".encode()).decode()
            async with _errors():
                await blob.stage_block(block_id, bytes(buffer), validate_content=True, retry_total=0)
            block_ids.append(block_id)
            buffer.clear()

        # Uncommitted blocks are reclaimed by the service; a failed stream
        # leaves no committed destination behind to clean up.
        async with _errors():
            async for chunk in content:
                digest.update(chunk)
                total += len(chunk)
                view = memoryview(chunk)
                offset = 0
                while offset < len(view):
                    take = min(STREAM_PART_SIZE - len(buffer), len(view) - offset)
                    buffer.extend(view[offset:offset + take])
                    offset += take
                    if len(buffer) == STREAM_PART_SIZE:
                        await stage()
            if not block_ids:
                response = await blob.upload_blob(
                    bytes(buffer), overwrite=True, metadata=metadata,
                    content_settings=settings, retry_total=0, **conditions,
                )
                etag = response.get("etag") if isinstance(response, dict) else getattr(response, "etag", None)
            else:
                if buffer:
                    await stage()
                from azure.storage.blob import BlockState, BlobBlock

                response = await blob.commit_block_list(
                    [BlobBlock(block_id=block_id, state=BlockState.UNCOMMITTED) for block_id in block_ids],
                    content_settings=settings, metadata=metadata, retry_total=0,
                    **conditions,
                )
                etag = response.get("etag") if isinstance(response, dict) else getattr(response, "etag", None)
        if not isinstance(etag, str) or not etag:
            raise StorageError("backend_error", provider_code="MissingCommittedVersion")
        return StreamWriteResult(_version(etag), total, digest.hexdigest())

    async def _read_sas_url(self, key: str, *, expires_in: int = 3600) -> str:
        from azure.storage.blob import BlobSasPermissions

        return await self._signed_url(
            key, permissions=BlobSasPermissions(read=True), expires_in=expires_in,
        )

    async def _signed_url(
        self, key: str, *, permissions: Any, expires_in: int,
        content_type: str | None = None, content_disposition: str | None = None,
    ) -> str:
        from azure.storage.blob import generate_blob_sas

        if not 1 <= expires_in <= 604800:
            raise ValueError("SAS lifetime must be between 1 and 604800 seconds")
        settings = self._settings
        issued_at = datetime.now(UTC)
        sas_args: dict[str, Any] = {
            "account_name": self._account_name(settings),
            "container_name": self._namespace,
            "blob_name": key,
            "permission": permissions,
            "expiry": issued_at + timedelta(seconds=expires_in),
        }
        if content_type is not None:
            sas_args["content_type"] = content_type
        if content_disposition is not None:
            sas_args["content_disposition"] = content_disposition
        if settings.azure_blob_auth == "account_key":
            key_value = settings.azure_blob_account_key
            if key_value is None or not key_value.get_secret_value():
                raise StorageConfigurationError("Azure Blob account key is not configured")
            sas_args["account_key"] = key_value.get_secret_value()
        else:
            async with _errors():
                sas_args["user_delegation_key"] = await self._service.get_user_delegation_key(
                    key_start_time=issued_at,
                    key_expiry_time=issued_at + timedelta(seconds=expires_in),
                )
        return f"{self._container.get_blob_client(key).url}?{generate_blob_sas(**sas_args)}"

    async def copy(
        self, source: ObjectInfo, destination_key: str, *, options: CopyOptions | None = None,
    ) -> WriteResult:
        opts = options if options is not None else CopyOptions()
        if not source.version or source.size < 0:
            raise ValueError("Copy requires a valid source snapshot")
        properties = options.properties if options is not None and options.properties is not None else source.properties
        metadata = options.metadata if options is not None and options.metadata is not None else source.metadata
        conditions = _conditions(opts)
        blob = self._container.get_blob_client(destination_key)
        if source.size == 0:
            try:
                async with self.open_read(source.key, expected_version=source.version) as read:
                    data = await read.body.read()
            except StorageError as error:
                if error.kind == "precondition_failed":
                    raise CopyConditionFailed("source") from error
                raise
            try:
                return await self.write(
                    destination_key, data,
                    options=WriteOptions(properties=properties, metadata=dict(metadata),
                                         create_only=opts.create_only,
                                         expected_version=opts.expected_version),
                )
            except StorageError as error:
                if error.kind == "precondition_failed":
                    raise CopyConditionFailed("destination") from error
                raise
        try:
            return await self._copy_server(blob, source, destination_key, properties, metadata, conditions)
        except StorageError as error:
            if error.kind != "unsupported" or error.provider_code != "APINotImplemented":
                raise
            # Emulators such as Azurite do not implement Put Block From URL.
            # Fall back to a conditional read plus ordinary block upload, like
            # S3's stream copy mode for providers without conditional part copy.
            try:
                async with self.open_read(source.key, expected_version=source.version) as read:
                    data = await read.body.read()
            except StorageError as read_error:
                if read_error.kind == "precondition_failed":
                    raise CopyConditionFailed("source") from read_error
                raise

            async def streamed() -> AsyncIterator[bytes]:
                yield data

            try:
                uploaded = await self.write_stream(
                    destination_key, streamed(),
                    options=WriteOptions(properties=properties, metadata=dict(metadata),
                                         create_only=opts.create_only,
                                         expected_version=opts.expected_version),
                )
                return WriteResult(uploaded.version)
            except StorageError as write_error:
                if write_error.kind == "precondition_failed":
                    raise CopyConditionFailed("destination") from write_error
                raise

    async def _copy_server(
        self, blob: Any, source: ObjectInfo, destination_key: str,
        properties: ContentProperties, metadata: dict[str, str] | Mapping[str, str],
        conditions: dict[str, Any],
    ) -> WriteResult:
        block_ids: list[str] = []
        try:
            async with _errors():
                source_url = await self._read_sas_url(source.key)
                for number, start in enumerate(range(0, source.size, COPY_PART_SIZE), 1):
                    end = min(start + COPY_PART_SIZE, source.size) - 1
                    block_id = base64.b64encode(f"{uuid4().hex}:{number:05d}".encode()).decode()
                    try:
                        await blob.stage_block_from_url(
                            block_id, source_url, source_offset=start,
                            source_length=end - start + 1, retry_total=0,
                            headers={"x-ms-source-if-match": source.version},
                        )
                    except Exception as error:
                        from azure.core.exceptions import (
                            HttpResponseError, ServiceRequestError, ServiceResponseError,
                        )

                        if isinstance(error, (HttpResponseError, ServiceRequestError, ServiceResponseError)):
                            mapped = _service_error(error)
                            if mapped.kind == "unsupported":
                                raise mapped from error
                            if mapped.kind in ("precondition_failed", "not_found"):
                                raise CopyConditionFailed("source") from error
                            raise mapped from error
                        raise
                    block_ids.append(block_id)
                from azure.storage.blob import BlockState, BlobBlock

                response = await blob.commit_block_list(
                    [BlobBlock(block_id=block_id, state=BlockState.UNCOMMITTED) for block_id in block_ids],
                    content_settings=_content_settings(properties),
                    metadata=_metadata(dict(metadata)), retry_total=0, **conditions,
                )
        except CopyConditionFailed:
            raise
        except StorageError as error:
            if error.kind == "precondition_failed":
                # A conditional commit failure is about the destination.
                raise CopyConditionFailed("destination") from error
            raise
        etag = response.get("etag") if isinstance(response, dict) else getattr(response, "etag", None)
        if not isinstance(etag, str) or not etag:
            raise StorageError("backend_error", provider_code="MissingCommittedVersion")
        return WriteResult(_version(etag))

    async def list_page(
        self, prefix: str = "", *, delimiter: str | None = None,
        cursor: PageCursor | None = None, limit: int = 1000,
    ) -> ObjectPage:
        if not 1 <= limit <= 1000:
            raise ValueError("Blob page size must be between 1 and 1000")
        async with _errors():
            if delimiter:
                listing = self._container.walk_blobs(
                    name_starts_with=prefix, delimiter=delimiter, results_per_page=limit,
                )
            else:
                listing = self._container.list_blobs(name_starts_with=prefix, results_per_page=limit)
            pager = listing.by_page(continuation_token=cursor)
            try:
                page = await anext(pager)
            except StopAsyncIteration:
                page = []
            # Azure's async pager advances continuation_token while the page is
            # consumed. Reading it before iterating the page silently truncates
            # listings at the first service page.
            if isinstance(page, list):
                blobs = list(page)
            else:
                blobs = [blob async for blob in cast(AsyncIterator[Any], page)]
            next_token = getattr(pager, "continuation_token", None)
        from azure.storage.blob.aio import BlobPrefix

        objects: list[ObjectSummary] = []
        prefixes: list[str] = []
        for blob in blobs:
            if isinstance(blob, BlobPrefix):
                prefixes.append(blob.name)
                continue
            objects.append(ObjectSummary(
                key=blob.name, size=int(blob.size or 0), version=_version(str(blob.etag or "")),
                modified_at=getattr(blob, "last_modified", None),
            ))
        return ObjectPage(
            tuple(objects), tuple(sorted(prefixes)),
            PageCursor(next_token) if isinstance(next_token, str) and next_token else None,
        )

    async def signed_request(
        self, key: str, method: Literal["GET", "PUT"], *, expires_in: int = 3600,
        content_type: str | None = None, content_length: int | None = None,
        response_content_type: str | None = None,
        response_content_disposition: str | None = None,
    ) -> SignedRequest:
        from azure.storage.blob import BlobSasPermissions

        if not 1 <= expires_in <= 604800:
            raise ValueError("SAS lifetime must be between 1 and 604800 seconds")
        if method not in ("GET", "PUT"):
            raise ValueError("Signed access supports GET and PUT")
        if method == "PUT" and (response_content_type is not None or response_content_disposition is not None):
            raise ValueError("Response overrides apply only to signed downloads")
        if method == "GET" and content_type is not None:
            raise ValueError("Content type is an upload request header; use a download response override")
        if content_length is not None:
            if method != "PUT" or isinstance(content_length, bool) or not isinstance(content_length, int) or content_length < 0:
                raise ValueError("Content length must be a nonnegative integer for a signed upload")
        async with _errors():
            url = await self._signed_url(
                key,
                permissions=BlobSasPermissions(
                    read=method == "GET", write=method == "PUT", create=method == "PUT",
                ),
                expires_in=expires_in, content_type=response_content_type,
                content_disposition=response_content_disposition,
            )
        if method == "GET":
            headers: dict[str, str] = {}
        else:
            # BlockBlob uploads require the type header; the browser client
            # forwards every returned header with its direct PUT.
            headers = {"x-ms-blob-type": "BlockBlob"}
            if content_type is not None:
                headers["Content-Type"] = content_type
            if content_length is not None:
                headers["Content-Length"] = str(content_length)
        return SignedRequest(method, url, headers, expires_in)


class SyncAzureObjectStore:
    """Narrow whole-object reads for parent-process import/setup callers."""

    def __init__(self, namespace: str, settings: Settings):
        from azure.storage.blob import BlobServiceClient

        if not namespace:
            raise StorageConfigurationError("An object storage namespace is required")
        if not settings.azure_blob_configured:
            raise StorageConfigurationError("Azure Blob storage is not configured")
        self._credential: Any = None
        try:
            if settings.azure_blob_auth == "account_key":
                from azure.core.credentials import AzureNamedKeyCredential

                key = settings.azure_blob_account_key
                if key is None or not key.get_secret_value():
                    raise StorageConfigurationError("Azure Blob account key is not configured")
                credential = AzureNamedKeyCredential(
                    AzureObjectStore._account_name(settings), key.get_secret_value()
                )
            elif settings.azure_blob_auth == "managed_identity":
                from azure.identity import ManagedIdentityCredential

                self._credential = ManagedIdentityCredential(
                    client_id=settings.azure_blob_managed_identity_client_id
                )
                credential = self._credential
            else:
                raise StorageConfigurationError(
                    f"Unsupported Azure Blob auth mode: {settings.azure_blob_auth}"
                )
            self._client = BlobServiceClient(settings.azure_blob_account_url, credential=credential)
        except BaseException:
            self.close()
            raise
        self._namespace = namespace

    def close(self) -> None:
        client, credential = getattr(self, "_client", None), self._credential
        self._client, self._credential = None, None
        try:
            if client is not None:
                client.close()
        finally:
            if credential is not None:
                credential.close()

    def read(self, key: str) -> bytes:
        from azure.core.exceptions import (
            HttpResponseError, ServiceRequestError, ServiceResponseError,
        )

        try:
            stream = self._client.get_blob_client(self._namespace, key).download_blob(decompress=False)
            content = stream.readall()
            integrity = _BlobIntegrity(
                stream.properties.size, stream.properties.content_settings.content_md5
            )
            integrity.update(content)
            integrity.finish()
            return content
        except (HttpResponseError, ServiceRequestError, ServiceResponseError) as error:
            raise _service_error(error) from error
