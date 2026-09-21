# Workspace Bundle Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import an existing Solution package into `_repo` as unattached workspace entities and files with previewed per-item collision decisions, destination-ID preservation, durable execution, and bounded memory.

**Architecture:** A planner parses the existing package format, prefetches natural keys, assigns stable target IDs, and stages a signed preview artifact. A `workspace.bundle_import` PlatformJob validates decisions, applies a partial manifest without stale deletion, promotes staged files, regenerates manifests, and leaves workspace Git dirty. The browser and CLI use the same preview/job contracts.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, SQLAlchemy, PostgreSQL, S3-compatible storage, PlatformJob, Click, React, TypeScript, TanStack Query, shadcn/Radix Dialog, Vitest, Playwright, pytest.

---

## File map

- Modify `api/src/models/contracts/solutions.py`: preview, item, decision, and accepted-job DTOs.
- Create `api/src/services/solutions/workspace_bundle_plan.py`: package classification and ID map.
- Create `api/src/services/solutions/workspace_bundle_storage.py`: chunked preview/package staging.
- Create `api/src/services/solutions/workspace_bundle_import.py`: partial manifest and staged-file apply.
- Create `api/src/jobs/platform/workspace_bundle_import.py`: durable job definition.
- Modify `api/src/jobs/platform/registry.py`: register the job.
- Modify `api/src/routers/solutions.py`: preview and enqueue routes.
- Modify `api/src/services/manifest_import.py`: explicit partial-target-ID adapter; no deletion entry point.
- Modify `api/src/services/repo_sync_writer.py`: accept the shared workspace mutation/finalize boundary.
- Modify `api/bifrost/commands/solution.py`: `import-workspace` command.
- Modify `client/src/services/solutions.ts`: preview/enqueue calls.
- Create `client/src/components/solutions/WorkspaceImportReview.tsx`: compact conflict workspace.
- Create `client/src/components/solutions/WorkspaceImportReview.test.tsx`: component behavior.
- Modify `client/src/components/solutions/CreateEditSolution.tsx`: destination mode and import flow.
- Modify `client/src/components/solutions/InstallSession.tsx`: wide scroll-owned dialog variant.
- Modify generated `client/src/lib/v1.d.ts` through `npm run generate:types` only.
- Test `api/tests/unit/test_workspace_bundle_plan.py`.
- Test `api/tests/unit/test_workspace_bundle_import.py`.
- Test `api/tests/unit/test_workspace_bundle_memory.py`.
- Test `api/tests/e2e/platform/test_workspace_bundle_import.py`.
- Test `api/tests/unit/test_cli_solution_import_workspace.py`.
- Create `client/e2e/solution-workspace-import.admin.spec.ts`.

### Task 1: Define the preview and decision contracts

**Files:**
- Modify: `api/src/models/contracts/solutions.py`
- Test: `api/tests/unit/test_solution_contracts.py`

- [ ] **Step 1: Write contract round-trip tests**

```python
def test_workspace_import_preview_requires_decisions_only_for_conflicts():
    preview = WorkspaceBundlePreview(
        preview_token="token",
        package_name="Customer operations",
        package_sha256="a" * 64,
        items=[
            WorkspaceBundleItem(id="entity:app:dashboard", kind="app", name="Dashboard", classification="conflict", match_key="slug", target_id=uuid4()),
            WorkspaceBundleItem(id="file:modules/new.py", kind="file", name="modules/new.py", classification="create"),
        ],
        warnings=[],
    )
    assert preview.conflict_count == 1


def test_workspace_import_request_rejects_duplicate_item_decisions():
    with pytest.raises(ValidationError):
        WorkspaceBundleImportRequest(
            preview_token="token",
            decisions=[
                WorkspaceBundleDecision(item_id="entity:app:dashboard", action="keep"),
                WorkspaceBundleDecision(item_id="entity:app:dashboard", action="replace"),
            ],
        )
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh tests/unit/test_solution_contracts.py -k workspace_import -v`

