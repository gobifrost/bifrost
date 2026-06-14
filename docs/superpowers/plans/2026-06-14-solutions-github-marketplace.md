# Solutions GitHub Marketplace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user install/update a Solution directly from a GitHub repo (or a subfolder of an omni-repo) through the same preview→confirm→install pipeline the zip path uses, with a scheduled update-available signal and a clean connect-later lifecycle.

**Architecture:** A new `repo_subpath` column threads a subfolder through the existing clone→`_parse_workspace`→deploy path. The install-from-repo preview reuses `_parse_workspace` (already extracted) behind a clone front-end. The New-install UI drops empty-shell create in favor of From-repo / From-zip. A scheduler job reads the descriptor `version:` at the repo HEAD, compares PEP-440 to installed, stores a signal + emits a builtin event, surfaced as a badge with a one-click "Update now" (pull + full-replace).

**Tech Stack:** FastAPI, SQLAlchemy (async), Alembic, Pydantic v2, GitPython, APScheduler, React + openapi-react-query, Click (CLI), pytest (`./test.sh`), vitest.

**Spec:** `docs/superpowers/specs/2026-06-14-solutions-github-marketplace-design.md`

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `api/src/models/orm/solutions.py` | add `repo_subpath`, `update_available_version` columns | Modify |
| `api/alembic/versions/<rev>_solution_repo_subpath_update_signal.py` | migration for the two columns | Create |
| `api/bifrost/solution_descriptor.py` | add `repo_subpath` to descriptor | Modify |
| `api/src/models/contracts/solutions.py` | `repo_subpath` on Base/Update; `ref`/`repo_subpath` on a new repo-preview request; `update_available_version` on DTO | Modify |
| `api/src/services/solutions/git_sync.py` | parameterize clone `ref` + subpath; expose a clone-to-dir helper | Modify |
| `api/src/services/solutions/zip_install.py` | (already has `_parse_workspace`) — no behavior change; reused | Reference |
| `api/src/services/solutions/update_check.py` | descriptor-version fetch + PEP-440 compare core | Create |
| `api/src/routers/solutions.py` | `preview-repo` endpoint; install-from-repo; "Update now"; surface signal | Modify |
| `api/src/services/events/builtins.py` | `emit_solution_update_available` | Modify |
| `api/src/jobs/schedulers/solution_update_check.py` | periodic per-install check job | Create |
| `api/src/jobs/schedulers/__init__.py` | register the job | Modify |
| `api/bifrost/commands/solution.py` | `deploy --org` | Modify |
| `client/src/services/solutions.ts` (or equivalent) | repo-preview + install-from-repo + update-now hooks | Modify |
| `client/src/pages/solutions/*` | New-install source picker; Details Connect/Disconnect; Update badge | Modify |
| `api/tests/unit/test_solution_marketplace.py` | unit tests for the new core fns | Create |
| `api/tests/e2e/platform/test_solution_install_from_repo.py` | e2e repo round-trip | Create |

---

## Phase A — Backbone (repo_subpath + install-from-repo preview)

### Task 1: `repo_subpath` + `update_available_version` columns + migration

**Files:**
- Modify: `api/src/models/orm/solutions.py` (near `git_repo_url`, line ~110)
- Create: `api/alembic/versions/<rev>_solution_repo_subpath_update_signal.py`

- [ ] **Step 1: Add the columns to the ORM**

In `api/src/models/orm/solutions.py`, immediately after the `git_repo_url` column:

```python
    # Subfolder within the connected repo holding this solution's
    # bifrost.solution.yaml (omni-repo: one repo, a folder per solution).
    # None/"" => repo root (backward compatible).
    repo_subpath: Mapped[str | None] = mapped_column(String(1024), nullable=True, default=None)

    # Git ref (branch or tag) the connected install tracks. None => the repo's
    # default branch. Lets a consumer pin to a tag while detection still reads
    # the descriptor version: at that ref's HEAD.
    git_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)

    # Newest descriptor version available at the connected repo's ref HEAD, when
    # it is PEP-440-greater than the installed `version`. None => up to date /
    # not git-connected / not yet checked. Written only by the update-check job.
    update_available_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
```

- [ ] **Step 2: Generate the migration**

Run: `cd api && alembic revision -m "solution repo_subpath, git_ref, update_available_version"`
Then edit the generated file's `upgrade()`/`downgrade()`:

```python
def upgrade() -> None:
    op.add_column("solutions", sa.Column("repo_subpath", sa.String(1024), nullable=True))
    op.add_column("solutions", sa.Column("git_ref", sa.String(255), nullable=True))
    op.add_column("solutions", sa.Column("update_available_version", sa.String(64), nullable=True))

def downgrade() -> None:
    op.drop_column("solutions", "update_available_version")
    op.drop_column("solutions", "git_ref")
    op.drop_column("solutions", "repo_subpath")
```

- [ ] **Step 3: Apply the migration to the test stack**

Run: `./test.sh stack up` (boots if needed). The migration runs via the init container. If using an already-up stack, follow the per-worktree migration-apply note (restart `-init-1` then `-api-1`).

- [ ] **Step 4: Verify columns exist**

Run a quick unit test asserting the ORM model has the attributes:

```python
# api/tests/unit/test_solution_marketplace.py
from src.models.orm.solutions import Solution

def test_solution_has_marketplace_columns():
    cols = set(Solution.__table__.columns.keys())
    assert {"repo_subpath", "git_ref", "update_available_version"} <= cols
```

Run: `./test.sh tests/unit/test_solution_marketplace.py::test_solution_has_marketplace_columns -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/src/models/orm/solutions.py api/alembic/versions/ api/tests/unit/test_solution_marketplace.py
git commit -m "feat(solutions): repo_subpath, git_ref, update_available_version columns"
```

### Task 2: Descriptor + DTO carry `repo_subpath` / `git_ref`

**Files:**
- Modify: `api/bifrost/solution_descriptor.py:43-44`
- Modify: `api/src/models/contracts/solutions.py` (`SolutionBase` ~18, `SolutionUpdate` ~46)

- [ ] **Step 1: Write the failing descriptor round-trip test**

```python
# api/tests/unit/test_solution_marketplace.py (append)
from bifrost.solution_descriptor import SolutionDescriptor

def test_descriptor_carries_repo_subpath_and_ref():
    d = SolutionDescriptor(slug="s", name="S", repo_subpath="microsoft-csp", git_ref="v1.2.0")
    assert d.repo_subpath == "microsoft-csp"
    assert d.git_ref == "v1.2.0"
    d2 = SolutionDescriptor(slug="s", name="S")
    assert d2.repo_subpath is None and d2.git_ref is None
```

Run: `./test.sh tests/unit/test_solution_marketplace.py::test_descriptor_carries_repo_subpath_and_ref -v`
Expected: FAIL (unexpected keyword / attribute missing)

- [ ] **Step 2: Add fields to the descriptor**

In `api/bifrost/solution_descriptor.py`, after `git_repo_url: str | None = None` (line 44):

```python
    # Subfolder of the connected repo holding this descriptor (omni-repo).
    # None => repo root. Set on the install at create/deploy/connect time.
    repo_subpath: str | None = None
    # Git ref (branch/tag) the install tracks. None => default branch.
    git_ref: str | None = None
```

- [ ] **Step 3: Add fields to the contracts**

In `api/src/models/contracts/solutions.py`, in `SolutionBase` (after `git_repo_url: str | None = None`, line ~20):

```python
    repo_subpath: str | None = None
    git_ref: str | None = None
```

In `SolutionUpdate` (after `git_repo_url: str | None = None`, line ~50):

```python
    repo_subpath: str | None = None
    git_ref: str | None = None
```

- [ ] **Step 4: Verify the test passes**

Run: `./test.sh tests/unit/test_solution_marketplace.py::test_descriptor_carries_repo_subpath_and_ref -v`
Expected: PASS

- [ ] **Step 5: Wire create-on-deploy + create endpoint to persist them**

In `api/src/routers/solutions.py` `create_solution` (line ~90), add to the `SolutionORM(...)` kwargs:

```python
        repo_subpath=body.repo_subpath,
        git_ref=body.git_ref,
```

In `api/bifrost/commands/solution.py` `deploy_cmd`'s create POST body (line ~1091), add:

```python
                    "repo_subpath": descriptor.repo_subpath,
                    "git_ref": descriptor.git_ref,
```

- [ ] **Step 6: Run DTO-parity + contract tripwire, refresh fingerprint if needed**

Run: `./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v`
If `test_contract_version` fails: the change is **additive** (new optional fields) → refresh `EXPECTED_CONTRACT_FINGERPRINT` only (no `CONTRACT_VERSION` bump). If `test_dto_flags` fails, add the new fields to `DTO_EXCLUDES` in `api/bifrost/dto_flags.py` with a comment (`# repo connection metadata, set by connect flow, not a content field`) OR surface on the relevant CLI command — prefer the exclude here (these are connection-state fields).

- [ ] **Step 7: Commit**

```bash
git add api/bifrost/solution_descriptor.py api/src/models/contracts/solutions.py api/src/routers/solutions.py api/bifrost/commands/solution.py api/bifrost/dto_flags.py api/tests/unit/test_solution_marketplace.py api/tests/unit/test_contract_version.py
git commit -m "feat(solutions): descriptor + DTO carry repo_subpath/git_ref"
```

### Task 3: Parameterize the git clone (ref + clone-to-dir helper)

**Files:**
- Modify: `api/src/services/solutions/git_sync.py` (clone block ~218-231)

- [ ] **Step 1: Write the failing test for the clone helper signature**

```python
# api/tests/unit/test_solution_marketplace.py (append)
import inspect
from src.services.solutions import git_sync

def test_clone_helper_exists_with_ref_param():
    fn = getattr(git_sync, "clone_repo_to_dir", None)
    assert fn is not None, "clone_repo_to_dir helper missing"
    params = inspect.signature(fn).parameters
    assert {"repo_url", "dest", "ref"} <= set(params)
```

Run: `./test.sh tests/unit/test_solution_marketplace.py::test_clone_helper_exists_with_ref_param -v`
Expected: FAIL (helper missing)

- [ ] **Step 2: Extract a clone-to-dir helper, ref-parameterized**

