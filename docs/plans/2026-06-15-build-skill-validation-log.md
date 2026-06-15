# Build-Skill Validation Log

Empirical validation of the rebuilt `bifrost:build` skill (Tasks 11–12). Fresh
Sonnet subagents build real artifacts against the debug stack
(`http://localhost:37791`, port mode) following ONLY the skill. Done bar per
track: **3 consecutive clean runs with no skill-doc edits between them.** Any
misleading-moment fix resets the streak to 0.

## SDK-surface coverage target
- Python SDK: 71 public methods across 14 namespaces (`generated/python-sdk-signatures.md`)
- Web SDK: 22 exports (`generated/web-sdk-surface.md`)
- The union of Track A + Track B must exercise the surface; gaps logged with a reason.

### Python SDK namespace checklist (tick when a run drives it)
- [ ] agents (run)
- [ ] ai (complete/stream/get_model_info)
- [ ] config (get/set/list/delete)
- [ ] events (emit)
- [ ] executions (get/list/get_current_logs)
- [ ] files (list/read/write/delete/exists/get_signed_url)
- [ ] forms (get/list/...)
- [ ] integrations (get/...)
- [ ] knowledge (search/...)
- [ ] organizations (get/list/...)
- [ ] roles (get/list/...)
- [ ] tables (get/insert/update/delete/query)
- [ ] users (get/list/...)
- [ ] workflows (run/...)

### Web SDK export checklist (tick when a run drives it)
- [ ] BifrostProvider · useBifrostContext · BifrostHeader
- [ ] useWorkflow / useWorkflowQuery / useWorkflowMutation
- [ ] useTable / useInfiniteTable
- [ ] tables CRUD (get/insert/update/delete + error classes)
- [ ] (remaining exports per `generated/web-sdk-surface.md`)

## A1 skill-doc findings to apply during the loop (queued)
1. **capture→pull→deploy** is now the real flow — **DONE** (Task 7 rewrote solutions.md). Verify a run follows it cleanly.
2. **capture org-scope rule**: a global (`organization_id: null`) entity isn't capturable into an org-scoped install without re-stamp; same-org required. Document if a run trips on it.
3. **`solution start [APP_SLUG]`** positional needed with multiple apps — document if a run trips.
4. The "don't edit .bifrost/" vs "manually add a workflow UUID entry" contradiction — reconcile if a run trips (pull should now obviate manual edits).

---

## Track A — Solution build (read-only invariant in force)

Goal: `solution init` → scaffold a Tailwind-styled app → get an agent + table +
form/config into the solution → `solution start` + drive → update an entity →
`solution deploy`. Pin down the entities-into-a-solution open question.

| Run | Result | Styled | Entities | Update | Deploy | Invariant | Misleading moments → fix | Streak |
|-----|--------|--------|----------|--------|--------|-----------|--------------------------|--------|
| A1 | PARTIAL | Tailwind configured, sample uses inline styles | workflows round-trip; table/form/agent/config **captured then DELETED by next deploy** | yes (workflow) | workflows+app clean; **captured entities destroyed** | ✓ 409 on solution-managed update | see below — **blocked on platform bug** | 0 |
| A2 | INVALID (wrong skill) | yes | **all 4 round-trip + survive** ✓ | yes ✓ | clean ✓ | ✓ 409 | tested the STALE installed plugin, not the rebuilt worktree skill — see note | 0 |
| A3 | NEEDS-FIX (valid) | yes (manual Tailwind) | **all 4 round-trip + survive** ✓ | yes ✓ | clean ✓ | ✓ 409 | 4 real doc fixes (below) → applied, streak reset | 0 |
| A4 | NEEDS-FIX (valid) | yes (styling callout WORKED) | **all 4 round-trip + survive** ✓ | yes ✓ (.bifrost edit path) | clean ✓ | ✓ 409 | 3 fixes: pull `--org`, entities.md `.bifrost` contradiction, scaffold `src/` tree → applied | 0 |
| A5 | NEEDS-FIX (1, self-inflicted) | yes (styling + file layout matched) | **all 4 round-trip + survive** ✓ | yes ✓ | clean ✓ | ✓ 409 | 1 fix: the "capture by id re-stamps global" claim was FALSE → corrected | 0 |
| A6 | NEEDS-FIX (1) | yes | **all 4 round-trip + survive** ✓ | yes ✓ | clean ✓ | ✓ 409 | drove app in **real browser** ✓; 1 fix: deploy-first ordering for a form→workflow ref → applied | 0 |
| A7 | NEEDS-FIX (1) | yes (Tailwind v4 compiled, curl-verified) | table+form round-trip + survive ✓ | yes (.bifrost edit) ✓ | clean ✓ | ✓ 409 (forms+tables) | 1 fix: deploy does NOT auto-register a NEW `functions/*.py` workflow (only manifest-listed ones) → register+capture+pull is the real flow → applied | 0 |

