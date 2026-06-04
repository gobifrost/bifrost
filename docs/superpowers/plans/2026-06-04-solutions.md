# Solutions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add "Solutions" — scoped, deployable, read-only installable surfaces of Bifrost functionality — on top of the unchanged ad-hoc `_repo/` workspace, proven end-to-end against the real `bifrost-workspace`.

**Architecture:** A nullable `solution_id` makes any workflow/app/form/agent/table "solution-managed" (read-only on platform; one writer per install — `bifrost deploy` OR git-connected auto-pull). Python source installs to a new S3 `_solutions/{solution_id}/` prefix; the per-execution virtual-import root is set from the running workflow's `solution_id` (fallback to `_repo/` only when global-repo-access is on). React app `src/` is build-input only — deploy builds it and ships `dist/` to `_apps/`. Reuses the existing split-manifest import/export machinery; a `bifrost.solution.yaml` descriptor indexes it.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Alembic / Pydantic, PostgreSQL + Redis + S3 (SeaweedFS), React/Vite/TS, the `bifrost` CLI (`api/bifrost/`), `./test.sh` + `./debug.sh`.

**Source of truth (intent):** `docs/plans/2026-06-04-solutions-success-criteria.md` — the 18 success criteria below trace to it.

---

## Scope & Sequencing (NOT phasing-out)

This is **one continuous effort to all 18 success criteria.** The sub-plans below are an *implementation order* (you cannot enforce read-only on a column that does not exist; you cannot auto-pull a deploy that is not built), not a subset to ship and stop at. Each sub-plan ends at a working, tested increment so an interrupted run leaves a coherent system.

| # | Sub-plan | Proves criteria | Depth in this doc |
|---|---|---|---|
| 0 | **Headless deploy/sync path** (no interactive sync-selection prompt) | 17 | **Full** |
| 1 | **Core: `solution_id`, `_solutions/` storage, per-execution import root, deploy=full-replace, descriptor** | 1,2,3,4,8,9,10,14,16 | **Full** |
| 2 | **Read-only enforcement + editable carve-out** | 6,7 | Roadmap → expand at task time |
| 3 | **Tables: schema/policies from solution, data preserved** | 11 | Roadmap → expand at task time |
| 4 | **Export Solution + shared-dep vendoring scan; prereq scrub fixes** | 5, prereqs | Roadmap → expand at task time |
| 5 | **Git-connected mode (auto-pull, deploy disabled)** | 13 | Roadmap → expand at task time |
| 6 | **React apps: build → dist → `_apps/`; `npm run dev` loop** | 12 | Roadmap → expand (likely own sub-spec) |
| 7 | **Offline dev loop: local exec, live data-plane** | 15 | Roadmap → expand at task time |
| 8 | **End-to-end proof on real `bifrost-workspace` + full verification** | 18 + all | Roadmap → expand at task time |

> Later sub-plans are intentionally roadmap-only here: their exact steps depend on outcomes of earlier tasks (e.g. the descriptor schema settled in Sub-plan 1 dictates the export format in Sub-plan 4). Expand each to full bite-sized depth when reached — this is continuation of the same effort, not re-planning.

---

## Conventions (read once)

- **Worktree:** all work in this worktree (`solutions-success-criteria`). Never edit the primary checkout.
- **Tests:** `./test.sh` manages the Dockerized stack. `./test.sh stack up` once, then `./test.sh tests/unit/test_x.py::test_y -v`. Backend logic → `api/tests/unit/`; endpoint/exec → `api/tests/e2e/`. JUnit XML at `/tmp/bifrost-<project>/test-results.xml`.
- **Migrations:** create with `cd api && alembic revision -m "..."`; apply by restarting `bifrost-init` then `api` (NOT api alone — hot reload does not run alembic).
- **DTO parity:** any `XxxCreate`/`XxxUpdate` change must pass `./test.sh tests/unit/test_dto_flags.py` (add field to CLI+MCP or to `DTO_EXCLUDES`).
- **Commit cadence:** commit after every green step. End messages with the Co-Authored-By trailer.
- **Datetime:** `datetime.now(timezone.utc)` + `DateTime(timezone=True)` only (enforced by `test_datetime_consistency.py`).

---

## Sub-plan 0 — Headless deploy/sync path

