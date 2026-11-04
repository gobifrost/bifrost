"""Kill a real checkpoint writer; recover through another process's checkout.

No platform jobs/builds: these cases isolate the object-storage publication
boundary. The existing thin-worker journey covers job dispatch and remote push.
"""

import asyncio
import json
import shutil
import subprocess
import sys

import pytest

from src.services.git_repo_manager import GitRepoManager
from src.services.git_workspace_state import GitWorkspaceState, GitWorkspaceStateError

pytestmark = pytest.mark.e2e


WRITER = """
import asyncio, json, os, sys
from pathlib import Path
from src.services.git_repo_manager import GitRepoManager
from src.services.git_workspace_state import GitWorkspaceState
from tests.storage_contract import local_settings
import src.services.git_workspace_state as module

provider, pointer_key, root, phase = json.loads(sys.stdin.read())
module.STATE_KEY = pointer_key
manager = GitRepoManager(settings=local_settings(provider))
state = GitWorkspaceState(manager)

async def main():
    previous, etag = await state.read()
    if phase == 'install':
        def crash(staging, target):
            (target / 'file.txt').write_text('interrupted install')
            os._exit(73)
        state._install = crash
        await state.restore(Path(root), previous, etag)
        raise AssertionError('install seam was not reached')
    (Path(root) / 'file.txt').write_text('second')
    if phase == 'checkpoint':
        create = manager.checkpoint_workspace
        async def crash(source):
            await create(source)
            os._exit(73)
        manager.checkpoint_workspace = crash
    elif phase == 'pointer':
        state.record = lambda *args: os._exit(73)
    elif phase == 'receipt':
        async def crash(checkpoint):
            os._exit(73)
        manager.delete_workspace_checkpoint = crash
    await state.persist(Path(root), previous, etag)

asyncio.run(main())
raise AssertionError('crash seam was not reached')
"""


@pytest.mark.parametrize("phase", ["checkpoint", "pointer", "receipt", "install"])
async def test_process_death_recovers_only_the_acknowledged_pointer(backend, tmp_path, monkeypatch, phase):
    settings, client, prefix = backend
    key = prefix + "local-state.json"
    monkeypatch.setattr("src.services.git_workspace_state.STATE_KEY", key)
    manager = GitRepoManager(settings=settings)
    state = GitWorkspaceState(manager)
    root = tmp_path / "writer"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    manager._write_storage_baseline(root, "a" * 64)
    (root / "file.txt").write_text("first")

    async def keys():
        return {
            item["Key"]
            async for page in client.get_paginator("list_objects_v2").paginate(
                Bucket=settings.storage_bucket, Prefix="_workspace_sync_checkpoints/",
            ) for item in page.get("Contents", [])
        }

    before = await keys()
    try:
        await state.persist(root, None, None)
        previous, old_etag = await state.read()
        assert previous is not None
        if phase == "install":
            stale = tmp_path / "stale-reader"
            shutil.copytree(root, stale)
            state.record(stale, old_etag, await state.fingerprint(stale))
            (root / "file.txt").write_text("second")
            await state.persist(root, previous, old_etag)
            root = stale
        result = await asyncio.to_thread(
            subprocess.run, [sys.executable, "-c", WRITER],
            input=json.dumps([settings.object_storage_provider, key, str(root), phase]),
            text=True, capture_output=True, check=False,
        )
        assert result.returncode == 73, result.stderr
        current, etag = await state.read()
        assert current is not None
        expected = "first" if phase == "checkpoint" else "second"
        successor = tmp_path / "cold-successor"
        successor.mkdir()
        await state.restore(successor, current, etag)
        assert (successor / "file.txt").read_text() == expected
        assert (successor / ".git" / "bifrost-storage-baseline").read_text() == "a" * 64
        if phase == "checkpoint":
            assert etag == old_etag
        else:
            assert etag != old_etag
        if phase in {"pointer", "install"}:
            with pytest.raises(GitWorkspaceStateError, match="unsaved local work"):
                await state.restore(root, current, etag)
        # A killed writer cannot reclaim either immutable version. Account for
        # both rather than silently treating retained storage as cleaned up.
        snapshots = {item.split("/")[1] for item in await keys() - before}
        assert len(snapshots) == (1 if phase == "install" else 2)
        assert current.checkpoint in snapshots
    finally:
        for stored_key in await keys() - before:
            await client.delete_object(Bucket=settings.storage_bucket, Key=stored_key)

