# Git Connection Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give developers explicit, recoverable CLI and UI workflows for connecting and updating the workspace repository and managed Solutions from Git.

**Architecture:** Workspace Git gains a preview-first connection/reconciliation service and accurately named sync/abort commands. Managed Solutions retain their separate one-writer lifecycle but expose install-from-repo, connect/disconnect, and update-from-ref commands through existing REST endpoints. All mutations use PlatformJobs; all conflict/action states are structured for humans and agents.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, GitPython, Click, React, TypeScript, PlatformJob, pytest, Vitest, Playwright.

---

## File map

- Modify `api/src/models/contracts/github.py`: first-connect preview/request DTOs.
- Modify `api/src/services/github_sync.py`: reconciliation planner and apply path.
- Modify `api/src/routers/github.py`: connect preview/enqueue and abort routes.
- Modify `api/bifrost/git_commands.py`: `sync`, `connect`, and `abort-merge`.
- Modify `api/bifrost/commands/solution.py`: managed Solution Git commands.
- Modify `client/src/services/github.ts`: connect preview/apply services.
- Modify `client/src/services/solutions.ts`: managed Git update helpers if missing.
- Modify `client/src/pages/settings/GitHub.tsx`: first-connect reconciliation UI.
- Modify `client/src/pages/SolutionDetail.tsx`: connect/disconnect/update actions.
- Create `api/tests/unit/test_cli_git.py`.
- Test `api/tests/unit/test_cli_solution_git.py`.
- Test `api/tests/e2e/platform/test_git_sync_local.py`.
- Test `api/tests/e2e/platform/test_solution_git_connected_e2e.py`.
- Test `client/e2e/github-settings-acceptance.admin.spec.ts`.
- Test `client/e2e/solution-lifecycle.admin.spec.ts`.

### Task 1: Add first-connect reconciliation contracts and planning

**Files:**
- Modify: `api/src/models/contracts/github.py`
- Modify: `api/src/services/github_sync.py`
- Test: `api/tests/e2e/platform/test_git_sync_local.py`

- [ ] **Step 1: Write reconciliation classification tests**

```python
async def test_connect_preview_classifies_dirty_detached_workspace(sync_service, remote_repo):
    await write_repo_file("workflows/local.py", b"local")
    remote_repo.write("workflows/remote.py", b"remote")
    preview = await sync_service.preview_connect(remote_repo.url, branch="main")
    assert preview.state == "requires_reconciliation"
    assert find(preview.items, "workflows/local.py").classification == "local_only"
    assert find(preview.items, "workflows/remote.py").classification == "remote_only"


async def test_connect_preview_reports_same_path_conflict(sync_service, remote_repo):
    await write_repo_file("modules/shared.py", b"local")
    remote_repo.write("modules/shared.py", b"remote")
    preview = await sync_service.preview_connect(remote_repo.url, branch="main")
    assert find(preview.items, "modules/shared.py").classification == "conflict"
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -k connect_preview -v`

Expected: FAIL because preview does not exist.

- [ ] **Step 3: Add typed contracts**

```python
class GitConnectItem(BaseModel):
    path: str
    classification: Literal["local_only", "remote_only", "identical", "conflict"]
    local_sha256: str | None = None
    remote_sha256: str | None = None


class GitConnectPreview(BaseModel):
    token: str
    repository_url: str
    branch: str
    state: Literal["ready", "requires_reconciliation"]
    items: list[GitConnectItem]


class GitConnectRequest(BaseModel):
    preview_token: str
    strategy: Literal["publish_local", "start_from_remote", "reconcile"]
    decisions: dict[str, Literal["local", "remote"]] = Field(default_factory=dict)
```

- [ ] **Step 4: Implement preview without mutating workspace state**

```python
async def preview_connect(self, repository_url: str, branch: str) -> GitConnectPreview:
    local = await self._workspace_hashes()
    async with temporary_clone(repository_url, branch) as remote_root:
        remote = await hash_tree(remote_root)
    items = classify_trees(local, remote)
    return await self._connect_preview_store.save(repository_url, branch, items)
```

The token binds hashes, repository URL, branch, caller, and expiration. Refuse
symlinks and unsupported repository shapes during preview.

- [ ] **Step 5: Run connect preview tests**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -k connect_preview -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/models/contracts/github.py api/src/services/github_sync.py api/tests/e2e/platform/test_git_sync_local.py
git commit -m "feat: preview workspace git connection"
```

### Task 2: Execute first connection through `workspace.git`

**Files:**
- Modify: `api/src/services/github_sync.py`
- Modify: `api/src/jobs/platform/git_operation.py`
- Modify: `api/src/routers/github.py`
- Test: `api/tests/e2e/platform/test_git_sync_local.py`

- [ ] **Step 1: Add stale-plan and strategy tests**

```python
async def test_connect_refuses_when_workspace_changed_after_preview(...):
    preview = await preview_connect()
    await write_repo_file("after-preview.txt", b"changed")
    result = await enqueue_connect(preview, strategy="publish_local")
    assert result.error_code == "git_connect_plan_stale"


