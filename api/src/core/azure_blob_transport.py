"""Azure Blob storage client for workspace and upload objects.

Adapted from Midtown-Technology-Group/bifrost at
162d3f47722baf2992e60d11a6026cb95b9dbf04 (AGPL-3.0). The fork supplies
operation ownership, credential lifecycle, listing and SAS signing; current
upstream requires the CRT transport response and conditional block contracts.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from io import BytesIO
from uuid import uuid4
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import wraps
from types import SimpleNamespace
from typing import Any, Concatenate, ParamSpec, TypeVar, cast
from urllib.parse import urlparse

from src.config import Settings
from src.core.s3_transport import S3Error, S3NoSuchKey, S3ListObjectsV2Paginator


_P = ParamSpec("_P")
_R = TypeVar("_R")


def _storage_error(exc: Any) -> Exception:
    from azure.core.exceptions import ServiceRequestError, ServiceResponseError

    if isinstance(exc, (ServiceRequestError, ServiceResponseError)):
        return ConnectionError("Azure Blob transport failed")
    status = exc.status_code or 500
    native_code = getattr(exc, "error_code", None)
    code = str(getattr(native_code, "value", native_code) or status)
    if code == "BlobNotFound":
        return S3NoSuchKey("NoSuchKey", 404, "Blob not found")
    if status == 412 or code == "BlobAlreadyExists":
        code, status = "PreconditionFailed", 412
    elif status in {401, 403}:
        code = "AccessDenied"
    return S3Error(code, status, "Azure Blob operation failed")


def _owned_operation(
    method: Callable[Concatenate["AzureBlobTransport", _P], Awaitable[_R]],
) -> Callable[Concatenate["AzureBlobTransport", _P], Awaitable[_R]]:
    """Give each top-level call its own transport; nested calls share that owner."""

    @wraps(method)
    async def run(
        self: "AzureBlobTransport", *args: _P.args, **kwargs: _P.kwargs
    ) -> _R:
        async with self.get_client() as client:
            from azure.core.exceptions import (
                HttpResponseError,
                ServiceRequestError,
                ServiceResponseError,
            )

            try:
                return await method(client, *args, **kwargs)
            except (
                HttpResponseError,
                ServiceRequestError,
                ServiceResponseError,
            ) as exc:
                raise _storage_error(exc) from None

    return run


class AzureBlobTransport:
    """Client for Azure Blob storage operations."""

    exceptions = SimpleNamespace(NoSuchKey=S3NoSuchKey)

    def __init__(self, settings: Settings):
        self.settings = settings
        self._operation_owned = False
        self._credential: Any = None
        self._service_client: Any = None
        self._container_client: Any = None
        self._uploads: dict[str, dict[str, Any]] = {}
        self._account_name = (
            settings.azure_blob_account_name
            or self._parse_account_name(settings.azure_blob_account_url or "")
        )

    async def __aenter__(self) -> AzureBlobTransport:
        self._operation_owned = True
        try:
            await self._ensure_client()
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def aclose(self) -> None:
        await self.close()

    @staticmethod
    def _parse_account_name(account_url: str) -> str:
        host = urlparse(account_url).hostname or ""
        return host.split(".", 1)[0]

    async def close(self) -> None:
        service, credential = self._service_client, self._credential
        self._service_client = self._container_client = self._credential = None
        self._uploads.clear()
        try:
            if service is not None:
                await service.close()
        finally:
            close_credential = getattr(credential, "close", None)
            if close_credential is not None:
                await close_credential()

    async def _ensure_client(self) -> None:
        if self._container_client is not None:
            return
        if not self.settings.azure_blob_configured:
            raise RuntimeError("Azure Blob storage not configured")

        from azure.storage.blob.aio import BlobServiceClient

        credential: Any = None
        if self.settings.azure_blob_auth == "account_key":
            from azure.core.credentials import AzureNamedKeyCredential

            credential = AzureNamedKeyCredential(
                self._account_name,
                self.settings.azure_blob_account_key.get_secret_value(),
            )
        elif self.settings.azure_blob_auth == "managed_identity":
            from azure.identity.aio import ManagedIdentityCredential

            credential = ManagedIdentityCredential(
                client_id=self.settings.azure_blob_managed_identity_client_id
            )
        else:
            raise RuntimeError(
                f"Unsupported Azure Blob auth mode: {self.settings.azure_blob_auth}"
            )

        self._credential = credential
        self._service_client = BlobServiceClient(
            self.settings.azure_blob_account_url,
            credential=credential,
        )
        self._container_client = self._service_client.get_container_client(
            self.settings.azure_blob_container
        )

    @asynccontextmanager
    async def get_client(self) -> AsyncIterator["AzureBlobTransport"]:
        if self._operation_owned:
            yield self
            return
        client = AzureBlobTransport(self.settings)
        client._operation_owned = True
        try:
            await client._ensure_client()
            yield client
        finally:
            await client.close()

    def get_paginator(self, operation_name: str):
        if operation_name != "list_objects_v2":
            raise NotImplementedError(f"Unsupported paginator: {operation_name}")
        return S3ListObjectsV2Paginator(cast(Any, self))

    @_owned_operation
    async def head_bucket(self, *, Bucket: str):
        """S3-compatible container availability check used by health probes."""
        self._check_bucket(Bucket)
        await self._ensure_client()
        return await self._container_client.get_container_properties()

    @_owned_operation
    async def list_objects_v2(
        self,
        *,
        Bucket: str,
        Prefix: str = "",
        Delimiter: str | None = None,
        ContinuationToken: str | None = None,
        MaxKeys: int | None = None,
    ) -> dict:
        """S3-shaped list operation for RepoStorage."""
        self._check_bucket(Bucket)
        await self._ensure_client()

        list_kwargs: dict[str, Any] = {"name_starts_with": Prefix}
        if MaxKeys is not None:
            list_kwargs["results_per_page"] = MaxKeys
        listing = (
            self._container_client.walk_blobs(delimiter=Delimiter, **list_kwargs)
            if Delimiter
            else self._container_client.list_blobs(**list_kwargs)
        )
        pager = listing.by_page(
            continuation_token=ContinuationToken,
        )
        try:
            page = await anext(pager)
        except StopAsyncIteration:
            page = []

        contents: list[dict] = []
        common_prefixes: set[str] = set()
        if hasattr(page, "__aiter__"):
            blobs = [blob async for blob in cast(AsyncIterator[Any], page)]
        else:
            blobs = list(cast(Iterable[Any], page))
        # Azure's async pager advances ``continuation_token`` while the page is
        # consumed. Reading it before iterating the page silently truncates
        # listings at the first service page.
        next_token = getattr(pager, "continuation_token", None)

        for blob in blobs:
            name = blob.name
            from azure.storage.blob.aio import BlobPrefix

            if isinstance(blob, BlobPrefix):
                common_prefixes.add(name)
                continue
            # Azure's list XML SDK strips ETag quotes, while GET/HEAD/write
            # responses retain HTTP entity-tag syntax. Keep one opaque token
            # representation for freshness comparisons and If-Match requests.
            etag = str(getattr(blob, "etag", "") or "")
            if etag and not etag.startswith(('"', 'W/"')):
                etag = f'"{etag}"'
            contents.append(
                {
                    "Key": name,
                    "Size": getattr(blob, "size", None),
                    "ETag": etag,
                    "LastModified": getattr(blob, "last_modified", None),
                }
            )

        response = {
            "Contents": contents,
            "IsTruncated": bool(next_token),
        }
        if Delimiter:
            response["CommonPrefixes"] = [
                {"Prefix": prefix} for prefix in sorted(common_prefixes)
            ]
        if next_token:
            response["NextContinuationToken"] = next_token
        return response

    async def _generate_blob_sas(
        self,
        key: str,
        *,
        permissions,
        expires_in: int,
        content_type: str | None = None,
        content_disposition: str | None = None,
    ) -> str:
        from azure.storage.blob import generate_blob_sas

        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(seconds=expires_in)
        sas_args = {
            "account_name": self._account_name,
            "container_name": self.settings.azure_blob_container,
            "blob_name": key,
            "permission": permissions,
            "expiry": expires_at,
        }
        if content_type is not None:
            sas_args["content_type"] = content_type
        if content_disposition is not None:
            sas_args["content_disposition"] = content_disposition

        if self.settings.azure_blob_auth == "account_key":
            sas_args["account_key"] = (
                self.settings.azure_blob_account_key.get_secret_value()
            )
        else:
            user_delegation_key = await self._service_client.get_user_delegation_key(
                key_start_time=issued_at,
                key_expiry_time=expires_at,
            )
            sas_args["user_delegation_key"] = user_delegation_key

        return generate_blob_sas(**sas_args)

    @staticmethod
    def _conditions(values: dict[str, Any]) -> dict[str, Any]:
        from azure.core import MatchConditions

        match, absent = values.get("IfMatch"), values.get("IfNoneMatch")
        if absent not in (None, "*") or (
            match is not None and (not match or absent is not None)
        ):
            raise ValueError("Unsupported or conflicting write preconditions")
        if match is not None:
            return {"etag": match, "match_condition": MatchConditions.IfNotModified}
        if absent == "*":
            return {"match_condition": MatchConditions.IfMissing}
        return {}

    @staticmethod
    def _properties(properties: Any) -> dict[str, Any]:
        metadata = {
            key.lower(): value for key, value in (properties.metadata or {}).items()
        }
        encoded = metadata.pop("bifrost_metadata", None)
        if encoded is not None:
            decoded = json.loads(base64.b64decode(encoded, validate=True))
            if not isinstance(decoded, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in decoded.items()
            ):
                raise ValueError("Invalid Bifrost Blob metadata envelope")
            if metadata:
                raise ValueError("Mixed native and Bifrost Blob metadata is ambiguous")
            metadata = decoded
        result = {
            "ContentLength": properties.size,
            "ETag": properties.etag,
            "LastModified": properties.last_modified,
            "Metadata": metadata,
        }
        for name, attribute in _CONTENT_HEADERS.items():
            value = getattr(properties.content_settings, attribute, None)
            if value is not None:
                result[name] = value
        return result

    @staticmethod
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

    @staticmethod
    def _content(values: dict[str, Any]) -> Any:
        from azure.storage.blob import ContentSettings

        if values.get("Expires") is not None:
            raise ValueError(
                "Azure Blob has no HTTP Expires object property; this contract is not supported"
            )
        return ContentSettings(
            **{
                attribute: values[name]
                for name, attribute in _CONTENT_HEADERS.items()
                if name in values
            }
        )

    @_owned_operation
    async def put_object(
        self, *, Bucket: str, Key: str, Body: Any, **kwargs: Any
    ) -> dict[str, Any]:
        self._check_bucket(Bucket)
        _require_options(
            kwargs,
            set(_CONTENT_HEADERS) | {"Expires", "Metadata", "IfMatch", "IfNoneMatch"},
        )
        response = await self._container_client.get_blob_client(Key).upload_blob(
            Body,
            overwrite=True,
            metadata=self._metadata(kwargs.get("Metadata")),
            retry_total=0,
            content_settings=self._content(kwargs),
            **self._conditions(kwargs),
        )
        return {"ETag": response["etag"], "LastModified": response.get("last_modified")}

    def _check_bucket(self, bucket: str) -> None:
        if bucket != self.settings.azure_blob_container:
            raise ValueError("Azure Blob request must use the configured container")

    @_owned_operation
    async def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self._check_bucket(Bucket)
        return self._properties(
            await self._container_client.get_blob_client(Key).get_blob_properties()
        )

    @_owned_operation
    async def get_object(
        self, *, Bucket: str, Key: str, ChecksumMode: str | None = None
    ) -> dict[str, Any]:
        self._check_bucket(Bucket)
        if ChecksumMode not in (None, "ENABLED"):
            raise ValueError("Unsupported checksum mode")
        blob = self._container_client.get_blob_client(Key)
        stream = await blob.download_blob(decompress=False)
        result = self._properties(stream.properties)
        result["Body"] = AzureBlobBody(
            stream,
            size=result["ContentLength"],
            content_md5=stream.properties.content_settings.content_md5,
        )
        return result

    @_owned_operation
    async def delete_object(
        self, *, Bucket: str, Key: str, IfMatch: str | None = None
    ) -> dict[str, Any]:
        from azure.core.exceptions import ResourceNotFoundError

        self._check_bucket(Bucket)
        try:
            await self._container_client.delete_blob(
                # Like PUT/commit, a lost response leaves the mutation's result
                # ambiguous. Do not replay against a possible newer version.
                Key, retry_total=0, **self._conditions({"IfMatch": IfMatch})
            )
        except ResourceNotFoundError as exc:
            if getattr(exc.error_code, "value", exc.error_code) != "BlobNotFound":
                raise
        return {}

    @_owned_operation
    async def delete_objects(
        self, *, Bucket: str, Delete: dict[str, Any]
    ) -> dict[str, Any]:
        deleted, errors = [], []
        for item in Delete.get("Objects", []):
            if set(item) != {"Key"}:
                raise ValueError("Versioned deletes are not used by Bifrost")
            try:
                await self.delete_object(Bucket=Bucket, Key=item["Key"])
                deleted.append({"Key": item["Key"]})
            except S3Error as exc:
                errors.append(
                    {"Key": item["Key"], "Code": exc.code, "Message": exc.message}
                )
        return {"Deleted": deleted, "Errors": errors}

    @_owned_operation
    async def copy_object(
        self, *, Bucket: str, Key: str, CopySource: dict[str, str], **kwargs: Any
    ) -> dict[str, Any]:
        self._check_bucket(Bucket)
        self._check_bucket(CopySource["Bucket"])
        _require_options(
            kwargs,
            set(_CONTENT_HEADERS)
            | {"Expires", "Metadata", "MetadataDirective", "CopySourceIfMatch"},
        )
        directive = kwargs.get("MetadataDirective", "COPY")
        if directive not in ("COPY", "REPLACE"):
            raise ValueError("Unsupported metadata directive")
        source = await self.head_object(Bucket=Bucket, Key=CopySource["Key"])
        etag = kwargs.get("CopySourceIfMatch", source["ETag"])
        if etag != source["ETag"]:
            raise S3Error("PreconditionFailed", 412)
        values = kwargs if directive == "REPLACE" else source
        content = {name: values[name] for name in _CONTENT_HEADERS if name in values}
        if values.get("Expires") is not None:
            content["Expires"] = values["Expires"]
        if source["ContentLength"] == 0:
            from azure.core import MatchConditions

            stream = await self._container_client.get_blob_client(
                CopySource["Key"]
            ).download_blob(
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
                decompress=False,
            )
            data = await stream.readall()
            response = await self.put_object(
                Bucket=Bucket,
                Key=Key,
                Body=data,
                Metadata=values.get("Metadata", {}),
                **content,
            )
            return {"CopyObjectResult": {"ETag": response["ETag"]}}
        created = await self.create_multipart_upload(
            Bucket=Bucket, Key=Key, Metadata=values.get("Metadata", {}), **content
        )
        upload_id = created["UploadId"]
        try:
            parts = []
            for number, start in enumerate(
                range(0, source["ContentLength"], 256 * 1024**2), 1
            ):
                end = min(start + 256 * 1024**2, source["ContentLength"]) - 1
                part = await self.upload_part_copy(
                    Bucket=Bucket,
                    Key=Key,
                    UploadId=upload_id,
                    PartNumber=number,
                    CopySource=CopySource,
                    CopySourceIfMatch=etag,
                    CopySourceRange=f"bytes={start}-{end}",
                )
                parts.append(
                    {"PartNumber": number, "ETag": part["CopyPartResult"]["ETag"]}
                )
            response = await self.complete_multipart_upload(
                Bucket=Bucket,
                Key=Key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
            return {"CopyObjectResult": {"ETag": response["ETag"]}}
        except BaseException:
            await self.abort_multipart_upload(
                Bucket=Bucket, Key=Key, UploadId=upload_id
            )
            raise

    @_owned_operation
    async def create_multipart_upload(
        self, *, Bucket: str, Key: str, **kwargs: Any
    ) -> dict[str, Any]:
        self._check_bucket(Bucket)
        _require_options(kwargs, set(_CONTENT_HEADERS) | {"Expires", "Metadata"})
        content = self._content(kwargs)
        upload_id = uuid4().hex
        self._uploads[upload_id] = {
            "key": Key,
            "content": content,
            "metadata": kwargs.get("Metadata"),
            "parts": {},
            "lock": asyncio.Lock(),
        }
        return {"UploadId": upload_id}

    def _upload(self, key: str, upload_id: str) -> dict[str, Any]:
        upload = self._uploads.get(upload_id)
        if upload is None or upload["key"] != key:
            raise S3Error("NoSuchUpload", 404)
        return upload

    @staticmethod
    def _block_id(upload_id: str, number: int) -> str:
        if not 1 <= number <= 10000:
            raise ValueError("PartNumber must be between 1 and 10000")
        return base64.b64encode(f"{upload_id}:{number:05d}".encode()).decode()

    @_owned_operation
    async def upload_part(
        self, *, Bucket: str, Key: str, UploadId: str, PartNumber: int, Body: bytes
    ) -> dict[str, Any]:
        self._check_bucket(Bucket)
        upload = self._upload(Key, UploadId)
        async with upload["lock"]:
            self._upload(Key, UploadId)
            block = self._block_id(UploadId, PartNumber)
            await self._container_client.get_blob_client(Key).stage_block(
                block, Body, validate_content=True
            )
            etag = hashlib.sha256(Body).hexdigest()
            upload["parts"][PartNumber] = (block, etag)
            return {"ETag": etag}

    @_owned_operation
    async def upload_part_copy(
        self,
        *,
        Bucket: str,
        Key: str,
        UploadId: str,
        PartNumber: int,
        CopySource: dict[str, str],
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._check_bucket(Bucket)
        self._check_bucket(CopySource["Bucket"])
        _require_options(kwargs, {"CopySourceIfMatch", "CopySourceRange"})
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", kwargs.get("CopySourceRange", ""))
        if match is None or int(match[2]) < int(match[1]):
            raise ValueError("Expected a bounded inclusive copy range")
        upload = self._upload(Key, UploadId)
        async with upload["lock"]:
            self._upload(Key, UploadId)
            block = self._block_id(UploadId, PartNumber)
            url = await self.generate_presigned_url(
                "get_object", Params={"Bucket": Bucket, "Key": CopySource["Key"]}
            )
            conditions = {}
            if kwargs.get("CopySourceIfMatch"):
                # Unlike download_blob, this SDK method does not consume etag /
                # match_condition keywords. Send the REST source condition.
                conditions = {
                    "headers": {"x-ms-source-if-match": kwargs["CopySourceIfMatch"]},
                }
            await self._container_client.get_blob_client(Key).stage_block_from_url(
                block,
                url,
                source_offset=int(match[1]),
                source_length=int(match[2]) - int(match[1]) + 1,
                **conditions,
            )
            receipt = uuid4().hex
            upload["parts"][PartNumber] = (block, receipt)
            return {"CopyPartResult": {"ETag": receipt}}

    @_owned_operation
    async def complete_multipart_upload(
        self,
        *,
        Bucket: str,
        Key: str,
        UploadId: str,
        MultipartUpload: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        from azure.storage.blob import BlobBlock, BlockState

        self._check_bucket(Bucket)
        _require_options(kwargs, {"IfNoneMatch", "IfMatch"})
        upload = self._upload(Key, UploadId)
        async with upload["lock"]:
            self._upload(Key, UploadId)
            blocks, previous = [], 0
            for part in MultipartUpload.get("Parts", []):
                number = part["PartNumber"]
                stored = upload["parts"].get(number)
                if number <= previous or stored is None or stored[1] != part["ETag"]:
                    raise S3Error("InvalidPart", 400)
                blocks.append(
                    BlobBlock(block_id=stored[0], state=BlockState.UNCOMMITTED)
                )
                previous = number
            response = await self._container_client.get_blob_client(
                Key
            ).commit_block_list(
                blocks,
                content_settings=upload["content"],
                metadata=self._metadata(upload["metadata"]),
                retry_total=0,
                **self._conditions(kwargs),
            )
            del self._uploads[UploadId]
            return {
                "ETag": response["etag"],
                "LastModified": response.get("last_modified"),
            }

    @_owned_operation
    async def abort_multipart_upload(
        self, *, Bucket: str, Key: str, UploadId: str
    ) -> dict[str, Any]:
        self._check_bucket(Bucket)
        upload = self._upload(Key, UploadId)
        async with upload["lock"]:
            self._uploads.pop(UploadId, None)
        # Azure reclaims uncommitted blocks; deleting the blob would destroy an
        # existing committed destination or another concurrent upload.
        return {}

    @_owned_operation
    async def generate_presigned_url(
        self, ClientMethod: str, Params: dict[str, Any], ExpiresIn: int = 600
    ) -> str:
        from azure.storage.blob import BlobSasPermissions

        self._check_bucket(Params["Bucket"])
        if not 0 < ExpiresIn <= 604800:
            raise ValueError("SAS lifetime must be between 1 and 604800 seconds")
        if ClientMethod not in ("get_object", "put_object"):
            raise ValueError("Unsupported presign operation")
        sas = await self._generate_blob_sas(
            Params["Key"],
            permissions=BlobSasPermissions(
                read=ClientMethod == "get_object",
                write=ClientMethod == "put_object",
                create=ClientMethod == "put_object",
            ),
            expires_in=ExpiresIn,
            content_type=Params.get("ResponseContentType"),
            content_disposition=Params.get("ResponseContentDisposition"),
        )
        return f"{self._container_client.get_blob_client(Params['Key']).url}?{sas}"


_CONTENT_HEADERS = {
    "ContentType": "content_type",
    "ContentDisposition": "content_disposition",
    "ContentEncoding": "content_encoding",
    "ContentLanguage": "content_language",
    "CacheControl": "cache_control",
}


def _require_options(values: dict[str, Any], supported: set[str]) -> None:
    if unknown := values.keys() - supported:
        raise ValueError(f"Unsupported Azure Blob options: {sorted(unknown)}")


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
            raise S3Error("InvalidDigest", 502, "Invalid Azure Blob content MD5")

    def update(self, data: bytes) -> None:
        self._received += len(data)
        self._digest.update(data)

    def finish(self) -> None:
        if self._received != self._size:
            raise S3Error("IncompleteBody", 502, "Azure Blob response length mismatch")
        if self._expected is not None and self._digest.digest() != self._expected:
            raise S3Error("BadDigest", 502, "Azure Blob response MD5 mismatch")


class AzureBlobBody:
    """Incremental SDK download; the owning client context must outlive it."""

    def __init__(
        self,
        stream: Any,
        *,
        size: int,
        content_md5: bytes | bytearray | str | None = None,
    ):
        self._chunks = stream.chunks().__aiter__()
        self._buffer = bytearray()
        self._closed = False
        self._integrity = _BlobIntegrity(size, content_md5)

    async def __aenter__(self) -> AzureBlobBody:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def close(self) -> None:
        self._closed = True
        self._buffer.clear()
        close = getattr(self._chunks, "aclose", None)
        if close is not None:
            await close()

    async def read(self, amt: int | None = None) -> bytes:
        from azure.core.exceptions import (
            HttpResponseError,
            ServiceRequestError,
            ServiceResponseError,
        )

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
            except (
                HttpResponseError,
                ServiceRequestError,
                ServiceResponseError,
            ) as exc:
                raise _storage_error(exc) from None
        size = len(self._buffer) if amt is None else amt
        result = bytes(self._buffer[:size])
        del self._buffer[:size]
        return result

    async def iter_chunks(
        self, chunk_size: int = 8 * 1024 * 1024
    ) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        while chunk := await self.read(chunk_size):
            yield chunk


class SyncAzureBlobTransport:
    """Parent-only import fallback; no event loop or inherited async session."""

    def __init__(self, settings: Settings):
        from azure.storage.blob import BlobServiceClient

        if not settings.azure_blob_configured:
            raise RuntimeError("Azure Blob storage not configured")
        self._credential: Any = None
        self._client: Any = None
        credential: Any
        if settings.azure_blob_auth == "account_key":
            from azure.core.credentials import AzureNamedKeyCredential

            account = (
                settings.azure_blob_account_name
                or AzureBlobTransport._parse_account_name(
                    settings.azure_blob_account_url or ""
                )
            )
            credential = AzureNamedKeyCredential(
                account, settings.azure_blob_account_key.get_secret_value()
            )
        else:
            from azure.identity import ManagedIdentityCredential

            self._credential = ManagedIdentityCredential(
                client_id=settings.azure_blob_managed_identity_client_id
            )
            credential = self._credential
        try:
            self._client = BlobServiceClient(
                settings.azure_blob_account_url, credential=credential
            )
        except BaseException:
            self.close()
            raise
        self._container = settings.azure_blob_container

    def close(self) -> None:
        client, credential = self._client, self._credential
        self._client = self._credential = None
        try:
            if client is not None:
                client.close()
        finally:
            if credential is not None:
                credential.close()

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        from azure.core.exceptions import (
            HttpResponseError,
            ServiceRequestError,
            ServiceResponseError,
        )

        if Bucket != self._container:
            raise ValueError("Azure Blob request must use the configured container")
        try:
            stream = self._client.get_blob_client(Bucket, Key).download_blob(
                decompress=False
            )
            content = stream.readall()
            integrity = _BlobIntegrity(
                stream.properties.size, stream.properties.content_settings.content_md5
            )
            integrity.update(content)
            integrity.finish()
            return {"Body": BytesIO(content)}
        except (HttpResponseError, ServiceRequestError, ServiceResponseError) as exc:
            raise _storage_error(exc) from None
