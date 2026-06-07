# Solutions end-to-end shakeout — findings

Date: 2026-06-07
Driver: acting as (a) a brand-new user/LLM and (b) an existing user whose workflow changed.
Live stack: port-mode debug at http://localhost:37791 (worktree-mounted, branch code).
Scratch CLI: `/tmp/bifrost-shakeout/.venv/bin/bifrost` (API-matched, logged in).

Legend: **[PLATFORM]** = experience/bug/sloppiness to fix in the product ·
**[CODE]** = scalability/architecture problem I own · **[OK]** = verified good ·
**[Q]** = needs a user decision.

---

## CLI discoverability

### F1 [PLATFORM] `solution` command missing from top-level `bifrost --help`
`bifrost --help` lists entity commands (orgs/roles/workflows/forms/agents/apps/
integrations/configs/tables/events/requirements) but NOT `solution`, despite
`bifrost solution {init,deploy,install,scaffold-app}` existing and working. A new
user or LLM reading help has no signal Solutions exist as a CLI surface. The
top-level help is hand-maintained (the entity list is a literal block), so the
new command group was never added.
- Fix: add `solution` to the help's command listing (+ an example or two:
  `bifrost solution init`, `bifrost solution scaffold-app <slug>`,
  `bifrost solution install <zip>`).
- Scalability note: a hand-maintained help block is itself drift-prone — every
  new command group risks this. Worth a follow-up to derive the listing.

---

## Architecture audits (code-answerable)

### F2 [CODE] Solution-first resolution is NOT centralized — scattered/ad-hoc per entity
The "prefer the install's own entity, then fall back to org→global cascade" logic
is implemented differently for every entity type and is NOT in the canonical
`OrgScopedRepository`:
- **Tables:** inline in the ROUTER (`tables.py::_resolve_solution_table_by_name`)
  — raw SQL, app_id→solution_id lookup, runs OUTSIDE the repo/cascade.
- **Workflows:** in the REPOSITORY (`workflows.py::_resolve_by_path_ref`, via a
  `solution_scope=` param) — but only for `path::fn` refs; manually partitions
  rows after the cascade.
- **Configs:** NO solution-first logic at all (works "by accident" today because
  Config has no solution_id; a future per-install config override would need it
  built from scratch).
- **`OrgScopedRepository`:** no `solution_scope` concept; the cascade only knows
  how to EXCLUDE solution rows from name lookups, not PREFER an install's own.
Duplicated across entities: the `X-Bifrost-App`→`Application.solution_id` lookup +
the `own = [r for r in rows if r.solution_id == scope]` filter. Adding a 6th
solution-owned entity = copy-paste it again. **This is the "should be intertwined
with the org cascade" concern — confirmed not done.**
- Fix (my list): a single `OrgScopedRepository.resolve(name, *, solution_scope=)`
  primitive that does solution-first-then-cascade in ONE place, used by all
  entity types; document it in `repositories/README.md`. Sizeable but the right
  consolidation. NOTE: this is shared canonical code touched by the whole branch —
  worth doing carefully / possibly its own follow-up PR rather than rushed here.

### F3 [CODE] Manifest/bundle is "semi-derived" — hand-enumerated field lists, no drift test
Models are SHARED (one `ManifestSolutionConfigSchema`, one ORM) — good — but each
entity's field set is hand-listed in ~4 places: the ORM, the manifest Pydantic
model, the CLI collector (`_collect_config_schemas`), and the deploy upsert
(`_upsert_config_schemas`). Adding ONE field requires editing all 4; miss the
collector and the field silently defaults to None/0 with NO test catching it.
Also: cross-ref remap field names (`_FORM_WORKFLOW_REF_FIELDS` etc.) are
hand-enumerated — a new ref field silently won't remap. No round-trip/parity test
across ORM↔Pydantic↔collector.
- Fix (my list): collector uses `ManifestSolutionConfigSchema.model_validate(body)`
  and the upsert uses `.model_dump()` (kill the hand lists); add a field-parity
  test (like `test_dto_flags.py`) asserting ORM ↔ manifest ↔ collector agree.
  Cheap and high-leverage.

### F4 [PLATFORM] `bifrost watch` pushes a Solution developer's changes to `_repo/` (wrong place)
`watch` is exclusion-based and syncs code to the global `_repo/` workspace only —
it has NO solution awareness and never deploys. A developer iterating on a Solution
locally who runs `watch` pushes their `apps/`/`workflows/` into `_repo/`, NOT to
their install. The Solution deploy path is separate (`bifrost solution deploy` /
the UI). So the local-dev loop for a Solution is: edit → `bifrost solution deploy`
(full redeploy), with `watch` actively misleading (it appears to sync but to the
wrong target). This is the biggest local-dev-experience gap.
- Decision needed [Q]: should `watch` detect a Solution workspace (a
  `bifrost.solution.yaml` descriptor present) and either (a) refuse with a hint
  ("this is a Solution workspace; use `bifrost solution deploy`"), or (b) do a
  solution-scoped auto-deploy on change? At minimum (a) to stop the silent-wrong-
  target footgun.