async def test_reconcile_requires_decision_for_each_conflict(...):
    preview = await preview_with_conflict()
    response = await post_connect(preview.token, strategy="reconcile", decisions={})
    assert response.status_code == 422
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -k 'connect_refuses or reconcile_requires' -v`

Expected: FAIL.

- [ ] **Step 3: Implement validated reconciliation**

```python
async def connect(self, request: GitConnectRequest) -> SyncResult:
    preview = await self._connect_preview_store.load(request.preview_token)
    await self._assert_preview_current(preview)
    selected = resolve_connect_items(preview.items, request.strategy, request.decisions)
    await materialize_selected_tree(selected, destination=self.work_dir)
    await self._configure_remote(preview.repository_url, preview.branch)
    plan = await self.prepare_desktop_sync()
    return await self.apply_desktop_sync(plan, confirm_deletes=False)
```

`start_from_remote` must require explicit confirmation when it would discard
local-only/different content. `publish_local` must refuse a nonempty divergent
remote instead of force-pushing. `reconcile` requires every conflict decision.

- [ ] **Step 4: Add routes and PlatformJob dispatch**

```python
@router.post("/connect/preview", response_model=GitConnectPreview)
async def preview_connect(body: GitConnectPreviewRequest, ctx: Context, user: CurrentSuperuser):
    return await GitHubSyncService(ctx.db).preview_connect(body.repository_url, body.branch)


@router.post("/connect", response_model=PlatformJobAccepted, status_code=202)
async def connect(body: GitConnectRequest, ctx: Context, user: CurrentSuperuser):
    job, reused = await enqueue_workspace_git("connect", body.model_dump(mode="json"), user)
    return PlatformJobAccepted(job_id=job.id, status=job.status, reused=reused)
```

- [ ] **Step 5: Run Git E2E coverage**

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/github_sync.py api/src/jobs/platform/git_operation.py api/src/routers/github.py api/tests/e2e/platform/test_git_sync_local.py
git commit -m "feat: reconcile first workspace git connection"
```

### Task 3: Correct workspace Git CLI semantics

**Files:**
- Modify: `api/bifrost/git_commands.py`
- Create: `api/tests/unit/test_cli_git.py`

- [ ] **Step 1: Add command tests**

```python
def test_git_sync_uses_sync_endpoint(runner, mock_client):
    result = runner.invoke(git_group, ["sync"])
    assert result.exit_code == 0
    mock_client.post.assert_called_with("/api/github/sync", json=ANY)


def test_abort_merge_is_exposed(runner, mock_client):
    result = runner.invoke(git_group, ["abort-merge"])
    assert result.exit_code == 0
    mock_client.post.assert_called_with("/api/github/abort-merge", json=ANY)


def test_connect_prints_dirty_reconciliation(runner, connect_preview):
    result = runner.invoke(git_group, ["connect", connect_preview.repo], input="r\nl\n")
    assert "Local only" in result.output
    assert "Conflict" in result.output
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh tests/unit/test_cli_git.py -v`

Expected: FAIL because `sync`, `connect`, or `abort-merge` is missing.

- [ ] **Step 3: Add accurately named commands**

```python
@git_group.command("sync")
@click.option("--confirm-deletes", is_flag=True)
def sync_cmd(confirm_deletes: bool):
    """Fetch, reconcile, validate, publish, and import workspace Git changes."""
    asyncio.run(_run_git_job("sync", {"confirm_deletes": confirm_deletes}))


@git_group.command("abort-merge")
def abort_merge_cmd():
    """Restore the working tree to its state before the current merge."""
    asyncio.run(_run_git_job("abort_merge", {}))


@git_group.command("connect")
@click.argument("repository_url")
@click.option("--branch", default="main", show_default=True)
@click.option("--strategy", type=click.Choice(["publish-local", "start-from-remote", "reconcile"]))
def connect_cmd(repository_url: str, branch: str, strategy: str | None):
    asyncio.run(_connect(repository_url, branch, strategy))
```

Keep `push` only if current released-CLI compatibility requires it, implemented
as a thin deprecated alias that prints `Use bifrost git sync`; otherwise remove
it in the same change. Do not retain two implementations.

- [ ] **Step 4: Add structured action handling**

```python
if result.get("requires_action") == "confirm_deletes":
    render_pending_deletes(result["pending_deletes"])
    raise click.ClickException("Sync requires --confirm-deletes after review")
```

