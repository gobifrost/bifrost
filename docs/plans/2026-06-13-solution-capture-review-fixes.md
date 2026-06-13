# Solutions capture/claims review fixes (implementation brief)

Date: 2026-06-13
Branch: `worktree-solutions-success-criteria` (the active Solutions worktree)
Source: Fable code review of the uncommitted "workflows-by-name → capture → update" work,
plus an independent Codex review pass (Task 6). All findings verified against the code.
Jack-confirmed dispositions are in Obsidian `Projects/Bifrost/Platform Overhaul/subplans/Solutions.md`
(§ "2026-06-13 Fable code review").

**Execution model:** a Fable session IMPLEMENTS these tasks directly. After each logical chunk,
gate with an independent **Codex review** (`codex review --uncommitted`, read-only — the /codex
skill) and triage its findings before moving on. Codex is the reviewer, not the implementer.

Follow repo `AGENTS.md`/`CLAUDE.md`: worktree rule, `./test.sh` for tests, full pre-completion
verification. All work is on the existing branch (do NOT branch, do NOT commit/push unless asked —
leave it as reviewable working-tree changes).

There are SIX tasks (Tasks 1–5 from Fable's review + Task 6's four sub-items from Codex's
independent pass).

## Parallelization plan (file-overlap analysis → 4 independent lanes)

`api/src/services/solutions/capture.py` is touched by **five** items (T3, T5, T6a, T6b, T6c),
so those CANNOT be parallel — they share one file and must be one agent/lane. The rest touch
disjoint files. Net = **4 parallel lanes**, then T4, then the Codex review gate:

| Lane | Items | Files (disjoint across lanes) | Why grouped |
|------|-------|-------------------------------|-------------|
| **A — capture.py** | 6a, 6b, 6c, 3, 5 | `capture.py`, capture tests, reuse `api/bifrost/solution_vendoring.py` + `cli.py` skip-list | all mutate `capture.py`; do 6a (P1 `.env` leak) first within the lane |
| **B — workflows** | 6d | `api/src/repositories/workflows.py` (+ test) | disjoint file |
| **C — claims UI** | 1 | `client/src/pages/TablesClaimsTab.tsx` (+ test), `api/src/routers/claims.py` (`list_claims`) | disjoint files |
| **D — contract** | 2 | `api/shared/contract_version.py`, `api/bifrost/contract_version.py`, `api/tests/unit/test_contract_version.py` | disjoint files |

**Then T4 (capture/export dependency preview + walker)** — touches `api/src/routers/solutions.py`
+ a new walker module + `SolutionCaptureDialog.tsx`. Disjoint from Lane A's `capture.py`, but it
codes against the capture semantics Lane A settles, so run it AFTER A merges (or concurrently
against the brief spec if you accept minor rework). It's the heaviest single item.

