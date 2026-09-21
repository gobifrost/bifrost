# Workspace Git Sync Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make workspace Git synchronization a direct, durable, bounded-memory PlatformJob whose validation and confirmation gates occur before remote or `_repo` publication.

**Architecture:** Split synchronization into a read-only preparation phase and an apply/publish phase represented by a typed plan. The PlatformJob owns execution and progress directly, while `GitHubSyncService` remains the Git/manifest domain service. One explicit workspace lock protects Git sync and future bundle imports.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, SQLAlchemy, GitPython, S3-compatible `RepoStorage`, Redis locks, PlatformJob, pytest.

---

## File map

- Modify `api/src/models/contracts/github.py`: typed plan and requires-action result.
- Modify `api/src/models/contracts/platform_jobs.py`: shared terminal `requires_action` status.
- Modify `api/src/services/platform_jobs.py`: terminal-state handling and public projection.
- Modify `api/src/jobs/schedulers/platform_jobs.py`: treat action-required jobs as terminal.
- Modify `api/src/services/git_repo_manager.py`: named workspace mutation lock and streaming traversal helpers.
- Modify `api/src/services/github_sync.py`: prepare/apply phases and read-only status.
- Modify `api/src/jobs/platform/git_operation.py`: execute Git operations directly.
- Modify `api/src/scheduler/main.py`: remove the legacy Git operation handler.
- Modify `api/src/core/pubsub.py`: enqueue only through PlatformJobs; remove bespoke result publication.
- Modify `api/src/routers/github.py`: return shared PlatformJob contracts.
- Modify `api/src/models/orm/platform_jobs.py`: no schema change; status is already stored as a string.
- Test `api/tests/unit/test_github_sync_plan.py`: ordering and status purity.
- Test `api/tests/unit/test_git_operation_platform_job.py`: direct job behavior.
- Test `api/tests/e2e/platform/test_git_sync_local.py`: remote/S3/DB consistency and confirmation behavior.
- Test `api/tests/unit/test_git_sync_memory.py`: bounded traversal memory.

### Task 1: Encode the synchronization plan and action-required result

**Files:**
- Modify: `api/src/models/contracts/github.py`
- Modify: `api/src/models/contracts/platform_jobs.py`
- Modify: `api/src/services/platform_jobs.py`
- Modify: `api/src/jobs/schedulers/platform_jobs.py`
- Test: `api/tests/unit/test_github_sync_plan.py`
- Test: `api/tests/unit/services/test_platform_jobs.py`

- [ ] **Step 1: Write the failing contract tests**

```python
def test_sync_plan_round_trips_delete_preview():
    plan = WorkspaceSyncPlan(
        base_sha="abc",
        merge_sha="def",
        pending_deletes=[EntityChange(entity_type="workflow", entity_id="1", action="delete")],
        entity_changes=[],
        file_changes=[],
    )
    assert WorkspaceSyncPlan.model_validate(plan.model_dump()).merge_sha == "def"


def test_sync_result_requires_action_is_not_success():
    result = SyncResult(requires_action="confirm_deletes", pending_deletes=[])
    assert result.success is False
    assert result.requires_action == "confirm_deletes"


def test_requires_action_is_terminal_platform_job_status():
    assert PlatformJobStatus.REQUIRES_ACTION.value == "requires_action"
    assert "requires_action" in TERMINAL_PLATFORM_JOB_STATUSES
    assert "requires_action" not in ACTIVE_PLATFORM_JOB_STATUSES
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `./test.sh tests/unit/test_github_sync_plan.py -v`

Expected: FAIL because `WorkspaceSyncPlan` and `requires_action` do not exist.

- [ ] **Step 3: Add typed contracts**

```python
class WorkspaceFileChange(BaseModel):
    path: str
    action: Literal["create", "update", "delete"]
    sha256: str | None = None


class WorkspaceSyncPlan(BaseModel):
    base_sha: str | None
    merge_sha: str
    pending_deletes: list[EntityChange] = Field(default_factory=list)
    entity_changes: list[EntityChange] = Field(default_factory=list)
    file_changes: list[WorkspaceFileChange] = Field(default_factory=list)


class SyncResult(BaseModel):
    success: bool = False
    requires_action: Literal["confirm_deletes"] | None = None
    pending_deletes: list[EntityChange] = Field(default_factory=list)
    # retain the existing result fields unchanged


