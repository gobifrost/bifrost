# Import/Export Phase 1 — Clean Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the three confirmed write-path bugs' fixes and remove the verified dead manifest-import code, producing the "ultra-clean foundation" before any write-service / serializer centralization.

**This is Phase 1 of a 4-phase roadmap** (spec §12). Phase 1 only prunes + fixes — it builds no new abstraction. The later phases (NOT in this plan) put the *surviving* manifest-based machinery on shared contracts: **Phase 2** = one `EntityWriter.upsert` CRUD contract for the pruned manifest write path (§6.1); **Phase 3** = one `EntitySerializer` per entity with **Tables first** (the UI export/import feature and the Solutions tables service share one core, §11); **Phase 4** = remaining entities + the deferred bucket-B capability decisions (§10.3). The point of Phase 1 is to refactor a *clean, minimal* base so Phases 2-4 aren't disciplining dead code. Tables and Knowledge stay as UI features throughout.

**Architecture:** Phase 1 of the spec at `docs/superpowers/specs/2026-06-18-import-export-deadcode-and-contract-unification.md`. Three targeted bug fixes in the manifest→indexer→deploy path, plus an evidence-backed dead-code excision (§9 of the spec) that preserves the shared `ManifestResolver`/`_resolve_*` logic git-sync and Solutions still use (the substrate Phase 2 will discipline, not replace). NO new abstractions in this phase — `EntityWriter` (§6.1) and `EntitySerializer` (§11) are explicitly later phases.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy (async) / Pydantic; tests via `./test.sh` (Dockerised stack). Frontend untouched in this phase.

## Global Constraints

- All work happens in the worktree `/home/jack/GitHub/bifrost/.claude/worktrees/solutions-deadcode-audit` (branch `worktree-solutions-deadcode-audit`). Never edit the primary checkout.
- Tests run via `./test.sh` — never raw `pytest` on host (DB/queue/cache needed). Unit: `./test.sh tests/unit/<f>::<t> -v`. E2E: `./test.sh e2e` (runs the suite; you cannot filter a single e2e by path).
- Datetime: always `datetime.now(timezone.utc)`; never `datetime.utcnow()` / bare `datetime.now()`.
- No dead code, no unrequested fallbacks (CLAUDE.md). When removing a code path, remove everything reachable only from it in the same change.
- `tool_description` is portable *manifest* content but remains API/UI-only on the **source-`.py` indexer path** (`indexers/workflow.py`) — do NOT make the workflow source indexer set it. Only the manifest model + generator + deploy/import paths gain it.
- Solutions deploy fails loud via `SolutionDeployConflict` (→ HTTP 409). Use that existing exception type for the blank-name fix, not a bare raise or a 500.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `api/src/services/file_storage/indexers/agent.py` | Agent YAML → DB indexer | Fix blank-name silent no-op → raise |
| `api/src/services/solutions/deploy.py` | Solutions deploy upserts + counts | Translate indexer error to `SolutionDeployConflict`; carry `tool_description` in workflow values; count real upserts |
| `api/bifrost/manifest.py` | Portable manifest Pydantic models | Add `tool_description` to `ManifestWorkflow` |
| `api/src/services/manifest_generator.py` | DB → manifest (export) | Emit `tool_description` in `serialize_workflow` |
| `api/src/services/manifest_import.py` | Manifest → DB (git-sync import) | Carry `tool_description` in `_resolve_workflow`; **remove** dead `import_manifest_from_repo` + 2 private helpers |
| `api/src/routers/files.py` | Files router | **Remove** dead `POST /manifest/import` endpoint + request model |
| `api/src/models/contracts/files.py` + `__init__.py` | Contracts | **Remove** dead `ManifestImportRequest`/`ManifestImportResponse` |
| `api/tests/unit/test_agent_indexer_blank_name.py` | New | Blank-name raise unit test |
| `api/tests/unit/test_manifest_generator.py` | Existing | `tool_description` round-trip assertion |
| `api/tests/e2e/platform/test_manifest_import.py`, `test_manifest_import_config_cache.py` | Existing | Delete (drive the removed function) |
| `api/tests/e2e/platform/test_cli_push_pull.py` | Existing | Remove the two `/manifest/import` POST tests |