### F5 [OK] App-slug collision guarding is solid
Solution deploy refuses an app slug colliding with a visible app in the install's
scope (`deploy.py:742-788`, 409 `SolutionDeployConflict` with a clear "rename one"
message; pg advisory lock serializes concurrent same-slug deploys). Visibility-aware
(global vs org), cross-org same-slug allowed. Tested
(`test_solution_app_deploy.py`). No action.

### F6 [OK / gap-note] MCP correctly blocks mutations on solution-managed entities; no MCP path to manage Solutions
MCP entity tools (tables/forms/agents/apps) refuse mutating a solution-managed row
(the `before_flush` backstop + `assert_not_solution_managed`, returning the locked
message before any S3 write; tested in `test_mcp_solution_managed.py`). GOOD.
Gap (note, not necessarily a bug): there are NO MCP tools to create/deploy/list
Solutions — that lifecycle is REST/CLI/UI only. An LLM building via MCP cannot
install or manage a Solution. [Q] is that intended (admin op, out of MCP scope) or
a gap to fill?

---

## Local development drive (scaffold → npm install → dev server → run)

Drove the full new-user loop on the live stack. Scratch CLI at `/tmp/bifrost-shakeout`.
`bifrost solution init` → `scaffold-app dashboard` → `npm install` → `npm run dev`.

### F7 [OK] Scaffold + instance-served SDK install + tokenless dev server all WORK
- `solution init` writes a clean `bifrost.solution.yaml`; `scaffold-app` generates
  package.json (bifrost from `<instance>/api/sdk/download`), vite.config, index.html,
  main.tsx, App.tsx, .env.example, README — with clear "next:" guidance.
- `npm install` resolves the instance-served `bifrost` tarball cleanly (68 pkgs).
- `vite.config.ts` token discovery is genuinely well done: process env → walk-up
  `.env` → `bifrost auth token` (keyring) fallback, injected via `define` ONLY for
  `serve` (never `build`, so no token in the prod bundle). Device-code-login case
  handled. **My earlier worry that VITE_ prefixing would break it was WRONG — the
  config bridges it correctly. Verified by running it.**
- `npm run dev` boots; the app loads at :5173, authenticates (BifrostHeader shows
  "Log out" → token+baseUrl resolved), routing + refresh work. The local→API call
  is direct cross-origin and SUCCEEDS (CORS ok).

### F8 [PLATFORM] Scaffolded app's one button 404s out of the box with a raw error
A new user's first action is the scaffold's "Run workflow" button, ref
`workflows/your_workflow.py::main` — which doesn't exist → `workflow execution
failed: 404 Not Found` (raw). Zero guidance that they must create a workflow or
edit the ref. First-run looks broken.
- Fix: either (a) scaffold a matching trivial `workflows/your_workflow.py::main`
  so the button WORKS on first run (best — instant "it works!" moment), or (b)
  make the placeholder 404 self-explanatory in App.tsx (catch + show "No workflow
  yet — create workflows/your_workflow.py or edit this ref").

### F9 [PLATFORM] BifrostHeader is unstyled in standalone/local dev — looks broken
In `npm run dev` the `BifrostHeader` renders with no styling: "Bifrost" link and
the "My App" title jammed together, back-arrow overlapping, default-browser
"Log out" button. Deployed, it sits in platform chrome with CSS; standalone it
ships no styles. A builder's first `npm run dev` looks half-broken.
- Fix: the SDK's BifrostHeader should carry minimal self-contained styling (or the
  scaffold should import a tiny stylesheet) so standalone dev looks intentional.

### F10 [Q] Local dev resolves GLOBAL workflows, never the install's own
In local dev there's no bootstrap, so `appId` is null → `useWorkflow("path::fn")`
posts with no app_id → resolves via the `_repo/` global cascade, NOT a specific
install's workflow. For a published-solution app that should prefer its OWN
workflow then fall back to global (the user's "can it fall back to global like a
published solution should if enabled?"), local dev only ever gets the global/_repo
side. That's arguably FINE for local dev (you're editing local `workflows/*.py`
which you'd deploy), but the "own-first, then global-if-enabled" resolution is a
DEPLOYED-only behavior — there's no way to exercise/preview install-scoped
resolution locally. [Q] Is local-dev-resolves-global acceptable, or do we want a
way to simulate install scope locally (e.g. a dev flag / VITE_BIFROST_APP_ID)?
Relates to F2 (resolution not centralized) — the fallback-if-enabled path
(`global_repo_access`) lives in the deployed resolver only.
