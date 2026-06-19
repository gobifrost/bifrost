# Import/Export Phase 1 — Clean Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

---

## 0. FRESH-SESSION ONBOARDING (read this first — this plan is self-contained)

This plan was produced by a long investigation in a prior session. **You do not need that
session's context.** Everything required is here or in the two companion docs. Read this section,
then execute Tasks 1–10. (Task 11 is a deliberate HOLD — see below.)

### Environment / where to work
- **Worktree (work HERE):** `/home/jack/GitHub/bifrost/.claude/worktrees/solutions-deadcode-audit`,
  branch `worktree-solutions-deadcode-audit`, based on `origin/main` @ `9a76c95c` (Solutions
  PR #347 already merged). Do NOT work in the primary checkout.
- **Companion docs (committed in this worktree):**
  - Spec / full reasoning: `docs/superpowers/specs/2026-06-18-import-export-deadcode-and-contract-unification.md` (§1–§18)
  - Stability audit + verdict: `docs/superpowers/specs/2026-06-19-solutions-stability-audit.md`
- **Dev stack** (for live repro, optional): `./debug.sh up` / `./debug.sh status`. A scratch CLI
  venv may exist at `/tmp/bifrost-cli-deadcode`; if not, follow CLAUDE.md "Spinning up the dev
  environment". Note: stack defaults to **netbird mode** where browser automation hangs — drive
  via CLI/API or the in-`api`-container `python -c` pattern (see §8 live-repro in the spec).
- **Tests:** ALWAYS `./test.sh` (Dockerised). Unit filterable by `::test_name`; **e2e is NOT
  filterable** (`./test.sh e2e` runs the whole suite) — read results from
  `/tmp/bifrost-<project>/test-results.xml`.

### What this is and why (one paragraph)
Bifrost has multiple write paths that turn portable entity declarations into DB rows for the same
entities (REST routers, the manifest/git-sync `_resolve_*` path, Solutions deploy `_upsert_*`, and
MCP tools). They have **drifted** — each re-decides field mapping and create-vs-update rules — so
fields the REST contract carries get silently dropped on other paths. Two such bugs were
**reproduced live** (tool_description dropped end-to-end; blank-name agent silently swallowed +
false "success" count). A 31-agent stability audit of Solutions found one **CRITICAL** (git-sync
wipes all event triggers — live-confirmed) and a cluster of highs. **Phase 1 (this plan)** fixes
the reproducible bugs and removes verified-dead code to leave a clean base. **Convergence
(Phases 2–4, NOT here)** then puts the surviving paths on one shared contract — see §Handoff below.

### Task map (what each task is, and which are blocked on what)
- **Tasks 1–2** — Bug B: AgentIndexer rejects blank name (was silent no-op) + deploy surfaces it
  as 409 / count reflects reality. (Task 1 is the shared-indexer fix; it also hardens git-sync.)
- **Tasks 3–5** — Bug C: `tool_description` round-trips (manifest model → export → import+deploy).
- **Tasks 6–7** — Dead-code removal: the orphaned `POST /api/files/manifest/import` endpoint +
  request/response models (Task 6) and the now-unreachable `import_manifest_from_repo` + 2 private
  helpers + their two helper-only tests (Task 7). **Each has a pre-delete grep gate** proving no
  live caller AND proving the shared `ManifestResolver`/`_diff_and_collect`/content-helpers
  survive — do not skip those.
- **Task 8** — Regenerate `client/src/lib/v1.d.ts` + full verification sweep.
- **Task 9** — audit CRITICAL: git-connected Solutions sync carries events (1-line; live-confirmed
  1→0 today).
- **Task 10** — audit HIGH: remap EventSubscription own-id per install (else 2nd-install 500).
- **Task 11** — audit HIGH: table orphan cross-solution data bleed. **ON HOLD — DO NOT IMPLEMENT.**
  The obvious fix breaks legitimate same-solution reattach by design (slug is the intended reattach
  key because reinstall gets a new `solution.id`). It needs a product decision first. Leave it; it
  is documented in-task so the decision isn't lost.

**Execution order:** 1 → 2 → 3 → 4 → 5 → 9 → 10 (the fixes; verify e2e green), then 6 → 7 → 8 (the
removals; they change the OpenAPI schema so types regen comes last). Commit per task (each task
ends with a commit step). Run the full verification sweep (Task 8 Step 4) before declaring done.

### Definition of done for Phase 1
Tasks 1–10 implemented, every task's tests green via `./test.sh all` + `./test.sh client unit`,
`pyright` + `ruff check .` + `npm run tsc` + `npm run lint` all clean, `client/src/lib/v1.d.ts`
regenerated. Then update this plan's checkboxes and proceed to the §Handoff.

### → HANDOFF TO CONVERGENCE (do this AFTER Phase 1 is green; it's the next focus)
Convergence is the real prize and is fully designed in the spec — read these sections in order:
- **§6.1** — the decision: ONE write-core per entity, identity + resolution as parameters.
- **§16–§17** — the SHARPENED design: **two orthogonal axes** — (A) an *annotated contract*
  (fields tagged content/identifying/backup/secret/match-key; consumers opt into field-classes)
  and (B) a *ReconciliationPolicy* object (match_strategy / role_policy / org_mode / table_gates /
  guards). `_repo` and Solutions become two policy instances over one contract. Note §17's caveat:
  "secret" needs a predicate (config value), not a static flag.
- **§18** — Codex's adversarial review (verified): "one writer + 5 knobs" is NOT fewer rules; it
  relocates them into hooks. **VERDICT: pursue-with-changes.** The likely true shape is "shared
  contract + shared utilities + thin per-entity reconcilers," NOT a universal writer. **The spike
  that decides this must be Config + Integration (the HARD cases), NOT workflow** (workflow is the
  easy case that flatters the design — see §18.1).
- **§13** — sync==deploy + dry-run: Solutions git-sync ALREADY is "deploy from GitHub"; the
  missing piece is a `validate_only` dry-run extracted from deploy's existing pre-commit phase.
- **§14–§15** — `_repo` git-sync manifest CRUD is **load-bearing, keep it** (issue **#313** is the
  accepted plan-of-record to migrate `_resolve_*` to `OrgScopedRepository`; fold #313 into Phase 2).
- **Convergence acceptance checklist** (the deferred audit findings — the converged writer is
  "done" only when each round-trips): `auto_fill` (form field, dropped by shared FormIndexer),
  agent-delegation order-independence (shared single-pass in `manifest_import.py:1255-1270`),
  `tool_description` / `max_run_timeout` / `event_type` / `display_name` field parity, and the
  Task 11 slug-identity decision.
- **Recommended first convergence step:** a throwaway **Config + Integration** spike of axis A +
  axis B, success bar = "reproduces BOTH `_resolve_*` and `_upsert_*` with NO opaque per-entity
  callbacks." If it can't, name it "shared-utils + reconcilers" and stop calling it one writer.

---

**Goal:** Land the three confirmed write-path bugs' fixes, three Solutions-path stability fixes from the §audit, and remove the verified dead manifest-import code, producing the "ultra-clean foundation" before any write-service / serializer centralization.

**Stability fixes added (Tasks 9-11)** from the Solutions stability audit (`docs/superpowers/specs/2026-06-19-solutions-stability-audit.md`): per Jack's triage, only the **Solutions-path reproducible** findings are fixed here (events-wipe critical, EventSubscription PK reuse, table orphan cross-solution reattach). The shared/`_repo`/field-parity findings (auto_fill, agent-delegation order, tool_description/max_run_timeout/event_type/display_name) are DEFERRED to convergence as its acceptance checklist — NOT in this plan.

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

## Task 9: Git-connected Solutions sync stops wiping all event triggers (audit CRITICAL)

`solutions/git_sync.py:read_workspace_bundle` builds the bundle **without** `events=`, so a git-connected solution's deploy reconciles against an empty event set and deletes every EventSource / schedule / webhook for the install on every sync. The zip path passes `events=` and is unaffected. Live-confirmed 1→0 (`docs/superpowers/specs/2026-06-19-solutions-stability-audit.md`). The CLI already exports `_collect_events`.

**Files:**
- Modify: `api/src/services/solutions/git_sync.py` (`read_workspace_bundle`, the `return SolutionBundle(...)` ~line 131)
- Test: `api/tests/e2e/platform/test_solution_git_sync_events.py` (create)

**Interfaces:**
- Consumes: `_collect_events` from `bifrost.commands.solution` (same module `read_workspace_bundle` already imports `_collect_python_files`/`_collect_apps`/`_collect_claims`/`_collect_connection_schemas` from).
- Produces: `read_workspace_bundle` returns a `SolutionBundle` whose `.events` is populated from `.bifrost/events.yaml`.

- [ ] **Step 1: Write the failing test**

Create `api/tests/e2e/platform/test_solution_git_sync_events.py`:

```python
"""read_workspace_bundle must carry events, or a git-connected sync deletes every
EventSource/schedule/webhook for the install (audit CRITICAL, live-confirmed 1->0)."""
import pathlib
import pytest

from src.services.solutions.git_sync import read_workspace_bundle


def test_read_workspace_bundle_carries_events(tmp_path, seeded_solution):
    bifrost = tmp_path / ".bifrost"
    bifrost.mkdir()
    (tmp_path / "bifrost.solution.yaml").write_text(
        f"slug: {seeded_solution.slug}\nname: T\nversion: 0.1.0\n"
    )
    (bifrost / "events.yaml").write_text(
        "events:\n"
        "  11111111-1111-1111-1111-111111111111:\n"
        "    id: 11111111-1111-1111-1111-111111111111\n"
        "    name: nightly\n"
        "    source_type: schedule\n"
        "    is_active: true\n"
        "    schedule: {cron: '0 0 * * *', timezone: UTC}\n"
        "    subscriptions: []\n"
    )
    bundle = read_workspace_bundle(seeded_solution, tmp_path)
    assert len(bundle.events) == 1, "events.yaml must populate bundle.events"
    assert bundle.events[0]["name"] == "nightly"
```

> `seeded_solution` — reuse the fixture/inline install from `test_solution_deploy_async.py` (read it first). Only its `.slug` is used here.

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh e2e`
Expected: FAIL — `bundle.events` is `[]` today (the omission). Check `/tmp/bifrost-<project>/test-results.xml`.

- [ ] **Step 3: Collect events in read_workspace_bundle**

In `api/src/services/solutions/git_sync.py`, in `read_workspace_bundle`, add the import alongside the existing CLI collector imports and pass `events=` in the `SolutionBundle(...)` return:

```python
    from bifrost.commands.solution import _collect_events
```
and in the `return SolutionBundle(...)`, add (next to `connection_schemas=...`):
```python
        events=_collect_events(workspace),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh e2e`
Expected: PASS for `test_read_workspace_bundle_carries_events`.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/solutions/git_sync.py api/tests/e2e/platform/test_solution_git_sync_events.py
git commit -m "fix(solutions): git-connected sync carries events — stop wiping all triggers on every sync"
```

---

## Task 10: Remap EventSubscription own-id per install (audit HIGH — 2nd-install 500)

`_remapped_bundle` remaps the EventSource's own id (pass 1) and the subscription's `workflow_id`/`agent_id` refs (pass 2), but **never remaps the subscription's own `id`**. So `_upsert_events` inserts it verbatim (`deploy.py:1623`), and installing the same bundle into a second install raises a duplicate-PK 500. Fix belongs in pass 2 of `_remapped_bundle` (keep all remapping in one place), using `solution_entity_id` directly — the subscription id is an own-id, not an in-bundle cross-ref, so `_remap_ref` doesn't apply.

**Files:**
- Modify: `api/src/services/solutions/deploy.py` (`_remapped_bundle` pass-2 event loop, ~line 569-577)
- Test: `api/tests/e2e/platform/test_solution_event_sub_remap.py` (create)

**Interfaces:**
- Consumes: `solution_entity_id(install_id, manifest_id)` (already defined `deploy.py:100`), `sid` (in scope in `_remapped_bundle`).
- Produces: after `_remapped_bundle`, each subscription's `id` is `solution_entity_id(sid, original_id)`; two installs of the same bundle get distinct subscription PKs.

- [ ] **Step 1: Write the failing test**

Create `api/tests/e2e/platform/test_solution_event_sub_remap.py`:

```python
"""The same bundle (with an event subscription) must install into two installs
without a duplicate-PK collision — the subscription's own id must be remapped
per install (audit HIGH)."""
import uuid
import pytest

from src.services.solutions.deploy import SolutionDeployer, SolutionBundle, solution_entity_id


@pytest.mark.asyncio
async def test_event_subscription_id_remapped_per_install(db_session, two_seeded_solutions):
    sol_a, sol_b = two_seeded_solutions
    sub_id = "33333333-3333-3333-3333-333333333333"
    src_id = "22222222-2222-2222-2222-222222222222"
    def bundle(sol):
        return SolutionBundle(
            solution=sol, version="0.1.0",
            events=[{
                "id": src_id, "name": "nightly", "source_type": "schedule", "is_active": True,
                "schedule": {"cron": "0 0 * * *", "timezone": "UTC"},
                "subscriptions": [{"id": sub_id, "target_type": "workflow", "is_active": True}],
            }],
        )
    await SolutionDeployer(db_session).deploy(bundle(sol_a), force=True)
    # Must NOT raise a duplicate-PK IntegrityError:
    await SolutionDeployer(db_session).deploy(bundle(sol_b), force=True)
    # And the two installs hold DISTINCT subscription ids:
    assert solution_entity_id(sol_a.id, uuid.UUID(sub_id)) != solution_entity_id(sol_b.id, uuid.UUID(sub_id))
```

> `two_seeded_solutions` — two installs in different orgs; build inline mirroring the single-install fixture in `test_solution_deploy_async.py`. If a same-session second deploy needs a commit/rollback boundary, follow that file's pattern.

- [ ] **Step 2: Run test to verify it fails**

Run: `./test.sh e2e`
Expected: FAIL — the second `deploy` raises a duplicate-key `IntegrityError` on `event_subscriptions_pkey`.

- [ ] **Step 3: Remap the subscription own-id in pass 2**

In `api/src/services/solutions/deploy.py`, in `_remapped_bundle`, inside the pass-2 event loop (where `msub` `workflow_id`/`agent_id` are remapped), add remapping of the subscription's own id. After the `for fld in ("workflow_id", "agent_id"):` block, within the same `for msub in ...` loop:

```python
                if msub.get("id") is not None:
                    msub["id"] = str(solution_entity_id(sid, UUID(str(msub["id"]))))
```

(`solution_entity_id` and `sid` are already in scope; `UUID` is imported.)

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh e2e`
Expected: PASS — both installs succeed, distinct subscription ids.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/solutions/deploy.py api/tests/e2e/platform/test_solution_event_sub_remap.py
git commit -m "fix(solutions): remap EventSubscription own-id per install — no duplicate-PK on 2nd install"
```

---

## Task 11: Table orphan adoption cross-solution bleed — NEEDS A DESIGN DECISION (do not blind-fix)

> **HOLD — this one is NOT a clean reproducible-fix.** Verifying the ORM during planning revealed
> the obvious fix would break the feature's intended behavior. Flagged for a decision before any
> code; left in the plan so it isn't lost, but **do not implement Steps blindly.**

**The defect (real):** `_upsert_tables` orphan adoption (`deploy.py:894-905`) matches an orphaned
table on `(origin_solution_slug==slug, name==name, org)`. Slug is author-controlled and unique
only per (slug, org) install — so an unrelated solution sharing a slug+table-name in an org adopts
the previous solution's table rows AND documents (data bleed). Audit HIGH.

**Why it's not a one-liner (the trap):** `tables.py:61-62` states `origin_solution_id` is
*"informational (NOT a FK — the Solution row is gone); origin_solution_slug is the stable reattach
key."* The design INTENT is slug-keying, precisely because a reinstall of "the same solution" gets
a **new** `solution.id` (the old Solution row was deleted). So matching adoption on
`origin_solution_id == solution.id` would **never reattach even the legitimate same-solution
reinstall** — it would silently break orphan recovery, which is the feature's whole point. The
verified facts: `origin_solution_id` exists and is stamped (`solutions.py:778,834`), but is
explicitly NOT meant as the match key.

**The actual question (Jack's call before coding):** slug is doing double duty as both "portable
definition identity" and "the reattach key," and those conflict the moment two solutions share a
slug. Options:
- (a) **Make slug a trusted, unique solution-definition identity** — enforce global slug
  uniqueness (or per-org), so slug-keyed reattach is safe by construction. Changes install
  validation; affects community bundles.
- (b) **Add a stable definition-id that survives reinstall** (distinct from the per-install
  `solution.id`), stamp it on orphan + match on it. New column + capture/deploy plumbing.
- (c) **Require the reinstalling solution to explicitly claim the orphan** (opt-in adoption),
  rather than silent slug-match. Safest against bleed, changes UX.

This is a Phase-1.5 / convergence-adjacent decision, not a clean-foundation one-liner. **Defer
until decided.** When decided, the test below (cross-solution must NOT adopt) plus a sibling
(same-definition reinstall MUST adopt) are the acceptance pair — but the fix body depends entirely
on which option above is chosen, so it is intentionally NOT written here.

```python
# Acceptance test SHAPE (write once the design is chosen):
# - unrelated solution B (same slug, same table name, same org) deploys -> B's table has 0 of A's documents
# - the SAME solution definition reinstalled -> DOES re-adopt its own orphan + documents
# (db_session, two solutions, soft-delete-to-orphan mirroring routers/solutions.py:771-781)
```

---

## Self-Review notes

- **Spec coverage:** Tasks 1-2 = §8 Bug B (blank-name silent swallow + lying count). Tasks 3-5 = §8 Bug C / §4.1 (tool_description end-to-end). Tasks 6-7 = §9 dead-code removal manifest (endpoint + function + 2 helpers + their tests). Task 8 = generated-types hygiene. Bug A in §4.2 ("name behaves differently") is the same root cause as Bug B and is resolved by Task 1's validation (the indexer now matches the REST `min_length` contract).
- **Stability audit (Tasks 9-11):** Task 9 = audit CRITICAL (git-sync events wipe, live-confirmed, 1-line). Task 10 = audit HIGH (EventSubscription PK reuse, 2nd-install 500). Task 11 = audit HIGH (table orphan cross-solution bleed) — **ON HOLD pending a design decision** (the obvious fix breaks legitimate same-solution reattach by design; slug is intentionally the reattach key). Execute Tasks 9-10; do NOT implement Task 11 until the slug-identity question is decided.
- **Deferred to convergence (NOT in this plan):** the shared/`_repo`/field-parity audit findings (`auto_fill` drop via shared FormIndexer, agent-delegation order via shared single-pass, `tool_description`/`max_run_timeout`/`event_type`/`display_name` parity). These become the convergence acceptance checklist (spec §18.2 + audit triage).
- **Out of scope (later phases, intentionally):** `EntityWriter` (§6.1), `EntitySerializer` (§11), the bucket-B capability decisions (§10.3), `/api/export-import` consolidation. Phase 1 is the clean foundation only.
- **Preservation guard:** Tasks 6-7 each include an explicit grep step proving no live caller before deletion and an explicit grep proving the shared `ManifestResolver`/`_diff_and_collect`/content-helpers survive. This is the "extract the dead wrapper, keep the shared core" requirement from §9.2.
- **Test-filter caveat:** e2e cannot be filtered to one path (`./test.sh e2e` runs the suite); read `/tmp/bifrost/test-results.xml` for individual results. Unit tests CAN be filtered by `::test_name`.