Expected: FAIL because the DTOs do not exist.

- [ ] **Step 3: Add the contracts**

```python
class WorkspaceBundleDiffLine(BaseModel):
    field: str
    existing: Any | None = None
    incoming: Any | None = None


class WorkspaceBundleItem(BaseModel):
    id: str
    kind: Literal["workflow", "integration", "config", "app", "table", "event", "form", "agent", "claim", "policy_rule", "file_policy", "file"]
    name: str
    classification: Literal["create", "unchanged", "conflict"]
    match_key: str | None = None
    source_id: UUID | None = None
    target_id: UUID | None = None
    diff: list[WorkspaceBundleDiffLine] = Field(default_factory=list)


class WorkspaceBundlePreview(BaseModel):
    preview_token: str
    package_name: str
    package_sha256: str
    items: list[WorkspaceBundleItem]
    warnings: list[str] = Field(default_factory=list)

    @computed_field
    @property
    def conflict_count(self) -> int:
        return sum(item.classification == "conflict" for item in self.items)


class WorkspaceBundleDecision(BaseModel):
    item_id: str
    action: Literal["keep", "replace"]


class WorkspaceBundleImportRequest(BaseModel):
    preview_token: str
    decisions: list[WorkspaceBundleDecision]

    @model_validator(mode="after")
    def unique_decisions(self):
        ids = [decision.item_id for decision in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("workspace import decisions must be unique")
        return self
```

- [ ] **Step 4: Run the contract tests**

Run: `./test.sh tests/unit/test_solution_contracts.py -k workspace_import -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/models/contracts/solutions.py api/tests/unit/test_solution_contracts.py
git commit -m "feat: define workspace bundle import contracts"
```

### Task 2: Stage package and preview artifacts with bounded memory

**Files:**
- Create: `api/src/services/solutions/workspace_bundle_storage.py`
- Test: `api/tests/unit/test_workspace_bundle_memory.py`

- [ ] **Step 1: Write staging integrity and memory tests**

```python
async def test_staged_bundle_round_trips_by_hash(tmp_path, storage):
    source = tmp_path / "bundle.zip"
    source.write_bytes(b"bundle")
    staged = await storage.stage(source, requested_by=uuid4())
    destination = tmp_path / "copy.zip"
    await storage.copy_package_to(staged.token, destination, expected_sha256=staged.sha256)
    assert destination.read_bytes() == b"bundle"


def test_large_bundle_staging_has_bounded_rss(large_zip, measure_peak_rss):
    delta = measure_peak_rss(lambda: asyncio.run(stage_fixture(large_zip)))
    assert delta < 96 * 1024 * 1024
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh tests/unit/test_workspace_bundle_memory.py -v`

Expected: FAIL because storage does not exist.

- [ ] **Step 3: Implement chunked staging**

```python
CHUNK_SIZE = 8 * 1024 * 1024


class WorkspaceBundleStorage:
    def __init__(self, preview_id: UUID, settings: Settings | None = None):
        self.preview_id = preview_id
        self.settings = settings or get_settings()
        self.storage = S3StorageClient(self.settings)
        self.root = f"_workspace_bundle_imports/{preview_id}"

    async def stage_package(self, source: Path) -> tuple[str, int]:
        async def chunks():
            with source.open("rb") as handle:
                while chunk := handle.read(CHUNK_SIZE):
                    yield chunk
        return await self.storage.put_object_from_chunks(
            f"{self.root}/package.zip", chunks(), content_type="application/zip"
        )

    async def copy_package_to(self, destination: Path, *, expected_sha256: str) -> int:
        digest = hashlib.sha256()
        size = 0
        with destination.open("wb") as handle:
            async for chunk in self.storage.iter_object_chunks(f"{self.root}/package.zip", chunk_size=CHUNK_SIZE):
                digest.update(chunk)
                handle.write(chunk)
                size += len(chunk)
        if digest.hexdigest() != expected_sha256:
            destination.unlink(missing_ok=True)
            raise WorkspaceBundleIntegrityError("staged package hash mismatch")
        return size
```