---

## Task 1: Agent indexer rejects blank name instead of silent no-op (Bug B)

Fixes the §8 live-repro bug: `index_agent` returns `False` on a missing/blank name, swallowing the agent. REST enforces `name` `min_length=1`; the indexer must be at least as strict. Raise `ValueError` so callers can surface it.

**Files:**
- Modify: `api/src/services/file_storage/indexers/agent.py:69-77`
- Test: `api/tests/unit/test_agent_indexer_blank_name.py` (create)

**Interfaces:**
- Produces: `AgentIndexer.index_agent(path, content)` now raises `ValueError` (message contains `"agent name is required"`) when the YAML `name` is missing/empty or `system_prompt` is missing/empty, instead of returning `False`.

- [ ] **Step 1: Write the failing test**

Create `api/tests/unit/test_agent_indexer_blank_name.py`:

```python
"""AgentIndexer must reject blank-name agents loudly, not silently no-op.

Regression for the Solutions-deploy 'lying success' bug: a manifest agent with
an empty name was swallowed (index_agent returned False) while deploy still
reported agents_upserted=1.
"""
import pytest
import yaml

from src.services.file_storage.indexers.agent import AgentIndexer


def _yaml(**fields) -> bytes:
    return yaml.dump(fields).encode("utf-8")


@pytest.mark.asyncio
async def test_index_agent_raises_on_blank_name(db_session):
    indexer = AgentIndexer(db_session)
    content = _yaml(id="11111111-1111-1111-1111-111111111111", name="", system_prompt="hi")
    with pytest.raises(ValueError, match="agent name is required"):
        await indexer.index_agent("agents/x.agent.yaml", content)


@pytest.mark.asyncio
async def test_index_agent_raises_on_missing_system_prompt(db_session):
    indexer = AgentIndexer(db_session)
    content = _yaml(id="11111111-1111-1111-1111-111111111111", name="Valid")
    with pytest.raises(ValueError, match="system_prompt is required"):
        await indexer.index_agent("agents/x.agent.yaml", content)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_agent_indexer_blank_name.py -v`
Expected: FAIL — `index_agent` currently returns `False`, so `pytest.raises(ValueError)` does not trigger (`DID NOT RAISE`).

- [ ] **Step 3: Replace the silent returns with raises**

In `api/src/services/file_storage/indexers/agent.py`, change the two guard blocks (currently lines 69-77):

```python
        name = agent_data.get("name")
        if not name:
            raise ValueError(f"agent name is required (file: {path})")

        system_prompt = agent_data.get("system_prompt")
        if not system_prompt:
            raise ValueError(f"system_prompt is required (file: {path})")
```

(Leave the `yaml.YAMLError` guard above them returning `False` — a malformed file is a different, non-content failure.)

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_agent_indexer_blank_name.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add api/src/services/file_storage/indexers/agent.py api/tests/unit/test_agent_indexer_blank_name.py
git commit -m "fix(indexer): reject blank-name/empty-prompt agents loudly instead of silent no-op"
```

---

## Task 2: Deploy surfaces the blank-name error as 409 and counts real upserts (Bug B cont.)

With Task 1, `index_agent` now raises mid-deploy. Translate it to `SolutionDeployConflict` (the existing fail-loud → 409 path) so a bad bundle rolls back with a clear message, and stop reporting `agents_upserted=len(rb.agents)` when an agent didn't land.

**Files:**
- Modify: `api/src/services/solutions/deploy.py:1295-1300` (the `index_agent` call in `_upsert_agents`)
- Modify: `api/src/services/solutions/deploy.py:477` (the `agents_upserted=len(rb.agents)` count)
- Test: `api/tests/e2e/platform/test_solution_deploy_blank_name.py` (create)

**Interfaces:**
- Consumes: `AgentIndexer.index_agent` raising `ValueError` (Task 1).
- Produces: deploying a bundle with a blank-name agent raises `SolutionDeployConflict`; a successful deploy's `agents_upserted` equals the number of agents actually indexed.

- [ ] **Step 1: Write the failing e2e test**

Create `api/tests/e2e/platform/test_solution_deploy_blank_name.py`:

```python
"""A solution bundle with a blank-name agent must fail the deploy loudly,
not silently swallow the agent while reporting success (the §8 live-repro bug)."""
import pytest

