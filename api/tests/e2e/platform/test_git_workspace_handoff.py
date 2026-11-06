"""Force Git jobs onto separate local checkouts sharing real object storage."""

from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from git import Repo

from src.services.github_sync import GitHubSyncService
from src.services.repo_storage import RepoStorage
from src.services.git_repo_manager import GitRepoManager
from src.models.contracts.github import GitConnectRequest

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def neighboring_workspace(async_session_factory):
    """Reproduce prior tests leaving committed entities without source files."""
    from src.models.orm.applications import Application
    from src.models.orm.workflows import Workflow

    suffix = uuid4().hex
    workflow = Workflow(name=f"neighbor_{suffix}", function_name="neighbor", path=f"neighbor_{suffix}.py")
    app = Application(name=f"neighbor-{suffix}", slug=f"neighbor-{suffix}", repo_path=f"apps/neighbor-{suffix}")
    async with async_session_factory() as session:
        session.add_all([workflow, app])
        await session.commit()
        ids = workflow.id, app.id
    try:
        yield
    finally:
        async with async_session_factory() as session:
            restored_workflow = await session.get(Workflow, ids[0])
            restored_app = await session.get(Application, ids[1])
            try:
                assert restored_workflow is not None
                assert restored_app is not None
            finally:
                if restored_workflow is not None:
                    await session.delete(restored_workflow)
                if restored_app is not None:
                    await session.delete(restored_app)
                await session.commit()


@pytest_asyncio.fixture
async def db_session(async_engine, neighboring_workspace):
    """Keep synthetic repositories independent of neighboring committed rows.

    Service commits release savepoints only. The outer rollback restores the
    existing workspace and every row removed by foreign-key cascades.
    """
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.models.orm.applications import Application
    from src.models.orm.services import ServiceDefinition
    from src.models.orm.workflows import Workflow

    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint",
        ) as session:
            try:
                await session.execute(delete(ServiceDefinition))
                await session.execute(delete(Workflow).where(Workflow.solution_id.is_(None)))
                await session.execute(delete(Application).where(Application.solution_id.is_(None)))
                await session.commit()
                yield session
            finally:
                await session.rollback()
                await transaction.rollback()


@pytest_asyncio.fixture
async def backend():
    from src.config import get_settings
    from src.core.s3_transport import S3Transport

    settings = get_settings()
    prefix = f"_contract/{uuid4().hex}/"
    async with S3Transport(
        endpoint_url=settings.s3_endpoint_url,
        access_key_id=settings.s3_access_key,
        secret_access_key=settings.s3_secret_key,
        region=settings.s3_region,
    ) as client:
        yield settings, client, prefix
        async for page in client.get_paginator("list_objects_v2").paginate(
            Bucket=settings.s3_bucket, Prefix=prefix,
        ):
            for item in page.get("Contents", []):
                await client.delete_object(Bucket=settings.s3_bucket, Key=item["Key"])


@pytest_asyncio.fixture
async def workspace_backend(backend, monkeypatch):
    settings, client, prefix = backend
    monkeypatch.setattr("src.services.git_workspace_state.STATE_KEY", prefix + "git-state.json")
    bucket = settings.s3_bucket

    async def checkpoint_keys():
        return {
            item["Key"]
            async for page in client.get_paginator("list_objects_v2").paginate(
                Bucket=bucket, Prefix="_workspace_sync_checkpoints/",
            )
            for item in page.get("Contents", [])
        }

    before = await checkpoint_keys()
    storage = RepoStorage(settings)
    saved = {path: await storage.read(path) for path in await storage.list("")}
    for path in saved:
        await storage.delete(path)
    try:
        yield settings, storage
    finally:
        for path in await storage.list(""):
            await storage.delete(path)
        for path, content in saved.items():
            await storage.write(path, content)
        for key in await checkpoint_keys() - before:
            await client.delete_object(Bucket=bucket, Key=key)


