"""Clear only an explicitly approved disposable Blob test container."""

import asyncio
import os

from src.config import Settings, get_settings
from src.core.azure_blob_transport import AzureBlobTransport


async def reset_blob_test_storage(settings: Settings, *, approved: bool) -> None:
    if (
        not approved
        or settings.environment != "testing"
        or settings.object_storage_provider != "azure_blob"
    ):
        raise ValueError("Blob test reset requires testing mode and explicit disposable-container approval")
    async with AzureBlobTransport(settings) as client:
        async for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=settings.storage_bucket, Prefix="",
        ):
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if objects:
                result = await client.delete_objects(
                    Bucket=settings.storage_bucket, Delete={"Objects": objects},
                )
                if result.get("Errors"):
                    raise RuntimeError("Blob test reset could not delete every fixture object")
        if (await client.list_objects_v2(Bucket=settings.storage_bucket))["Contents"]:
            raise RuntimeError("Blob test container is not empty after reset")


if __name__ == "__main__":
    asyncio.run(reset_blob_test_storage(
        get_settings(), approved=os.environ.get("BIFROST_TEST_AZURE_BLOB_RESET") == "1",
    ))
