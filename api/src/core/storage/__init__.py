"""The application entry point for configured object storage."""

from collections.abc import AsyncIterator, Hashable
from contextlib import AsyncExitStack, asynccontextmanager

from src.config import Settings
from src.core.storage.contracts import ObjectPage, ObjectRead, ObjectStore, PageCursor, StorageError, SyncObjectStore, WorkspaceMirror


def storage_configured(settings: Settings) -> bool:
    return settings.s3_configured


def storage_identity(settings: Settings) -> Hashable:
    """Connection-cache identity; never log this credential-bearing value."""
    return (
        "s3",
        settings.s3_endpoint_url, settings.s3_bucket, settings.s3_access_key,
        settings.s3_secret_key, settings.s3_region, settings.s3_public_endpoint_url,
        settings.s3_multipart_copy_mode,
    )


def object_store(settings: Settings) -> ObjectStore:
    from src.core.storage.s3 import S3ObjectStore

    return S3ObjectStore.from_settings(settings)


async def object_pages(
    store: ObjectStore, prefix: str = "", *, delimiter: str | None = None, limit: int = 1000,
) -> AsyncIterator[ObjectPage]:
    cursor: PageCursor | None = None
    seen: set[PageCursor] = set()
    while True:
        page = await store.list_page(prefix, delimiter=delimiter, cursor=cursor, limit=limit)
        if page.next_cursor is not None and page.next_cursor in seen:
            raise StorageError("backend_error", provider_code="RepeatedPageCursor")
        yield page
        cursor = page.next_cursor
        if cursor is None:
            return
        seen.add(cursor)


@asynccontextmanager
async def optional_read(store: ObjectStore, key: str) -> AsyncIterator[ObjectRead | None]:
    """Only initial confirmed absence is optional; body/consumer errors propagate."""
    async with AsyncExitStack() as stack:
        read: ObjectRead | None
        try:
            read = await stack.enter_async_context(store.open_read(key))
        except StorageError as error:
            if error.kind != "not_found":
                raise
            read = None
        yield read


def sync_object_store(settings: Settings) -> SyncObjectStore:
    from src.core.storage.s3 import SyncS3ObjectStore

    return SyncS3ObjectStore.from_settings(settings)


def workspace_mirror(settings: Settings) -> WorkspaceMirror:
    from src.core.storage.s3_mirror import S3WorkspaceMirror

    return S3WorkspaceMirror(settings)


async def close_shared_storage_clients() -> None:
    """Drain shared pools owned by the configured S3 provider."""
    from src.core.s3_transport import close_shared_s3_client
    await close_shared_s3_client()