Store the compact preview JSON separately, bind it to `requested_by`, hash, and
expiration, and delete the staging prefix after terminal job completion or TTL
cleanup.

- [ ] **Step 4: Run the tests**

Run: `./test.sh tests/unit/test_workspace_bundle_memory.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/solutions/workspace_bundle_storage.py api/tests/unit/test_workspace_bundle_memory.py
git commit -m "feat: stage workspace bundles with bounded memory"
```

### Task 3: Build deterministic collision plans and target-ID maps

**Files:**
- Create: `api/src/services/solutions/workspace_bundle_plan.py`
- Test: `api/tests/unit/test_workspace_bundle_plan.py`

- [ ] **Step 1: Write planner tests**

```python
async def test_conflict_preserves_destination_id(db_session, package_with_app, existing_app):
    preview = await WorkspaceBundlePlanner(db_session).plan(package_with_app)
    item = next(item for item in preview.items if item.kind == "app")
    assert item.classification == "conflict"
    assert item.target_id == existing_app.id


async def test_identical_entity_requires_no_decision(db_session, package_matching_workflow):
    preview = await WorkspaceBundlePlanner(db_session).plan(package_matching_workflow)
    item = next(item for item in preview.items if item.kind == "workflow")
    assert item.classification == "unchanged"


async def test_created_entities_receive_stable_target_ids(db_session, package_with_references):
    first = await WorkspaceBundlePlanner(db_session, preview_id=UUID(int=7)).plan(package_with_references)
    second = await WorkspaceBundlePlanner(db_session, preview_id=UUID(int=7)).plan(package_with_references)
    assert [(item.id, item.target_id) for item in first.items] == [(item.id, item.target_id) for item in second.items]
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh tests/unit/test_workspace_bundle_plan.py -v`

Expected: FAIL because the planner does not exist.

- [ ] **Step 3: Implement normalized planning**

```python
@dataclass(frozen=True)
class PlannedWorkspaceBundle:
    preview: WorkspaceBundlePreview
    manifest: Manifest
    id_map: dict[UUID, UUID]
    file_hashes: dict[str, str]


class WorkspaceBundlePlanner:
    async def plan(self, package: ParsedSolutionWorkspace) -> PlannedWorkspaceBundle:
        existing = await self._prefetch_natural_keys()
        items: list[WorkspaceBundleItem] = []
        id_map: dict[UUID, UUID] = {}
        for entity in iter_portable_entities(package.manifest):
            match = existing.find(entity.kind, entity.natural_key)
            target_id = match.id if match else uuid5(self.preview_id, entity.stable_key)
            id_map[entity.id] = target_id
            incoming = portable_definition(entity, id_map=id_map)
            classification = "create" if match is None else (
                "unchanged" if incoming == portable_definition(match) else "conflict"
            )
            items.append(to_preview_item(entity, match, target_id, classification))
        items.extend(await self._plan_files(package))
        return PlannedWorkspaceBundle(
            preview=build_preview(items), manifest=package.manifest,
            id_map=id_map, file_hashes=self.file_hashes,
        )
```

Use the natural-key definitions already encoded by `ManifestResolver`; extract
shared helpers rather than duplicating a second list that can drift. Portable
comparison must omit runtime/environment fields listed in `AGENTS.md`.

- [ ] **Step 4: Run planner tests**

Run: `./test.sh tests/unit/test_workspace_bundle_plan.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/solutions/workspace_bundle_plan.py api/tests/unit/test_workspace_bundle_plan.py
git commit -m "feat: plan workspace bundle collisions"
```

### Task 4: Add partial manifest application with typed ID rewriting

**Files:**
- Create: `api/src/services/solutions/workspace_bundle_import.py`
- Modify: `api/src/services/manifest_import.py`
- Test: `api/tests/unit/test_workspace_bundle_import.py`

