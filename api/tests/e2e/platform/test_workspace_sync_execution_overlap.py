"""Characterize workspace publication across running and queued executions.

Cost: one baseline Fetch, one real Sync and two workflow executions. Unlike
local Git service tests (which stub storage publication), this crosses the HTTP,
durable queue, worker importer, Redis and S3 boundaries. Handler admission is
held only in this disposable test DB so B is provably queued before publication.
This documents live-update semantics, not atomic release isolation.
"""

from __future__ import annotations

import json
import shutil
import uuid
from functools import partial
from pathlib import Path

import pytest
from git import Repo
from sqlalchemy import delete, select, update

from bifrost.manifest import read_manifest_from_dir
from src.config import get_settings
from src.models.orm.config import Config
from src.models.orm.execution_workers import ExecutionWorker
from src.models.orm.integrations import Integration
from src.models.orm.work_dispatches import WorkDispatch
from src.services.git_repo_manager import GitRepoManager
from src.services.github_config import (
    delete_github_config,
    get_github_config,
    save_github_config,
)
from src.services.github_sync import GitHubSyncService
from src.services.repo_storage import RepoStorage
from tests.e2e.conftest import poll_until, write_and_register
from tests.e2e.platform.thin_live_helpers import wait_for_job

pytestmark = pytest.mark.e2e


def _empty_directory(path: Path) -> None:
    for entry in path.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


@pytest.fixture
def isolated_git_checkouts(tmp_path):
    """Start both physical executors fresh without merging their local caches."""
    root = Path("/tmp/bifrost/solution-repo-fixtures/git-checkouts")
    checkouts = [root / name for name in ("worker", "scheduler")]
    for checkout in checkouts:
        assert checkout.is_dir(), f"missing test-harness Git mount: {checkout}"
        shutil.copytree(checkout, tmp_path / checkout.name, symlinks=True)
    try:
        for checkout in checkouts:
            _empty_directory(checkout)
        yield
    finally:
        for checkout in checkouts:
            _empty_directory(checkout)
            shutil.copytree(
                tmp_path / checkout.name, checkout, dirs_exist_ok=True, symlinks=True,
            )


def _source(function: str, eager: str, lazy: str, version: str) -> str:
    return f'''import asyncio
import time
from bifrost import tables, workflow
from {eager} import VERSION as EAGER_VERSION

@workflow(name="{function}")
async def {function}(barrier_table: str, hold: bool = False):
    if hold:
        await tables.upsert(barrier_table, "entered", {{"ready": True}}, scope="global")
        deadline = time.monotonic() + 90
        while True:
            released = await tables.get(barrier_table, "release", scope="global")
            if released and released.data.get("ready"):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("test did not release publication barrier")
            await asyncio.sleep(0.1)
    from {lazy} import VERSION as lazy_version
    return {{"entry": "{version}", "eager": EAGER_VERSION, "lazy": lazy_version}}
'''


def _execution(client, headers, execution_id: str, *, terminal: bool = True):
    response = client.get(f"/api/executions/{execution_id}", headers=headers)
    assert response.status_code == 200, response.text
    execution = response.json()
    if not terminal or execution["status"] in {"Success", "Failed", "Cancelled", "Timeout"}:
        return execution
    return None


