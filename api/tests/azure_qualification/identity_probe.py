"""Standalone Azure-hosted managed-identity probe using the candidate store.

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
from src.core.storage import object_pages
from src.core.storage.azure import AzureObjectStore, SyncAzureObjectStore
from src.core.storage.azure_mirror import AzureWorkspaceMirror
from src.core.storage.contracts import StorageError, WriteOptions


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

    async with AzureObjectStore.from_settings(settings) as client:
        try:
            await client.probe()
            key = prefix + "source"
            original = await client.write(
                key, b"identity-fixture",
                options=WriteOptions(create_only=True, metadata={"bifrost-move-row": "fixture"}),
            )
            await record("managed_identity_write_and_container_health")
            try:
                await client.write(
                    key, b"stale",
                    options=WriteOptions(expected_version='"unobserved-version"'),
                )
            except StorageError as exc:
                assert exc.kind == "precondition_failed"
            else:
                raise AssertionError("stale write succeeded")
            assert (await client.stat(key)).version == original.version
            await record("stale_write_preserves_observed_version")

            async with httpx.AsyncClient() as http:
                get_ticket = await client.signed_request(key, "GET", expires_in=60)
                response = await http.get(get_ticket.url, headers=get_ticket.headers)
                assert response.status_code == 200 and response.content == b"identity-fixture"
                put_ticket = await client.signed_request(prefix + "signed", "PUT", expires_in=60)
                response = await http.request(
                    put_ticket.method, put_ticket.url,
                    headers={**put_ticket.headers, "Content-Type": "text/plain"},
                    content=b"delegated-upload",
                )
                assert response.status_code == 201
            await record("user_delegation_sas_get_and_put")

            def sync_read():
                reader = SyncAzureObjectStore(settings.azure_blob_container, settings)
                try:
                    return reader.read(key)
                finally:
                    reader.close()
            assert await asyncio.to_thread(sync_read) == b"identity-fixture"
            await record("synchronous_managed_identity_read")

            # 300 MiB crosses the 256 MiB server-side copy range boundary.
            chunk = bytes(range(256)) * 4096
            digest = hashlib.sha256()
            with TemporaryFile() as content:
                for _ in range(300):
                    content.write(chunk)
                    digest.update(chunk)
                content.seek(0)
                await client.write(prefix + "large", content.read())
            snapshot = await client.stat(prefix + "large")
            await client.copy(snapshot, prefix + "large-copy")
            actual = hashlib.sha256()
            async with client.open_read(prefix + "large-copy") as read:
                async for data in read.body.iter_chunks():
                    actual.update(data)
            assert actual.digest() == digest.digest()
            await record("300_mib_multiblock_source_copy_and_stream_readback")

            with TemporaryDirectory() as directory:
                root = Path(directory)
                source, restored = root / "source", root / "restored"
                source.mkdir()
                (source / "fixture.txt").write_bytes(b"git-checkpoint-fixture")
                mirror = AzureWorkspaceMirror(settings)
                await mirror.upload(source, prefix + "checkpoint/")
                await mirror.download(prefix + "checkpoint/", restored)
                assert (restored / "fixture.txt").read_bytes() == b"git-checkpoint-fixture"
            await record("managed_identity_native_mirror_recovery")
        finally:
            listed = [item async for page in object_pages(client, prefix) for item in page.objects]
            if listed:
                await client.delete_many([item.key for item in listed])
            remaining = [item async for page in object_pages(client, prefix) for item in page.objects]
            assert not remaining
            print(json.dumps({"fixture_cleanup": "verified"}), flush=True)
    print(json.dumps({"passed_count": len(passed), "source": os.environ["QUAL_SOURCE_SHA"], "versions": {name: importlib.metadata.version(name) for name in ("azure-storage-blob", "azure-identity", "azure-core")}}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