In `api/src/services/solutions/git_sync.py`, add a module-level async helper and call it from the existing clone block:

```python
async def clone_repo_to_dir(repo_url: str, dest: Path, ref: str | None = None) -> None:
    """Shallow-clone ``repo_url`` (optionally at ``ref``) into ``dest``, off the
    event loop. ``ref`` None => the remote's default branch (GitPython resolves
    HEAD); a branch or tag name otherwise."""
    from git import Repo as GitRepo  # GitPython (already a dep)

    kwargs: dict[str, object] = {"depth": 1}
    if ref:
        kwargs["branch"] = ref  # GitPython --branch accepts a tag or branch
    await asyncio.to_thread(GitRepo.clone_from, repo_url, str(dest), **kwargs)
```

Then replace the inline `await asyncio.to_thread(GitRepo.clone_from, repo_url, str(work_dir), branch="main", depth=1)` (lines ~224-230) with:

```python
        await clone_repo_to_dir(repo_url, work_dir, ref=solution.git_ref)
```

(Drop the now-unused `from git import Repo as GitRepo` at line 214 if nothing else in the function uses it.)

- [ ] **Step 3: Thread `repo_subpath` into the deploy workspace root**

Immediately after the clone in the same function, set the workspace to the subpath when present:

```python
        work_dir = Path(tmp)
        await clone_repo_to_dir(repo_url, work_dir, ref=solution.git_ref)
        deploy_root = work_dir / solution.repo_subpath if solution.repo_subpath else work_dir
        if not (deploy_root / _DESCRIPTOR_FILENAME).is_file():
            raise NotASolutionWorkspace(
                f"No {_DESCRIPTOR_FILENAME} at "
                f"{solution.repo_subpath or '<repo root>'} in {repo_url}"
            )
        logger.info("Cloned connected solution %s from %s", solution.id, solution.git_repo_url)
        result = await deploy_from_workspace(db, solution, deploy_root)
```

(`NotASolutionWorkspace` is the existing exception at line 44 — confirm the name; if it differs, use the existing one.)

- [ ] **Step 4: Verify the helper test passes**

Run: `./test.sh tests/unit/test_solution_marketplace.py::test_clone_helper_exists_with_ref_param -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/src/services/solutions/git_sync.py api/tests/unit/test_solution_marketplace.py
git commit -m "feat(solutions): ref-parameterized clone + repo_subpath deploy root"
```

### Task 4: `preview-repo` endpoint (clone → _parse_workspace → preview)

**Files:**
- Modify: `api/src/models/contracts/solutions.py` (add `SolutionRepoPreviewRequest`)
- Modify: `api/src/routers/solutions.py` (new endpoint, after `install_preview` ~1118)

- [ ] **Step 1: Add the request DTO**

In `api/src/models/contracts/solutions.py`:

```python
class SolutionRepoPreviewRequest(BaseModel):
    """Resolve an install plan from a git repo (+ optional subfolder/ref).
    Parse-only — no DB write, no S3, no build."""

    repo_url: str = Field(min_length=1, max_length=1024)
    repo_subpath: str | None = None
    git_ref: str | None = None
```

- [ ] **Step 2: Write the failing e2e test (preview a local fixture repo)**

```python
# api/tests/e2e/platform/test_solution_install_from_repo.py
import subprocess, textwrap
from pathlib import Path

def _make_fixture_repo(tmp_path: Path, subdir: str = "") -> str:
    root = tmp_path / "repo"
    sol = root / subdir if subdir else root
    sol.mkdir(parents=True)
    (sol / "bifrost.solution.yaml").write_text(textwrap.dedent("""
        slug: fixture-sol
        name: Fixture Solution
        version: 1.0.0
        scope: org
    """).lstrip())
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=root, check=True)
    return f"file://{root}"

async def test_preview_repo_resolves_descriptor(async_client, superuser_headers, tmp_path):
    repo_url = _make_fixture_repo(tmp_path, subdir="microsoft-csp")
    resp = await async_client.post(
        "/api/solutions/install/preview-repo",
        json={"repo_url": repo_url, "repo_subpath": "microsoft-csp"},
        headers=superuser_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slug"] == "fixture-sol"
    assert body["version"] == "1.0.0"
```