- [ ] **Step 1: Write identity, state, and deletion-safety tests**

```python
async def test_replace_keeps_id_and_runtime_state(db_session, planned_bundle, existing_table):
    original_id = existing_table.id
    original_rows = await read_table_rows(existing_table)
    await WorkspaceBundleImporter(db_session).apply(planned_bundle, replace_conflicts())
    await db_session.refresh(existing_table)
    assert existing_table.id == original_id
    assert await read_table_rows(existing_table) == original_rows


async def test_keep_target_still_rewrites_incoming_references(db_session, bundle_referring_to_existing_workflow, existing_workflow):
    await WorkspaceBundleImporter(db_session).apply(bundle_referring_to_existing_workflow, keep_workflow_replace_form())
    form = await load_imported_form(db_session)
    assert form.workflow_id == existing_workflow.id


async def test_partial_import_never_deletes_absent_entities(db_session, planned_bundle, unrelated_workflow):
    await WorkspaceBundleImporter(db_session).apply(planned_bundle, replace_conflicts())
    assert await db_session.get(Workflow, unrelated_workflow.id) is not None
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh tests/unit/test_workspace_bundle_import.py -v`

Expected: FAIL because the importer and partial adapter do not exist.

- [ ] **Step 3: Add an explicit partial import request to `ManifestResolver`**

```python
@dataclass(frozen=True)
class PartialImportSelection:
    included_item_ids: frozenset[str]
    target_ids: Mapping[UUID, UUID]
    solution_id: None = None


async def plan_partial_import(
    self,
    manifest: Manifest,
    *,
    selection: PartialImportSelection,
    work_dir: Path,
    progress_fn=None,
) -> list[SyncOp]:
    rewritten = rewrite_manifest_references(manifest, selection.target_ids)
    return await self.plan_import(
        filter_manifest(rewritten, selection.included_item_ids),
        work_dir=work_dir,
        progress_fn=progress_fn,
        install_id=None,
    )
```

Keep `_resolve_deletions` out of this method and assert every created/updated ORM
row has `solution_id is None`. Change the existing entity resolvers so a supplied
target ID is used consistently in both update and insert paths.

- [ ] **Step 4: Implement importer decision validation**

```python
class WorkspaceBundleImporter:
    async def apply(self, plan: PlannedWorkspaceBundle, decisions: Sequence[WorkspaceBundleDecision]) -> ImportResult:
        by_id = {decision.item_id: decision.action for decision in decisions}
        conflicts = {item.id for item in plan.preview.items if item.classification == "conflict"}
        if set(by_id) != conflicts:
            raise WorkspaceBundleDecisionError("every conflict requires exactly one decision")
        included = {
            item.id for item in plan.preview.items
            if item.classification == "create" or (item.classification == "conflict" and by_id[item.id] == "replace")
        }
        ops = await ManifestResolver(self.db).plan_partial_import(
            plan.manifest,
            selection=PartialImportSelection(frozenset(included), plan.id_map),
            work_dir=plan.work_dir,
            progress_fn=self.progress_fn,
        )
        return ImportResult.from_ops(ops)
```

- [ ] **Step 5: Run importer and manifest regression tests**

Run: `./test.sh tests/unit/test_workspace_bundle_import.py tests/unit/test_manifest_import.py tests/unit/test_manifest.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/solutions/workspace_bundle_import.py api/src/services/manifest_import.py api/tests/unit/test_workspace_bundle_import.py
git commit -m "feat: apply partial workspace bundle manifests"
```

### Task 5: Add staged file promotion and the PlatformJob

**Files:**
- Modify: `api/src/services/solutions/workspace_bundle_import.py`
- Create: `api/src/jobs/platform/workspace_bundle_import.py`
- Modify: `api/src/jobs/platform/registry.py`
- Modify: `api/src/services/repo_sync_writer.py`
- Test: `api/tests/e2e/platform/test_workspace_bundle_import.py`

- [ ] **Step 1: Write an E2E test for durable apply and retry**

