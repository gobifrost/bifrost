# Solutions: configs ownership + management UI (design)

Date: 2026-06-06
Branch: `worktree-solutions-success-criteria` (draft PR #347)
Status: **design, approved in brainstorming — ready for implementation plan.**
Companions:
- `docs/plans/2026-06-04-solutions-success-criteria.md` (original 18 criteria — CLI-first)
- `docs/plans/2026-06-06-solutions-own-configs-and-install-view.md` (the evolved "Solutions OWN their entities" note this spec executes)
- `docs/plans/2026-06-05-solutions-RESUME.md` (build/audit history)

## Why this exists (and the honest framing)

The original Solutions spec was **deliberately CLI-first**: a Solution is created,
deployed, and installed headlessly (criterion 17 — "no TUI, no interactive prompt").
The only UI requirement in the 18 criteria was the *inverse* — solution-managed
entities render **read-only** in the existing pages (criterion 6) and are otherwise
**invisible** to end users (criterion 16). There was never a line item for a Solutions
*management* screen, and even the read-only badge only got wired into `Applications.tsx`.

So the branch's "done" was true against the written (correctness/isolation/CLI)
criteria, but the feature has **no operator-facing UI** and **configs were never built
as an owned entity**. This spec is **net-new product scope** on the same branch: the
configs-ownership feature plus the Solutions management UI (list, detail, lifecycle).
It is not "finishing" the original spec — it is the dashboard over the engine.

## Goals

1. **Configs as a solution-owned entity** — a Solution *declares* the config it needs;
   the *install* holds the values. The portable artifact is structurally incapable of
   carrying a secret.
2. **Solutions list page** — view all installs; drag-and-drop a zipped Solution to
   install (preview → scope → confirm → server-side deploy); delete.
3. **Solution detail view** — one screen showing everything an install owns; click an
   entity → its existing single-entity page, with a clean path back to the solution.
4. **Editable install-local fields** — scope, install settings, config values/secrets.
   Portable Solution content stays read-only.
5. **Read-only badge** — added to Forms/Workflows/Fleet, admin-only, links to the
   owning solution; compact Applications cards enlarged.

## Non-goals (explicit — do not build)

- **Provider-org access.** Platform admins only for now. Provider-org members would get
  read-only access *later*, under the upcoming RBAC work. Not in scope.
- **Editing portable Solution content in the UI** (workflow code, form fields, app
  source, table schema, config *declarations*). One-writer rule stands; git-connected
  installs would overwrite UI edits on next sync.
- **A stored config-status state machine.** "Required config unset" is a *derived,
  computed-on-read* fact, never a persisted state.

## The editability principle (the crux)

The branch enforces **one writer** per install. The dividing line is **portable content
vs. install-local fields** (the same split CLAUDE.md already draws for the manifest):

- **Portable content** = what the Solution *is* (workflow code, form fields, app source,
  table schema, **config declarations**). Lives in git/the bundle. **Read-only in UI**,
  locked harder when git-connected (UI edits would be stomped on the next sync).
- **Install-local fields** = belong to the install, never in git/the bundle: **scope
  (organization_id)**, **install settings** (name, `global_repo_access`, git URL/mode),
  and **config values** (incl. secrets). **Editable in UI.**

---

## Section 1 — Configs as a solution-owned entity

### Model: declaration vs. value

**New table `SolutionConfigSchema` (declarations — portable, owned by the Solution):**

| Column | Notes |
|--------|-------|
| `id` | UUID PK (uuid5-remapped per install at deploy, like other owned entities) |
| `solution_id` | FK → Solution, `ondelete=CASCADE` |
| `key` | config key (e.g. `STRIPE_KEY`) |
| `type` | string / int / bool / json / secret |
| `required` | bool |
| `description` | human-readable "what this is for" |
| `default` | nullable; **never set for `secret` type** |
| `position` | display order |

- **Uniqueness:** partial unique index `(solution_id, key) WHERE solution_id IS NOT NULL`
  — mirrors the table-name solution-scope fix (migration `20260606_table_name_sol_scope`).
  One declaration per key per install.
- **This is the only config thing in the bundle.** Portable, in git, round-trips through
  the manifest. **No `value` column exists** — a developer cannot commit a secret.

**Values stay on the existing `Config` row (instance-owned — unchanged):**

- An install's config value is a plain `Config` row in the install's org, encrypted if
  secret, linked to its declaration via the existing `config_schema_id` FK.
- **It gets no `solution_id` and never enters any bundle/export.** The existing
  `bifrost/portable.py` scrub rules already exclude config values; we do not weaken them.

### `configs.yaml` (workspace) — declarations only

```yaml
# .bifrost/configs.yaml — declarations, NEVER values
- key: STRIPE_KEY
  type: secret
  required: true
  description: "Stripe secret key for billing"
- key: REGION
  type: string
  required: false
  default: us-east
  description: "Deployment region"
```

Local dev supplies actual values via `.env` (the existing walk-up mechanism that
`bifrost run` / vite already use). Declarations and values never mix in one file.

### Deploy

New `_upsert_solution_config_schema()` in `api/src/services/solutions/deploy.py`,
**mirroring `_upsert_tables` exactly**:

- Ownership guard: an existing row's `solution_id` must match the deploying install, else
  `SolutionDeployConflict` (cannot hijack `_repo/` or another install's declaration).
- Upsert-by-id with uuid5 remap (`_remapped_bundle`).
- Full-replace of declaration fields from the bundle (no stale metadata).
- Reconcile-deletions scoped to `WHERE solution_id == sid AND id NOT IN bundle_ids`.
- **Never touches `Config` values.** Redeploying/updating a Solution never disturbs a
  secret the operator already entered.

### Resolution (runtime)

`config.get("key")` resolves **solution-first by key** via the already-built
`X-Bifrost-App` / `ExecutionContext.app_id` → `solution_id` plumbing (the same path
tables use), then falls through to the normal org→global cascade. The *value* still
comes from the instance `Config` row; the declaration scopes *which* key is the
install's. **Reuse `OrgScopedRepository` — no new cascade primitive** (per
`api/src/repositories/README.md`).

### Required-but-unset = derived warning, never a block

`required_configs_unset` = (required declarations) − (values present), computed on read.
Not stored, no state transitions.

- **UI install:** the install step shows declared configs with descriptions and lets the
  operator enter values inline; a missing required value is a **warning, not a gate** —
  they can finish anyway.
- **Git-connected install:** deploys as it must (no human, no values in the repo); the
  same derived warning surfaces in the detail view afterward.
- One consistent rule everywhere: *we tell you what's missing, we never stop you.*
- Runtime `config.get` on an unset required key fails loudly on its own (unchanged).

### Parity (CLAUDE.md "three surfaces" rule)

- `ManifestSolutionConfigSchema` Pydantic model + `manifest_generator` (DB→manifest) +
  manifest import (manifest→DB).
- CLI `_collect_*` reads `configs.yaml` into the bundle.
- DTO-parity test + round-trip unit test (`test_manifest.py`-style) + E2E.

---

## Section 2 — Backend API surface

All endpoints **platform-admin only** for now.

1. **`GET /api/solutions/{id}/entities`** (read) — the install + everything it owns
   (workflows, apps, forms, agents, tables) + **config declarations paired with
   value-set status** + derived `required_configs_unset`. One call, no N+1. Kept
   separate from `GET /api/solutions` so the list view stays lightweight.

2. **`POST /api/solutions/install/preview`** — unzip to a temp area, **parse manifests
   only** (cheap, no build), return "what this will create" + declared configs. Nothing
   persisted.

3. **`POST /api/solutions/install`** (commit) — body: chosen scope + optional
   `config_values` map. Runs the **existing server-side deploy pipeline** (build, uuid5
   remap, write-lock, DB-then-S3). **Atomic:** config values are applied post-deploy
   **under the same write-lock**, reusing the existing set-config logic — so the install
   never exists in a window without its just-entered secrets.

4. **`PATCH /api/solutions/{id}`** — edit **install-local fields only** (scope, name,
   `global_repo_access`, git URL/mode). **Rejects** any attempt to touch portable
   content. Changing scope re-resolves owned entities' org under the write-lock.

5. **`DELETE /api/solutions/{id}`** — cascade delete (CASCADE on `solution_id` across
   all owned types). Returns/confirms the deletion summary the UI shows. Git-connected
   installs deletable (the repo is untouched).

6. **Config values are written via the existing `POST /api/config`** — **no new config
   endpoint.** The install's value is a plain instance Config row in the install's org;
   `POST /api/config` already handles secret encryption + cache dual-write. Adding a
   `/api/solutions/{id}/configs/{key}` endpoint would re-implement that logic (the
   MCP-router-drift mistake CLAUDE.md warns against).

7. **Expose `solution_id`** on the five public response models (`AgentPublic`,
   `ApplicationPublic`, `FormPublic`, `TablePublic`, `WorkflowMetadata`) so
   the read-only badge can link to the owning solution. It is a **response-only,
   environment-specific** field — must stay out of the portable scrub path (it already
   is: declarations carry their own remap, values have no `solution_id`).

**CLI parity:** `bifrost solution install <zip> --org ...` routes through the same
preview+commit path.

---

## Section 3 — Solutions list page + lifecycle

Route **`/solutions`**, `requirePlatformAdmin`, nav entry with the admin surfaces.

**Layout:** grid/list of installs. Each shows name, slug, **scope** (org name / "Global"),
**source chip** (Git-connected / Manual), owned-entity counts ("4 workflows · 2 apps · 1
table"), and a **warning chip** if `required_configs_unset` is non-empty ("2 configs need
values"). Click → detail view.

**Drag-and-drop install — whole-page drop + button:**
- The entire list area accepts a dropped `.zip` (drag-over overlay), plus an explicit
  **"Install Solution"** header button (file picker).
- On drop → `POST .../install/preview` → **preview dialog**: what it will create +
  declared configs + a **scope picker** (org / global) + **inline fields for declared
  configs** (required marked; missing-required = warning, not block).
- Confirm → `POST .../install` (atomic deploy + values under lock) → progress (server
  build can take seconds) → land on the new install's detail view.

**Delete:** from list row menu and detail view → **type-to-confirm dialog** with the
cascade summary ("deletes the install and all N owned entities, including X secret config
values"; for connected installs: "This will not touch the git repository"). Requires
typing the install name.

**Empty state:** "No Solutions installed — drag a .zip here to install one."

**Design quality:** follow modern shadcn patterns (RoleDetail / IntegrationDetail era),
not the older compact pages. Roomy cards, clear status chips.

---

## Section 4 — Solution detail view

Route **`/solutions/:solutionId`**, `requirePlatformAdmin`. Modeled on **RoleDetail.tsx**.

- **Breadcrumb:** `Solutions / {install name}` (the "back" path; Pattern A, consistent
  with RoleDetail).
- **Header:** name, scope chip, source chip; actions **Edit** (scope/settings dialog →
  `PATCH`) and **Delete** (type-to-confirm). For git-connected: "Synced from {repo}" line
  and a "Sync now" affordance **only if** it maps cleanly to the existing sync endpoint
  (`POST /api/solutions/{id}/sync`); if not trivial, omit rather than half-build.
- **Warning banner (conditional):** if `required_configs_unset` non-empty — "N required
  configs need values", jumps to the Configs tab.
- **Tabs (count badge each):**
  - **Workflows / Apps / Forms / Agents / Tables** — list of owned entities; each row
    links to that entity's **existing single-entity page** (`/tables/:id`, `/agents/:id`,
    `/forms/:id/edit`, `/workflows/:name/execute`, `/apps/:id/edit`). Content read-only
    (already enforced via `is_solution_managed`).
  - **Configs** — each **declared** config: description, type, required flag, **value
    status (set / unset)**. Secrets are **entry-only** (write-only field; never displays
    the stored secret). Editing a value calls the existing `POST /api/config` for the
    install's org.

**"Back to solution" from an entity page:** deep-links carry `?from=solution:{id}`. The
entity page's existing back-link retargets to "← Back to {Solution}"; **falls back to its
default** ("Back to Tables") when the param is absent (non-solution nav unchanged). Lands
on the real, full-context entity page. No new global state.

**Symmetric navigation:** solution → entity (deep-link `?from`), entity card → solution
(badge link, Section 5).

---

## Section 5 — Read-only badge + card sizing

**Gap:** `is_solution_managed` is on all five types, but only `Applications.tsx` renders
the badge / hides edit-delete.

- **Shared `SolutionManagedBadge` component** (mirror the existing
  `SolutionManagedBanner` style) used on all five list pages — one place to change.
- **The badge is a link** to `/solutions/{solution_id}` (requires `solution_id` on the
  public types — Section 2 #7). Clicking it on any managed entity card jumps to the
  owning solution.
- **Admin-only:** badge renders only when `isPlatformAdmin` (`useAuth()`). This **also
  fixes `Applications.tsx`**, which currently shows it to all users.
- **Add to Forms, Workflows, Fleet:** badge + hide/disable Edit/Delete when
  `is_solution_managed`. (Workflows edit already admin-gated; Fleet already list-read-only
  — badge only.)
- **Non-admin view:** **no badge, no edit/delete** on managed entities. Edit/delete stay
  hidden for managed entities regardless of role (the badge is the admin-only
  *explanation*); read-only enforcement is server-side regardless of the badge.
- **Card sizing:** enlarge the compact Applications grid (currently 260px min) so cards
  breathe, matching the newer pages. Keep Forms/Workflows visually consistent; resize
  only if they look off beside the new Applications cards.

---

## Build sequence (Approach A — backend-first on a stable contract)

Each pass leaves the tree green and shippable.

**Pass 1 — Configs ownership (backend, end-to-end):**
- `SolutionConfigSchema` ORM + migration (partial unique `(solution_id, key)`).
- Bundle/manifest: `configs.yaml` declarations; `ManifestSolutionConfigSchema`;
  generator + import; CLI `_collect_*`.
- Deploy `_upsert_solution_config_schema` (mirror `_upsert_tables`; never touch values).
- Solution-first resolution via existing `app_id` plumbing; reuse `OrgScopedRepository`.
- `required_configs_unset` derived helper.
- Tests: unit (deploy upsert, ownership guard, reconcile, resolution), round-trip
  manifest, DTO-parity, E2E (declare → deploy → install value → resolve).

**Pass 2 — Solutions UI + lifecycle:**
- Backend: `GET .../entities`, `install/preview`, `install` (atomic), `PATCH`, `DELETE`;
  `solution_id` on public models; regenerate `v1.d.ts`. CLI `solution install <zip>`.
- Frontend: routes; list page (whole-page dropzone + preview/scope/values/deploy;
  delete); detail view (RoleDetail-style tabs + configs tab + warning banner; `?from`
  back-nav); `SolutionManagedBadge` (admin-only, links to solution) across Forms/
  Workflows/Fleet + Applications fix; enlarge Applications cards.
- Tests: backend unit + E2E for each endpoint; vitest for the badge/list/detail
  components and any new `lib/`/`services/` modules; a Playwright happy-path (install a
  zip → see it in the list → open detail → set a config → navigate to an entity and
  back).

## Testing & verification

Full pre-completion sequence per CLAUDE.md: `pyright`, `ruff check`, regenerate types,
`npm run tsc`, `npm run lint`, `./test.sh all`, `./test.sh client unit`, relevant
`./test.sh client e2e`. The solution suite (RESUME doc, line ~440) must stay green.