**Why first:** The autonomy bar (criterion 17) requires the whole create→deploy→run→verify flow to run unattended. Login is already scriptable; the blocker is that the sync path prompts for interactive sync-selection choices that cannot be answered headless. Solutions deploy must take the **full-replace contract as given** and never prompt.

**Files:**
- Modify: `api/bifrost/cli.py` (sync/deploy entry; locate the interactive selection prompt)
- Test: `api/tests/unit/test_cli_headless.py` (create)

### Task 0.1: Pin the interactive prompt

- [ ] **Step 1: Locate every interactive selection in the sync path**

Run:
```bash
cd /home/jack/GitHub/bifrost/.claude/worktrees/solutions-success-criteria
rg -n "input\(|select\(|Confirm|questionary|inquirer|prompt" api/bifrost/cli.py api/bifrost/commands/*.py | grep -iv "system_prompt\|--prompt\|endpoint"
```
Expected: identify the sync-selection prompt(s) the deploy/sync flow hits with no non-interactive escape. Record exact `cli.py:line` in this step's notes before proceeding.

- [ ] **Step 2: Write the failing headless test**

```python
# api/tests/unit/test_cli_headless.py
import subprocess, sys, os

def _run(args, cwd, env):
    return subprocess.run([sys.executable, "-m", "bifrost", *args],
                          cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=60)

def test_deploy_help_is_noninteractive():
    # The deploy command must exist and advertise a non-interactive contract.
    env = {**os.environ}
    r = _run(["deploy", "--help"], cwd="/tmp", env=env)
    assert r.returncode == 0, r.stderr
    assert "--yes" in r.stdout or "non-interactive" in r.stdout.lower()
```

- [ ] **Step 3: Run it — expect FAIL**

Run: `./test.sh tests/unit/test_cli_headless.py::test_deploy_help_is_noninteractive -v`
Expected: FAIL (no `deploy` command yet, or no `--yes`). This is the anchor; the `deploy` command is implemented in Sub-plan 1 — for now make the test assert the *contract* and mark xfail-until-1 if needed:

```python
import pytest
@pytest.mark.xfail(reason="deploy command implemented in Sub-plan 1", strict=False)
def test_deploy_help_is_noninteractive(): ...
```

- [ ] **Step 4: Add `--yes/-y` + `BIFROST_NONINTERACTIVE` to the sync path's selection prompt**

In the prompt site found in Step 1, gate the interactive call:
```python
# cli.py — at the interactive sync-selection site
noninteractive = "--yes" in argv or "-y" in argv or os.environ.get("BIFROST_NONINTERACTIVE") == "1"
if noninteractive:
    selection = _default_full_selection()   # take ALL changes — matches the full-replace contract
else:
    selection = _interactive_select(...)     # unchanged existing behavior
```
Add `--yes, -y` and `--noninteractive` to the command's help text.

- [ ] **Step 5: Run the test — expect PASS (or xpass)**

Run: `./test.sh tests/unit/test_cli_headless.py -v`
Expected: PASS / XPASS.

- [ ] **Step 6: Commit**

```bash
git add api/bifrost/cli.py api/tests/unit/test_cli_headless.py
git commit -m "feat(cli): non-interactive sync selection (--yes / BIFROST_NONINTERACTIVE)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Sub-plan 1 — Core Solutions runtime + deploy

The load-bearing increment. After this, a Solution can be deployed and executes side-by-side with `_repo/`, with scoped imports — criteria 1,2,3,4,8,9,10,14,16.

**File structure (new + touched):**
- Create: `api/src/models/orm/solutions.py` — `Solution` + `SolutionInstall` ORM (id, slug, name, scope/org, global_repo_access, source-mode).
- Modify: `api/src/models/orm/workflows.py:56` — add `solution_id` FK column. Same column added to `applications.py`, and the form/agent/table ORMs.
- Create: `api/alembic/versions/<rev>_add_solutions.py` — table(s) + nullable `solution_id` on the five entity tables.
- Create: `api/shared/models.py` additions — `SolutionCreate/Update/Response`, `SolutionDeployRequest` Pydantic models (models live here per project rule).
- Create: `api/src/services/solutions/storage.py` — `SolutionStorage` (S3 `_solutions/{solution_id}/` wrapper, mirrors `RepoStorage`).
- Create: `api/src/services/solutions/deploy.py` — full-replace reconcile scoped to `solution_id` (reuses manifest import; deletion re-gated to "absent from THIS solution's bundle").
- Modify: `api/src/services/execution/virtual_import.py:252-435` — per-execution root from `solution_id`; precedence over `_repo`; fallback only if `global_repo_access`.
- Modify: `api/src/jobs/consumers/workflow_execution.py:539-698` — thread `solution_id` into worker context.
- Create: `api/src/handlers/solutions_handlers.py` — REST: create/list/get solution, `POST /api/solutions/{id}/deploy`.
- Modify: `api/bifrost/commands/` — `bifrost solution init|deploy` reading `bifrost.solution.yaml`.
- Create: `api/bifrost/solution_descriptor.py` — parse/validate `bifrost.solution.yaml`, detect Solution workspace.
- Tests: `api/tests/unit/test_solution_storage.py`, `test_solution_descriptor.py`, `test_solution_deploy_reconcile.py`; `api/tests/e2e/test_solution_execution.py`.

### Task 1.1: `solution_id` column + Solution ORM + migration

- [ ] **Step 1: Failing test — Workflow accepts a solution_id**

```python
# api/tests/unit/test_solutions_orm.py
from src.models.orm.workflows import Workflow
def test_workflow_has_solution_id_column():
    assert "solution_id" in Workflow.__table__.columns
    assert Workflow.__table__.columns["solution_id"].nullable is True
