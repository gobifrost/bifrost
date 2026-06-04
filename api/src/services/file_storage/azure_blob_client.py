"""Azure Blob storage client for workspace and upload objects.

This adapter intentionally presents the small aiobotocore/S3-shaped surface
used by FileStorageService so the rest of the storage path can move one piece
at a time.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import urlparse

from src.config import Settings


class _AsyncBody:
    def __init__(self, content: bytes):
        self._content = content

    async def read(self) -> bytes:
        return self._content


class _ListObjectsV2Paginator:
    def __init__(self, client: "AzureBlobStorageClient"):
        self._client = client

    async def paginate(self, *, Bucket: str, Prefix: str = ""):
        del Bucket
        async for blob in self._client._container_client.list_blobs(
            name_starts_with=Prefix
        ):
            yield {
                "Contents": [
                    {
                        "Key": blob.name,
                        "Size": getattr(blob, "size", None),
                        "ETag": str(getattr(blob, "etag", "") or "").strip('"'),
                    }
                ]
            }


class AzureBlobStorageClient:
    """Client for Azure Blob storage operations."""

    class exceptions:
        class NoSuchKey(Exception):
            """Raised when a blob key does not exist."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._credential = None
        self._service_client = None
        self._container_client = None
        self._account_name = self._parse_account_name(
            settings.azure_blob_account_url or ""
        )

    @staticmethod
    def _parse_account_name(account_url: str) -> str:
        host = urlparse(account_url).hostname or ""
        return host.split(".", 1)[0]

    async def close(self) -> None:
        if self._service_client is not None:
            await self._service_client.close()
        close_credential = getattr(self._credential, "close", None)
        if close_credential is not None:
            await close_credential()

    async def _ensure_client(self) -> None:
        if self._container_client is not None:
            return
        if not self.settings.azure_blob_configured:
            raise RuntimeError("Azure Blob storage not configured")

        from azure.storage.blob.aio import BlobServiceClient

        credential = None
        if self.settings.azure_blob_auth == "account_key":
            credential = self.settings.azure_blob_account_key
        elif self.settings.azure_blob_auth == "default_credential":
            from azure.identity.aio import DefaultAzureCredential

            credential = DefaultAzureCredential()
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
    async def get_client(self):
        await self._ensure_client()
        yield self

    def get_paginator(self, operation_name: str):
        if operation_name != "list_objects_v2":
            raise NotImplementedError(f"Unsupported paginator: {operation_name}")
        return _ListObjectsV2Paginator(self)

    async def head_bucket(self, *, Bucket: str):
        """S3-compatible container availability check used by health probes."""
        del Bucket
        await self._ensure_client()
        return await self._container_client.get_container_properties()

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
        del Bucket
        await self._ensure_client()

        list_kwargs: dict[str, Any] = {"name_starts_with": Prefix}
        if MaxKeys is not None:
            list_kwargs["results_per_page"] = MaxKeys
        pager = self._container_client.list_blobs(**list_kwargs).by_page(
            continuation_token=ContinuationToken,
        )
        try:
            page = await anext(pager)
        except StopAsyncIteration:
            page = []
        next_token = getattr(pager, "continuation_token", None)

        contents: list[dict] = []
        common_prefixes: set[str] = set()
        if hasattr(page, "__aiter__"):
            blobs = [blob async for blob in cast(AsyncIterator[Any], page)]
        else:
            blobs = list(cast(Iterable[Any], page))

        for blob in blobs:
            name = blob.name
            if Delimiter:
                remainder = name[len(Prefix) :]
                if Delimiter in remainder:
                    folder = Prefix + remainder.split(Delimiter, 1)[0] + Delimiter
                    common_prefixes.add(folder)
                    continue
            contents.append(
                {
                    "Key": name,
                    "Size": getattr(blob, "size", None),
                    "ETag": str(getattr(blob, "etag", "") or "").strip('"'),
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

    async def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str | None = None,
    ) -> None:
        del Bucket
        from azure.storage.blob import ContentSettings

        await self._ensure_client()
        await self._container_client.upload_blob(
            name=Key,
            data=Body,
            overwrite=True,
            content_settings=ContentSettings(content_type=ContentType),
        )

    async def get_object(self, *, Bucket: str, Key: str) -> dict[str, _AsyncBody]:
        del Bucket
        from azure.core.exceptions import ResourceNotFoundError

        await self._ensure_client()
        try:
            stream = await self._container_client.download_blob(Key)
            return {"Body": _AsyncBody(await stream.readall())}
        except ResourceNotFoundError as exc:
            raise self.exceptions.NoSuchKey(Key) from exc

    async def delete_object(self, *, Bucket: str, Key: str) -> None:
        del Bucket
        from azure.core.exceptions import ResourceNotFoundError

        await self._ensure_client()
        try:
            await self._container_client.delete_blob(Key)
        except ResourceNotFoundError:
            return

    async def head_object(self, *, Bucket: str, Key: str):
        del Bucket
        from azure.core.exceptions import ResourceNotFoundError

        await self._ensure_client()
        try:
            properties = await self._container_client.get_blob_client(
                Key
            ).get_blob_properties()
        except ResourceNotFoundError as exc:
            raise self.exceptions.NoSuchKey(Key) from exc

        return SimpleNamespace(
            content_length=properties.size,
            content_type=properties.content_settings.content_type,
            etag=str(properties.etag or "").strip('"'),
        )

    async def copy_object(self, *, Bucket: str, CopySource: dict, Key: str) -> None:
        del Bucket
        await self._ensure_client()
        source_key = CopySource["Key"]
        source_url = await self.generate_presigned_download_url(source_key)
        dest_blob = self._container_client.get_blob_client(Key)
        copy_result = await dest_blob.start_copy_from_url(source_url)
        if copy_result.get("copy_status") == "success":
            return

        for _ in range(30):
            properties = await dest_blob.get_blob_properties()
            copy_status = getattr(properties.copy, "status", None)
            if copy_status == "success":
                return
            if copy_status in {"failed", "aborted"}:
                raise RuntimeError(f"Azure Blob copy {copy_status}: {properties.copy}")
            await asyncio.sleep(0.5)

        raise TimeoutError(f"Azure Blob copy did not complete for {Key!r}")

    async def _generate_blob_sas(
        self,
        key: str,
        *,
        permissions,
        expires_in: int,
        content_type: str | None = None,
    ) -> str:
        from azure.storage.blob import generate_blob_sas

        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        sas_args = {
            "account_name": self._account_name,
            "container_name": self.settings.azure_blob_container,
            "blob_name": key,
            "permission": permissions,
            "expiry": expires_at,
        }
        if content_type is not None:
            sas_args["content_type"] = content_type

        if self.settings.azure_blob_auth == "account_key":
            sas_args["account_key"] = self.settings.azure_blob_account_key
        else:
            user_delegation_key = await self._service_client.get_user_delegation_key(
                key_start_time=datetime.now(UTC) - timedelta(minutes=5),
                key_expiry_time=expires_at,
            )
            sas_args["user_delegation_key"] = user_delegation_key

        return generate_blob_sas(**sas_args)

    async def generate_presigned_upload_url(
        self,
        path: str,
        content_type: str,
        expires_in: int = 600,
    ) -> str:
        from azure.storage.blob import BlobSasPermissions

        await self._ensure_client()
        sas = await self._generate_blob_sas(
            path,
            permissions=BlobSasPermissions(write=True, create=True),
            expires_in=expires_in,
            content_type=content_type,
        )
        return f"{self._container_client.get_blob_client(path).url}?{sas}"

    def presigned_upload_headers(self, content_type: str) -> dict[str, str]:
        return {
            "Content-Type": content_type,
            "x-ms-blob-type": "BlockBlob",
        }

    async def generate_presigned_download_url(
        self,
        path: str,
        expires_in: int = 600,
    ) -> str:
        from azure.storage.blob import BlobSasPermissions

        await self._ensure_client()
        sas = await self._generate_blob_sas(
            path,
            permissions=BlobSasPermissions(read=True),
            expires_in=expires_in,
        )
        return f"{self._container_client.get_blob_client(path).url}?{sas}"

    async def read_uploaded_file(self, path: str) -> bytes:
        try:
            response = await self.get_object(Bucket="", Key=path)
        except self.exceptions.NoSuchKey as exc:
            raise FileNotFoundError(f"Uploaded file not found: {path}") from exc
        return await response["Body"].read()