(Use whatever the suite's actual superuser-client fixtures are named; mirror an existing test in `test_solution_*` for fixture names.)

Run: `./test.sh e2e tests/e2e/platform/test_solution_install_from_repo.py` (note: `./test.sh e2e <path>` runs the whole e2e suite per the test.sh quirk; to run just this, follow the worktree workaround in memory if needed).
Expected: FAIL (404 — endpoint missing)

- [ ] **Step 3: Implement the endpoint**

In `api/src/routers/solutions.py`, after `install_preview`:

```python
@router.post(
    "/install/preview-repo",
    response_model=SolutionInstallPreview,
    summary="Preview a Solution install from a git repo (parse-only, admin only)",
)
async def install_preview_repo(
    body: SolutionRepoPreviewRequest, ctx: Context, user: CurrentSuperuser
) -> SolutionInstallPreview:
    """Clone the repo (+ optional subpath/ref), parse the workspace, and report
    the install plan — the same plan the zip preview returns. No DB write."""
    import tempfile
    from pathlib import Path
    from src.services.solutions.git_sync import clone_repo_to_dir
    from src.services.solutions.zip_install import (
        _parse_workspace, compute_upgrade_diff, find_install,
    )

    with tempfile.TemporaryDirectory(prefix="bifrost-repo-preview-") as tmp:
        work = Path(tmp)
        try:
            await clone_repo_to_dir(body.repo_url, work, ref=body.git_ref)
        except Exception as exc:  # GitPython GitCommandError etc.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Could not clone {body.repo_url}: {exc}",
            ) from exc
        root = work / body.repo_subpath if body.repo_subpath else work
        if not (root / "bifrost.solution.yaml").is_file():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"No bifrost.solution.yaml at "
                       f"{body.repo_subpath or '<repo root>'} in {body.repo_url}",
            )
        result = _parse_workspace(root)
    # existing-install detection mirrors install_preview (the same block).
    return await _preview_to_dto(ctx, result)
```

Extract the response-assembly + existing-install/diff block at the END of `install_preview` (lines ~1071-1118, the `existing_install`/`diff` lookup + `return SolutionInstallPreview(...)`) into a shared helper `_preview_to_dto(ctx, result, organization_id=None)` and have BOTH `install_preview` and `install_preview_repo` call it. This keeps the upgrade-routing logic identical and DRY.

- [ ] **Step 4: Verify the e2e test passes**

Run the e2e as in Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/models/contracts/solutions.py api/src/routers/solutions.py api/tests/e2e/platform/test_solution_install_from_repo.py
git commit -m "feat(solutions): install/preview-repo endpoint (clone -> parse -> plan)"
```

### Task 5: Install-from-repo (commit the connected install)

**Files:**
- Modify: `api/src/routers/solutions.py` (new `install_from_repo` endpoint)

- [ ] **Step 1: Write the failing e2e test (full install from repo)**

```python
# append to test_solution_install_from_repo.py
async def test_install_from_repo_creates_connected_install(async_client, superuser_headers, tmp_path):
    repo_url = _make_fixture_repo(tmp_path, subdir="microsoft-csp")
    resp = await async_client.post(
        "/api/solutions/install/from-repo",
        json={"repo_url": repo_url, "repo_subpath": "microsoft-csp"},
        headers=superuser_headers,
    )
    assert resp.status_code in (200, 201), resp.text
    sol = resp.json()
    assert sol["git_connected"] is True
    assert sol["repo_subpath"] == "microsoft-csp"
    # deploy must now be refused (pull is the writer)
    dep = await async_client.post(f"/api/solutions/{sol['id']}/deploy", json={}, headers=superuser_headers)
    assert dep.status_code == 409
```

Run the e2e. Expected: FAIL (404).

- [ ] **Step 2: Implement `install_from_repo`**

In `api/src/routers/solutions.py`, after `install_preview_repo`. It: parses the descriptor (reuse the clone+parse to get slug/name/scope), creates the `Solution` row with `git_connected=True`, `git_repo_url`, `repo_subpath`, `git_ref`, then triggers `git_sync.sync(...)` to clone+deploy. Return the `SolutionDTO`.

```python
@router.post(
    "/install/from-repo",
    response_model=SolutionDTO,
    status_code=status.HTTP_201_CREATED,
    summary="Install a Solution from a git repo (git-connected, admin only)",
)
async def install_from_repo(
    body: SolutionRepoPreviewRequest, ctx: Context, user: CurrentSuperuser
) -> SolutionDTO:
    import tempfile
    from pathlib import Path
    from src.services.solutions.git_sync import clone_repo_to_dir, sync
    from src.services.solutions.zip_install import _parse_workspace, find_install

    with tempfile.TemporaryDirectory(prefix="bifrost-repo-install-") as tmp:
        work = Path(tmp)
        try:
            await clone_repo_to_dir(body.repo_url, work, ref=body.git_ref)
        except Exception as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=f"Could not clone {body.repo_url}: {exc}") from exc
        root = work / body.repo_subpath if body.repo_subpath else work
        parsed = _parse_workspace(root)
    if not parsed.slug:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Repo has no valid bifrost.solution.yaml")
    # Resolve-or-reject existing install for (slug, scope) in the caller's org —
    # an existing one should go through upgrade, not a duplicate create.
    org_id = ctx.org_id if parsed.scope == "org" else None
    existing = await find_install(ctx.db, slug=parsed.slug, organization_id=org_id)
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail=f"An install of '{parsed.slug}' already exists; "
                                   f"reconnect or update it instead.")
    solution = SolutionORM(
        slug=parsed.slug, name=parsed.name or parsed.slug,
        scope=parsed.scope or "org", organization_id=org_id,
        git_connected=True, git_repo_url=body.repo_url,
        repo_subpath=body.repo_subpath, git_ref=body.git_ref,
    )
    ctx.db.add(solution)
    await ctx.db.commit()
    await ctx.db.refresh(solution)
    await sync(ctx.db, solution)  # clone + full-replace deploy
    await ctx.db.refresh(solution)
    return _solution_to_dto(solution)
