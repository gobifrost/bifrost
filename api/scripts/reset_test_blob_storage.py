"""Clear only an explicitly approved disposable Blob test container."""

import asyncio
import os

from src.config import Settings, get_settings
from src.core.storage import object_pages
from src.core.storage.azure import AzureObjectStore


async def reset_blob_test_storage(settings: Settings, *, approved: bool) -> None:
    if (
        not approved
        or settings.environment != "testing"
        or settings.object_storage_provider != "azure_blob"
    ):
        raise ValueError("Blob test reset requires testing mode and explicit disposable-container approval")
    async with AzureObjectStore.from_settings(settings) as store:
        listing = [item async for page in object_pages(store, "") for item in page.objects]
        if listing:
            await store.delete_many([item.key for item in listing])
        remaining = [item async for page in object_pages(store, "") for item in page.objects]
        if remaining:
            raise RuntimeError("Blob test container is not empty after reset")


if __name__ == "__main__":
    asyncio.run(reset_blob_test_storage(
        get_settings(), approved=os.environ.get("BIFROST_TEST_AZURE_BLOB_RESET") == "1",
    ))