```python
async def test_workspace_import_promotes_files_regenerates_manifest_and_marks_dirty(e2e_client, package, db):
    preview = await preview_workspace_bundle(e2e_client, package)
    job = await enqueue_workspace_bundle(e2e_client, preview, replace_all(preview))
    terminal = await wait_for_platform_job(db, job.id)
    assert terminal.status == "succeeded"
    assert await RepoStorage().read("modules/customer_helpers.py") == b"expected"
    assert await RepoStorage().exists(".bifrost/apps.yaml")
    assert await workspace_is_dirty()


async def test_workspace_import_retry_is_idempotent_after_file_promotion_failure(...):
    # Inject failure after DB commit and before finalize completion.
    # Retry the same platform job and assert one entity row and expected file hash.
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh tests/e2e/platform/test_workspace_bundle_import.py -v`

Expected: FAIL because the job is not registered.

- [ ] **Step 3: Implement staged promotion and finalize record**

```python
async def promote_selected_files(self, plan, included: set[str]) -> list[PromotedFile]:
    promoted = []
    for item in plan.preview.items:
        if item.kind != "file" or item.id not in included:
            continue
        expected = plan.file_hashes[item.name]
        if await self.repo_storage.sha256(item.name) == expected:
            promoted.append(PromotedFile(item.name, expected, already_present=True))
            continue
        await self.staging.copy_file_to_repo(item.name, expected_sha256=expected)
        promoted.append(PromotedFile(item.name, expected, already_present=False))
    return promoted
```

Persist a compact finalize journal in the PlatformJob result/checkpoint before
promotion. Regenerate `.bifrost/*` only after entity operations succeed. Mark
the workspace dirty only after manifests and indexes are current.

- [ ] **Step 4: Implement and register the job**

```python
class WorkspaceBundleImportPayload(BaseModel):
    preview_id: UUID
    preview_token: str
    package_sha256: str
    decisions: list[WorkspaceBundleDecision]


async def run_workspace_bundle_import(context: PlatformJobContext, payload: WorkspaceBundleImportPayload) -> dict:
    async with get_db_context() as db:
        importer = await WorkspaceBundleImporter.load(db, payload, context=context)
        result = await importer.run()
        return result.model_dump(mode="json")


WORKSPACE_BUNDLE_IMPORT_DEFINITION = PlatformJobDefinition(
    job_type="workspace.bundle_import",
    payload_version=1,
    payload_model=WorkspaceBundleImportPayload,
    handler=run_workspace_bundle_import,
    policy=PlatformJobPolicy(timeout_seconds=60 * 60, max_attempts=2, max_concurrency=1, min_memory_headroom_mb=512),
    encrypt_payload=True,
)
```

Use the same literal `workspace` resource lock as `workspace.git`.

- [ ] **Step 5: Run E2E and PlatformJob tests**

Run: `./test.sh tests/e2e/platform/test_workspace_bundle_import.py tests/unit/test_workspace_bundle_import.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/solutions/workspace_bundle_import.py api/src/jobs/platform/workspace_bundle_import.py api/src/jobs/platform/registry.py api/src/services/repo_sync_writer.py api/tests/e2e/platform/test_workspace_bundle_import.py
git commit -m "feat: run workspace bundle imports as platform jobs"
```

### Task 6: Expose preview and enqueue APIs

**Files:**
- Modify: `api/src/routers/solutions.py`
- Modify: `api/src/models/contracts/solutions.py`
- Test: `api/tests/e2e/platform/test_workspace_bundle_import.py`

- [ ] **Step 1: Add endpoint tests**

```python
def test_workspace_preview_returns_compact_classification(e2e_client, admin_headers, bundle_zip):
    response = e2e_client.post("/api/solutions/import-workspace/preview", headers=admin_headers, files={"file": ("bundle.zip", bundle_zip, "application/zip")})
    assert response.status_code == 200
    assert {item["classification"] for item in response.json()["items"]} <= {"create", "unchanged", "conflict"}


def test_workspace_import_refuses_unresolved_conflicts(e2e_client, admin_headers, preview):
    response = e2e_client.post("/api/solutions/import-workspace", headers=admin_headers, json={"preview_token": preview["preview_token"], "decisions": []})
    assert response.status_code == 422
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh tests/e2e/platform/test_workspace_bundle_import.py -k 'preview or unresolved' -v`