class PlatformJobStatus(str, Enum):
    # retain queued/running/waiting/cancel_requested/succeeded/failed/cancelled
    REQUIRES_ACTION = "requires_action"
```

Ensure every existing successful construction passes `success=True`; do not
derive success from truthiness or from an absent error.

Add `requires_action` to `TERMINAL_PLATFORM_JOB_STATUSES`, public projections,
list ordering, and scheduler terminal checks. A job ending in this state stores
the typed action and preview data in `result`; it is not retryable until a new
job is enqueued with explicit confirmation.

- [ ] **Step 4: Run the focused tests**

Run: `./test.sh tests/unit/test_github_sync_plan.py tests/unit/services/test_platform_jobs.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/models/contracts/github.py api/src/models/contracts/platform_jobs.py api/src/services/platform_jobs.py api/src/jobs/schedulers/platform_jobs.py api/tests/unit/test_github_sync_plan.py api/tests/unit/services/test_platform_jobs.py
git commit -m "refactor: model workspace git sync plans"
```

### Task 2: Make Git status observational

**Files:**
- Modify: `api/src/services/github_sync.py`
- Test: `api/tests/unit/test_github_sync_plan.py`

- [ ] **Step 1: Add a regression test that snapshots index and conflict state**

```python
async def test_desktop_status_does_not_mutate_index_or_merge_state(sync_service, repo):
    before_index = repo.git.diff("--cached", "--binary")
    before_merge_head = (Path(repo.working_tree_dir) / ".git/MERGE_HEAD").read_text() \
        if (Path(repo.working_tree_dir) / ".git/MERGE_HEAD").exists() else None

    await sync_service.desktop_status()

    assert repo.git.diff("--cached", "--binary") == before_index
    merge_head = Path(repo.working_tree_dir) / ".git/MERGE_HEAD"
    assert (merge_head.read_text() if merge_head.exists() else None) == before_merge_head
```

- [ ] **Step 2: Run the regression test and verify it fails**

Run: `./test.sh tests/unit/test_github_sync_plan.py::test_desktop_status_does_not_mutate_index_or_merge_state -v`

Expected: FAIL because status currently stages or resolves files.

- [ ] **Step 3: Replace mutation-based inspection with Git read commands**

Use `git status --porcelain=v2 -z`, `git diff --name-status -z`, and
`git ls-files -u -z`. Remove `git add -A`, checkout, and auto-resolution from
the status call path. Keep conflict mutation exclusively in
`desktop_resolve()`.

```python
def _read_status(repo: Repo) -> tuple[list[GitFileStatus], list[GitConflict]]:
    porcelain = repo.git.status("--porcelain=v2", "-z")
    unmerged = repo.git.ls_files("-u", "-z")
    return parse_porcelain_v2(porcelain), parse_unmerged_entries(unmerged)


async def desktop_status(self) -> GitStatusResponse:
    async with self.repo_manager.read_checkout() as work_dir:
        repo = self._open_or_init(work_dir)
        files, conflicts = _read_status(repo)
        return GitStatusResponse(files=files, conflicts=conflicts, **self._ahead_behind(repo))
```

- [ ] **Step 4: Run all Git status unit tests**

Run: `./test.sh tests/unit/test_github_sync_plan.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/github_sync.py api/tests/unit/test_github_sync_plan.py
git commit -m "fix: make workspace git status read only"
```

### Task 3: Prepare and validate before publication

**Files:**
- Modify: `api/src/services/github_sync.py`
- Test: `api/tests/e2e/platform/test_git_sync_local.py`

- [ ] **Step 1: Add failure-ordering and confirmation tests**

```python
async def test_sync_import_failure_does_not_push_or_sync_storage(sync_service, remote, repo_storage, monkeypatch):
    before_remote = remote.head_sha()
    before_storage = await repo_storage.snapshot_hashes()
    monkeypatch.setattr(sync_service, "_import_all_entities", AsyncMock(side_effect=ValueError("bad manifest")))

    result = await sync_service.desktop_sync()

    assert result.success is False
    assert remote.head_sha() == before_remote
    assert await repo_storage.snapshot_hashes() == before_storage


async def test_delete_confirmation_occurs_before_publish(sync_service, remote, repo_storage):
    before_remote = remote.head_sha()
    result = await sync_service.desktop_sync(confirm_deletes=False)
    assert result.requires_action == "confirm_deletes"
    assert remote.head_sha() == before_remote
    assert repo_storage.sync_count == 0
