# Solution workflow resolution: one early resolve, one last-DB-grab enrichment

**Date:** 2026-06-08
**Worktree:** solutions-success-criteria
**Status:** design — awaiting review

## Problem

A Solution-managed app, form, or agent references its workflow by a portable
`path::function` ref (e.g. `functions/dashboard.py::main`), **not** by the
per-install workflow UUID — it cannot know the `uuid5` id assigned at install
time. To run the *install's own* workflow (and not a sibling install's that
shares the path, nor the bare `_repo/` one), the `/execute` path must learn the
caller's **install scope** and feed it to workflow resolution.

Today that scope is derived **only from `request.app_id`**
(`api/src/routers/workflows.py:755–768`):

```python
solution_scope: UUID | None = None
if request.app_id:
    solution_scope = (await db.execute(
        select(Application.solution_id).where(Application.id == app_uuid)
    )).scalar_one_or_none()
workflow = await workflow_repo.resolve(request.workflow_id, solution_scope=solution_scope)
```

So:

- **Apps** send `app_id` → `Application.solution_id` → correct own-first
  resolution. ✅
- **Forms** and **agents** have **no scope source**. A solution-managed form
  whose `workflow_id` is a `path::fn` ref (forms can hold a portable ref, not
  just a UUID) resolves with `solution_scope=None` → `_resolve_by_path_ref`
  falls to the `_repo/`-preferred branch
  (`api/src/repositories/workflows.py:147–148`) → **the form silently runs the
  `_repo/` workflow, never the install's.** ❌ Agents reach the same dead end
  through their tool-invocation path.

The defect is **entirely at this early resolution**. Everything downstream is
correct: once the right workflow row is picked, its `solution_id` already rides
the `ExecutionContext` (landed 2026-06-07, commit `7a8e7dab`), the module
loader already roots code at `_solutions/{id}/` with the `global_repo_access`
fallback, and table/config resolution already resolves own-first. The engine is
being fed the **wrong row's UUID**, then faithfully propagating that wrong row's
facts (`solution_id = None`).

## Secondary problem: a redundant second DB grab

The execution consumer fetches the workflow once for metadata, then opens a
**second** DB session to fetch the same install's `global_repo_access`:

```python
# api/src/jobs/consumers/workflow_execution.py
workflow_data = await get_workflow_for_execution(workflow_id, db=db)   # grab 1 (L553)
...
solution_id = workflow_data.get("solution_id")
if solution_id:
    async with get_db_context() as db:                                 # grab 2 (L574)
        solution = await SolutionRepository(db).get_by_id(solution_id)
    solution_global_repo_access = solution.global_repo_access
```

`get_workflow_for_execution` (`api/src/services/execution/service.py:126`) is
the **last DB-touching moment before the engine runs** — the engine subprocess
has no DB access by design. It already reads `solution_id` off the row but
stops short of deriving `global_repo_access`, forcing the consumer to re-hit the
DB for a fact that belongs to the same enrichment.

## Design principle

> The caller supplies only **`(solution_id, path::function)`**. A single,
> repeatable **last-DB-grab** turns the resolved workflow into the *complete,
> DB-free set of install facts* the engine obeys.

The caller does not — and should not — know about `global_repo_access`, code
roots, or any engine plumbing. It knows its own install and the ref. Two DB
touches, clear division of labor:

1. **Early resolve** — `(solution_id, path::fn) → definitive workflow UUID`.
   The only place install scope is *needed*; the only thing the caller is
   responsible for. Own-first logic already exists in `_resolve_by_path_ref`.
2. **Last-DB-grab enrichment** — given that UUID, fetch the row one final time
   and derive *everything the engine needs* from it: `solution_id` (where its
   code lives first), `can_access_global_repo` (whether `_repo/` fallback is
   allowed), org, timeout, type. One chokepoint, every caller benefits, no
   duplicated lookups.

## Changes

### Fix A — forms & agents supply install scope to the early resolve

`solution_scope` at `/execute` must be derivable for **all three** solution
caller archetypes, not just apps. Two sub-parts:

- Add an explicit **`solution_id`** field to the execute request
  (`api/src/models/contracts/executions.py`, alongside the existing `app_id`).
  A solution **form** and a solution **agent** set it from their own
  `solution_id` when invoking. This is the direct, caller-knows-its-install
  path and mirrors how `ExecutionContext.solution_id` already works for a
  solution *workflow* calling the SDK.
