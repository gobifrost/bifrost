"""Workspace bundle staging stays stream-based and integrity checked."""
from __future__ import annotations

import hashlib
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