Conflicts print path, ours/theirs availability, exact `resolve` commands, and
the `abort-merge` recovery command. JSON mode emits the PlatformJob result.

- [ ] **Step 5: Run CLI and contract tests**

Run: `./test.sh tests/unit/test_cli_git.py tests/unit/test_contract_version.py -v`

Expected: PASS.

- [ ] **Step 6: Regenerate skill truth and commit**

Run: `python api/scripts/skill-truth/generate.py`

```bash
git add api/bifrost/git_commands.py api/tests/unit api/bifrost/skills
git commit -m "feat: clarify workspace git CLI workflows"
```

Stage only the specific test and generated files changed.

### Task 4: Expose managed Solution Git lifecycle in the CLI

**Files:**
- Modify: `api/bifrost/commands/solution.py`
- Test: `api/tests/unit/test_cli_solution_git.py`
- Test: `api/tests/e2e/platform/test_solution_git_connected_e2e.py`

- [ ] **Step 1: Write CLI contract tests**

```python
def test_install_repo_posts_existing_endpoint(runner, mock_client):
    result = runner.invoke(solution_group, ["install-repo", "https://example/repo.git", "--ref", "main"])
    assert result.exit_code == 0
    mock_client.post.assert_called_with("/api/solutions/install/from-repo", json=expect.object_containing({"repo_url": "https://example/repo.git", "git_ref": "main"}))


def test_solution_git_connect_patches_one_writer_fields(runner, mock_client):
    result = runner.invoke(solution_group, ["git", "connect", "support", "https://example/repo.git"])
    assert result.exit_code == 0
    assert mock_client.patch.call_args.kwargs["json"]["git_connected"] is True


def test_solution_sync_uses_update_endpoint(runner, mock_client):
    result = runner.invoke(solution_group, ["sync", "support"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh tests/unit/test_cli_solution_git.py -v`

Expected: FAIL because commands are missing.

- [ ] **Step 3: Add commands over existing REST behavior**

```python
@solution_group.command("install-repo")
@click.argument("repository_url")
@click.option("--subpath")
@click.option("--ref", "git_ref")
def install_repo_cmd(repository_url, subpath, git_ref):
    asyncio.run(_install_solution_repo(repository_url, subpath, git_ref))


@solution_git_group.command("connect")
@click.argument("solution_ref")
@click.argument("repository_url")
@click.option("--subpath")
@click.option("--ref", "git_ref", default="main")
def solution_git_connect(solution_ref, repository_url, subpath, git_ref):
    asyncio.run(_connect_solution_git(solution_ref, repository_url, subpath, git_ref))


@solution_git_group.command("disconnect")
@click.argument("solution_ref")
def solution_git_disconnect(solution_ref):
    asyncio.run(_disconnect_solution_git(solution_ref))


@solution_group.command("sync")
@click.argument("solution_ref")
def solution_sync(solution_ref):
    asyncio.run(_sync_solution_from_configured_ref(solution_ref))
```

Use existing preview, install-from-repo, PATCH, and update-now endpoints. Do not
reimplement ORM behavior in CLI. Refusals must explain the one-writer rule.

- [ ] **Step 4: Rename the unrelated materialization command**

Rename existing `solution pull` to `solution pull-manifests`. Keep a deprecated
alias only if the explicit compatibility decision in Task 3 kept aliases; both
aliases must call one implementation.

- [ ] **Step 5: Run CLI and Solution Git E2E tests**

Run: `./test.sh tests/unit/test_cli_solution_git.py tests/e2e/platform/test_solution_git_connected_e2e.py tests/e2e/platform/test_solution_install_from_repo.py -v`

Expected: PASS.

- [ ] **Step 6: Regenerate skill truth and commit**

Run: `python api/scripts/skill-truth/generate.py`

```bash
git add api/bifrost/commands/solution.py api/tests/unit/test_cli_solution_git.py api/tests/e2e/platform/test_solution_git_connected_e2e.py api/bifrost/skills
git commit -m "feat: expose managed solution git lifecycle in CLI"
```

### Task 5: Add workspace first-connect UI

**Files:**
- Modify: `client/src/services/github.ts`
- Modify: `client/src/pages/settings/GitHub.tsx`
- Test: `client/src/pages/settings/GitHub.test.tsx`
- Test: `client/e2e/github-settings-acceptance.admin.spec.ts`

- [ ] **Step 1: Write component tests for detached dirty state**

```tsx
it("does not connect a divergent repository without reconciliation", async () => {
  mockPreviewConnect({ state: "requires_reconciliation", items: [conflictItem] });
  render(<GitSettings />);
  await userEvent.type(screen.getByLabelText("Repository URL"), "https://example/repo.git");
  await userEvent.click(screen.getByRole("button", { name: "Review connection" }));
  expect(screen.getByText("Conflicting content")).toBeVisible();
  expect(screen.queryByText("Connected")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh client unit src/pages/settings/GitHub.test.tsx`