### A7 — first run against the FINAL `--org`/scope docs; 1 real fix (new-workflow registration)

A7 confirmed the **unified `--org` standard reads true**: `solution init` has no `--scope`; omit→home org (Provider `…0002`), `--org "Provider"` resolved, `--global`/`--org none` accepted, `--organization` synonym works, and the pull/deploy "same `--org`" guidance matches behavior. Tailwind v4 compiled + served (curl-verified the dev origin returned the SPA + transformed source with the used utility classes). Table + form captured→pulled→**survived** deploy; `.bifrost` field-edit update redeployed cleanly; read-only 409 invariant held on live `forms update` AND `tables update`. Chrome not attempted (host not configured) — curl fallback used, per protocol (ENVIRONMENT, not a skill bug).

**The one real misleading moment (VERIFIED at code level):** the skill implied "write the workflow in `functions/` → deploy once registers it." A7 wrote `functions/tasks.py` (two `@workflow`s) and deployed — only the **scaffold's** `hello.py::main` registered; `tasks.py` was silently ignored. Root cause confirmed in `solution.py:702` `_collect_workflows`: deploy creates a workflow **row** ONLY for functions listed in `.bifrost/workflows.yaml` (it bundles all `functions/*.py` source but never auto-scans/registers new files). The scaffold pre-writes the sample's manifest entry (`solution.py:162`), so the sample works — but a NEW workflow needs `bifrost workflows register` then `solution capture --workflow` (capture operates on an existing row — `capture.py:130`), i.e. the SAME capture→pull→deploy road as tables/forms/agents. → Fixed both the §3 "Write workflows" section and the form→workflow ordering note to distinguish scaffold-sample (pre-registered) from new workflows (register+capture+pull), and explicitly steered AWAY from hand-writing UUID-keyed manifest entries (A7's instinct, but the skill's own anti-pattern — `pull` materializes them, server assigns the UUID). Lint 0, mirror synced. Streak resets to 0.

**Platform notes (NOT skill bugs — for the platform side):**
- **`solution start` silently skips a workflow whose import fails** (`from bifrost import sdk` → "could not import functions/tasks.py: cannot import name 'sdk'"), discovering only the working function with no loud user-facing error. A7's import was its own mistake (`sdk.tables` vs `from bifrost import tables` — skill is correct), but the silent-skip is a UX gap: a hard error or "check your imports" warning would help.
- **`pull --org none` printed "2 entity(ies)" alongside "0 manifest files"** when targeting global scope with no global install — a stale/cross-scope count in the message. Minor display inconsistency.

| A8 | NEEDS-FIX (3) | yes (Tailwind, curl-verified) | table+form+**custom workflow** round-trip + survive ✓ | yes (.bifrost edit) ✓ | clean ✓ | ✓ 409 (forms+tables) | 3 fixes: (1) new-workflow `register` needs source on platform → deploy-first; (2) apps.md stale `main.tsx` boot; (3) `start --port` = proxy, Vite = port+1 → all applied | 0 |

### A8 — tested A7's new-workflow fix; 3 real fixes (all VERIFIED at code level)

A8 followed A7's corrected new-workflow flow and surfaced that the fix was *incomplete*, plus two adjacent stale spots. All three verified in source before fixing:

1. **`workflows register` reads the `.py` from the PLATFORM file store, not local disk** (`workflows.py:1212` `service.read_file` → 404 "File not found"). A7's fix said register→capture→pull but omitted that the source must be uploaded first. A8 hit the 404. A `solution deploy` uploads all `functions/*.py` source (`deploy.py:680` `_write_python` ← `_collect_python_files` sweeps the whole root, `solution.py:691`), so the solution-native prerequisite is **deploy-first**, NOT `bifrost push` (which A8 suggested but is GLOBAL_BANNED in skill docs — `lint_claims.py:23`). → Rewrote the §3 note + the ordering note to: write → `solution deploy` (uploads source) → `workflows register` → capture → pull → deploy.
2. **apps.md `main.tsx` example was stale** (`window.__BIFROST_API_URL__`/`__BIFROST_TOKEN__` props). The scaffold's real `main.tsx` (`solution.py:338-358`) uses the nonce-keyed `window.__BIFROST_APPS__[__m]` registry with `boot?.baseUrl/token/orgScope/mountEl` + Vite-env fallbacks (multi-app, per-viewer token). → Replaced the example with the real boot protocol + "keep the scaffolded main.tsx, don't hand-roll it."
3. **`solution start --port` is the PROXY port; Vite binds to `--port + 1`** (`solution.py:1664` `vite_port = port + 1`). Undocumented. → Added a note: drive the app at the proxy port the command prints, not the Vite port.