```

- [ ] **Step 2: Run both tests and verify they fail**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -k 'import_failure_does_not_push or confirmation_occurs_before_publish' -v`

Expected: FAIL with remote/storage mutations observed.

- [ ] **Step 3: Introduce explicit prepare and apply methods**

```python
async def prepare_desktop_sync(self, *, progress_fn=None) -> WorkspaceSyncPlan:
    pull_result = await self._do_pull(self.work_dir, self.repo, progress_fn=progress_fn)
    if not pull_result.success:
        raise WorkspaceMergeConflict(pull_result.conflicts)
    async with self.db.begin_nested():
        entity_ops = await self._resolver.plan_import(
            await self._load_manifest(self.work_dir),
            work_dir=self.work_dir,
            dry_run=True,
        )
        deletes = await self._resolver._resolve_deletions(work_dir=self.work_dir, dry_run=True)
        await self.db.rollback()
    return WorkspaceSyncPlan(
        base_sha=pull_result.base_sha,
        merge_sha=self.repo.head.commit.hexsha,
        entity_changes=changes_from_ops(entity_ops),
        pending_deletes=[item for item in deletes if item.action != "keep"],
        file_changes=await self._plan_file_changes(self.work_dir),
    )


async def apply_desktop_sync(self, plan: WorkspaceSyncPlan, *, confirm_deletes: bool, progress_fn=None) -> SyncResult:
    if self.repo.head.commit.hexsha != plan.merge_sha:
        raise WorkspacePlanStale("working tree changed after validation")
    if plan.pending_deletes and not confirm_deletes:
        return SyncResult(requires_action="confirm_deletes", pending_deletes=plan.pending_deletes)
    async with self.db.begin():
        imported, changes = await self._import_all_entities(self.work_dir, progress_fn=progress_fn)
        if plan.pending_deletes:
            changes.extend(await self._resolver._resolve_deletions(work_dir=self.work_dir))
        await self._update_file_index(self.work_dir)
    push = self._do_push(self.work_dir, self.repo)
    if not push.success:
        raise WorkspacePublishError(push.error)
    await self.repo_manager.sync_up(self.work_dir)
    await self._sync_app_previews(self.work_dir)
    return SyncResult(success=True, entities_imported=imported, entity_changes=changes)
```

If import commits before a later push error, record the plan as retryable and do
not clear dirty state. Do not add a fallback publication route.

- [ ] **Step 4: Route `desktop_sync()` through prepare/apply**

```python
async def desktop_sync(self, job_id: str | None = None, confirm_deletes: bool = False) -> SyncResult:
    async with self.repo_manager.lock() as work_dir:
        self._bind_checkout(work_dir)
        plan = await self.prepare_desktop_sync(progress_fn=self._job_progress(job_id))
        return await self.apply_desktop_sync(
            plan,
            confirm_deletes=confirm_deletes,
            progress_fn=self._job_progress(job_id),
        )
```

- [ ] **Step 5: Run the targeted E2E tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -k 'sync or confirmation or conflict' -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/github_sync.py api/tests/e2e/platform/test_git_sync_local.py
git commit -m "fix: validate workspace sync before publication"
```

### Task 4: Move Git execution fully into PlatformJobs

**Files:**
- Modify: `api/src/jobs/platform/git_operation.py`
- Modify: `api/src/scheduler/main.py`
- Modify: `api/src/core/pubsub.py`
- Modify: `api/src/routers/github.py`
- Test: `api/tests/unit/test_git_operation_platform_job.py`

- [ ] **Step 1: Test that the job does not construct `Scheduler`**

```python
async def test_git_job_calls_sync_service_directly(context, monkeypatch):
    sync = AsyncMock(return_value=SyncResult(success=True))
    monkeypatch.setattr("src.jobs.platform.git_operation.GitHubSyncService.desktop_sync", sync)
    result = await run_git_operation(context, GitOperationPayload(operation="sync"))
    assert result["success"] is True
    sync.assert_awaited_once()
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh tests/unit/test_git_operation_platform_job.py -v`

Expected: FAIL because the job delegates to `Scheduler()._handle_git_operation()`.

- [ ] **Step 3: Implement direct dispatch with typed operations**

```python
class GitOperationPayload(BaseModel):
    operation: Literal["fetch", "status", "commit", "sync", "resolve", "discard", "abort_merge"]
    organization_id: UUID | None = None
    options: dict[str, Any] = Field(default_factory=dict)