@pytest.mark.parametrize("warm_executor", [False, True], ids=["cold", "stale"])
async def test_commit_is_published_by_another_executor(
    db_session, workspace_backend, tmp_path: Path, monkeypatch, warm_executor,
):
    settings, storage = workspace_backend
    bare = tmp_path / "remote.git"
    Repo.init(bare, bare=True)
    (bare / "HEAD").write_text("ref: refs/heads/main\n")
    seed = tmp_path / "seed"
    seed.mkdir()
    remote = Repo.init(seed, initial_branch="main")
    (seed / "seed.txt").write_text("existing remote commit\n")
    remote.index.add(["seed.txt"])
    remote.index.commit("remote baseline")
    remote.create_remote("origin", str(bare)).push("main")
    first = tmp_path / "executor-a"
    second = tmp_path / "executor-b"
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", first)
    service = GitHubSyncService(
        db=db_session, repo_url=f"file://{bare}", branch="main", settings=settings,
    )
    await storage.write("handoff.txt", b"durable local commit\n")
    fetched = await service.desktop_fetch()
    assert fetched.success, fetched.error
    if warm_executor:
        await service.repo_manager.sync_down(second)
    committed = await service.desktop_commit("commit before executor handoff")
    assert committed.success, committed.error
    assert committed.commit_sha
    if warm_executor:
        # Force regenerated manifest bytes to differ without adding an entity
        # absent from the pending commit (which correctly needs delete review).
        from src.services.git_workspace_state import GitWorkspaceState, tree_fingerprint

        pending_fingerprint = tree_fingerprint(first)
        receipt_path = GitWorkspaceState._receipt_path(first)
        receipt = receipt_path.read_bytes()
        regenerate = service._regenerate_manifest_to_dir
        manifest_paths = []

        async def regenerate_with_comment(db, work_dir):
            await regenerate(db, work_dir)
            manifest = next((work_dir / ".bifrost").glob("*.yaml"))
            manifest.write_text(manifest.read_text() + "\n# Reimport regression\n")
            manifest_paths.append(manifest.relative_to(work_dir).as_posix())

        monkeypatch.setattr(service, "_regenerate_manifest_to_dir", regenerate_with_comment)
        await service.reimport_from_repo()
        assert b"# Reimport regression" in await storage.read(manifest_paths[0])
        assert tree_fingerprint(first) == pending_fingerprint
        assert receipt_path.read_bytes() == receipt
        assert (await service.desktop_status()).commits_ahead == 1
    preflight = await service.preflight()
    assert preflight.valid, preflight.issues

    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", second)
    successor = GitHubSyncService(
        db=db_session, repo_url=f"file://{bare}", branch="main", settings=settings,
    )
    status = await successor.desktop_status()
    assert status.commits_ahead == 1
    published = await successor.desktop_sync()
    assert published.success, published.error
    assert published.commit_sha == committed.commit_sha
    assert Repo(bare).commit("main").hexsha == committed.commit_sha
    assert Repo(bare).git.show("main:handoff.txt") == "durable local commit"


async def test_handoff_retains_the_original_editor_write_baseline(
    db_session, workspace_backend, tmp_path, monkeypatch,
):
    settings, storage = workspace_backend
    bare = tmp_path / "remote.git"
    Repo.init(bare, bare=True)
    (bare / "HEAD").write_text("ref: refs/heads/main\n")
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", tmp_path / "first")
    service = GitHubSyncService(db=db_session, repo_url=f"file://{bare}", branch="main", settings=settings)
    await storage.write("handoff.txt", b"before\n")
    assert (await service.desktop_fetch()).success
    assert (await service.desktop_commit("local commit")).success
    await storage.write("handoff.txt", b"edited after the commit\n")
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", tmp_path / "successor")
    result = await service.desktop_sync()
    assert not result.success
    assert "Run Fetch" in (result.error or "")
    assert await storage.read("handoff.txt") == b"edited after the commit\n"
    assert not Repo(bare).heads


async def test_readonly_requests_preserve_same_executor_pending_state(
    db_session, workspace_backend, tmp_path, monkeypatch,
):
    settings, storage = workspace_backend
    bare = tmp_path / "remote.git"
    Repo.init(bare, bare=True)
    (bare / "HEAD").write_text("ref: refs/heads/main\n")
    checkout = tmp_path / "executor"
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", checkout)
    service = GitHubSyncService(db=db_session, repo_url=f"file://{bare}", branch="main", settings=settings)
    await storage.write("draft.txt", b"published bytes\n")
    assert (await service.desktop_fetch()).success
    committed = await service.desktop_commit("pending local commit")
    assert committed.success, committed.error
    # A failed/cancelled local operation may leave unacknowledged bytes. Neither
    # preflight nor connection preview is permission to discard that work.
    (checkout / "draft.txt").write_text("unacknowledged local work\n")
    from src.services.git_workspace_state import GitWorkspaceState

    state = GitWorkspaceState(service.repo_manager)
    receipt = state._receipt_path(checkout).read_bytes()
    assert (await service.preflight()).valid
    assert (checkout / "draft.txt").read_text() == "unacknowledged local work\n"
    assert Repo(checkout).head.commit.hexsha == committed.commit_sha
    await service.preview_connect(
        "owner/preview", "main", requested_by_user_id=str(uuid4()), organization_id=None,
    )
    assert (checkout / "draft.txt").read_text() == "unacknowledged local work\n"
    assert Repo(checkout).head.commit.hexsha == committed.commit_sha
    assert state._receipt_path(checkout).read_bytes() == receipt
    assert await storage.read("draft.txt") == b"published bytes\n"