Expected: FAIL because connection currently saves configuration without reconciliation.

- [ ] **Step 3: Add preview/apply services**

```typescript
export async function previewGitConnect(body: GitConnectPreviewRequest): Promise<GitConnectPreview> {
  const { data, error } = await apiClient.POST("/api/github/connect/preview", { body });
  if (error) throw new Error(getErrorMessage(error, "Failed to preview Git connection"));
  return data;
}

export async function connectGit(body: GitConnectRequest): Promise<PlatformJobAccepted> {
  const { data, error } = await apiClient.POST("/api/github/connect", { body });
  if (error) throw new Error(getErrorMessage(error, "Failed to connect Git"));
  return data;
}
```

- [ ] **Step 4: Build reconciliation review**

Show local-only, remote-only, identical, and conflict counts. Explain all three
strategies. For reconcile, require a local/remote decision for each conflict.
Dirty local files are ordinary review items, not an error. Completion navigates
to the normal Git status view.

- [ ] **Step 5: Run component and browser tests**

Run: `./test.sh client unit src/pages/settings/GitHub.test.tsx`

Run: `./test.sh client e2e e2e/github-settings-acceptance.admin.spec.ts`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add client/src/services/github.ts client/src/pages client/src/components client/e2e/github-settings-acceptance.admin.spec.ts
git commit -m "feat: add safe workspace git connection review"
```

Stage only files actually changed.

### Task 6: Add managed Solution Git actions to Solution detail

**Files:**
- Modify: `client/src/pages/SolutionDetail.tsx`
- Modify: `client/src/services/solutions.ts`
- Test: `client/src/pages/SolutionDetail.test.tsx`
- Test: `client/e2e/solution-lifecycle.admin.spec.ts`

- [ ] **Step 1: Add component tests**

```tsx
it("offers Connect Git for a locally installed solution", async () => {
  renderSolutionDetail({ git_connected: false });
  expect(screen.getByRole("button", { name: "Connect Git" })).toBeVisible();
});

it("offers Update from main for a connected solution", async () => {
  renderSolutionDetail({ git_connected: true, git_ref: "main" });
  expect(screen.getByRole("button", { name: "Update from main" })).toBeVisible();
});
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh client unit src/pages/SolutionDetail.test.tsx`

Expected: FAIL because the actions are absent or unclear.

- [ ] **Step 3: Implement explicit actions**

Use existing Solution PATCH and update-now services. Connection first previews
the configured repo/ref, states that Git becomes the only writer, and refuses
while local deploy work is active. Disconnect explains that future updates
become manual and does not alter installed entities.

```tsx
{solution.git_connected ? (
  <Button onClick={openUpdateDialog}>Update from {solution.git_ref ?? "main"}</Button>
) : (
  <Button onClick={openConnectDialog}>Connect Git</Button>
)}
```

- [ ] **Step 4: Run component and browser tests**

Run: `./test.sh client unit src/pages/SolutionDetail.test.tsx`

Run: `./test.sh client e2e e2e/solution-lifecycle.admin.spec.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add client/src/pages/SolutionDetail.tsx client/src/pages/SolutionDetail.test.tsx client/src/services/solutions.ts client/src/services/solutions.test.ts client/e2e/solution-lifecycle.admin.spec.ts
git commit -m "feat: clarify managed solution git lifecycle"
```

### Task 7: Final verification

**Files:**
- Modify only files required by failures from the commands below.

- [ ] **Step 1: Run API quality and targeted backend tests**

Run: `./test.sh quality api`

Run: `./test.sh tests/e2e/platform/test_git_sync_local.py tests/e2e/platform/test_solution_git_connected_e2e.py tests/e2e/platform/test_solution_install_from_repo.py tests/unit/test_cli_git.py tests/unit/test_cli_solution_git.py -v`

Expected: PASS.

- [ ] **Step 2: Run contract tripwires**

Run: `./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v`

Expected: PASS.

- [ ] **Step 3: Run frontend quality and focused UI tests**

Run: `(cd client && npm run tsc && npm run lint)`

Run: `./test.sh client unit src/pages/settings/GitHub.test.tsx src/pages/SolutionDetail.test.tsx`

Expected: PASS.

- [ ] **Step 4: Run the two browser journeys**

Run: `./test.sh client e2e e2e/github-settings-acceptance.admin.spec.ts e2e/solution-lifecycle.admin.spec.ts`

Expected: PASS.

- [ ] **Step 5: Commit verification fixes, if any**

```bash
git add api client
git commit -m "test: verify git connection workflows"
```

Do not create an empty commit when no fixes were required. Record all broader
suites not run in the final implementation handoff.