- In `workflows.py`, derive `solution_scope` from **`request.solution_id` first,
  then fall back to the `app_id → Application.solution_id` derivation** (apps
  keep working unchanged). Same org-gating already in place: a foreign/typo'd
  scope simply yields no narrowing → the caller 404s rather than crossing
  installs.

No change to `_resolve_by_path_ref` — it already disambiguates own-first given a
scope.

### Fix B — make `get_workflow_for_execution` the single enrichment chokepoint

`get_workflow_for_execution` returns `can_access_global_repo` derived from the
resolved workflow's `solution_id`, in the **same** DB session as the metadata
fetch. The redundant `SolutionRepository.get_by_id` block in
`workflow_execution.py:574` is **removed**; the consumer reads
`workflow_data["can_access_global_repo"]` instead.

Resulting returned dict gains one key:

```python
"can_access_global_repo": <bool>,   # False when not solution-managed
```

derived via a join/lookup to `Solution.global_repo_access` when
`workflow_record.solution_id` is set, else `False`. One DB session, one
enrichment site.

### Open question (NOT part of this change) — should data fallback be gated at all?

Today `global_repo_access` governs **only code** resolution (the virtual module
loader's `_repo/` fallback). **Tables, configs, and storage have no fallback
gate at all** — a solution reads `_repo/` data regardless of the flag. This is
an asymmetry: a "sealed" install (`global_repo_access=False`) still cannot
import `_repo/` code but *can* read a `_repo/` table or config.

Whether to close that asymmetry is an **open design question**, deliberately
out of scope for this spec. Three live options:

1. **Reuse `global_repo_access`** to gate data fallback too — one flag, "can
   this install touch `_repo/` at all" (code + data). Simplest mental model;
   risks over-coupling code-sharing and data-sharing decisions that an author
   may want to make independently.
2. **Add a separate data-fallback flag** — e.g. `global_data_access` — so code
   and data sharing are independently toggleable. More expressive, more surface.
3. **Do nothing** — accept the asymmetry; data fallback stays ungated. Maybe
   the current behavior is correct and code is the only thing worth sealing.

Fix B makes whichever flag is chosen cheap to carry to the engine (it already
becomes a first-class enriched fact), so this question does **not** block A/B.
It needs its own brainstorm + decision before any implementation.

## Out of scope

- **Per-org `global_repo_access` override.** Confirmed not wanted — the flag is
  per-install only.
- **Embed-token path.** A separate verification: confirm an app embedded via
  token (not the `X-Bifrost-App` header) still reaches `/execute` with a scope
  source. Tracked as a check in the plan, not a change here.
- Any change to the engine subprocess, module loader, or downstream table/config
  resolution beyond Fix C — they already obey the carried facts correctly.

## Testing

- **Fix A:** e2e — a solution-managed **form** (workflow_id = `path::fn`) and a
  solution **agent** each resolve and run the *install's own* workflow, not the
  `_repo/` one; a sibling install sharing the path is never hijacked; a foreign
  `solution_id` 404s (org gate). Mirror the existing app-path coverage.
- **Fix B:** unit — `get_workflow_for_execution` returns
  `can_access_global_repo` matching the install for a solution workflow and
  `False` for a `_repo/` workflow; assert the consumer no longer opens a second
  DB session (no `SolutionRepository.get_by_id` call on the execution path).
(The data-fallback open question has no tests here — it isn't a planned change
until its own brainstorm resolves which option, if any, to build.)

## Affected files

| File | Change |
| ---- | ------ |
| `api/src/models/contracts/executions.py` | add `solution_id` field to the execute request |
| `api/src/routers/workflows.py` (~755) | derive `solution_scope` from `request.solution_id` then `app_id` |
| `api/src/services/execution/service.py:126` | `get_workflow_for_execution` also returns `can_access_global_repo` |
| `api/src/jobs/consumers/workflow_execution.py:574` | remove redundant `SolutionRepository` grab; read enriched field |
| forms/agents invocation sites | set `solution_id` on the execute request when solution-managed |

The data-fallback open question, if it resolves to a build, would additionally
touch the table/config/storage read paths and the gate-2 README prose — but
that is a separate spec, not this one.