Expected: FAIL with 404.

- [ ] **Step 3: Implement thin endpoints**

```python
@router.post("/import-workspace/preview", response_model=WorkspaceBundlePreview)
async def preview_workspace_import(file: Annotated[UploadFile, File()], ctx: Context, user: CurrentSuperuser):
    path = await _spool_upload_to_temp(file, prefix="bifrost-workspace-preview-")
    try:
        return await workspace_bundle_service(ctx.db).preview(path, requested_by=user.user_id)
    finally:
        _cleanup_file(path)


@router.post("/import-workspace", response_model=PlatformJobAccepted, status_code=202)
async def enqueue_workspace_import(body: WorkspaceBundleImportRequest, ctx: Context, user: CurrentSuperuser):
    job, reused = await workspace_bundle_service(ctx.db).enqueue(body, requested_by=user.user_id)
    return PlatformJobAccepted(job_id=job.id, status=job.status, reused=reused)
```

- [ ] **Step 4: Run API, DTO parity, and contract tests**

Run: `./test.sh tests/e2e/platform/test_workspace_bundle_import.py tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v`

Expected: PASS. Refresh the expected contract fingerprint for compatible additive DTOs; do not bump `CONTRACT_VERSION`.

- [ ] **Step 5: Commit**

```bash
git add api/src/routers/solutions.py api/src/models/contracts/solutions.py api/tests/e2e/platform/test_workspace_bundle_import.py api/shared/version.py
git commit -m "feat: expose workspace bundle import API"
```

Only stage `api/shared/version.py` if the contract tripwire legitimately changed it.

### Task 7: Add the CLI review and agent-safe output

**Files:**
- Modify: `api/bifrost/commands/solution.py`
- Test: `api/tests/unit/test_cli_solution_import_workspace.py`

- [ ] **Step 1: Write CLI tests for interactive and noninteractive safety**

```python
def test_noninteractive_import_requires_explicit_decisions(runner, mock_client, bundle):
    result = runner.invoke(solution_group, ["import-workspace", str(bundle)], input="")
    assert result.exit_code != 0
    assert "Use --keep-all, --replace-all, or --decisions" in result.output


def test_completed_import_prints_review_warning(runner, mock_client, bundle):
    mock_successful_workspace_import(mock_client)
    result = runner.invoke(solution_group, ["import-workspace", str(bundle), "--replace-all"])
    assert result.exit_code == 0
    assert "uncommitted workspace changes" in result.output
    assert "review references" in result.output.lower()
    assert "run compatibility checks" in result.output.lower()
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh tests/unit/test_cli_solution_import_workspace.py -v`

Expected: FAIL because the command does not exist.

- [ ] **Step 3: Implement the two-phase command**

```python
@solution_group.command("import-workspace")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--keep-all", is_flag=True)
@click.option("--replace-all", is_flag=True)
@click.option("--decisions", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "json_output", is_flag=True)
def import_workspace_cmd(archive, keep_all, replace_all, decisions, json_output):
    """Import a Solution bundle as unattached workspace content."""
    asyncio.run(_import_workspace(archive, keep_all, replace_all, decisions, json_output))
```

Print the package-cohesion warning before prompting. For a TTY, render compact
numbered conflicts and prompt Keep/Replace with apply-to-all choices. For
automation, fail closed unless every conflict is covered. JSON output must emit
the raw preview and terminal PlatformJob result without decorative text.

- [ ] **Step 4: Run CLI tests**

Run: `./test.sh tests/unit/test_cli_solution_import_workspace.py -v`

Expected: PASS.