from src.services.solutions.deploy import SolutionDeployer, SolutionDeployConflict
from src.models.contracts.solutions import SolutionBundle


@pytest.mark.asyncio
async def test_deploy_blank_name_agent_raises(db_session, seeded_solution):
    bundle = SolutionBundle(
        slug=seeded_solution.slug,
        version="0.1.0",
        agents=[{
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "",  # blank — must not silently vanish
            "system_prompt": "You are a test agent.",
        }],
    )
    with pytest.raises(SolutionDeployConflict):
        await SolutionDeployer(db_session).deploy(bundle, force=True)
```

> Note: `seeded_solution` — if no such fixture exists, create the install row inline mirroring `test_solution_deploy_async.py`'s setup. Read that file first and reuse its fixture/helpers; do not invent a new bundle shape.

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh e2e` (e2e cannot be filtered to one path; let the suite run and read `/tmp/bifrost/test-results.xml` for this test).
Expected: FAIL — today the agent is swallowed and `deploy` returns success, so `pytest.raises` does not trigger.

- [ ] **Step 3: Wrap the indexer call in deploy**

In `api/src/services/solutions/deploy.py`, in `_upsert_agents`, replace the bare call (line ~1300):

```python
            try:
                await indexer.index_agent(f"agents/{agent_id}.agent.yaml", content)
            except ValueError as exc:
                raise SolutionDeployConflict(
                    f"agent {agent_id}: {exc}"
                ) from exc
```

- [ ] **Step 4: Make the count reflect reality**

`_upsert_agents` currently returns nothing and `deploy` sets `agents_upserted=len(rb.agents)` at line 477. With Task 1+Step 3 a blank-name agent aborts the whole deploy, so any *successful* deploy did upsert every agent — `len(rb.agents)` is now accurate. Add a guard comment so the invariant is explicit and a future "skip-bad-agent" change can't silently re-introduce the lie:

At deploy.py:477, change:

```python
            agents_upserted=len(rb.agents),
```

to:

```python
            # Accurate because _upsert_agents aborts the deploy (SolutionDeployConflict)
            # if any agent fails to index — a partial success is impossible here.
            agents_upserted=len(rb.agents),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./test.sh e2e`
Expected: PASS for `test_deploy_blank_name_agent_raises` (check `/tmp/bifrost/test-results.xml`).

- [ ] **Step 6: Commit**

```bash
git add api/src/services/solutions/deploy.py api/tests/e2e/platform/test_solution_deploy_blank_name.py
git commit -m "fix(solutions): blank-name agent fails deploy as 409 instead of silent swallow + false count"
```

---

## Task 3: Add `tool_description` to the manifest workflow model (Bug C, part 1)

The portable manifest must carry `tool_description` so it survives export/capture. Add the field to `ManifestWorkflow`.

**Files:**
- Modify: `api/bifrost/manifest.py:81-100` (`ManifestWorkflow`)
- Test: `api/tests/unit/test_manifest.py` (existing — add a field-presence assertion)

**Interfaces:**
- Produces: `ManifestWorkflow.tool_description: str | None` (default `None`).

- [ ] **Step 1: Write the failing test**

In `api/tests/unit/test_manifest.py`, add:

```python
def test_manifest_workflow_carries_tool_description():
    from bifrost.manifest import ManifestWorkflow

    wf = ManifestWorkflow(
        id="33333333-3333-3333-3333-333333333333",
        path="functions/x.py",
        function_name="x",
        tool_description="curated tool blurb",
    )
    assert wf.tool_description == "curated tool blurb"
    # Default is None when omitted
    wf2 = ManifestWorkflow(id="44444444-4444-4444-4444-444444444444", path="p.py", function_name="f")
    assert wf2.tool_description is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_manifest.py::test_manifest_workflow_carries_tool_description -v`
Expected: FAIL — `ManifestWorkflow` has no `tool_description` field (`unexpected keyword argument` or attribute error).

- [ ] **Step 3: Add the field**

In `api/bifrost/manifest.py`, inside `ManifestWorkflow`, after the `description` field:

```python
    tool_description: str | None = Field(
        default=None,
        description="LLM/agent-facing tool description (portable). API/UI-set, not derived from source.",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_manifest.py::test_manifest_workflow_carries_tool_description -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/manifest.py api/tests/unit/test_manifest.py
git commit -m "feat(manifest): carry tool_description on ManifestWorkflow"
```

---

## Task 4: Emit `tool_description` on export (Bug C, part 2)

`serialize_workflow` (DB → manifest) must populate the new field so capture/export carries it.

**Files:**
- Modify: `api/src/services/manifest_generator.py` (`serialize_workflow`, ~line with `category=`/`tags=`)
- Test: `api/tests/unit/test_manifest_generator.py` (existing)

**Interfaces:**
- Consumes: `ManifestWorkflow.tool_description` (Task 3).
- Produces: `serialize_workflow(wf)` sets `tool_description=wf.tool_description`.

- [ ] **Step 1: Write the failing test**

In `api/tests/unit/test_manifest_generator.py`, add:

```python
def test_serialize_workflow_carries_tool_description():
    from types import SimpleNamespace
    from src.services.manifest_generator import serialize_workflow

    wf = SimpleNamespace(
        id="55555555-5555-5555-5555-555555555555",
        name="hello", path="functions/hello.py", function_name="main",
        type="tool", description="plain", tool_description="CURATED-TOOLDESC",
        organization_id=None, access_level="role_based", endpoint_enabled=False,
        timeout_seconds=1800, public_endpoint=False, category="General", tags=[],
    )
    out = serialize_workflow(wf)
    assert out.tool_description == "CURATED-TOOLDESC"
```

> If `test_manifest_generator.py` builds a real `Workflow` ORM via a factory rather than `SimpleNamespace`, follow that file's existing pattern instead and set `tool_description` on the factory object.

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh tests/unit/test_manifest_generator.py::test_serialize_workflow_carries_tool_description -v`
Expected: FAIL — `serialize_workflow` does not set `tool_description`, so `out.tool_description` is `None`.

- [ ] **Step 3: Emit the field**

In `api/src/services/manifest_generator.py`, in `serialize_workflow`, add to the `ManifestWorkflow(...)` constructor (next to `description=wf.description`):

```python
        tool_description=wf.tool_description,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh tests/unit/test_manifest_generator.py::test_serialize_workflow_carries_tool_description -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/manifest_generator.py api/tests/unit/test_manifest_generator.py
git commit -m "feat(manifest): serialize tool_description on workflow export"
```

---

## Task 5: Carry `tool_description` on import + deploy (Bug C, part 3)

Both write paths that consume a manifest workflow — git-sync import (`_resolve_workflow`) and Solutions deploy (`_upsert_workflows`) — must write `tool_description` to the DB so the round-trip closes.

**Files:**
- Modify: `api/src/services/manifest_import.py:1466-1478` (`_resolve_workflow` `wf_values`)
- Modify: `api/src/services/solutions/deploy.py:778-794` (workflow `values` dict)
- Test: `api/tests/e2e/platform/test_solution_tool_description_roundtrip.py` (create)

**Interfaces:**
- Consumes: `ManifestWorkflow.tool_description` (Task 3).
- Produces: after deploy/import of a manifest workflow whose entry has `tool_description`, the `Workflow.tool_description` column equals that value.

- [ ] **Step 1: Write the failing e2e test**

Create `api/tests/e2e/platform/test_solution_tool_description_roundtrip.py`:

```python
"""tool_description must survive a Solutions deploy (the §8 live-repro bug:
the field was dropped end-to-end across capture/manifest/deploy)."""
import pytest
from sqlalchemy import select