```

(Use the existing DTO-mapper — check how `create_solution`/`get_solution` build their `SolutionDTO`; reuse that exact helper instead of `_solution_to_dto` if it's named differently.)

- [ ] **Step 3: Verify the e2e passes**

Run the e2e. Expected: PASS (install created, git_connected, deploy 409).

- [ ] **Step 4: Commit**

```bash
git add api/src/routers/solutions.py api/tests/e2e/platform/test_solution_install_from_repo.py
git commit -m "feat(solutions): install/from-repo (git-connected from birth)"
```

---

## Phase B — CLI (`deploy --org`)

### Task 6: `bifrost solution deploy --org`

**Files:**
- Modify: `api/bifrost/commands/solution.py` (`deploy_cmd` ~1032; `_resolve_target_install` ~980)

- [ ] **Step 1: Add the `--org` option + resolve org**

In `deploy_cmd`, add the option decorator above the function:

```python
@click.option("--org", "org", default=None, help="Target org (id or slug) to resolve-or-create the install in (default: your org).")
```

Add `org: str | None` to the signature. Inside `_run()`, after `client = BifrostClient.get_instance(...)`, resolve the target org id when `--org` is given (look up by id or slug via `GET /api/organizations` — mirror how other `--org` commands resolve; grep `--org` in `api/bifrost/commands/` for the existing resolver helper and reuse it). Use the resolved id as `deployer_org_id` passed to `_resolve_target_install`, and as `organization_id` in the create POST body.

- [ ] **Step 2: Write a unit test for org resolution in `_resolve_target_install`**

`_resolve_target_install` already takes `deployer_org_id` — the test confirms an explicit org targets that org's install, not the caller's:

```python
# api/tests/unit/test_solution_marketplace.py (append)
from bifrost.commands.solution import _resolve_target_install

def test_resolve_targets_explicit_org():
    installs = [
        {"id": "a", "slug": "s", "scope": "org", "organization_id": "org-A"},
        {"id": "b", "slug": "s", "scope": "org", "organization_id": "org-B"},
    ]
    assert _resolve_target_install(installs, "s", "org", "org-B") == "b"
    assert _resolve_target_install(installs, "s", "org", "org-A") == "a"
```

Run: `./test.sh tests/unit/test_solution_marketplace.py::test_resolve_targets_explicit_org -v`
Expected: PASS (logic already supports it; this locks the contract that `--org` feeds it).

- [ ] **Step 3: Manually verify the flag wiring** (no network in unit): `cd api && python -c "from bifrost.commands.solution import deploy_cmd; print([p.name for p in deploy_cmd.params])"` — expect `org` present.

- [ ] **Step 4: Commit**

```bash
git add api/bifrost/commands/solution.py api/tests/unit/test_solution_marketplace.py
git commit -m "feat(cli): bifrost solution deploy --org targets an explicit org"
```

---

## Phase C — Drive (risk gate) — build the CSP omni-repo fixture, drive install + server-side build

### Task 7: Build the microsoft-csp omni-repo fixture (source-only)

**Files:**
- Create: a local fixture repo OUTSIDE the worktree (e.g. `/tmp/bifrost-omnirepo/`) with `microsoft-csp/` — descriptor (2 declared connection schemas), an app with TSX source (NO committed `dist/`), 2+ shared modules, a README + setup, and `rtm-portal/` as a second folder. Source-adapt from `../bifrost-workspace/apps/microsoft-csp` and `../bifrost-workspace/solutions/rtm-portal` — strip ALL client-specific names/secrets/IDs (generic placeholders only; the repo is public-adjacent).

- [ ] **Step 1:** Boot the debug stack: `BIFROST_FORCE_PORT=1 ./debug.sh up` (port mode — Chrome can't drive netbird). Capture URL from `./debug.sh status`.
- [ ] **Step 2:** Scaffold the omni-repo: two folders, each a valid solution workspace with its own `bifrost.solution.yaml`; microsoft-csp has `version: 1.0.0`, scope org, 2 `connection_schemas`, an app under `apps/` with TSX source and no `dist/`, 2 shared modules under `modules/`. `git init` + commit. This is the fixture, not committed to the bifrost repo.
- [ ] **Step 3:** Document the fixture's structure in `docs/plans/2026-06-14-solutions-github-story-findings.md` (create it) under "Fixture".
- [ ] **Step 4: Commit** the findings doc skeleton (the fixture itself is external, uncommitted):

```bash
git add docs/plans/2026-06-14-solutions-github-story-findings.md
git commit -m "docs(solutions): GitHub-story drive findings skeleton + fixture notes"
```

### Task 8: Drive install-from-repo + server-side source build (the risk gate)

- [ ] **Step 1:** Connect the CLI from a scratch dir (per CLAUDE.md "Spinning up the dev environment"): install the API-matched CLI, log in.
- [ ] **Step 2:** Drive **install-from-repo** for `microsoft-csp`: `POST /api/solutions/install/from-repo` with `repo_url=file:///tmp/bifrost-omnirepo/.git` (or a real GitHub repo if you push the fixture), `repo_subpath=microsoft-csp`. Confirm the install is created, git_connected, and the app **built from source server-side** (no committed dist) — check the app renders.
- [ ] **Step 3:** Record every friction point in the findings doc: clone auth, subpath resolution, server-side build success/failure (missing deps?), connection-shell creation, Setup wizard state. **If the server-side build fails for a real reason, STOP and fix it** (Task 8b, ad hoc) before proceeding — this is the gate.
- [ ] **Step 4:** Verify the deploy-refused invariant holds for the subpath install (CLI `bifrost solution deploy` against it → 409 / git-connected message).
- [ ] **Step 5: Commit** findings updates.