- [ ] **Step 5: Regenerate CLI/skill truth and run contract tests**

Run: `python api/scripts/skill-truth/generate.py`

Run: `./test.sh tests/unit/test_contract_version.py tests/unit/test_dto_flags.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/bifrost/commands/solution.py api/tests/unit/test_cli_solution_import_workspace.py api/bifrost/skills
git commit -m "feat: add workspace bundle import CLI"
```

Adjust the generated-skill path in `git add` to the files actually changed by the generator.

### Task 8: Build the compact, scroll-owned review UI

**Files:**
- Modify: `client/src/services/solutions.ts`
- Create: `client/src/components/solutions/WorkspaceImportReview.tsx`
- Create: `client/src/components/solutions/WorkspaceImportReview.test.tsx`
- Modify: `client/src/components/solutions/CreateEditSolution.tsx`
- Modify: `client/src/components/solutions/InstallSession.tsx`
- Test: `client/src/services/solutions.test.ts`
- Test: `client/src/components/solutions/CreateEditSolution.test.tsx`

- [ ] **Step 1: Add service and component tests**

```tsx
it("keeps header and footer outside the scrolling review body", () => {
  render(<WorkspaceImportReview preview={preview} decisions={decisions} onDecision={onDecision} />);
  expect(screen.getByTestId("workspace-import-scroller")).toHaveClass("overflow-y-auto");
  expect(screen.getByTestId("workspace-import-footer")).not.toHaveClass("overflow-y-auto");
});

it("resolves all conflicts with Replace all", async () => {
  render(<WorkspaceImportReview preview={preview} decisions={{}} onDecision={onDecision} />);
  await userEvent.click(screen.getByRole("button", { name: "Replace all" }));
  expect(onDecision).toHaveBeenCalledWith(expect.objectContaining({ conflictA: "replace", conflictB: "replace" }));
});
```

- [ ] **Step 2: Run and verify failure**

Run: `./test.sh client unit src/components/solutions/WorkspaceImportReview.test.tsx src/services/solutions.test.ts`

Expected: FAIL because the component and service functions do not exist.

- [ ] **Step 3: Add typed service functions**

```typescript
export async function previewWorkspaceBundle(file: File): Promise<WorkspaceBundlePreview> {
  const body = new FormData();
  body.append("file", file);
  const response = await authFetch("/api/solutions/import-workspace/preview", { method: "POST", body });
  if (!response.ok) throw new Error(await parseUploadError(response, "Failed to preview workspace import"));
  return response.json();
}

export async function importWorkspaceBundle(request: WorkspaceBundleImportRequest): Promise<PlatformJobAccepted> {
  const { data, error } = await apiClient.POST("/api/solutions/import-workspace", { body: request });
  if (error) throw new Error(getErrorMessage(error, "Failed to start workspace import"));
  return data;
}
```

- [ ] **Step 4: Implement the compact master-detail review**

```tsx
export function WorkspaceImportReview({ preview, decisions, onDecisionsChange }: Props) {
  const conflicts = preview.items.filter((item) => item.classification === "conflict");
  const [selectedId, setSelectedId] = useState(conflicts[0]?.id ?? null);
  const selected = preview.items.find((item) => item.id === selectedId) ?? null;
  return (
    <div className="grid min-h-0 grid-cols-1 md:grid-cols-[minmax(0,1.45fr)_minmax(20rem,.75fr)]">
      <section className="min-w-0 border-r">
        <ConflictToolbar conflicts={conflicts} decisions={decisions} onChange={onDecisionsChange} />
        <CompactImportRows items={preview.items} decisions={decisions} selectedId={selectedId} onSelect={setSelectedId} onChange={onDecisionsChange} />
      </section>
      <ImportItemInspector item={selected} decision={selected ? decisions[selected.id] : undefined} onChange={onDecisionsChange} />
    </div>
  );
}
```

Use real buttons or radio controls with visible focus, selected state, and
accessible labels. Creates and unchanged rows are visible but have no decision
control. Do not render a detail pane per row.