from src.models.orm.workflows import Workflow
from src.services.solutions.deploy import SolutionDeployer
from src.models.contracts.solutions import SolutionBundle


@pytest.mark.asyncio
async def test_deploy_carries_tool_description(db_session, seeded_solution):
    wid = "66666666-6666-6666-6666-666666666666"
    bundle = SolutionBundle(
        slug=seeded_solution.slug,
        version="0.1.0",
        workflows=[{
            "id": wid,
            "name": "hello",
            "function_name": "main",
            "path": "functions/hello.py",
            "type": "tool",
            "tool_description": "CURATED-TOOLDESC-DO-NOT-LOSE",
        }],
    )
    await SolutionDeployer(db_session).deploy(bundle, force=True)
    row = (await db_session.execute(select(Workflow).where(Workflow.id == wid))).scalar_one()
    assert row.tool_description == "CURATED-TOOLDESC-DO-NOT-LOSE"
```

> Reuse `seeded_solution` / bundle setup from `test_solution_deploy_async.py` (read it first). The remapped workflow id may differ from `wid` if the bundle goes through per-install remap — if the deploy remaps ids, query by `(solution_id, name)` instead of the raw `wid`.

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh e2e`
Expected: FAIL — `row.tool_description` is `None` (field dropped today). Check `/tmp/bifrost/test-results.xml`.

- [ ] **Step 3: Carry it in the deploy values dict**

In `api/src/services/solutions/deploy.py`, in `_upsert_workflows`, add to the `values` dict (next to `"description": mwf.get("description"),`):

```python
                "tool_description": mwf.get("tool_description"),
```

- [ ] **Step 4: Carry it on the git-sync import path**

In `api/src/services/manifest_import.py`, in `_resolve_workflow`, after the `if mwf.description is not None:` block, add:

```python
        if getattr(mwf, "tool_description", None) is not None:
            wf_values["tool_description"] = mwf.tool_description
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./test.sh e2e`
Expected: PASS for `test_deploy_carries_tool_description`.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/solutions/deploy.py api/src/services/manifest_import.py api/tests/e2e/platform/test_solution_tool_description_roundtrip.py
git commit -m "fix(solutions/git-sync): carry tool_description through deploy and import"
```

---

## Task 6: Remove the dead `/api/files/manifest/import` endpoint + request/response models

§9 removal manifest. The endpoint has no client/CLI caller (only generated types + tests); its last production caller (CLI watch manifest-push) was deliberately removed and is guarded by `test_watch_regression_disappearing_entity.py`.

**Files:**
- Modify: `api/src/routers/files.py` (delete `POST /manifest/import` handler + `ManifestImportRequest` class)
- Modify: `api/src/models/contracts/files.py` (delete `ManifestImportRequest`, `ManifestImportResponse`)
- Modify: `api/src/models/contracts/__init__.py` (remove the two exports — lines ~297, ~1031)
- Modify: `api/tests/e2e/platform/test_cli_push_pull.py` (remove the two `/manifest/import` POST tests)

**Interfaces:**
- Produces: nothing — pure removal. `GET /api/files/manifest` stays. `ManifestResolver`, `_diff_and_collect`, `generate_manifest` stay.

- [ ] **Step 1: Confirm no live caller remains (guard against a stale assumption)**

Run:
```bash
grep -rn "manifest/import" api/src client/src api/bifrost --include='*.py' --include='*.ts' --include='*.tsx' | grep -v v1.d.ts
```
Expected: only `api/src/routers/files.py` (the handler being deleted). If anything else appears, STOP and re-evaluate — do not delete.

- [ ] **Step 2: Delete the endpoint handler + request model**

In `api/src/routers/files.py`, delete the `ManifestImportRequest` class (~lines 427-454) and the entire `@router.post("/manifest/import", ...)` handler (~lines 457-511). Remove the now-unused `ManifestImportRequest`/`ManifestImportResponse` names from the file's contracts import at the top (`files.py:30`).

- [ ] **Step 3: Delete the dead contracts + exports**

In `api/src/models/contracts/files.py`, delete the `ManifestImportRequest` and `ManifestImportResponse` model definitions. In `api/src/models/contracts/__init__.py`, delete the `ManifestImportResponse` import (~line 297) and its `__all__` entry (~line 1031), and the same for `ManifestImportRequest` if exported there.

- [ ] **Step 4: Remove the dead endpoint tests**

In `api/tests/e2e/platform/test_cli_push_pull.py`, delete `test_push_bifrost_manifest` and `test_push_manifest_response_shape` (the two functions that POST `/api/files/manifest/import`). Leave the rest of the file (push/pull file-write tests) intact.

- [ ] **Step 5: Verify nothing else references the removed names**

Run:
```bash
grep -rn "ManifestImportRequest\|ManifestImportResponse" api/ --include='*.py' | grep -v v1.d.ts
```
Expected: zero results (or only the lines you're about to remove in Task 7's function — if Task 7 not yet done, the `import_manifest_from_repo` return annotation may still reference `ManifestImportResult`, which is a DIFFERENT name and stays).

- [ ] **Step 6: Run type check + targeted tests**

Run:
```bash
cd api && pyright && cd ..
./test.sh e2e
```
Expected: pyright clean; e2e green (the deleted tests are gone, nothing else broke). Confirm `test_watch_regression_disappearing_entity` still passes (it asserts the endpoint is never *called* — removal doesn't break it).

- [ ] **Step 7: Commit**

```bash
git add api/src/routers/files.py api/src/models/contracts/files.py api/src/models/contracts/__init__.py api/tests/e2e/platform/test_cli_push_pull.py
git commit -m "chore: remove dead /api/files/manifest/import endpoint + request/response models"
```

---

## Task 7: Remove dead `import_manifest_from_repo` + its private helpers

§9. With the endpoint gone (Task 6), `import_manifest_from_repo` and the two helpers it solely uses are unreachable in production. Their only remaining callers are two e2e tests that exist purely to exercise this function — delete them too (the underlying `_resolve_*` logic is covered by git-sync e2e + the Solutions deploy e2e from Tasks 2 & 5).

**Files:**
- Modify: `api/src/services/manifest_import.py` (delete `import_manifest_from_repo` ~524-736, `_apply_role_name_resolution` ~414-466, `_rewrite_org_ids` ~468-510)
- Delete: `api/tests/e2e/platform/test_manifest_import.py`
- Delete: `api/tests/e2e/platform/test_manifest_import_config_cache.py`

**Interfaces:**
- Produces: nothing — pure removal. PRESERVE `ManifestResolver`, `_diff_and_collect` and its diff helpers, `_form/_agent_content_from_manifest`, `_resolve_*_content`, `_resolve_role_names`, `ManifestImportResult` (return type still referenced by preserved code? verify in Step 1).

- [ ] **Step 1: Confirm callers + what dies with it**

Run:
```bash
grep -rn "import_manifest_from_repo" api/ --include='*.py'
grep -rn "_apply_role_name_resolution\|_rewrite_org_ids" api/src --include='*.py'
grep -rn "ManifestImportResult" api/src --include='*.py'
```
Expected: `import_manifest_from_repo` only in its own def + the two test files (the `files.py` caller is already gone from Task 6). The two helpers only inside `import_manifest_from_repo`. If `ManifestImportResult` is referenced ONLY by `import_manifest_from_repo`, it dies too — note it for Step 3. If anything else uses these, STOP.

- [ ] **Step 2: Delete the two helper-only tests**

```bash
git rm api/tests/e2e/platform/test_manifest_import.py api/tests/e2e/platform/test_manifest_import_config_cache.py
```

- [ ] **Step 3: Delete the dead functions**

In `api/src/services/manifest_import.py`, delete `import_manifest_from_repo` (the full `async def`, ~524-736), `_apply_role_name_resolution` (~414-466), and `_rewrite_org_ids` (~468-510). If Step 1 showed `ManifestImportResult` is used only by the deleted function, delete its dataclass too; otherwise leave it. Remove any now-unused imports at the top of the file that pyright flags.

- [ ] **Step 4: Verify the shared logic is intact**

Run:
```bash
grep -n "def _diff_and_collect\|class ManifestResolver\|def _resolve_role_names\|def _form_content_from_manifest\|def _agent_content_from_manifest" api/src/services/manifest_import.py
```
Expected: all still present (these are the live git-sync/Solutions helpers — must NOT have been deleted).

- [ ] **Step 5: Type check + full backend suite**

Run:
```bash
cd api && pyright && ruff check . && cd ..
./test.sh all
```
Expected: pyright + ruff clean (no unused-import/undefined-name from the deletion); `./test.sh all` green — git-sync e2e and the Solutions deploy e2e still pass, proving the `_resolve_*` logic is exercised without the deleted entrypoint.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/manifest_import.py
git commit -m "chore: remove dead import_manifest_from_repo + _apply_role_name_resolution + _rewrite_org_ids"
```