```bash
git add docs/plans/2026-06-14-solutions-github-story-findings.md
git commit -m "docs(solutions): drive findings — install-from-repo + server-side build"
```

---

## Phase D — UI + update signal

### Task 9: Update-check core (descriptor-version fetch + PEP-440 compare)

**Files:**
- Create: `api/src/services/solutions/update_check.py`

- [ ] **Step 1: Write the failing unit test**

```python
# api/tests/unit/test_solution_marketplace.py (append)
from src.services.solutions.update_check import compute_update_available

def test_compute_update_available():
    assert compute_update_available(installed="1.0.0", remote="1.1.0") == "1.1.0"
    assert compute_update_available(installed="1.1.0", remote="1.1.0") is None
    assert compute_update_available(installed="1.2.0", remote="1.1.0") is None  # remote older
    assert compute_update_available(installed=None, remote="1.0.0") == "1.0.0"
    assert compute_update_available(installed="1.0.0", remote="not-a-version") is None
```

Run: `./test.sh tests/unit/test_solution_marketplace.py::test_compute_update_available -v`
Expected: FAIL (module missing)

- [ ] **Step 2: Implement the compare core**

```python
# api/src/services/solutions/update_check.py
"""Update-available detection for git-connected Solution installs.

The remote version is the ``version:`` field of bifrost.solution.yaml at the
connected ref's HEAD — NOT a git tag. A repo-wide tag cannot version N
solutions in an omni-repo's subfolders, so detection is descriptor-driven and
needs no release ceremony (authors just bump ``version:``)."""
from __future__ import annotations

from packaging.version import InvalidVersion, Version


def compute_update_available(*, installed: str | None, remote: str | None) -> str | None:
    """Return ``remote`` if it is a clean PEP-440 increment over ``installed``,
    else None. Unparseable remote, or remote <= installed, => None (no signal)."""
    if not remote:
        return None
    try:
        rv = Version(remote)
    except InvalidVersion:
        return None
    if installed is None:
        return remote
    try:
        iv = Version(installed)
    except InvalidVersion:
        # installed unparseable => treat any parseable remote as available
        return remote
    return remote if rv > iv else None
```

- [ ] **Step 3: Verify the test passes**

Run: `./test.sh tests/unit/test_solution_marketplace.py::test_compute_update_available -v`
Expected: PASS

- [ ] **Step 4: Add a remote-version fetch (clone-light)**

Append a function that reads the remote descriptor version. Reuse `clone_repo_to_dir` (shallow) into a temp dir and read the descriptor `version:` at the subpath — simplest, correct, and consistent with preview:

```python
async def fetch_remote_version(
    *, repo_url: str, repo_subpath: str | None, ref: str | None
) -> str | None:
    """Shallow-clone and read the descriptor ``version:`` at the subpath.
    Returns None if the repo/descriptor can't be read (logged by the caller)."""
    import tempfile
    from pathlib import Path
    from src.services.solutions.git_sync import clone_repo_to_dir
    from bifrost.solution_descriptor import is_solution_workspace, load_descriptor

    with tempfile.TemporaryDirectory(prefix="bifrost-update-check-") as tmp:
        work = Path(tmp)
        await clone_repo_to_dir(repo_url, work, ref=ref)
        root = work / repo_subpath if repo_subpath else work
        if not is_solution_workspace(root):
            return None
        return load_descriptor(root).version
```

- [ ] **Step 5: Commit**

```bash
git add api/src/services/solutions/update_check.py api/tests/unit/test_solution_marketplace.py
git commit -m "feat(solutions): update-check core (descriptor-version PEP-440 compare)"
```

### Task 10: `solution.update_available` builtin event

**Files:**
- Modify: `api/src/services/events/builtins.py` (mirror `emit_integration_refresh_failed` ~268)

- [ ] **Step 1:** Read `emit_integration_refresh_failed` (line ~268) to copy its exact emit shape (topic name, payload dict, `emit_topic` call).
- [ ] **Step 2: Add the emitter**, mirroring that function:

```python
async def emit_solution_update_available(
    db, *, solution_id, slug: str, installed_version: str | None, available_version: str
) -> None:
    """Emit when a git-connected install newly detects a newer descriptor version."""
    # mirror emit_integration_refresh_failed: build topic + payload, call processor.emit_topic
    ...  # exact body copies the sibling emitter; topic e.g. "solution.update_available"
```

(Fill the body to match the sibling exactly — same processor call, same payload-builder pattern.)

- [ ] **Step 3:** If builtin event topics are enumerated/registered anywhere (grep for the sibling topic string `integration.refresh_failed`), add `solution.update_available` to the same registry/list so it's subscribable.
- [ ] **Step 4: Commit**