A8 also re-confirmed the `--org` standard (init no `--scope`; home/global/org as documented; pull/deploy same-org guidance accurate), table+form+**custom workflow** all survived deploy, `.bifrost`-edit update redeployed, read-only 409 held on forms+tables. Drive: curl (A8 mis-read the stack as netbird and skipped Chrome — the debug stack is port mode/Chrome-drivable; an A8 environment-judgment miss, not a skill finding). Lint 0, mirror synced. **Platform bug (not skill):** `workflows register` 500s (should 409) on a duplicate-name register of an existing solution-managed workflow.

| A9 | NEEDS-FIX (1) — corrected MY OWN A8 error | yes (Tailwind v4, curl) | table+form+**custom workflow** round-trip + survive ✓ (via push→register→capture) | yes (.bifrost edit) ✓ | clean ✓ | ✓ 409 (forms+tables) | A8's "deploy-first then register" was WRONG → empirically re-derived + corrected to the manifest-entry path | 0 |

### A9 — caught my A8 fix was WRONG; empirically re-derived the real new-workflow flow

A9 followed A8's "deploy-first → `workflows register`" flow and hit **404 "File not found"** on register — proving A8's fix was factually wrong. I had mis-read the code TWICE. Resolved it EMPIRICALLY this time (drove the CLI directly, `/tmp/bifrost-verify-wf`):

- `solution deploy`'s `_write_python` writes source to `_solutions/{id}/` (deploy.py:700), the SOLUTION bundle — NOT the `_repo/` store that `workflows register`'s `service.read_file` reads (workflows.py:1212). So deploy does NOT put the file where register looks; A8's "deploy uploads it for register" was false. A9's `bifrost push` worked (push writes `_repo/`), but `push` is GLOBAL_BANNED in skill docs.
- **The real solution-native path (verified by running it):** `_upsert_workflows` (deploy.py:713) creates the workflow row **directly from the `.bifrost/workflows.yaml` entry** — no `register`, no `push`, no capture. I drove it: wrote `functions/tasks.py`, hand-added a `workflows.yaml` entry (id/name/path/function_name), `solution deploy` → **"2 workflow(s) upserted"**, `my_task` registered + executes. The register→capture→pull road is for adopting a PRE-EXISTING `_repo/` workflow, overkill for one authored in the solution.

