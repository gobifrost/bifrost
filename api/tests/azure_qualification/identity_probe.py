"""Standalone Azure-hosted managed-identity probe using the candidate transport.

Run only against a task-owned account/container. This executable smoke lane is
separate from Dockerized pytest: it must execute where Azure issues the managed
identity token. It prints case names and versions, never credentials or SAS URLs.
"""

import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile
from uuid import uuid4

import httpx

from src.config import Settings
from src.core.azure_blob_transport import AzureBlobTransport, SyncAzureBlobTransport
from src.core.s3_transport import S3Error
from src.services.blob_workspace_mirror import BlobWorkspaceMirror


async def main():
    settings = Settings(
        _env_file=None,
        secret_key="qualification-only-unused-key-000000000000",
        object_storage_provider="azure_blob",
        azure_blob_account_url=os.environ["QUAL_ACCOUNT_URL"],
        azure_blob_container="adapter-qualification",
        azure_blob_auth="managed_identity",
        azure_blob_account_key=None,
        s3_bucket=None,
        s3_access_key=None,
        s3_secret_key=None,
    )
    prefix = f"_adapter_qualification/{uuid4().hex}/"
    passed = []

    async def record(name):
        passed.append(name)
        print(json.dumps({"passed": name}), flush=True)

    async with AzureBlobTransport(settings) as client:
        try:
            await client.head_bucket(Bucket=settings.storage_bucket)
            key = prefix + "source"
            original = await client.put_object(
                Bucket=settings.storage_bucket, Key=key, Body=b"identity-fixture",
                Metadata={"bifrost-move-row": "fixture"}, IfNoneMatch="*",
            )
            await record("managed_identity_write_and_container_health")
            try:
                await client.put_object(
                    Bucket=settings.storage_bucket, Key=key, Body=b"stale",
                    IfMatch='"unobserved-version"',
                )
            except S3Error as exc:
                assert exc.status_code == 412
            else:
                raise AssertionError("stale write succeeded")
            assert (await client.head_object(Bucket=settings.storage_bucket, Key=key))["ETag"] == original["ETag"]
            await record("stale_write_preserves_observed_version")

            async with httpx.AsyncClient() as http:
                get_url = await client.generate_presigned_url(
                    "get_object", Params={"Bucket": settings.storage_bucket, "Key": key},
                    ExpiresIn=60,
                )
                response = await http.get(get_url)
                assert response.status_code == 200 and response.content == b"identity-fixture"
                put_url = await client.generate_presigned_url(
                    "put_object", Params={"Bucket": settings.storage_bucket, "Key": prefix + "signed"},
                    ExpiresIn=60,
                )
                response = await http.put(
                    put_url, content=b"delegated-upload",
                    headers={"x-ms-blob-type": "BlockBlob", "Content-Type": "text/plain"},
                )
                assert response.status_code == 201
            await record("user_delegation_sas_get_and_put")

            def sync_read():
                reader = SyncAzureBlobTransport(settings)
                try:
                    return reader.get_object(Bucket=settings.storage_bucket, Key=key)["Body"].read()
                finally:
                    reader.close()
            assert await asyncio.to_thread(sync_read) == b"identity-fixture"
            await record("synchronous_managed_identity_read")

            # 300 MiB crosses copy_object's 256 MiB source-block boundary.
            chunk = bytes(range(256)) * 4096
            digest = hashlib.sha256()
            with TemporaryFile() as content:
                for _ in range(300):
                    content.write(chunk)
                    digest.update(chunk)
                content.seek(0)
                await client.put_object(Bucket=settings.storage_bucket, Key=prefix + "large", Body=content)
            await client.copy_object(
                Bucket=settings.storage_bucket, Key=prefix + "large-copy",
                CopySource={"Bucket": settings.storage_bucket, "Key": prefix + "large"},
            )
            response = await client.get_object(Bucket=settings.storage_bucket, Key=prefix + "large-copy")
            actual = hashlib.sha256()
            async with response["Body"] as body:
                async for data in body.iter_chunks():
                    actual.update(data)
            assert actual.digest() == digest.digest()
            await record("300_mib_multiblock_source_copy_and_stream_readback")

            with TemporaryDirectory() as directory:
                root = Path(directory)
                source, restored = root / "source", root / "restored"
                source.mkdir()
                (source / "fixture.txt").write_bytes(b"git-checkpoint-fixture")
                mirror = BlobWorkspaceMirror(settings)
                await mirror.upload(source, prefix + "checkpoint/")
                await mirror.download(prefix + "checkpoint/", restored)
                assert (restored / "fixture.txt").read_bytes() == b"git-checkpoint-fixture"
            await record("managed_identity_native_mirror_recovery")
        finally:
            async for page in client.get_paginator("list_objects_v2").paginate(Bucket=settings.storage_bucket, Prefix=prefix):
                for item in page["Contents"]:
                    await client.delete_object(Bucket=settings.storage_bucket, Key=item["Key"])
            assert not (await client.list_objects_v2(Bucket=settings.storage_bucket, Prefix=prefix))["Contents"]
            print(json.dumps({"fixture_cleanup": "verified"}), flush=True)
    print(json.dumps({"passed_count": len(passed), "source": os.environ["QUAL_SOURCE_SHA"], "versions": {name: importlib.metadata.version(name) for name in ("azure-storage-blob", "azure-identity", "azure-core")}}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
