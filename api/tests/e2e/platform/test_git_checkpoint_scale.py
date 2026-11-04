"""Bounded scale probe: real storage, Git pack bytes and many workspace files.

This is a measured sample, not a production size/latency certification. No
platform jobs or builds are needed to measure the checkpoint protocol itself.
"""

import json
import time

import pytest

from src.services.git_repo_manager import GitRepoManager
from src.services.git_workspace_state import GitWorkspaceState

pytestmark = pytest.mark.e2e


async def test_checkpoint_pack_and_many_files_round_trip(backend, tmp_path, monkeypatch):
    settings, client, prefix = backend
    monkeypatch.setattr("src.services.git_workspace_state.STATE_KEY", prefix + "state.json")
    manager = GitRepoManager(settings=settings)
    state = GitWorkspaceState(manager)
    root = tmp_path / "writer"
    pack = root / ".git" / "objects" / "pack" / "fixture.pack"
    pack.parent.mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    manager._write_storage_baseline(root, "a" * 64)
    # Generate without a whole-pack allocation; hash/copy/download also stream.
    chunk = bytes(range(256)) * 4096
    with pack.open("wb") as target:
        for _ in range(64):
            target.write(chunk)
    for number in range(256):
        path = root / "workspace" / f"file-{number:04}.txt"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(f"file {number}\n".encode() * 128)

    async def keys():
        return {
            item["Key"]
            async for page in client.get_paginator("list_objects_v2").paginate(
                Bucket=settings.storage_bucket, Prefix="_workspace_sync_checkpoints/",
            ) for item in page.get("Contents", [])
        }

    before = await keys()
    timings = {}
    try:
        started = time.monotonic()
        await state.persist(root, None, None)
        timings["save_verified_seconds"] = time.monotonic() - started
        current, etag = await state.read()
        assert current is not None
        stored = await keys() - before
        assert len(stored) == 259  # HEAD, baseline marker, pack and workspace files
        started = time.monotonic()
        await state.persist(root, current, etag)
        timings["unchanged_save_seconds"] = time.monotonic() - started
        assert (await state.read())[1] == etag
        assert await keys() - before == stored
        successor = tmp_path / "successor"
        successor.mkdir()
        started = time.monotonic()
        await state.restore(successor, current, etag)
        timings["cold_restore_seconds"] = time.monotonic() - started
        assert await state.fingerprint(successor) == current.fingerprint
        assert (successor / pack.relative_to(root)).stat().st_size == 64 * 1024**2
        assert len(list((successor / "workspace").iterdir())) == 256
        started = time.monotonic()
        await state.restore(successor, current, etag)
        timings["warm_restore_seconds"] = time.monotonic() - started
        print(json.dumps({"provider": settings.object_storage_provider, "pack_mib": 64,
                          "workspace_files": 256, **timings}, sort_keys=True))
    finally:
        for key in await keys() - before:
            await client.delete_object(Bucket=settings.storage_bucket, Key=key)