async def test_sync_updates_queued_code_without_replacing_running_imports(
    e2e_client, platform_admin, async_session_factory, isolated_git_checkouts,
):
    """A keeps loaded v1 code, lazily imports v2; queued B starts entirely on v2."""
    headers = platform_admin.headers
    suffix = uuid.uuid4().hex[:8]
    function = f"overlap_{suffix}"
    eager, lazy = f"overlap_eager_{suffix}", f"overlap_lazy_{suffix}"
    paths = [f"{function}.py", f"{eager}.py", f"{lazy}.py"]
    owner = f"thin-overlap-{suffix}"
    root = Path("/tmp/bifrost/solution-repo-fixtures/thin-github") / owner
    root.mkdir(parents=True)
    remote = Repo.init(root / "workspace.git", bare=True)
    remote.git.symbolic_ref("HEAD", "refs/heads/main")
    seed = root / "seed"
    storage = RepoStorage(get_settings())
    manager = GitRepoManager(get_settings())
    original_files = {path: await storage.read(path) for path in await storage.list("")}
    async with async_session_factory() as db:
        original_config = await get_github_config(db, None)
    table_id = None
    hidden_config_id = None
    hidden_integration_id = None
    executions: list[str] = []
    paused: dict[uuid.UUID, list[str]] = {}
    try:
        table = e2e_client.post(
            "/api/tables?scope=global", headers=headers,
            json={"name": f"overlap_barrier_{suffix}"},
        )
        assert table.status_code == 201, table.text
        table_id = table.json()["id"]
        for path in paths[1:]:
            written = e2e_client.put(
                "/api/files/editor/content", headers=headers,
                json={"path": path, "content": 'VERSION = "v1"\n', "encoding": "utf-8"},
            )
            assert written.status_code in {200, 201}, written.text
        workflow = write_and_register(
            e2e_client, headers, paths[0], _source(function, eager, lazy, "v1"),
            function, organization_id=None,
        )
        # The workspace manifest intentionally omits configs owned by a
        # non-exportable Integration. This mirrors an earlier test's retained
        # state and proves a code-only Sync never treats that omission as a
        # user-requested config deletion.
        async with async_session_factory() as db:
            hidden = Integration(name=f"overlap-hidden-{suffix}", is_deleted=True)
            db.add(hidden)
            await db.flush()
            hidden_config = Config(
                key="overlap_hidden",
                value={"value": "hidden"},
                integration_id=hidden.id,
                updated_by="overlap-test",
            )
            db.add(hidden_config)
            await db.flush()
            hidden_config_id = hidden_config.id
            hidden_integration_id = hidden.id
            await db.commit()
        assert hidden_config_id is not None

        # Seed the already-published baseline without paying for a setup Sync.
        await manager.sync_down(seed)
        if (seed / ".git").exists():
            shutil.rmtree(seed / ".git")
        async with async_session_factory() as db:
            await GitHubSyncService._regenerate_manifest_to_dir(db, seed)
        assert str(hidden_config_id) not in {
            config.id for config in read_manifest_from_dir(seed / ".bifrost").configs.values()
        }
        repo = Repo.init(seed, initial_branch="main")
        with repo.config_writer() as config:
            config.set_value("user", "name", "Overlap Test")
            config.set_value("user", "email", "overlap@example.test")
        repo.index.add([
            str(path.relative_to(seed)) for path in seed.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(seed).parts
        ])
        repo.index.commit("published v1 baseline")
        repo.create_remote("origin", f"file://{remote.working_dir}")
        repo.git.push("-u", "origin", "main")
        await manager.sync_up(seed)
        async with async_session_factory() as db:
            await save_github_config(
                db, None, "local-overlap-fixture-token", f"{owner}/workspace", "main", "overlap-test",
            )
        fetched = e2e_client.post("/api/github/fetch", headers=headers)
        assert fetched.status_code == 202, fetched.text
        baseline = wait_for_job(e2e_client, headers, fetched.json()["job_id"], "workspace.git")
        assert baseline["result"]["success"] is True, baseline
        assert baseline["result"]["changed_files"] == [], baseline

        def enqueue(hold: bool) -> str:
            response = e2e_client.post(
                "/api/workflows/execute", headers=headers,
                json={"workflow_id": workflow["id"], "sync": False,
                      "input_data": {"barrier_table": table_id, "hold": hold}},
            )
            assert response.status_code in {200, 202}, response.text
            execution_id = response.json()["execution_id"]
            executions.append(execution_id)
            return execution_id

        running_id = enqueue(True)

        def entered():
            response = e2e_client.get(
                f"/api/tables/{table_id}/documents/entered?scope=global", headers=headers,
            )
            if response.status_code == 404:
                current = _execution(e2e_client, headers, running_id, terminal=False)
                assert current["status"] not in {"Success", "Failed", "Cancelled", "Timeout"}, current
                return None
            assert response.status_code == 200, response.text
            return response.json()["data"].get("ready")

        assert poll_until(entered, max_wait=30), "A did not reach the loaded-v1 barrier"
        # Only the canonical workflow claim is gated; running A and Git jobs continue.
        async with async_session_factory() as db:
            workers = (await db.execute(select(ExecutionWorker).where(
                ExecutionWorker.state == "ready",
                ExecutionWorker.supported_handlers.op("?")("workflow.execute"),
            ).with_for_update())).scalars().all()
            assert workers, "no ready workflow executor"
            for worker in workers:
                paused[worker.id] = list(worker.supported_handlers)
                worker.supported_handlers = [
                    handler for handler in worker.supported_handlers
                    if handler != "workflow.execute"
                ]
            await db.commit()
        queued_id = enqueue(False)
        async with async_session_factory() as db:
            assert await db.scalar(select(WorkDispatch.id).where(
                WorkDispatch.subject_id == uuid.UUID(queued_id),
            )), "B must have a real, unclaimed durable dispatch"
        assert _execution(e2e_client, headers, queued_id, terminal=False)["status"] == "Pending"

        (seed / paths[0]).write_text(_source(function, eager, lazy, "v2"))
        for path in paths[1:]:
            (seed / path).write_text('VERSION = "v2"\n')
        repo.index.add(paths)
        deployed_sha = repo.index.commit("publish v2 while A runs and B waits").hexsha
        repo.git.push("origin", "main")
        synced = e2e_client.post(
            "/api/github/sync", headers=headers, json={"confirm_deletes": False},
        )
        assert synced.status_code == 202, synced.text
        publication = wait_for_job(e2e_client, headers, synced.json()["job_id"], "workspace.git")
        assert publication["result"]["success"] is True, publication
        async with async_session_factory() as db:
            assert await db.get(Config, hidden_config_id) is not None
        # A pull-only Sync legitimately has no pushed commit SHA. Verify the
        # published workspace bytes, not that optional push receipt.
        for path in paths:
            assert await storage.read(path) == (seed / path).read_bytes(), path
        assert _execution(e2e_client, headers, running_id, terminal=False)["status"] == "Running"
        assert _execution(e2e_client, headers, queued_id, terminal=False)["status"] == "Pending"

        release = e2e_client.post(
            f"/api/tables/{table_id}/documents/upsert?scope=global", headers=headers,
            json={"id": "release", "data": {"ready": True}},
        )
        assert release.status_code in {200, 201}, release.text
        async with async_session_factory() as db:
            for worker_id, handlers in paused.items():
                await db.execute(
                    update(ExecutionWorker).where(ExecutionWorker.id == worker_id)
                    .values(supported_handlers=handlers)
                )
            await db.commit()
        paused.clear()
        results = []
        for execution_id in executions:
            result = poll_until(lambda: _execution(e2e_client, headers, execution_id), max_wait=30)
            assert result is not None, f"execution {execution_id} did not finish"
            assert result["status"] == "Success", result
            results.append(result["result"])
        assert results == [
            {"entry": "v1", "eager": "v1", "lazy": "v2"},
            {"entry": "v2", "eager": "v2", "lazy": "v2"},
        ]
        print("WORKSPACE_SYNC_OVERLAP " + json.dumps({
            "commit": deployed_sha, "running": results[0], "queued": results[1],
        }, sort_keys=True))
    finally:
        async with async_session_factory() as db:
            for worker_id, handlers in paused.items():
                await db.execute(
                    update(ExecutionWorker).where(ExecutionWorker.id == worker_id)
                    .values(supported_handlers=handlers)
                )
            await db.commit()
        for execution_id in executions:
            current = _execution(e2e_client, headers, execution_id, terminal=False)
            if current["status"] in {"Pending", "Running"}:
                cancelled = e2e_client.post(f"/api/executions/{execution_id}/cancel", headers=headers)
                assert cancelled.status_code in {200, 202}, cancelled.text
                assert poll_until(
                    partial(_execution, e2e_client, headers, execution_id), max_wait=30,
                ), f"execution {execution_id} did not stop during cleanup"
        for path in paths:
            deleted = e2e_client.delete("/api/files/editor", params={"path": path}, headers=headers)
            assert deleted.status_code in {200, 204, 404}, deleted.text
        if table_id:
            deleted = e2e_client.delete(f"/api/tables/{table_id}?scope=global", headers=headers)
            assert deleted.status_code in {200, 204}, deleted.text
        async with async_session_factory() as db:
            if original_config:
                await save_github_config(db, None, original_config.token, original_config.repo_url, original_config.branch, "overlap-test-restore")
            else:
                await delete_github_config(db, None)
            if hidden_integration_id:
                await db.execute(
                    delete(Integration).where(Integration.id == hidden_integration_id)
                )
                await db.commit()
        for path in await storage.list(""):
            await storage.delete(path)
        for path, content in original_files.items():
            await storage.write(path, content)
        shutil.rmtree(root)