@pytest.mark.parametrize("operation", ["resolve", "abort"])
async def test_merge_state_survives_executor_handoffs(
    db_session, workspace_backend, tmp_path, monkeypatch, operation,
):
    settings, storage = workspace_backend
    bare = tmp_path / "remote.git"
    Repo.init(bare, bare=True)
    (bare / "HEAD").write_text("ref: refs/heads/main\n")
    first, second = tmp_path / "first", tmp_path / "second"
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", first)
    service = GitHubSyncService(db=db_session, repo_url=f"file://{bare}", branch="main", settings=settings)
    await storage.write("handoff.txt", b"base\n")
    assert (await service.desktop_fetch()).success
    assert (await service.desktop_commit("base")).success
    assert (await service.desktop_sync()).success
    other = Repo.clone_from(str(bare), tmp_path / "remote-editor", branch="main")
    (Path(other.working_dir) / "handoff.txt").write_text("theirs\n")
    other.git.add(A=True)
    other.index.commit("remote edit")
    other.git.push("origin", "main")
    await storage.write("handoff.txt", b"ours\n")
    assert (await service.desktop_fetch()).success
    local = await service.desktop_commit("local edit")
    assert local.success, local.error
    conflict = await service.desktop_sync()
    assert not conflict.success
    assert conflict.conflicts
    assert await storage.read("handoff.txt") == b"ours\n"

    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", second)
    if operation == "resolve":
        resolved = await service.desktop_resolve({"handoff.txt": "ours"})
        assert resolved.success, resolved.error
    else:
        aborted = await service.desktop_abort_merge()
        assert aborted.success, aborted.error
    assert not (second / ".git" / "MERGE_HEAD").exists()
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", first)
    if operation == "resolve":
        published = await service.desktop_sync()
        assert published.success, published.error
        assert Repo(bare).commit("main").hexsha == published.commit_sha
        assert Repo(bare).git.show("main:handoff.txt") == "ours"
    else:
        diff = await service.desktop_diff("handoff.txt")
        assert diff.working_content == "ours\n"
        assert Repo(first).head.commit.hexsha == local.commit_sha
    assert not (first / ".git" / "MERGE_HEAD").exists()
    assert b"<<<<<<<" not in await storage.read("handoff.txt")


async def test_disconnect_sanitizes_the_durable_workspace_snapshot(
    workspace_backend, tmp_path, monkeypatch,
):
    settings, _ = workspace_backend
    first = tmp_path / "first"
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", first)
    repo = Repo.init(first)
    token = "qualification-only-token"
    repo.create_remote("origin", f"https://x-access-token:{token}@github.com/owner/repo.git")
    (first / "file.txt").write_text("content\n")
    repo.index.add(["file.txt"])
    repo.index.commit("legacy local commit")
    manager = GitRepoManager(settings)
    await manager.sync_up(first)
    async with manager.workspace():
        pass
    await manager.remove_stored_credentials()
    successor = tmp_path / "successor"
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", successor)
    async with manager.workspace():
        assert token not in (successor / ".git" / "config").read_text()
        assert Repo(successor).head.commit.hexsha == repo.head.commit.hexsha


async def test_first_connection_replaces_the_previous_durable_git_state(
    db_session, workspace_backend, tmp_path, monkeypatch,
):
    settings, storage = workspace_backend
    first = tmp_path / "first"
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", first)
    old_remote, new_remote = tmp_path / "old.git", tmp_path / "new.git"
    for path in (old_remote, new_remote):
        Repo.init(path, bare=True)
        (path / "HEAD").write_text("ref: refs/heads/main\n")
    previous = GitHubSyncService(
        db=db_session, repo_url=f"file://{old_remote}", branch="main", settings=settings,
    )
    await storage.write("handoff.txt", b"platform content\n")
    assert (await previous.desktop_fetch()).success
    assert (await previous.desktop_commit("old local commit")).success
    await previous.repo_manager.remove_stored_credentials()
    current = GitHubSyncService(
        db=db_session, repo_url=f"file://{new_remote}", branch="main", settings=settings,
    )
    requester = str(uuid4())
    preview = await current.preview_connect(
        "owner/new-repository", "main", requested_by_user_id=requester, organization_id=None,
    )
    connected = await current.desktop_connect(
        GitConnectRequest(preview_token=preview.token, strategy="publish_local"),
        requested_by_user_id=requester, organization_id=None,
    )
    assert connected.success, connected.error
    successor = tmp_path / "successor"
    monkeypatch.setattr("src.services.git_repo_manager.PERSISTENT_WORK_DIR", successor)
    published = await current.desktop_sync()
    assert published.success, published.error
    # No-op sync may omit its result SHA; verify the actual hydrated Git head.
    assert Repo(successor).head.commit.hexsha == connected.commit_sha
    assert Repo(new_remote).commit("main").hexsha == connected.commit_sha