→ **Rewrote** the §3 note + the form→workflow ordering note to the manifest-entry path (add a `.bifrost/workflows.yaml` entry → deploy), and corrected the over-broad "don't hand-write manifest entries" warning — adding a NEW workflow row by hand is exactly what the scaffold does and is the intended mechanism (the warning is about not corrupting an EXISTING entity's identity). A8's apps.md `main.tsx` fix and `start --port` fix were CORRECT and stay (A9 kept the scaffolded main.tsx, no issue). Lint 0, mirror synced.

A9 re-confirmed `--org` reads true (init no `--scope`; deploy `--help` shows `--org/--organization/--scope` synonyms + `--global`; `--org none|global` → NULL org). Table+form+workflow survived; read-only 409 held. **Lesson (for me): two wrong code-reads in a row on the same mechanism — should have driven it empirically after the FIRST contradiction, per `feedback_org_scoping_blocker2_retracted`.**

**Platform gap (NEW — for the platform side, NOT a skill bug):** there is **no CLI command to add a new workflow to a solution's `.bifrost/workflows.yaml`** — the scaffold writes the sample entry programmatically, but a builder authoring a 2nd+ workflow must hand-edit the manifest (or take the awkward push→register→capture road). A `bifrost solution add-workflow <path::fn>` (or having `pull` discover decorated functions in `functions/`) would close this. This is the recurring friction A6/A7/A8/A9 all circled.

| A10 | NEEDS-FIX (1) | yes (Tailwind, curl) | table+form+**custom workflow** round-trip + survive + **execute** ✓ | yes (.bifrost edit) ✓ | clean ✓ | ✓ 409 (forms+tables) | the A9 manifest-entry flow WORKED zero-surprise ✓; 1 fix: solutions.md `def main(ctx)` example is wrong (no ctx) → corrected | 0 |

### A10 — the A9 manifest-entry flow WORKED; 1 unrelated fix (`ctx` in the workflow example)

**A9's fix held perfectly:** A10 followed "write the function → add a `.bifrost/workflows.yaml` entry → deploy" verbatim → **"3 workflow(s) upserted"**, both custom workflows registered, solution-managed, and **executed** (`status: Success`, rows confirmed in the table). No `register`/`push`/capture, zero surprises. The recurring new-workflow friction is finally documented correctly. (A10 noted `pull` rewrites the hand-typed UUID with the server's canonical one — expected; added a one-line heads-up so it's not surprising.)

The one finding is an **unrelated, pre-existing** error in the §3 code example: it showed `@workflow def main(ctx):` — a SYNC function with a bogus `ctx` positional param. Following it literally, A10 wrote `def create_task_a10(ctx, title, priority)` and the platform called it `create_task_a10(title=…, priority=…)` → `missing 1 required positional argument: 'ctx'`. VERIFIED against three sources: the scaffold's real sample (`solution.py:49` `async def main():`), `workflows-python.md` (`async def greet_user(name, count=1)` — params are inputs, no ctx), and the module-level SDK (`from bifrost import tables`). → Corrected the example to `async def main():` + added a line that workflows take inputs as parameters, no `ctx`, SDK via top-level imports. Lint 0, mirror synced.

A10 re-confirmed `--org` reads true (init no `--scope`/`--org`; deploy synonyms present; home/org/global as documented), table+form+workflow survived + executed, read-only 409 held on forms+tables.

| A11 | NEEDS-FIX (1) | yes (Tailwind v4, curl) | table+form+**custom workflow** round-trip + survive + **execute w/ working SDK** ✓ | yes (.bifrost edit) ✓ | clean ✓ | ✓ 409 (forms+tables) | wrote the workflow FROM the docs ✓; 1 fix: python-sdk.md `doc["id"]` subscript crashes (DocumentData is attribute-access) → corrected | 0 |

### A11 — wrote the workflow from the docs (not the scaffold); 1 fix (DocumentData subscript)

A11 authored its workflow signature FROM `workflows-python.md` (`async def add_task_a11(title: str, priority: str = "medium") -> dict`, typed params, no `ctx` — A10's fix held) and registered it via the manifest-entry flow (A9's fix held: add `.bifrost/workflows.yaml` entry → deploy → upserted + executed). Both workflows ran `status: Success` with working `tables.insert`/`tables.query` calls. Table+form+workflow survived deploy, `.bifrost`-edit update redeployed, read-only 409 held on forms+tables. `--org` reads true (init no `--scope`).

The one finding is an **isolated, pre-existing** error in `python-sdk.md`: its tables examples used **subscript access** (`doc["id"]`, `doc["data"]`), but `DocumentData` is a pydantic model (`api/bifrost/models.py:283`) — subscript raises `'DocumentData' object is not subscriptable` at runtime (A11's first workflow `Failed`). The correct access is attribute (`doc.id`, `doc.data`, `results.documents`/`.total`). VERIFIED against the model def. → Rewrote the `python-sdk.md` tables block to attribute access + added an explicit "not subscriptable" note + showed reading query results via `.documents` / `.data`. (Note: `references/tables.md` — the dedicated table reference the skill points to for the full model — was already CORRECT, `result.documents[n].data`; only the `python-sdk.md` quick-ref was stale.) Lint 0, mirror synced.

### SDK-example audit + permanent gate (post-A11, before parallelizing)

Exhaustive audit of every Python/TS example across all 12 references vs the real SDK (`api/bifrost/models.py`, namespace modules, `client/src/lib/app-sdk/*`). **2 hard findings**, both fixed: `python-sdk.md` `data.oauth_token` → `data.oauth` (IntegrationData field, models.py:140); `tables.md` error-class import from internal `@/lib/app-sdk/tables` → `"bifrost"` (barrel re-export, index.v2.ts:40). The `doc["id"]` subscript class confirmed contained (only python-sdk.md, already fixed in A11). **Codified into a permanent CI gate** (`lint_examples.py` + `test_skill_examples.py`, commit `3b0c4162`): introspects the live SDK and flags subscript-on-model, nonexistent-method, `ctx`-param-workflow, and internal-path-v2-import in reference code blocks. Rides `test-unit`. **This is the durable fix Jack asked for** — example drift now fails CI, not a validation run.

### W-batch 1 — 3 Sonnet agents in PARALLEL (Workflow `build-skill-validation-batch`); 2 fixes

First parallel batch (bar = 3 concurrent CLEAN against one doc state = "3 consecutive, no edits between"). All 3 agents returned **identical green scorecards** — styled, table+form+custom-workflow survive, workflow executes w/ working SDK, update, deploy clean, read-only 409, `org_standard_ok: true` in all 3. NOT clean: 2 findings (verified at code level):
1. **`apps.md` §11 `bifrost tables create my_tasks`** (bare positional name) — hit by **2 of 3 agents independently**. `tables create` has no positional name arg; needs `--name my_tasks` (`Got unexpected extra argument`). → Fixed to `--name`. (Tried adding positional-arity checking to the claims-linter to catch this class; reverted — 20 false positives from quoted multi-word values + trailing comments, not worth breaking the green gate.)
2. **`apps.md` `main.tsx` example** (agent 1) — my A8 rewrite used `document.currentScript?.dataset?.m` for the nonce, but the scaffold uses `new URL(import.meta.url).searchParams.get("m")` (solution.py:349; `currentScript` is null for platform-loaded module scripts) + passes `appId`/`theme`/`supportsTheme`/`onLogout`. → Replaced with a faithful excerpt of the real scaffold output + "use it verbatim, don't retype from memory." (Same lesson: copy the source of truth, don't approximate.)

Drive: all 3 curl (Chrome MCP denied localhost site-permission — env, not skill). Streak resets to 0; next batch tests these fixes.

### W-batch 2 — 3 Sonnet in PARALLEL; **2 CLEAN, 1 NEEDS-FIX**; 1 fix (+ 1 borderline)

Closest yet: **agents 1 and 2 both fully CLEAN** (every structural check green, `--org` true, workflow executed). Agent 3 → NEEDS-FIX on 1 verified finding:

- **`workflows-python.md` "Lifecycle Commands" (`register`/`replace`/`remap`/`delete`) lacked a workspace-scope qualifier.** SKILL.md routes "write/debug a Python workflow" here, so a solution-workspace builder reading the Register section would run `bifrost workflows register` — which mints a loose `_repo` row that collides with the deploy-owned manifest row and breaks subsequent deploys. The correct solution flow (manifest entry + deploy, "no register/push/capture") is in solutions.md but not cross-referenced from workflows-python.md. → Added a callout at the top of "Lifecycle Commands": in a Solution workspace, register a workflow via a `.bifrost/workflows.yaml` entry + `solution deploy`; the `register/replace/remap/delete` commands are `_repo`-only. VERIFIED the gap (no qualifier existed).

Plus a **borderline finding agent 1 raised as a platform note but is really a doc error** (fixed proactively): the `apps.md` AND `web-sdk-v2.md` app-structure diagrams showed `main.tsx`/`App.tsx` at the app root, but the scaffold writes them under `src/` (`solution.py:648-650`: `src/main.tsx`, `src/App.tsx`, `src/index.css`, `src/lib/utils.ts`; `package.json`/`vite.config.ts`/`index.html` at root). Agent 1 didn't take a wrong action (it read solutions.md + used the scaffold), but the diagrams were factually wrong. → Corrected both diagrams to the real `src/`-based layout.

Other agent-3 platform notes (NOT skill fixes, logged for the platform side): `solution start` crashes with a raw aiohttp OSError (not a friendly "port in use") when port N **or** N+1 is taken — surfaced by parallel agents sharing the host; `solution pull` overwrites `.bifrost/apps.yaml` `repo_path` to match the app slug, so a manual slug rename without renaming the `apps/<slug>` dir → `solution start` FileNotFoundError on next pull. Form-schema select `options` shape (`[{value,label}]` not `['a','b']`) is undocumented (agent 1; their wrong attempt was their own assumption, not skill text — a gap, not a misleading moment).

Streak resets to 0; next batch tests the workflows-python.md scope callout + the structure-diagram fixes. (2/3 clean is the high-water mark — the remaining gaps are doc cross-reference + diagram accuracy, not flows.)

### W-batch 3 — 3 Sonnet in PARALLEL; **3/3 CLEAN — TRACK A DONE** ✅

All three agents built the complete solution end-to-end following ONLY the corrected docs with **zero misleading moments** (`misleading_moments: []` for all 3): styled, table+form+custom-workflow survive + execute w/ working SDK, update, deploy clean, read-only 409, `--org` true. Per the confirmed bar (3 concurrent CLEAN against one doc state = "3 consecutive, no edits between"), **Track A meets the done-bar.** Convergence: W-batch 1 (0/3) → W-batch 2 (2/3) → W-batch 3 (3/3). The workflows-python.md scope callout + the `src/` structure-diagram fixes held.

This batch's only findings were `platform_notes` (NOT skill faults, logged for the platform side):
- `solution deploy` summary line counts only "N workflow(s)/claim(s) upserted" — it OMITS tables and forms even when they deployed (entities are correctly solution-managed; just the printout is incomplete → users may think their table/form changes didn't ship).
- `scaffold-app` completion message prints "Deploy with `bifrost deploy`" but the command is `bifrost solution deploy` (CLI output bug; the docs are correct everywhere).
- `tables list --json` returns `{"tables":[...]}` (wrapped) while other entity `list --json` return bare arrays — an API inconsistency.
- Chrome MCP denies localhost site-permission on this host (every run; env, not skill — curl + `workflows execute` are the working drive).

## TRACK A: COMPLETE (3 consecutive clean, parallel batch W3, no doc edits between)

Cumulative Track-A doc fixes that got here (A1→A11 serial + W1→W3 parallel): capture→pull→deploy is the real entity road; capture scope-rule (author in the install's scope first); `solution start [APP_SLUG]` positional; `.bifrost` update path vs "don't hand-edit"; styling-callout; `src/` file layout; the unified `--org` standard (init no `--scope`; home/global/org; synonyms); new-workflow registration = `.bifrost/workflows.yaml` entry + deploy (NOT register/push/capture, which are `_repo`-only); `async def` no-`ctx` workflow signature; `DocumentData` attribute access not subscript; `data.oauth` not `oauth_token`; v2 error-class import from `"bifrost"`; `tables create --name`; the real scaffold `main.tsx` nonce protocol; workflows-python lifecycle commands are `_repo`-only. Plus the permanent **example-drift CI gate** (`lint_examples.py`) so these don't regress.

### A6 — full clean scorecard (incl. real browser drive); 1 ordering fix

A6 confirmed A3/A4/A5 fixes ALL held, and for the first time the **browser drive succeeded** (localhost:4000, Tailwind classes rendered). Its one finding is a real lifecycle-ordering gap: a form's `workflow_id` must resolve to a **registered** workflow UUID (verified `forms.py` router validates `workflow_id` exists in WorkflowORM), but a fresh solution's `functions/*.py` workflow isn't registered until its first deploy. → Added an "Ordering for a form/agent that references a workflow" note to Path A (deploy once to register → create form/agent → capture → pull → deploy) + the ambiguous-bare-name caveat.

Also landed the **fork-vs-instance** clarity (from the Jack exchange): repo = definition, nothing stamps install identity into it, one slug → N installs (instances), **fork = new slug** for a divergent solution; `scope` only picks global-vs-org *kind* at create (export recomputes it). This makes the "One definition, many installs" section answer the real builder question.

Claims lint 0, mirror synced. Platform design questions logged below (not skill bugs).

### A5 — cleanest run yet; the only finding was my own scope-rule error

A5 verified A4's fixes ALL landed (styling guidance matched, file layout `src/` matched, `.bifrost` update path worked, 409 guard + read-only invariant ✓). Its single finding corrected an error **I** introduced during the Jack scope-rule exchange: I wrote that capturing a global entity by id into an org-scoped install "succeeds with a re-stamp." A5 proved empirically it FAILS with the same candidate-gate error as by-name. Root cause (verified in `capture_cmd`, solution.py:1764): the CLI fetches `/capture/candidates` and resolves selectors against that list BEFORE calling capture — so the service's latent global→org re-stamp path is **unreachable via the CLI**. → Rewrote the "Scope and capture" section to the accurate rule: author the entity in the install's scope first; capture won't fix scope for you. Lint 0, mirror synced.

**This means A5 is effectively a clean run against the skill as it stood before MY edit polluted it** — the loop's own fixes (A3/A4) held. The next run (A6) tests the corrected scope section; barring new findings, the streak begins.

### A4 — A3's styling fix verified clean; 3 new fixes applied (+ the scope-rule correction from the Jack exchange)

A4 confirmed A3's fix #3 landed ("the skill correctly documents that scaffold generates inline styles and says to replace with Tailwind"). Round-trip + 409 guard + read-only invariant + the `.bifrost` update path all ✓. Three new valid fixes, all applied (streak stays 0):
1. **solutions.md Path A** — `bifrost solution pull` needs the **same `--org`** as deploy when the install is in a non-default org; without it pull resolves the WRONG install, downloads stale state, and deploy keeps 409-ing. → Added `--org` to the pull/deploy examples + a "`--org` must match across deploy and pull" note (and the `--solution <id>` escape hatch). VERIFIED against `pull_cmd`'s `_resolve_target_install(slug, scope, deployer_org_id)`.
2. **entities.md `.bifrost/` is export-only** (lines 5 + 315) flatly contradicted solutions.md's update path. → Scoped both to the global `_repo` workspace with an explicit Solution-workspace carve-out pointing to solutions.md.
3. **solutions.md scaffold file tree** listed `main.tsx`/`App.tsx` at the app root; they're under `apps/<app>/src/`. → Corrected to show config-at-root, source-under-`src/`. VERIFIED against the scaffold's file-writing dict.

Plus the **capture scope-rule correction** (from the Jack exchange, committed separately `…e7bbf2f`-prior): capture **re-stamps** a different-scope entity to the install's scope (global→org migration), only cross-tenant is refused; the candidate-list-vs-capture-by-id wrinkle is documented. (Earlier A3 over-generalized "global isn't capturable".)

Claims lint 0, mirror synced, verified_at_sha bumped. **Platform candidate-vs-action note (for the platform side, NOT a skill bug):** `/capture/candidates` hides global entities from an org install, but `capture()` accepts+re-stamps them by id — the list under-reports what the action allows.

### A3 — first VALID run (read the worktree skill directly). Platform fix re-confirmed; 4 doc fixes applied.

Followed `.claude/skills/bifrost-build/SKILL.md` → `references/solutions.md`. Round-trip + 409 guard + read-only invariant all ✓ again. Browser blocked by Chrome localhost permission (ENVIRONMENT, not skill) — verified the app via curl + grepping the deployed bundle for Tailwind classes. Four legitimate doc fixes, all applied this commit (streak → 0):
1. **solutions.md Path A** said author "in a scratch or **global** context" — wrong: global (org-null) entities are NOT capture candidates for an org-scoped install. → Rewrote to require same-org authoring (`--organization <uuid>`), with the candidate-pool rule spelled out.
2. **solutions.md "manifest is machine-managed"** misled — direct `.bifrost/*.yaml` **content** edits ARE the update path for an already-owned entity (live update 409s). → Added an "Updating an already-owned entity" section: edit the field + redeploy; never hand-add/remove UUID keys.
3. **solutions.md scaffold step** didn't warn the scaffold emits inline styles. → Added a callout: Tailwind is wired; replace the inline styles with classes.
4. **entities.md `solution start`** showed `start my-app --org <ref>` without noting `my-app` is a positional app-slug. → Changed to `start [APP_SLUG]` with a clarifying comment.

Linter trap handled: the mode-conditional ban correctly flags any live-mutation verb (`bifrost forms create`, `bifrost agents update`) in a solution-context doc, so the fixes describe those as forbidden/`_repo`-side in prose rather than as literal commands. Claims lint 0, appendices fresh, mirror synced.

### A2 — platform fix VALIDATED LIVE, but tested the wrong skill copy

A2 invoked the `Skill` tool for `bifrost:build`, which resolves to the **stale installed plugin** (`~/.claude/remote/plugins/*/skills/bifrostbuild/` — old flat structure: `app-patterns.md`, no dispatcher), NOT this worktree's rebuilt dispatcher skill. So its "misleading moments" (no Solutions section, llms.txt empty, etc.) describe the OLD skill and are moot.

**What A2 DID prove (the valuable part) — the platform fix works end-to-end against the live debug stack:**
- table + form + agent + config **all captured → pulled → deployed and SURVIVED** (the exact bug A1 found is fixed).
- deploy **409-blocked** post-capture/pre-pull naming all 4 entities ("Run `bifrost solution pull`, then deploy"); `bifrost solution pull` cleared the queue and unblocked the deploy.
- update round-tripped; read-only invariant (409 on live solution-managed `tables/forms/agents update`) holds.
- One platform note: a fresh debug stack needed the `20260615_pending_captures` migration applied (restart init+api) — expected for a new migration on a live stack (memory `project_debug_stack_migration_apply`), not a code bug.

**Distribution gap surfaced:** the rebuilt skill is correct in the worktree (`.claude/skills/bifrost-build/` + `plugins/bifrost/skills/bifrost-build/`) but is NOT what the `Skill` tool loads — that's the installed plugin, still stale. Validation must point the subagent at the worktree skill FILES directly (read `.claude/skills/bifrost-build/SKILL.md`), which is what A3+ do. Installing the rebuilt plugin is a release-flow step, not part of this branch's diff.

### A1 — pivotal finding (verified at code level)

**The entities-into-a-solution mechanism is broken at the PLATFORM level, not the skill level.**
- `bifrost solution capture` is a pure server call (`POST /api/solutions/{id}/capture`, `commands/solution.py:1581+`) — it sets `solution_id`/`is_solution_managed` on the DB record but does **NOT** write `.bifrost/{tables,forms,agents}.yaml`.
- `bifrost solution deploy` is manifest-driven full-replace. So the next deploy **deletes** any captured table/form/agent that isn't in the on-disk manifest. Reproduced twice with a table; confirmed in source.
- **Workflows are the only entity that round-trips** — and only because you manually add a UUID-keyed entry to `.bifrost/workflows.yaml` (deploy does not auto-scan `functions/`).

**Consequence:** the skill cannot be edited into "consistently produces a good solution *with entities*" because no working capture→deploy round-trip exists for table/form/agent/config. This is a release-blocker-class platform gap, escalated to the user (scope decision).

**Genuine skill-doc findings (fixable independent of the bug):** capture requires entities be in the SAME org as the install (global `organization_id: null` not capturable) — undocumented; `solution start [APP_SLUG]` positional needed with multiple apps; capture-by-UUID more reliable than by-name; adding a 2nd workflow needs a manual `.bifrost/workflows.yaml` UUID entry (contradicts the "don't edit .bifrost" guidance — needs reconciling).

**Status: Track A BLOCKED pending user decision on the platform bug.**

---

## Track B — Repo/global build (live mutation correct)

Goal: author workflow `.py` + entities via live CLI create/update → execute →
iterate. Cover SDK surface Track A didn't reach.

| Run | Result | UI/exec | Entities | Update | Execute | Invariant | Misleading moments → fix | Streak |
|-----|--------|---------|----------|--------|---------|-----------|--------------------------|--------|
| B1 | NEEDS-FIX (1) | exec ✓ (workflows execute + bifrost run) | wf+table+form+agent+config all created LIVE ✓ | live update ✓ (no 409 — repo isolated from solution guard) | ✓ | live update succeeds (confirms repo≠solution) | 2 fixes: watch is workspace-specific (register 404s if file unsynced); forms create example missing required --form-schema | 0 |

### B1 — scout run; the whole repo live-mutation surface works; 2 doc fixes

First Track-B run (single scout). The entire repo/global live-mutation surface works as documented: workflow registered + executed (`workflows execute` + `bifrost run`), table/form/agent/config all created LIVE via CLI, live `tables/forms/agents/configs/workflows update` ALL succeeded with **no 409** (confirming the read-only guard is solution-only — repo isolation verified), discovery (list/get) across all entity groups, and the **`--org` standard fully exercised** (omit/`--global`/`--org none`/`--org global`/`--org name`/`--org uuid`/`--organization`/`--scope` — all routed correctly, confirmed via `org_id` in responses). Broad SDK/CLI coverage Track A didn't reach: configs, agents, forms, orgs/roles/events/integrations list, executions via REST, `bifrost run` local exec, `config.get` from a workflow.

Two doc fixes (both verified at code level):
1. **repo.md "Creation Flow" assumed watch is syncing THIS workspace.** B1 wrote a `.py` in a fresh scratch dir, saw a `pgrep -f 'bifrost watch'` hit (the user's OTHER watch), and ran `workflows register` → **404 "File not found"** (register reads from the platform; watch hadn't synced the scratch dir). repo.md's watch-check `pgrep` passes for *any* watch, not one watching *this* dir. → Added: watch is workspace-specific; a pgrep hit ≠ watching this workspace; an unsynced file 404s on register. Cross-referenced at the register step too. (The agent still never runs watch/push itself — repo.md's deliberate design, line 13 — it asks the user; the fix is making the workspace-specificity explicit.)
2. **repo.md `forms create` example omitted the required `--form-schema`** (FormCreate.form_schema has no default → 422). entities.md had it; repo.md didn't. → Added `--form-schema @schema.yaml`.

**Platform note (not skill):** `bifrost push <file.py>` errors "is not a valid directory" — push only takes directory paths (consistent with `--help`; not a doc claim). `tables list --json` is `{"tables":[...],"total":N}` wrapped vs bare arrays elsewhere (re-confirmed from Track A).

---

## Platform design questions surfaced during validation (NOT skill bugs — for the platform side)

1. **`/capture/candidates` vs `capture()` disagree on global→org.** The candidate list (which the CLI gates capture on, solution.py:1764) hides global entities from an org install, but `capture()` has a latent global→org re-stamp path. Either make the gate honor the re-stamp (capture-by-id re-stamps), or drop the dead re-stamp branch. Today the gate wins; the doc tells users to set scope up front.
2. **Install resolution could resolve on a unique `(slug, scope)` match regardless of org.** Today `_resolve_target_install` binds org-scope resolution to the deployer's own org, so an install in a different org needs `--org`/`--solution` even when there's exactly one same-slug install visible. A unique-match fast path would remove the re-specify friction while keeping the anti-clobber check for the 2+ case.
3. **Is `scope` in the descriptor worth keeping?** ~~It only selects global-vs-org *kind* at create and is recomputed from the install's org on export.~~ **SETTLED — workstream 3 REMOVED descriptor `scope`** (install kind is the deploy-time `--org`/`--global` choice, derived server-side from `organization_id`).
4. **No CLI command to add a new workflow to a solution's manifest** (surfaced repeatedly A6–A9). `scaffold-app` writes the sample's `.bifrost/workflows.yaml` entry programmatically, but a builder authoring a 2nd+ workflow has no command — they must hand-edit `workflows.yaml` (which works: deploy's `_upsert_workflows` creates the row from the entry) or take the awkward `bifrost push` → `workflows register` → `solution capture` → `pull` road (and `push` is banned in skill docs). A `bifrost solution add-workflow <path::fn>` — or having deploy/`pull` discover `@workflow`-decorated functions under `functions/` and auto-add their manifest entries — would remove the single biggest authoring papercut the validation loop found.
5. **`workflows register` 500s instead of 409** on a duplicate-name register of an existing solution-managed workflow (A8). Wrong status code for a conflict.
6. **`solution start` silently skips a workflow whose import fails** (A7) — discovered functions drop to 0 with no loud error; a hard error / "check your imports" warning would help.
