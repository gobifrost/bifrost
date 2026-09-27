"""Workspace bundle staging stays stream-based and integrity checked."""
from __future__ import annotations

import hashlib
import resource
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest


class _Storage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_object_from_chunks(self, key, chunks, *, content_type):
        data = b"".join([chunk async for chunk in chunks])
        self.objects[key] = data
        return hashlib.sha256(data).hexdigest(), len(data)

    async def iter_object_chunks(self, key, *, chunk_size):
        data = self.objects[key]
        for start in range(0, len(data), chunk_size):
            yield data[start:start + chunk_size]


class _HashingStorage:
    """S3 stand-in that consumes each staged chunk without retaining it."""

    def __init__(self) -> None:
        self.largest_chunk = 0
        self.digest = ""
        self.size = 0

    async def put_object_from_chunks(self, _key, chunks, *, content_type):
        hasher = hashlib.sha256()
        async for chunk in chunks:
            self.largest_chunk = max(self.largest_chunk, len(chunk))
            self.size += len(chunk)
            hasher.update(chunk)
        self.digest = hasher.hexdigest()
        return self.digest, self.size


@pytest.mark.asyncio
async def test_staged_bundle_round_trips_by_hash(tmp_path) -> None:
    from src.services.solutions.workspace_bundle_storage import WorkspaceBundleStorage

    source = tmp_path / "bundle.zip"
    source.write_bytes(b"bundle")
    storage = WorkspaceBundleStorage(uuid4())
    storage._storage = _Storage()

    sha256, size = await storage.stage_package(source)
    destination = tmp_path / "copy.zip"

    copied = await storage.copy_package_to(destination, expected_sha256=sha256)

    assert size == copied == len(b"bundle")
    assert destination.read_bytes() == b"bundle"


@pytest.mark.asyncio
async def test_bad_staged_bundle_is_removed_after_integrity_failure(tmp_path) -> None:
    from src.services.solutions.workspace_bundle_storage import (
        WorkspaceBundleIntegrityError,
        WorkspaceBundleStorage,
    )

    source = tmp_path / "bundle.zip"
    source.write_bytes(b"bundle")
    storage = WorkspaceBundleStorage(uuid4())
    storage._storage = _Storage()
    await storage.stage_package(source)
    destination = tmp_path / "copy.zip"

    with pytest.raises(WorkspaceBundleIntegrityError):
        await storage.copy_package_to(destination, expected_sha256="0" * 64)

    assert not destination.exists()


@pytest.mark.asyncio
async def test_large_bundle_staging_keeps_rss_and_chunk_size_bounded(tmp_path) -> None:
    """A 40 MiB upload is read as 8 MiB chunks, never as one archive buffer."""
    from src.services.solutions.workspace_bundle_storage import CHUNK_SIZE, WorkspaceBundleStorage

    source = tmp_path / "large.zip"
    with source.open("wb") as handle:
        handle.truncate(40 * 1024 * 1024)
    storage = WorkspaceBundleStorage(uuid4())
    sink = _HashingStorage()
    storage._storage = sink
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    digest, size = await storage.stage_package(source)

    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert size == 40 * 1024 * 1024
    assert digest == sink.digest
    assert sink.largest_chunk == CHUNK_SIZE
    # Linux reports KiB. A whole-archive read would add roughly 40 MiB here.
    assert after - before < 24 * 1024


@pytest.mark.asyncio
async def test_expired_preview_with_active_job_is_retained_for_retry(monkeypatch) -> None:
    """TTL cleanup must not delete the archive a queued retry still needs."""
    from src.services.solutions import workspace_bundle_storage as storage_module

    preview_id = str(uuid4())
    removed: list[str] = []

    class Client:
        async def list_objects_v2(self, **_kwargs):
            return {"Contents": [{"Key": f"_workspace_bundle_imports/{preview_id}/preview.json"}]}

    class RootStorage:
        def __init__(self, _settings):
            pass

        @asynccontextmanager
        async def get_client(self):
            yield Client()

    class Preview:
        def __init__(self, _preview_id, _settings):
            self.preview_id = _preview_id

        async def load_metadata(self):
            return {
                "platform_job_id": str(uuid4()),
                "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            }

        async def delete(self):
            removed.append(self.preview_id)

    class Db:
        async def get(self, _model, _id):
            return type("Job", (), {"status": "queued"})()

    @asynccontextmanager
    async def db_context():
        yield Db()

    class Settings:
        s3_bucket = "test"

    monkeypatch.setattr(storage_module, "get_settings", Settings)
    monkeypatch.setattr(storage_module, "S3StorageClient", RootStorage)
    monkeypatch.setattr(storage_module, "WorkspaceBundleStorage", Preview)
    monkeypatch.setattr("src.core.database.get_db_context", db_context)

    count = await storage_module.cleanup_expired_workspace_bundle_previews()

    assert count == 0
    assert removed == []