- [ ] **Step 5: Give `InstallSession` explicit size and scroll ownership**

```tsx
<DialogContent
  {...focus}
  className={cn(
    "flex max-h-[90dvh] flex-col overflow-hidden",
    wide ? "sm:max-w-6xl" : "sm:max-w-lg",
  )}
>
  <fieldset disabled={pending} className="flex min-h-0 flex-1 flex-col" aria-busy={pending}>
    {children}
  </fieldset>
</DialogContent>
```

In the workspace review step, put only the middle content inside
`data-testid="workspace-import-scroller"` with `min-h-0 overflow-y-auto`; keep
the dialog header and `data-testid="workspace-import-footer"` as siblings.

- [ ] **Step 6: Wire destination selection, decisions, and job acceptance**

Add the Managed Solution / Workspace import choice before review. Disable Start
until every conflict has a decision. On acceptance, close the dialog, show the
shared PlatformJob toast/notification, and invalidate workspace Git status when
the job completes.

- [ ] **Step 7: Generate API types and run component tests**

Run: `./debug.sh status | grep -q 'Status:   UP' || ./debug.sh up`

Run: `(cd client && npm run generate:types)`

Run: `./test.sh client unit src/components/solutions/WorkspaceImportReview.test.tsx src/components/solutions/CreateEditSolution.test.tsx src/services/solutions.test.ts`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add client/src/services/solutions.ts client/src/services/solutions.test.ts client/src/components/solutions/WorkspaceImportReview.tsx client/src/components/solutions/WorkspaceImportReview.test.tsx client/src/components/solutions/CreateEditSolution.tsx client/src/components/solutions/CreateEditSolution.test.tsx client/src/components/solutions/InstallSession.tsx client/src/lib/v1.d.ts
git commit -m "feat: add compact workspace import review"
```

### Task 9: Add the browser happy path and final verification

**Files:**
- Create: `client/e2e/solution-workspace-import.admin.spec.ts`
- Modify only implementation files required by failures.

- [ ] **Step 1: Write the Playwright happy path**

```typescript
test("imports a Solution bundle into the workspace", async ({ page }) => {
  await page.goto("/solutions");
  await page.getByRole("button", { name: /install solution/i }).click();
  await page.getByLabel("Workspace import").click();
  await page.getByTestId("install-file-input").setInputFiles("e2e/fixtures/workspace-import.zip");
  await expect(page.getByText("Review workspace import")).toBeVisible();
  await page.getByRole("button", { name: "Replace all" }).click();
  await page.getByRole("button", { name: "Start import job" }).click();
  await expect(page.getByText(/workspace import.*complete/i)).toBeVisible();
  await page.goto("/settings/github");
  await expect(page.getByText("modules/customer_helpers.py")).toBeVisible();
});
```

- [ ] **Step 2: Run focused browser coverage**

Run: `./test.sh client e2e e2e/solution-workspace-import.admin.spec.ts`

Expected: PASS.

- [ ] **Step 3: Run frontend quality**

Run: `(cd client && npm run tsc && npm run lint)`

Expected: PASS.

- [ ] **Step 4: Run backend quality and focused backend coverage**

Run: `./test.sh quality api`

Run: `./test.sh tests/unit/test_workspace_bundle_plan.py tests/unit/test_workspace_bundle_import.py tests/unit/test_workspace_bundle_memory.py tests/unit/test_cli_solution_import_workspace.py tests/e2e/platform/test_workspace_bundle_import.py -v`

Expected: PASS.

- [ ] **Step 5: Run contract tripwires**

Run: `./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py tests/unit/test_manifest.py -v`

Expected: PASS.

- [ ] **Step 6: Commit verification fixes, if any**

```bash
git add api client
git commit -m "test: verify workspace bundle import"
```

Do not create an empty commit when no fixes were needed. Record that full backend
E2E, full Vitest, full Playwright, and `./test.sh pre-pr` were not run unless they
were actually executed.