```bash
git add api/src/services/events/builtins.py
git commit -m "feat(solutions): solution.update_available builtin event"
```

### Task 11: Scheduler job `solution_update_check`

**Files:**
- Create: `api/src/jobs/schedulers/solution_update_check.py` (mirror `oauth_token_refresh.py`)
- Modify: `api/src/jobs/schedulers/__init__.py`

- [ ] **Step 1:** Read `api/src/jobs/schedulers/oauth_token_refresh.py` for the exact job shape (async fn, session acquisition, query loop, return dict) and how `__init__.py` + the APScheduler registration wire interval jobs.
- [ ] **Step 2: Implement the job**: query all `Solution` where `git_connected is True and git_repo_url is not None`; for each, `fetch_remote_version(...)`, `compute_update_available(installed=solution.version, remote=...)`; if the result differs from the stored `update_available_version`, update it; if it **newly** became non-None (was None, now set), call `emit_solution_update_available(...)`. Wrap each install in try/except so one bad repo doesn't abort the sweep (log + continue). Return `{"checked": n, "updates_found": k}`.
- [ ] **Step 3: Register** the job in `__init__.py` + the scheduler setup with a sensible interval (mirror oauth refresh cadence; e.g. every 6h — match the spec's "periodic"). Add it to the `__all__`.
- [ ] **Step 4: Unit-test the diff/emit-once logic** with a fake session + monkeypatched `fetch_remote_version`:

```python
# test that a None->version transition emits once, version->same does not re-emit
```

Run the targeted unit test. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/jobs/schedulers/solution_update_check.py api/src/jobs/schedulers/__init__.py api/tests/unit/test_solution_marketplace.py
git commit -m "feat(solutions): scheduled update-check job (badge + event signal)"
```

### Task 12: Surface `update_available_version` on the DTO + "Update now" endpoint

**Files:**
- Modify: `api/src/models/contracts/solutions.py` (`SolutionDTO` — add `update_available_version`)
- Modify: `api/src/routers/solutions.py` (DTO-mapper + an `update-now` endpoint)

- [ ] **Step 1:** Add `update_available_version: str | None = None` to `SolutionDTO` and set it in the DTO-mapper from the ORM field.
- [ ] **Step 2: Add the "Update now" endpoint** (git-connected only): `POST /api/solutions/{id}/update` → guard `git_connected`, call `git_sync.sync(db, solution)` (pull + full-replace), then clear `update_available_version`, commit, return DTO. Refuse (409) if not git_connected.

```python
@router.post("/{solution_id}/update", response_model=SolutionDTO,
             summary="Pull + full-replace a git-connected install to its repo HEAD (admin only)")
async def update_solution_now(solution_id: UUID, ctx: Context, user: CurrentSuperuser) -> SolutionDTO:
    solution = await ctx.db.get(SolutionORM, solution_id)
    if solution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Solution not found")
    if not solution.git_connected:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Not git-connected; nothing to pull.")
    from src.services.solutions.git_sync import sync
    await sync(ctx.db, solution)
    solution.update_available_version = None
    await ctx.db.commit()
    await ctx.db.refresh(solution)
    return _solution_to_dto(solution)
```

- [ ] **Step 3:** Run the contract tripwire (`update_available_version` is additive on `SolutionDTO`) → refresh fingerprint only.

Run: `./test.sh tests/unit/test_contract_version.py -v`

- [ ] **Step 4: Commit**

```bash
git add api/src/models/contracts/solutions.py api/src/routers/solutions.py api/tests/unit/test_contract_version.py
git commit -m "feat(solutions): surface update_available_version + POST .../update (Update now)"
```

### Task 13: Regenerate types + client hooks

**Files:**
- Modify: `client/src/lib/v1.d.ts` (generated)
- Modify: `client/src/services/solutions.ts` (or the actual solutions service file)

- [ ] **Step 1:** With the debug stack up, regenerate types: `cd client && OPENAPI_URL=<dev-url>/openapi.json npm run generate:types`.
- [ ] **Step 2:** Add service wrappers/hooks for: `install/preview-repo`, `install/from-repo`, `{id}/update`, and the existing PATCH for connect/disconnect. Add sibling `*.test.ts` covering the new wrappers' request shape.
- [ ] **Step 3:** Run vitest for the service file: `./test.sh client unit`. Expected: PASS.
- [ ] **Step 4: Commit**

```bash
git add client/src/lib/v1.d.ts client/src/services/
git commit -m "feat(client): types + service hooks for repo install/update"
```

### Task 14: New-install source picker (From-repo / From-zip; drop empty-shell create)

**Files:**
- Modify: the solutions New-install page/component (grep `git_repo_url` / the create form in `client/src/pages/solutions/` or `client/src/components/`)

- [ ] **Step 1:** Replace the create flow with a source picker: **From a repository** (repo_url + optional subpath + optional ref → calls `preview-repo` → read-only confirmation card → "Install" calls `from-repo`) and **From a zip** (existing drag-and-drop). Remove the empty-shell "create" path.
- [ ] **Step 2:** Build the read-only confirmation card: name, version, scope, entity counts, declared configs/connections, and upgrade-vs-fresh (from the preview's existing-install/diff fields).
- [ ] **Step 3:** Support the deep link `/solutions/new?repo=...&path=...&ref=...` pre-filling the From-repository form (read query params on mount).
- [ ] **Step 4:** Add vitest for the source picker + confirmation card (renders preview fields, calls the right mutation).
- [ ] **Step 5:** Run `./test.sh client unit`, `cd client && npm run tsc && npm run lint`. Expected: PASS/clean.
- [ ] **Step 6: Commit**

```bash
git add client/src/
git commit -m "feat(client): New-install source picker (From-repo/From-zip), deep link, drop empty-shell create"
```

### Task 15: Details view — Connect / Reconnect / Disconnect + Update badge

**Files:**
- Modify: the per-install detail/edit component

- [ ] **Step 1:** Rename "Edit" → "Details". Add a repository-connection section: **Connect repository** (disconnected install: PATCH `git_repo_url`+`repo_subpath`+`git_ref`, `git_connected=true`), **Reconnect** (change those on a connected install), **Disconnect** (`git_connected=false`). Reuse the existing `PATCH /api/solutions/{id}`.
- [ ] **Step 2:** Add the **"Update Available" badge** (when `update_available_version` is set) on the catalog card + Details, with an **"Update now"** button → `POST {id}/update` (with a confirm dialog).
- [ ] **Step 3:** Add vitest covering connect/disconnect actions + the badge + Update-now confirm.
- [ ] **Step 4:** `./test.sh client unit`, `npm run tsc`, `npm run lint`. Expected: PASS/clean.
- [ ] **Step 5: Commit**

```bash
git add client/src/
git commit -m "feat(client): Details view Connect/Reconnect/Disconnect + Update Available badge"
```

---

## Phase E — Remaining drive + findings

### Task 16: Drive upgrade, update-signal, connect-later, DR

- [ ] **Step 1: Upgrade** — bump `microsoft-csp` descriptor to `1.1.0` in the fixture repo, commit. Trigger the update-check job (or call it directly). Confirm `update_available_version=1.1.0`, badge appears, `solution.update_available` event fired. Click "Update now" → install at 1.1.0. Record in findings.
- [ ] **Step 2: Connect-later** — `bifrost solution deploy` a disconnected install from the CLI (create-on-deploy), then **Connect repository** in Details → confirm a subsequent pull works and deploy is then refused. Record.
- [ ] **Step 3: DR** — full backup export (encrypted secrets + table data) of the CSP install → install into a clean instance (second debug stack or wiped DB) → confirm everything materializes (entities, configs, table data, secrets decrypt). Map the CLI/API DR runbook in the findings. Record any gaps.
- [ ] **Step 4:** Write the findings doc's recommendations section: what's solved, what's deferred (additive-update mode, push/webhook detection, platform discovery API), with a recommended next-phase priority.
- [ ] **Step 5: Commit**

```bash
git add docs/plans/2026-06-14-solutions-github-story-findings.md
git commit -m "docs(solutions): drive findings — upgrade, update signal, connect-later, DR"
```

---

## Phase F — Full verification

### Task 17: Pre-completion verification sweep

- [ ] **Step 1:** Backend: `cd api && pyright && ruff check .` → 0 errors / all-passed.
- [ ] **Step 2:** Frontend: `cd client && npm run generate:types && npm run tsc && npm run lint` → clean.
- [ ] **Step 3:** Backend tests: `./test.sh all` → green (note the 3 known pre-existing failures: SafeHTMLRenderer, app_logo, export_404 — leave them; confirm no NEW failures).
- [ ] **Step 4:** Client tests: `./test.sh client unit` and (if UI changed materially) `./test.sh client e2e`.
- [ ] **Step 5:** Run the DTO/contract tripwires once more: `./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py`. Ensure fingerprint refreshed, no missed `CONTRACT_VERSION` bump (all changes were additive → fingerprint-only).
- [ ] **Step 6: Final commit** if anything outstanding; summarize the arc.

---

## Notes / gotchas (carried from memory + CLAUDE.md)

- **Worktree only.** All work in `solutions-success-criteria`; never touch the primary checkout.
- **`./test.sh` quirks:** JUnit XML at `/tmp/bifrost-<project>/test-results.xml`; `./test.sh e2e <path>` runs the whole suite — to run one e2e file use the per-worktree workaround in memory (`project_test_stack_api_exit_flake`). Never run 2 concurrent `./test.sh` in one worktree.
- **Migrations don't auto-apply** to a live debug DB — restart `-init-1` then `-api-1` (memory `project_debug_stack_migration_apply`).
- **Chrome needs port mode:** `BIFROST_FORCE_PORT=1 ./debug.sh up` for any browser drive.
- **No client specifics in the public repo** — the CSP fixture uses generic names only.
- **Draft PR #347 stays draft** — do not push/merge/un-draft without explicit say-so.
- **Thin-wrapper + manifest sync:** if any DTO field should round-trip in portable exports, update `api/bifrost/manifest.py` + `portable.py` scrub rules and `docs/llm.txt` (the connection-state fields here are install-local, likely excluded — confirm against `test_dto_flags`).
