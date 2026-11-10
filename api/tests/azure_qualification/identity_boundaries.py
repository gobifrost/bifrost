"""Explicit Azure-hosted identity authorization, SAS and race qualification.

Uses two task-owned accounts: the runner identity has a data role only on the
positive account. No role, account or container is created by this probe.
Only status summaries are printed, never tokens or signed URLs.
"""

import asyncio
import json
import os
from uuid import uuid4

import httpx

from src.config import Settings
from src.core.storage import object_pages
from src.core.storage.azure import AzureObjectStore
from src.core.storage.contracts import StorageError, WriteOptions


def configuration(url):
    return Settings(
        _env_file=None,
        secret_key="qualification-only-unused-key-000000000000",
        object_storage_provider="azure_blob",
        azure_blob_account_url=url,
        azure_blob_container="adapter-qualification",
        azure_blob_auth="managed_identity",
        azure_blob_account_key=None,
        s3_bucket=None, s3_access_key=None, s3_secret_key=None,
    )


async def main():
    denied = configuration(os.environ["QUAL_DENIED_ACCOUNT_URL"])
    async with AzureObjectStore.from_settings(denied) as client:
        for name, operation in (
            ("rbac_denies_container_read", client.probe),
            ("rbac_denies_delegation_key", lambda: client.signed_request("never-written", "GET")),
        ):
            try:
                await operation()
            except StorageError as error:
                assert error.kind == "permission_denied"
            else:
                raise AssertionError(f"{name}: operation unexpectedly authorized")
            print(json.dumps({"passed": name}), flush=True)

    settings = configuration(os.environ["QUAL_ACCOUNT_URL"])
    prefix = f"_adapter_qualification/{uuid4().hex}/"
    async with AzureObjectStore.from_settings(settings) as store:
        bucket = settings.storage_bucket
        assert bucket
        try:
            await store.write(prefix + "signed", b"signed-fixture")
            longest = await store.signed_request(prefix + "signed", "GET", expires_in=604800)
            async with httpx.AsyncClient() as http:
                response = await http.get(longest.url, headers=longest.headers)
                assert response.status_code == 200 and response.content == b"signed-fixture"
                shortest = await store.signed_request(prefix + "signed", "GET", expires_in=1)
                # Real service expiry is the boundary under test; this wait is
                # intentional, not a retry or a timing fix for another failure.
                await asyncio.sleep(3)
                assert (await http.get(shortest.url, headers=shortest.headers)).status_code == 403
            print(json.dumps({"passed": "delegation_sas_seven_day_and_expired_boundaries"}), flush=True)

            key = prefix + "race"

            async def create(data):
                return await store.write(key, data, options=WriteOptions(create_only=True))

            results = await asyncio.gather(create(b"first"), create(b"second"), return_exceptions=True)
            assert sum(not isinstance(result, BaseException) for result in results) == 1
            failures = [result for result in results if isinstance(result, BaseException)]
            assert len(failures) == 1 and failures[0].kind == "precondition_failed"
            async with store.open_read(key) as read:
                assert await read.body.read() in (b"first", b"second")
            print(json.dumps({"passed": "independent_conditional_completion_has_one_winner"}), flush=True)
        finally:
            listed = [item async for page in object_pages(store, prefix) for item in page.objects]
            if listed:
                await store.delete_many([item.key for item in listed])
            remaining = [item async for page in object_pages(store, prefix) for item in page.objects]
            assert not remaining
            print(json.dumps({"fixture_cleanup": "verified"}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