async def run_git_operation(context: PlatformJobContext, payload: GitOperationPayload) -> dict:
    async with get_db_context() as db:
        service = GitHubSyncService(db, organization_id=payload.organization_id)
        result = await dispatch_git_operation(service, payload, context.report)
    if getattr(result, "requires_action", None):
        raise PlatformJobRequiresAction(result.model_dump(mode="json"))
    if not result.success:
        raise PlatformJobFailure("git_operation_failed", result.error or "Git operation failed")
    return result.model_dump(mode="json")
```

Set `resource_lock_key` through the shared PlatformJob definition mechanism to
the literal workspace mutation key used by bundle import. Remove the scheduler
handler and the Git-only completion/result publication functions after all
callers use PlatformJob state.

- [ ] **Step 4: Update routers to enqueue and return shared job DTOs**

```python
job = await platform_job_service.enqueue(
    job_type="workspace.git",
    payload=GitOperationPayload(operation="sync", options=request.model_dump()),
    requested_by=user.user_id,
    resource_key="workspace",
)
return PlatformJobAccepted(job_id=job.id, status=job.status, reused=reused)
```

- [ ] **Step 5: Run focused job and route tests**

Run: `./test.sh tests/unit/test_git_operation_platform_job.py tests/e2e/platform/test_git_sync_local.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/jobs/platform/git_operation.py api/src/scheduler/main.py api/src/core/pubsub.py api/src/routers/github.py api/tests/unit/test_git_operation_platform_job.py api/tests/e2e/platform/test_git_sync_local.py
git commit -m "refactor: run workspace git through platform jobs"
```

### Task 5: Bound repository traversal memory

**Files:**
- Modify: `api/src/services/github_sync.py`
- Modify: `api/src/services/git_repo_manager.py`
- Test: `api/tests/unit/test_git_sync_memory.py`

- [ ] **Step 1: Add a large-tree RSS regression test**

```python
def test_tree_scan_memory_is_bounded(large_git_tree, measure_peak_rss):
    delta = measure_peak_rss(lambda: list(iter_tree_metadata(large_git_tree)))
    assert delta < 96 * 1024 * 1024
```

Generate at least 512 MiB of file content across entries, run the scan in a
child process, and assert peak RSS overhead rather than total process RSS.

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh tests/unit/test_git_sync_memory.py -v`

Expected: FAIL because `_walk_tree` retains file bytes.

- [ ] **Step 3: Yield metadata and stream hashes**

```python
@dataclass(frozen=True)
class TreeEntryMetadata:
    path: str
    size: int
    sha256: str


def iter_tree_metadata(root: Path) -> Iterator[TreeEntryMetadata]:
    for path in iter_repo_files(root):
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(8 * 1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        yield TreeEntryMetadata(path.relative_to(root).as_posix(), size, digest.hexdigest())
```

Change upload/download callers to consume iterators or chunk streams and never
materialize a `dict[path, bytes]` for the entire repository.

- [ ] **Step 4: Run memory and Git E2E tests**

Run: `./test.sh tests/unit/test_git_sync_memory.py tests/e2e/platform/test_git_sync_local.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/github_sync.py api/src/services/git_repo_manager.py api/tests/unit/test_git_sync_memory.py
git commit -m "perf: stream workspace git tree processing"
```

### Task 6: Verify the hardening boundary

**Files:**
- Modify only files required by failures found below.

- [ ] **Step 1: Run API quality and contract checks**

Run: `./test.sh quality api`

Expected: PASS.

- [ ] **Step 2: Run focused Git coverage**

Run: `./test.sh tests/unit/test_github_sync_plan.py tests/unit/test_git_operation_platform_job.py tests/unit/test_git_sync_memory.py tests/e2e/platform/test_git_sync_local.py -v`

Expected: PASS.

- [ ] **Step 3: Run PlatformJob regression coverage**

Run: `./test.sh tests/unit/test_platform_job_kubernetes_client.py tests/unit/jobs/schedulers/test_platform_jobs.py tests/unit/routers/test_platform_jobs.py tests/unit/services/test_platform_jobs.py -v`

Expected: PASS.

- [ ] **Step 4: Commit verification fixes, if any**

```bash
git add api
git commit -m "test: verify workspace git job consistency"
```

Do not create an empty commit when no fixes were needed.