---

## Task 8: Regenerate client types + full verification

Removing the endpoint changes the OpenAPI schema; regenerate the generated TS types so `v1.d.ts` no longer carries the dead route. Then run the full pre-completion sequence.

**Files:**
- Modify: `client/src/lib/v1.d.ts` (regenerated)

- [ ] **Step 1: Ensure the debug stack is up for this worktree**

Run: `./debug.sh status | grep -q "Status:   UP" || ./debug.sh up`
Get the URL/port from `./debug.sh status`.

- [ ] **Step 2: Regenerate types**

Run (set `OPENAPI_URL` to the worktree stack's URL from Step 1 if not on the default port):
```bash
cd client && npm run generate:types && cd ..
```

- [ ] **Step 3: Confirm the dead route is gone from generated types**

Run:
```bash
grep -n "files/manifest/import" client/src/lib/v1.d.ts || echo "GONE (expected)"
grep -n "files/manifest\"" client/src/lib/v1.d.ts && echo "GET /manifest still present (expected)"
```
Expected: `/manifest/import` GONE; `GET /api/files/manifest` still present.

- [ ] **Step 4: Full verification sweep**

Run:
```bash
cd api && pyright && ruff check . && cd ..
cd client && npm run tsc && npm run lint && cd ..
./test.sh all
./test.sh client unit
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add client/src/lib/v1.d.ts
git commit -m "chore: regenerate client types after removing dead manifest-import endpoint"
```

---

## Self-Review notes

- **Spec coverage:** Tasks 1-2 = §8 Bug B (blank-name silent swallow + lying count). Tasks 3-5 = §8 Bug C / §4.1 (tool_description end-to-end). Tasks 6-7 = §9 dead-code removal manifest (endpoint + function + 2 helpers + their tests). Task 8 = generated-types hygiene. Bug A in §4.2 ("name behaves differently") is the same root cause as Bug B and is resolved by Task 1's validation (the indexer now matches the REST `min_length` contract).
- **Out of scope (later phases, intentionally):** `EntityWriter` (§6.1), `EntitySerializer` (§11), the bucket-B capability decisions (§10.3), `/api/export-import` consolidation. Phase 1 is the clean foundation only.
- **Preservation guard:** Tasks 6-7 each include an explicit grep step proving no live caller before deletion and an explicit grep proving the shared `ManifestResolver`/`_diff_and_collect`/content-helpers survive. This is the "extract the dead wrapper, keep the shared core" requirement from §9.2.
- **Test-filter caveat:** e2e cannot be filtered to one path (`./test.sh e2e` runs the suite); read `/tmp/bifrost/test-results.xml` for individual results. Unit tests CAN be filtered by `::test_name`.