```

- [ ] **Step 2: Run — expect FAIL** — `./test.sh tests/unit/test_solutions_orm.py -v` → FAIL (no column).

- [ ] **Step 3: Add the Solution ORM**

```python
# api/src/models/orm/solutions.py
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from src.models.orm.base import Base  # match the actual base import used in workflows.py

class Solution(Base):
    __tablename__ = "solutions"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)  # None = global scope
    global_repo_access: Mapped[bool] = mapped_column(Boolean, default=False)
    git_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    git_repo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
```
(`solution_id` here is the install identity — one row per install. Multiple installs of one definition = multiple rows with the same `slug`, different `id`/scope.)

- [ ] **Step 4: Add `solution_id` to the five entity ORMs**

In `api/src/models/orm/workflows.py` after line 56 (`organization_id`):
```python
    solution_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("solutions.id", ondelete="CASCADE"), nullable=True, index=True
    )
```
Repeat the identical column in `applications.py` and the form/agent/table ORMs (grep their files for `organization_id` and add alongside).

- [ ] **Step 5: Create + apply migration**

```bash
cd api && alembic revision -m "add solutions and solution_id"
```
Edit the new file: `op.create_table("solutions", ...)` and `op.add_column("<each>", sa.Column("solution_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("solutions.id", ondelete="CASCADE"), nullable=True))` for workflows/applications/forms/agents/tables; create the index. Then:
```bash
docker restart $(docker ps --format '{{.Names}}' | grep -- '-bifrost-init-1' | head -1) 2>/dev/null || ./test.sh stack reset
```

- [ ] **Step 6: Run — expect PASS** — `./test.sh tests/unit/test_solutions_orm.py -v` → PASS.

- [ ] **Step 7: Commit**
```bash
git add api/src/models/orm/solutions.py api/src/models/orm/*.py api/alembic/versions/*add_solutions* api/tests/unit/test_solutions_orm.py
git commit -m "feat(db): Solution ORM + nullable solution_id on entities

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 1.2: `SolutionStorage` (S3 `_solutions/{id}/`)

- [ ] **Step 1: Failing test**
```python
# api/tests/unit/test_solution_storage.py
import uuid
from src.services.solutions.storage import SolutionStorage
def test_key_prefix():
    sid = uuid.uuid4()
    s = SolutionStorage(sid)
    assert s._key("workflows/triage.py") == f"_solutions/{sid}/workflows/triage.py"
```
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** — copy the shape of `api/src/services/repo_storage.py` (`read/write/delete/list/exists`, returns SHA-256 on write) but prefix `_solutions/{solution_id}/`.
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** (`feat(solutions): SolutionStorage S3 wrapper`).

### Task 1.3: Per-execution import root

- [ ] **Step 1: Failing e2e** — a workflow with `solution_id=S` importing `from modules.x import y` resolves to `_solutions/S/modules/x.py`, and (flag OFF) a `shared.*` import does NOT resolve.
```python
# api/tests/e2e/test_solution_execution.py  (sketch — fill exact harness from existing exec e2e tests)
def test_solution_local_import_resolves_and_global_blocked(deploy_solution, run_workflow):
    sid = deploy_solution(files={"modules/x.py": "VAL=42", "workflows/w.py": "from modules.x import VAL\n@workflow\ndef run():\n    return VAL"}, global_repo_access=False)
    assert run_workflow(sid, "run") == 42
    # shared.* must NOT resolve with global access off
    sid2 = deploy_solution(files={"workflows/w.py": "import shared.anything\n@workflow\ndef run():\n    return 1"}, global_repo_access=False)
    assert run_workflow(sid2, "run") raises ModuleNotFoundError
```
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** in `virtual_import.py`: when worker context carries `solution_id`, the finder resolves candidate paths against `_solutions/{solution_id}/` FIRST (prepend a solution-rooted resolver, or set the root on the finder per execution), and only consults `_repo/` if `global_repo_access` is true. Critical: today the finder is *appended* to `sys.meta_path` (`:435`) — the solution root must take precedence, so install the solution resolver ahead of the `_repo` one for the execution. Thread `solution_id` + `global_repo_access` from `workflow_execution.py:539-698` into the worker context dict (alongside `file_path`/`function_name`).
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** (`feat(exec): per-execution solution import root with opt-in _repo fallback`).

### Task 1.4: `bifrost.solution.yaml` descriptor

- [ ] **Step 1: Failing test** — parse a descriptor, detect a Solution workspace.
```python
# api/tests/unit/test_solution_descriptor.py
from bifrost.solution_descriptor import load_descriptor, is_solution_workspace
def test_load(tmp_path):
    (tmp_path/"bifrost.solution.yaml").write_text("slug: mna\nname: MNA\nscope: org\nglobal_repo_access: false\n")
    d = load_descriptor(tmp_path)
    assert d.slug == "mna" and d.global_repo_access is False
    assert is_solution_workspace(tmp_path) is True
```
- [ ] **Step 2: Run — FAIL.** **Step 3: Implement** the loader (Pydantic model: slug, name, scope[org|global], global_repo_access, git_connected, git_repo_url, deps). **Step 4: PASS. Step 5: Commit.**

### Task 1.5: Deploy = full-replace reconcile scoped to `solution_id`

- [ ] **Step 1: Failing test** — deploy bundle A (2 workflows) then redeploy bundle B (1 workflow, the other removed): the removed one is deleted **for this solution only**; a same-named `_repo/` workflow and another install are untouched.
```python
# api/tests/unit/test_solution_deploy_reconcile.py  (sketch)
def test_full_replace_scoped(db, deploy):
    deploy(sid, ["w1","w2"]); assert active(sid) == {"w1","w2"}
    deploy(sid, ["w1"]);      assert active(sid) == {"w1"}        # w2 deleted
    assert repo_workflow_named("w2") is not None                  # _repo untouched
    assert active(other_sid) unchanged                            # other install untouched
```
- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** `deploy.py`: write bundle Python to `SolutionStorage`, import entity manifests via the existing importer, and reconcile deletions **gated on "UUID absent from THIS solution's bundle"** (the viability-study prerequisite fix, scoped by `solution_id`) — NOT the current global `_path_exists` sweep. Stamp `solution_id` + inherited scope on every entity. Never query/delete outside `solution_id`.
- [ ] **Step 4: Run — PASS.** **Step 5: Commit** (`feat(solutions): deploy full-replace reconcile scoped to solution_id`).

### Task 1.6: REST + CLI deploy wiring (makes Sub-plan 0's headless contract real)

- [ ] **Step 1: Failing e2e** — `bifrost solution init` writes a descriptor; `bifrost deploy --yes` against a temp dir creates an install and entities; running the workflow returns its value. `bifrost deploy --help` now satisfies the Sub-plan 0 test (remove its xfail).
- [ ] **Step 2: FAIL. Step 3:** implement `solutions_handlers.py` (`POST /api/solutions/{id}/deploy`) + `bifrost solution init|deploy` (deploy reads descriptor, zips/uploads bundle, calls the endpoint; honors `--yes`/`BIFROST_NONINTERACTIVE`). Add DTOs to `shared/models.py`; run `./test.sh tests/unit/test_dto_flags.py` and reconcile. **Step 4: PASS. Step 5: Commit.**

### Task 1.7: Sub-plan 1 verification gate

- [ ] **Step 1:** `cd api && pyright && ruff check .` → 0 errors.
- [ ] **Step 2:** `cd client && npm run generate:types && npm run tsc && npm run lint` (if any client types changed).
- [ ] **Step 3:** `./test.sh all` → green; parse `/tmp/bifrost-<project>/test-results.xml`.
- [ ] **Step 4: Commit** any type regen. Sub-plan 1 done → criteria 1,2,3,4,8,9,10,14,16 demonstrable.

---

## Sub-plan 2 — Read-only enforcement + editable carve-out (roadmap)

Proves 6,7. When reached, expand to full TDD tasks:
- A mutation guard: any create/update/delete on an entity with `solution_id IS NOT NULL`, **except** the deploy path, returns HTTP 4xx *"Solution-managed entities can only be managed by deployment methods."* Apply at the router/service layer for workflows/apps/forms/agents/tables (and MCP tools — see CLAUDE.md MCP-thin-wrapper rule).
- Carve-out: OAuth token mappings + secret config **values** remain editable on solution-managed entities. Tests: mutation rejected with the exact message; OAuth-mapping + secret-value edits succeed.
- Frontend: render solution-managed entities read-only with a "managed by Solution" affordance.

## Sub-plan 3 — Tables: schema/policies from solution, data preserved (roadmap)

Proves 11. Deploy creates/migrates table **structure + policies** from the bundle; **never** writes/wipes rows. Redeploy with an added column migrates and preserves existing rows. Tests: seed rows → redeploy changed schema → rows intact.

## Sub-plan 4 — Export Solution + shared-dep vendoring; prereq scrub fixes (roadmap)

Proves 5 + prerequisites. (a) Fix `service_oauth_token_id` leak: add to `portable.py` `_OAUTH_SECRETS` + test. (b) Scope-aware `generate_manifest` (no cross-tenant dump) + test. (c) `bifrost solution export` scans the solution's Python for `shared.*`/`modules.*` imports and offers to vendor referenced shared modules into the bundle. Tests: exported solution installs on a *fresh* instance with no `_repo/` shared deps and its imports resolve to vendored copies.

## Sub-plan 5 — Git-connected mode (roadmap)

Proves 13. Connect a Solution to a repo; platform polls/webhooks `main` and auto-deploys (reuse `github_sync.py` pull half). `bifrost deploy` is **refused** for a connected install (one-writer invariant). Tests: push to main → auto-deploy observed; `deploy` returns the refusal error.

## Sub-plan 6 — React apps: build → dist → `_apps/`; `npm run dev` (roadmap, likely own sub-spec)

Proves 12. Deploy runs the app build (client-side in CI/`bifrost deploy` is the leaning — ship `dist`; `_solutions/` stays source-free for React) and copies `dist/` to `_apps/`. Standard React `src/` + `npm run dev` local loop; SDK as ordinary imports. Resolve the context-inheritance blocker the viability study flagged (`BundledAppShell` inline render vs standalone `createRoot`). **Before coding, re-enter brainstorming for this sub-spec** — it is large and partly orthogonal.

## Sub-plan 7 — Offline dev loop (roadmap)

Proves 15. `bifrost run` executes local Solution workflows offline (descriptor-detected workspace, local import root); tables/integrations/OAuth resolve against a live dev instance. `npm run dev` app talks to the local SDK for workflow calls, live backend for data-plane. Tests: `bifrost run` offline returns a local workflow's value while a table read hits the live stack.

## Sub-plan 8 — End-to-end proof + full verification (roadmap)

Proves 18 + all. Take a real slice of `bifrost-workspace` (`clients/mna` or `braytel`), turn it into a Solution, deploy to a debug stack, and demonstrate criteria 1–17 live. Run the full pre-completion sequence (pyright, ruff, tsc, lint, `./test.sh all`, client unit, relevant client e2e) green. This is the autonomous proof the goal prompt drives to.

---

## Self-Review (done at write time)

- **Spec coverage:** every one of the 18 criteria maps to a sub-plan (table at top). Prereq fixes → Sub-plan 4. Autonomy/headless → Sub-plan 0 + criterion 18 gate in Sub-plan 8.
- **Type consistency:** `solution_id` is the install identity throughout; `SolutionStorage._key`, descriptor fields (slug/name/scope/global_repo_access/git_connected/git_repo_url) reused consistently across tasks.
- **No placeholders in Sub-plans 0–1** (full depth). Sub-plans 2–8 are explicitly roadmap, to be expanded at task time — flagged, not hidden TBDs.
