"""Copy every object from the S3 store to the Azure Blob store, once, offline.

Stop-the-world operator tool for a first S3 to Blob cutover. The source
bucket is never modified, so flipping the provider back is the rollback.
Reads go through the S3 store and writes through the Azure store, so the
Azure metadata envelope and fresh concurrency versions are applied by the
same code that serves traffic. A raw CLI copy cannot do either.

Requires BIFROST_MIGRATE_OBJECT_STORAGE_ACK=1 plus S3 settings for the
source and BIFROST_AZURE_BLOB_* settings for the destination in the
environment. The destination container must already exist and be empty
unless --allow-nonempty-dest is passed for a resumed run.
"""

import argparse
import asyncio
import hashlib
import os
import sys

from src.config import Settings
from src.core.storage import object_pages
from src.core.storage.azure import AzureObjectStore
from src.core.storage.contracts import (
    ContentProperties, ObjectStore, StorageError, WriteOptions,
)
from src.core.storage.s3 import S3ObjectStore


def _source_settings() -> Settings:
    return Settings(_env_file=None, object_storage_provider="s3")


def _dest_settings() -> Settings:
    return Settings(_env_file=None, object_storage_provider="azure_blob")


async def _copy_key(
    source: ObjectStore, dest: ObjectStore, key: str, *, verify_digests: bool,
) -> tuple[str, str | None]:
    """Copy one key; return (outcome, detail). Outcomes: copied, skipped, expired, failed."""
    try:
        info = await source.stat(key)
    except StorageError as error:
        return "failed", f"stat source: {error.kind}/{error.provider_code}"
    try:
        current = await dest.stat(key)
    except StorageError as error:
        if error.kind != "not_found":
            return "failed", f"stat dest: {error.kind}/{error.provider_code}"
        current = None
    matches = current is not None and (
        current.size == info.size
        and current.metadata == info.metadata
        and current.properties == info.properties
    )
    if matches and not verify_digests:
        return "skipped", None
    try:
        async with source.open_read(key, expected_version=info.version) as read:
            data = await read.body.read()
    except StorageError as error:
        return "failed", f"read source: {error.kind}/{error.provider_code}"
    if matches:
        assert current is not None
        try:
            async with dest.open_read(key, expected_version=current.version) as read:
                existing = await read.body.read()
        except StorageError as error:
            return "failed", f"verify existing: {error.kind}/{error.provider_code}"
        if hashlib.sha256(existing).digest() == hashlib.sha256(data).digest():
            return "skipped", None
    properties = info.properties
    expired = False
    if properties.http_expires is not None:
        # Azure has no native Expires property. The copy keeps going without
        # it and reports the key; re-apply retention at the destination.
        properties = ContentProperties(
            content_type=properties.content_type,
            content_disposition=properties.content_disposition,
            content_encoding=properties.content_encoding,
            content_language=properties.content_language,
            cache_control=properties.cache_control,
            http_expires=None,
        )
        expired = True
    try:
        await dest.write(
            key, data,
            options=WriteOptions(properties=properties, metadata=dict(info.metadata)),
        )
    except StorageError as error:
        return "failed", f"write dest: {error.kind}/{error.provider_code}"
    try:
        written = await dest.stat(key)
    except StorageError as error:
        return "failed", f"verify stat: {error.kind}/{error.provider_code}"
    if (
        written.size != len(data)
        or written.metadata != info.metadata
        or written.properties != properties
    ):
        return "failed", "verify mismatch after write"
    if verify_digests:
        try:
            async with dest.open_read(key, expected_version=written.version) as read:
                if hashlib.sha256(await read.body.read()).hexdigest() != hashlib.sha256(data).hexdigest():
                    return "failed", "digest mismatch after write"
        except StorageError as error:
            return "failed", f"verify read: {error.kind}/{error.provider_code}"
    return ("expired", key) if expired else ("copied", None)


async def migrate(*, prefix: str, dry_run: bool, verify_digests: bool, allow_nonempty: bool) -> int:
    source_settings, dest_settings = _source_settings(), _dest_settings()
    if not source_settings.s3_configured:
        print("Source S3 storage is not configured", file=sys.stderr)
        return 2
    if not dest_settings.azure_blob_configured:
        print("Destination Azure Blob storage is not configured", file=sys.stderr)
        return 2
    async with S3ObjectStore.from_settings(source_settings) as source:
        async with AzureObjectStore.from_settings(dest_settings) as dest:
            first = [item async for page in object_pages(dest, "") for item in page.objects[:1]]
            if first and not allow_nonempty:
                print("Destination container is not empty; pass --allow-nonempty-dest to resume", file=sys.stderr)
                return 2
            keys = [item.key async for page in object_pages(source, prefix) for item in page.objects]
            print(f"{len(keys)} keys under {prefix or '/'}")
            if dry_run:
                for key in keys:
                    print(f"would copy {key}")
                return 0
            outcomes: dict[str, int] = {"copied": 0, "skipped": 0, "expired": 0, "failed": 0}
            expired_keys: list[str] = []
            failures: list[str] = []
            for key in keys:
                outcome, detail = await _copy_key(
                    source, dest, key, verify_digests=verify_digests,
                )
                outcomes[outcome] += 1
                if outcome == "expired":
                    expired_keys.append(detail or key)
                elif outcome == "failed":
                    failures.append(f"{key}: {detail}")
                done = outcomes["copied"] + outcomes["failed"]
                if done and done % 500 == 0:
                    print(f"... {outcomes['copied']} copied, {outcomes['skipped']} skipped, {outcomes['failed']} failed")
    print(f"done: {outcomes['copied']} copied, {outcomes['skipped']} skipped, "
          f"{outcomes['expired']} without Expires, {outcomes['failed']} failed")
    for key in expired_keys:
        print(f"expires dropped: {key}")
    for failure in failures:
        print(f"FAILED {failure}", file=sys.stderr)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default="", help="Only migrate keys under this prefix")
    parser.add_argument("--dry-run", action="store_true", help="List keys without copying")
    parser.add_argument("--verify-digests", action="store_true", help="Read back every object and compare SHA-256")
    parser.add_argument("--allow-nonempty-dest", action="store_true", help="Resume into a non-empty container")
    args = parser.parse_args()
    if os.environ.get("BIFROST_MIGRATE_OBJECT_STORAGE_ACK") != "1":
        print("Refusing to copy without BIFROST_MIGRATE_OBJECT_STORAGE_ACK=1", file=sys.stderr)
        return 2
    return asyncio.run(migrate(
        prefix=args.prefix, dry_run=args.dry_run,
        verify_digests=args.verify_digests, allow_nonempty=args.allow_nonempty_dest,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