**Execution mechanics (important — this repo's constraints):**
- Lanes write files concurrently → run each in its OWN git worktree (`isolation: 'worktree'`)
  so they don't collide. Files are disjoint across lanes, so merge-back is content-clean.
- **Do NOT run `./test.sh` in two lanes of the SAME worktree concurrently** — the pre-run state
  reset nukes the in-flight suite (CLAUDE.md + auto-memory). Worktree isolation avoids this:
  each lane has its own test stack. If lanes share a worktree, serialize their test runs.
- `client/src/lib/v1.d.ts` only needs regen if a contract shape changes (Lane D bumps the
  version but the claims contract change is already in the tree). Regen ONCE after all merges,
  not per-lane.
- After all lanes merge + T4 lands: ONE full pre-completion verification pass, then Codex review
  gate (`codex review --uncommitted`, read-only) on the whole consolidated diff.

Within a non-parallel single session, the equivalent order is: 6a first, then 1/2/6d, then
6b/6c, then 3/5, then 4 — each with its own tests, full verification at the end.

---

## Task 1 — Custom Claims managed affordance (🔴)

**Problem.** The org-level Custom Claims tab lets you Edit/Delete solution-managed claims and shows
no Managed badge. The claim row already carries `is_solution_managed` (computed from `solution_id`).

**Fix (frontend).** `client/src/pages/TablesClaimsTab.tsx`:
- Render `SolutionManagedBadge` (same component the other entity tabs use — grep for it) next to a
  claim's name when `claim.is_solution_managed`.
- Gate the Edit (line ~333) and Delete (line ~348) controls on `!claim.is_solution_managed`, exactly
  as the Workflows/Forms/Apps tabs gate their controls.

**Fix (backend, the twist).** `api/src/routers/claims.py::list_claims` (~line 181) is the ONLY claims
query that does NOT filter `solution_id IS NULL` (every other CRUD op does). Decide with the
frontend: simplest is to keep listing managed claims (so they're visible with the badge) — then the
frontend gate above is sufficient and the existing get/update/delete `solution_id IS NULL` filters
already make the server reject mutations. Confirm the 404-on-managed-mutation path returns a clean
error, not a 500.

**Tests.** Extend `client/src/pages/TablesClaimsTab.test.tsx` (or sibling) — managed claim shows
badge, no Edit/Delete; loose claim unchanged. Run `./test.sh client unit TablesClaimsTab`.

---

## Task 2 — Contract version bump for the claims widening (🟡→do it)

**Problem.** `CustomClaim.organization_id` changed `UUID → UUID | None` (this is the enabling change
for **global claims**, previously impossible). The tripwire fingerprint was refreshed silently.

**Fix.** Bump `CONTRACT_VERSION` in BOTH `api/shared/contract_version.py` and
`api/bifrost/contract_version.py`, then refresh `EXPECTED_CONTRACT_FINGERPRINT` in
`api/tests/unit/test_contract_version.py`. Add a one-line comment at the bump site:
"claims organization_id widened to nullable for global/solution-managed claims (2026-06-13)".
Run `./test.sh tests/unit/test_contract_version.py`.

---

## Task 3 — Capture import-closure: opt-in, not blind-glob (🟡 + bug)

**Problem.** `api/src/services/solutions/capture.py::_python_files` (~line 359) globs ALL of
`modules/*.py` into every export, regardless of what the captured workflows import.

**Decision (Jack).** There are exactly **two modes**, both closure-scoped (never folder-scoped):
- **Entities** (DEFAULT) — only the captured workflows' own `path` files. No modules.
- **Entities + Shared Imports** (opt-in) — recursively every module reached by following imports:
  imported directly by a captured workflow, by those modules, and so on (transitive closure).

Critically, "Shared Imports" pulls **only what is actually imported via the closure**, NEVER the whole
`modules/` tree — modules are *often intentionally global* and a module nothing in the solution imports
must never be bundled, in either mode. This applies to **export too** (Task 4 surfaces the same two
modes in the preview — do NOT auto-bundle imports on export either).

**Fix.**
- DELETE the blind `modules/` glob in `_python_files` (~line 359). It must never pull the whole tree.
- Use the existing recursive import scanner (the "vendoring shared-dep scanner" — grep `vendor`/`scan`
  under `api/src/services/solutions/`). It walks `from modules.x import …` / `import modules.x`
  transitively. Reuse it; do not write a new one.
- `_python_files` (and the capture/export selectors/request) take an `include_imports: bool = False`.
  False → only the captured workflows' own `path` files. True → add the transitive import closure
  (the scanner's output), nothing more.
- Caveat to document: the scan is static (source/AST). Dynamic imports
  (`importlib.import_module(var)`) are invisible — this is why the preview (Task 4) lets the human add
  a missed file manually.

**Tests.** Unit: capture a workflow that imports `modules/a` which imports `modules/b`; assert
default bundles neither, `include_imports=True` bundles both, and an *unrelated* `modules/c` is never
bundled. `./test.sh tests/unit/test_solution_capture.py`.

---

## Task 4 — Dependency preview for capture AND export (🔴 — replaces the missing scope guard)

**Problem.** Capture has no reverse-dependency/scope guard (capture-design §3.2) and no walker (§3.3);
`/capture/candidates` is a flat dump of all loose same-scope entities.

**Decision (Jack).** Both capture and export get a **deselectable preview** of what will be grabbed,
with outside-reference warnings. The preview IS the guard.

**Fix (backend).** `api/src/routers/solutions.py` candidates endpoint + a walker in
`api/src/services/solutions/` (reuse the import/`sdk.tables.*`/`useWorkflow` string scanners that
already exist in spirit for vendoring):
- Given a seed selection, compute the dependency closure (workflows→modules/tables/configs;
  forms→workflows; apps→workflows) and the **reverse** references (entities OUTSIDE the selection
  that reference something IN it).
- Return, per candidate: what it pulls in, and any outside-reference warning ("`orders` table is also
  used by workflow `nightly-sync` which is NOT being captured").
- Capture endpoint stays explicit/deselectable; outside-referenced entities are flagged, not
  silently blocked (the human is the authority via the checklist — matches §3.3).

**Fix (frontend).** `client/src/components/solutions/SolutionCaptureDialog.tsx` — render the grouped
preview with per-item checkboxes and the warnings, instead of the current flat list. (If export gets
a parallel dialog, share the preview component.)

**Tests.** Unit for the walker (closure + reverse refs). Component test for the dialog preview.
`./test.sh tests/unit/...` + `./test.sh client unit SolutionCaptureDialog`.

---

## Task 5 — Capture re-stamps org (global → org) with a visible warning (🟡→do it)

**Problem.** `capture.py:120` *rejects* any entity whose `organization_id != solution.organization_id`.
That blocks the common migration case: an **org-scoped solution** capturing a **global `_repo`
entity** (a loose global table that really belonged to one customer's portal).

**Decision (Jack).**
- ALLOW capturing a **global** entity (`organization_id IS NULL`) into an **org-scoped** solution,
  and **re-stamp** the entity's `organization_id` down to the solution's org as part of capture.
- Surface it explicitly in the preview ("moves `orders` from global → Org A").
- Capturing an **org-A** entity into an **org-B** solution stays **REFUSED** (cross-tenant, never).
- Org-matching and global→global cases are unchanged.
- Capture continues to re-stamp `solution_id` in place; Documents/rows are never touched (table data
  is captured in place, not copied).

**Fix.** In `_capture_model`, replace the flat `org_id != solution.organization_id` rejection with:
- `org_id == solution.organization_id` → ok (current behavior).
- `org_id IS NULL and solution.organization_id is not None` → ok, ALSO set `organization_id` in the
  same `update(...).values(...)` (and any org-stamped child rows that need it — check Documents'
  org handling for tables; rows themselves stay).
- otherwise (different concrete org) → keep raising `SolutionCaptureConflict`.

**Tests.** Unit per case in `test_solution_capture.py`: org-match ok; global→org re-stamps org +
solution_id; org-A→org-B refused; global→global solution keeps org NULL.

---

## Task 6 — Capture export fidelity (from Codex independent review, 2026-06-13)

All four verified real against the code; zero false positives. Concentrated in the capture
export path — complementary to Tasks 1–5, almost no overlap.

**6a [P1] `.env`/build files leak into captured exports** — `capture.py::_app_source_files`
(~383) reads EVERYTHING under the app's repo prefix with no filtering. The canonical collector
honors an exclusion list (`api/bifrost/cli.py:~50-69`: `node_modules/`, `dist/`, `build/`,
`.bifrost/`, caches, `.env*`, `.DS_Store`, `*.pyc`). Capture must apply the same skip list before
reading/serializing files. **Secret-leak + bloat path — fix first.** Test: capture an app whose
repo prefix contains `.env` and `node_modules/x` → neither appears in `src_files`/`bin_files`.

**6b [P2] Captured app logos don't round-trip** — `_app_entries` (~264) omits
`logo_b64`/`logo_content_type`. Deploy treats absent logo as "clear it" (`deploy.py:_decode_logo`
~62), so capture → re-deploy wipes the icon. Include the app's `logo_data`/`logo_content_type`
(base64) in the entry. Test: capture app with a logo → export carries it → redeploy keeps it.

**6c [P2] Role bindings export UUIDs only, no `role_names`** — `_role_ids` (~390) emits raw role
UUIDs. Cross-env install FK-fails or binds the wrong role. This is the SAME `role_names:`
portability bug the team already fixed once for workflows.yaml (see subplan history). Emit
`role_names` alongside the UUIDs (resolve Role.name for each id), matching what the deployer can
consume. Test: captured role-based entity exports role names; install in a fresh env binds by name.

> **6d disposition (2026-06-13, implementer):** FALSE POSITIVE — reverted. The premise
> ("uniqueness is on path+function, not name") is wrong on this branch. A committed partial
> unique index `uq_workflows_solution_name ON (solution_id, name) WHERE solution_id IS NOT NULL
> AND is_active` (migration `20260604_add_solutions.py`, commit `20007c3e` — a *prior* Codex
> sub-plan-1 review added it) guarantees ≤1 active match, so `scalar_one_or_none()` under
> solution_scope can never raise `MultipleResultsFound`. The org/global `_repo` namespaces are
> covered by `uq_workflows_org_name` / `uq_workflows_global_name` likewise. A test trying to
> insert the duplicate fails at the DB (`UniqueViolationError`). Kept `scalar_one_or_none()`
> with an explanatory comment naming the index; no defensive fallback added (would be an
> unrequested fallback for a DB-impossible state). Codex's 6d finding and the prior index-adding
> review were blind to each other.

**6d [P2] Duplicate workflow names → MultipleResultsFound 500** — the bare-name resolver
(`workflows.py:124`) uses `scalar_one_or_none()` under `solution_scope`. The DB allows two active
workflows with the same `name` (uniqueness is on `path+function`, not name), so this raises
`MultipleResultsFound` → uncaught 500. Make it handle ambiguity explicitly (controlled 404 /
ambiguity error, mirroring the path-ref resolver's `ed33fefa` refuse-ambiguous behavior). Test:
two same-name active solution workflows → bare-name resolve returns a clean error, not a 500.

---

> **T4 disposition (2026-06-13, implementer):** DONE — full walker built (Jack chose
> "build the scanners"). New: `ref_scanner.py` (string scanners for `tables.get`/`useTable`,
> `config.get`, `useWorkflow`/`useWorkflowQuery`/`useWorkflowMutation`), `dependency_walker.py`
> (`SolutionDependencyWalker` — transitive forward closure + reverse-ref warnings), preview DTOs
> in `solutions.py` contracts, `POST /{id}/capture/preview` endpoint, and a dialog rewrite
> (grouped preview + outside-ref warnings + `include_imports` toggle). A Codex review pass on the
> first walker draft found 7 real issues — all fixed: forward closure made transitive (workflow
> worklist), `_form_workflows` covers `launch_workflow_id`, agent reverse-refs added, reverse-refs
> compare against the effective closure (not just seed), `_load_configs` tightened to the
> candidates filter (`integration_id`/`config_schema_id` IS NULL), scanner catches the Query/Mutation
> hooks. The comment/string false-positive in the regex scan is accepted (documented static-scan
> limit; the human-deselectable preview is the backstop). Tests: `test_solution_ref_scanner.py`,
> `test_solution_dependency_walker.py`, `SolutionCaptureDialog.test.tsx` (preview + toggle).

## Verification (all tasks)

```
cd api && pyright && ruff check .
cd ../client && npm run tsc && npm run lint
cd .. && ./test.sh all && ./test.sh client unit
```
Regenerate types if any contract changed: `cd client && OPENAPI_URL=<debug-url>/openapi.json npm run generate:types`.
