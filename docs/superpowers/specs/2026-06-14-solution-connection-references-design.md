# Solution Connection References & the Unified Unmet-Needs Engine

**Date:** 2026-06-14
**Status:** Design — approved shape, pending spec review
**Worktree/branch:** `solutions-success-criteria` / `desloppify/code-health` (rides the draft PR #347 stack)
**Predecessor:** `2026-06-14-solution-export-import-portability-design.md` (export/import DONE)

## Problem

A Bifrost **Solution** that uses integrations installs **"dead."** Today a solution
declares the **configs** it needs (`SolutionConfigSchema`) and surfaces unset ones in a
Setup checklist with `setup_complete`. But it has **no equivalent for integrations**:

1. **Connections aren't declared.** A workflow calls `integrations.get("HaloPSA")`, but
   nothing in the bundle records "this solution needs a HaloPSA integration." The admin
   installing it gets zero signal which integrations to set up.
2. **Integrations don't travel as templates.** Even when the admin knows they need
   "HaloPSA," they must build the integration (config schema, OAuth provider shape, data
   provider) from scratch. The portable, client-agnostic *skeleton* of the integration
   is exactly the kind of thing capture could carry.
3. **Other unmet needs fail silently at runtime.** Missing shared `modules/*.py` and
   missing cross-solution dependencies are computed by the dependency walker at *capture*
   time, but install/upgrade does **not** check them — a missing module surfaces only as a
   runtime `ModuleNotFoundError`, not a blocked or flagged install. Power Platform blocks
   on missing dependencies; we don't.
4. **No install-time setup guidance.** There's nowhere for a solution author to write
   "here's how to wire this up / here's the disaster-recovery procedure."

This is the #1 competitive gap vs Power Platform's connection references (per the
export/import arc's competitive review). The fix is **declare-and-surface**, NOT
port-tokens — Power Platform doesn't port credentials either, and neither should we.

## Goals

- **Declare** the integrations a solution's code references (auto-scanned at capture).
- **Template** the safe, portable skeleton of each referenced integration so install can
  pre-create an empty shell the admin just fills in.
- **Surface** unmet needs (unset required configs + missing integrations) uniformly in the
  Setup checklist with a per-item "set it up" action and a solution-level warning badge.
- **Block** at install/upgrade on missing modules / cross-solution deps (the silent-runtime
  fix) — the one class of unmet need the admin *can't* self-resolve post-install.
- **Document** — a markdown README, sourced from the solution repo, rendered as the first
  tab.
- **Unify** all of the above into one "what does this solution need that isn't satisfied
  here" engine, overhauling the existing dependency walker rather than bolting on a parallel
  path.

## Non-Goals

- **Do NOT port OAuth tokens, client IDs, or client secrets.** Those are client-specific
  and travel with nobody (same rule as the secrets scrub in the export bundle).
- **Do NOT require a *connected* token for a connection to count as "satisfied."** Not all
  integrations use OAuth; existence of the global integration is the bar (see below).
- **Do NOT clobber an existing integration** on install. If a global `Integration` named X
  already exists, leave it entirely alone — never overwrite a working production config.
- No PP-style layering / managed-vs-unmanaged. (Deliberate prior decision — hard-409 on
  collision is correct.)
- No additive "Update" mode or promotion pipeline (separate future arcs).

## Key domain facts (verified in code)

- **Integrations are global.** `Integration.name` is globally unique; there is **no per-org
  Integration**. So "does integration X exist" is a **global** existence check, never
  org-scoped.
- **Bindings are org-scoped.** An `IntegrationMapping` (carrying `oauth_token_id`) can be
  org-specific or global/default. *Connectedness* lives here — but it's informational only
  (see below).
- **Workflows reference integrations by name string.** `integrations.get("HaloPSA")` — a
  string literal, statically scannable exactly like `tables.get("x")` / `config.get("k")`.
  This is what makes auto-scan-at-capture viable.
- **`Solution.setup_complete` already exists** as a column — the engine extends what feeds
  it; no migration for that field.
- **OAuthProvider safe vs secret fields are cleanly separable:** safe = `provider_name`,
  `display_name`, `oauth_flow_type`, `authorization_url`, `token_url`, `audience`,
  `token_url_defaults`, `entity_id_source`, `scopes`, `redirect_uri`; **secret/scrubbed** =
  `client_id`, `encrypted_client_secret`, `organization_id`, `status*`, tokens.

## Design

### 1. The unified requirement model

Every solution has **requirements** that may or may not be **satisfied** in the
install environment. Four kinds, computed by one engine, surfaced uniformly:

| Kind | Declared from | "Satisfied" means | Unmet → |
|------|--------------|-------------------|---------|
| `config` | `SolutionConfigSchema` (today) | a `Config` row with that key exists in install org scope | inline value input (today) |
| `connection` | **NEW** auto-scanned `integrations.get("X")` refs | a **global** `Integration` named X exists | "Set up integration" action → opens integration page (pre-created shell) |
| `module` | scanned `modules/*` import closure (walker) | the module file is present in the installed repo | **blocks install/upgrade** (informational in checklist) |
| `solution_dep` | cross-solution refs (walker) | the referenced solution is installed in scope | **blocks install/upgrade** (informational in checklist) |

**Connection "satisfied" = global Integration named X exists. Nothing more.** Not all
integrations use OAuth, so requiring a connected token would produce false "unmet"
warnings. Existence is the honest bar.

**Connectedness is a secondary informational icon, never a gate.** If we *can* tell an
integration has a resolving mapping/token, show a small confirmation icon (e.g. a plug /
secondary check) so the admin doesn't have to open the OAuth tab to verify. Its absence
does **not** mean "unmet" and does **not** imply the connection is required — it's a
convenience only.

`setup_complete` = all *required* `config` items satisfied **AND** all `connection` items
satisfied. `module` / `solution_dep` gaps do not feed `setup_complete` (they block earlier,
at install).

### 2. Connection declaration (auto-scan at capture)

Add `scan_integration_refs(source) -> set[str]` to `ref_scanner.py` — a regex matching
`integrations.get("NAME")` / `sdk.integrations.get("NAME")` (first arg = integration name),
mirroring `scan_config_refs`. The dependency walker already drains a per-workflow scan loop
(`while wf_worklist - scanned_wf`); add an integration sweep there, collecting referenced
integration names. Computed/dynamic refs stay invisible — same documented static-scan
tradeoff as configs/tables (the human-checked preview is the backstop).

At capture, for each referenced integration name that resolves to a global `Integration`
row, build a **`SolutionConnectionSchema`** declaration + a portable **integration
template** (§3). Store on the solution like config declarations:

- **New ORM:** `SolutionConnectionSchema` (`solution_id`, `integration_name`,
  `position`, plus the template JSON — see §3). Mirrors `SolutionConfigSchema`.
- **New `capture.py` step** `_connection_entries(...)` analogous to `_config_entries`,
  populating the bundle's new `connection_declarations` list.

### 3. Integration template (safe skeleton)

For each declared connection, capture serializes a **client-agnostic skeleton** so install
can pre-create an empty integration to fill in:

**Carried (safe):**
- Integration: `name`, `entity_id_name`, `default_entity_id`, data-provider attachment
  (`list_entities_data_provider_id` — remapped/resolved by name on install if the provider
  workflow travels in the same bundle; otherwise left null with a note).
- Integration config schema: the full `IntegrationConfigSchema` list (key/type/required/
  description/options/position) — the "fields to fill out."
- OAuth provider shape (if present): `provider_name`, `display_name`, `oauth_flow_type`,
  `authorization_url`, `token_url`, `audience`, `token_url_defaults`, `entity_id_source`,
  `scopes`, `redirect_uri`.

**Scrubbed (client-specific / secret):**
- `client_id`, `encrypted_client_secret`, any `oauth_token`/mapping rows,
  `organization_id`, `status`/`status_message`/`last_token_refresh`, `entity_id` values
  from mappings.

Scrub rules live in `portable.py` (new integration-scrub section, mirroring existing entity
scrubs). A round-trip test asserts no secret field survives serialization.

**On install/deploy** (`deploy.py` new `_upsert_integration_shells`):
- If a global `Integration` named X **does not exist** → create the empty shell: the
  Integration row + its `IntegrationConfigSchema` rows + an `OAuthProvider` skeleton
  (with `client_id=""`, `encrypted_client_secret=b""`, `status="not_connected"`). The admin
  lands on a pre-built integration with empty credential fields.
- If it **already exists** → no-op. Never touch a working integration.
- Track created shells in the deploy result for the install summary.

### 4. Install-time dependency blocking (the silent-runtime fix)

Overhaul the dependency walker into the engine that also runs at **install/upgrade preview**
(not just capture). Add a `check_install(bundle, target_scope) -> UnmetNeeds` path that, for
the entities about to be installed, verifies:

- **modules:** every `modules/*.py` import in the bundle's workflows resolves to a file
  present in the bundle (or already installed in the target repo).
- **solution_dep:** every cross-solution `path::fn` / entity ref resolves to a solution
  already installed in the target scope (or included in this bundle).

A missing module or unresolved cross-solution dep **blocks install/upgrade** with a clear
error naming what's missing (mirrors `RequiredConfigUnset`'s "names the key" ergonomic).
This is wired into the existing install preview + the `zip_install` / `deploy` entrypoints.
Connections and configs do **not** block (they're set-up-after-install needs); modules and
deps do (they're unresolvable post-install).

### 5. Solution README (markdown, first tab)

- **New column** `Solution.readme: Text | None` (one migration; alongside it,
  `SolutionConnectionSchema`'s table).
- **Source of truth:** `README.md` in the solution repo root. Pulled into `Solution.readme`
  on deploy (CLI `solution deploy` + git sync), travels in the export bundle, and editing
  in-app writes back to the repo file on next sync (same round-trip discipline as
  code/schema).
- **Render:** first tab of the solution detail page, via the existing TipTap editor
  (read + edit). Markdown.
- If no README present, the tab shows an empty-state / "add setup instructions" prompt.

### 6. Surfacing — the Setup checklist + warning badge

Extend `setup_status.py` → the unified engine produces a `SolutionSetupStatus` whose
`items` now include both config and connection items (a `kind` discriminator). The
`SolutionSetupChecklist` component renders:

- **config items:** unchanged (inline value input, yellow border when required+unset).
- **connection items:** integration name + "Set up integration" button (deep-links to the
  existing integration settings page for the pre-created shell, identified by integration
  name/id — opens in a new tab so setup-then-return matches Jack's "open a new tab, set it
  up, come back to a checkmark" flow) + the informational connectedness icon when
  resolvable. Yellow border when the integration doesn't exist.
- **`setup_complete` banner** when everything required is satisfied.
- **Solution-level warning indicator:** a yellow-triangle badge + "Continue Setup" affordance
  on the solution (detail header / list row) whenever `setup_complete` is false — the
  at-a-glance "this needs attention" signal Jack described.

`module` / `solution_dep` needs don't appear here in the normal case (they blocked at
install); if somehow present (legacy install), they render as read-only warning rows.

## Data flow

```
CAPTURE (capture.py + walker)
  scan workflow source → integration name refs
    → SolutionConnectionSchema rows + integration templates (scrubbed via portable.py)
  bundle.connection_declarations = [...]
  bundle.integration_templates   = [...]   (safe skeletons)
  bundle.readme                  = README.md from repo root

EXPORT → zip (unchanged transport; new bundle fields ride along)

INSTALL / DEPLOY (deploy.py + zip_install.py)
  check_install(bundle, scope):
    modules / solution_dep unmet → BLOCK (clear error)
  _upsert_integration_shells: create empty integration if absent, else no-op
  write Solution.readme
  recompute setup_complete (config + connection)

VIEW (solutions router + client)
  GET /solutions/{id}/setup → unified SolutionSetupStatus (config + connection items)
  README tab (TipTap) ← Solution.readme
  warning badge ← setup_complete
  RUNTIME backstop: integrations.get("X") on a missing integration → loud
    RequiredConnectionUnset (mirrors RequiredConfigUnset), naming the integration.
```

## Components & boundaries

| Unit | Responsibility | Depends on |
|------|---------------|-----------|
| `ref_scanner.scan_integration_refs` | static scan of `integrations.get("X")` | regex only |
| `dependency_walker` (overhauled) | capture preview **+** `check_install` unmet-needs | scanners, DB, repo |
| `SolutionConnectionSchema` (ORM) | persisted connection declarations + template | — |
| `portable.py` integration scrub | strip secrets from integration template | — |
| `capture._connection_entries` | build connection declarations + templates | walker, DB |
| `deploy._upsert_integration_shells` | create empty integration if absent | DB |
| `deploy` / `zip_install` install-block | enforce module/dep unmet → error | walker.check_install |
| `setup_status` (unified) | config + connection → `SolutionSetupStatus` | DB |
| `Solution.readme` + sync | README round-trip repo ↔ DB | git_sync, deploy |
| `SolutionSetupChecklist` (client) | render config + connection items, actions | — |
| README tab (client) | TipTap render/edit of `Solution.readme` | existing editor |
| `RequiredConnectionUnset` (SDK/runtime) | loud error on missing integration | — |

## Error handling

- **Missing integration at runtime:** `integrations.get("X")` for a solution-declared,
  nonexistent integration raises `RequiredConnectionUnset(name, "set it up in Integrations")`
  — parallel to `RequiredConfigUnset`. (Non-declared `integrations.get` keeps returning
  `None` as today — we only escalate declared ones.) **Declared-ness is known at runtime
  because the SDK call carries the executing solution's id** (the same `ctx.solution_id`
  the table/config resolver already uses — see the F2 fix in
  `project_solutions_implementation`); the integrations endpoint checks whether the named
  integration is declared by that solution's `SolutionConnectionSchema` before deciding to
  return `None` vs raise. A bare/loose (non-solution) call path is unaffected.
- **Missing module / cross-solution dep at install:** hard error from `check_install`,
  naming the missing item; nothing lands (pre-deploy check).
- **Integration shell creation collision:** existing integration → silent no-op (never
  clobber).
- **Data provider in template references a provider not in the bundle:** create the shell
  with a null data-provider + a note; don't fail the install.
- **README absent:** empty-state tab; never an error.

## Testing

- **Unit:** `scan_integration_refs` (incl. `sdk.` prefix, ignores dynamic refs);
  portable integration scrub (no secret survives — assert on every secret field);
  `check_install` blocks on missing module/dep and passes when present; unified
  `setup_status` mixes config + connection items + computes `setup_complete` correctly;
  `_upsert_integration_shells` creates-if-absent / no-op-if-present.
- **E2E:** capture a solution whose workflow calls `integrations.get("X")` → export →
  install into a fresh org → assert (a) an empty Integration X shell was created with its
  config schema + OAuth skeleton and no secrets, (b) `setup_complete` is false with a
  connection item unmet, (c) install of a bundle with a missing module is **blocked**,
  (d) README round-trips repo → DB → tab.
- **Client vitest:** `SolutionSetupChecklist` renders connection items + "Set up
  integration" action + connectedness icon; warning badge shows when `setup_complete` false.
- **Contract/DTO parity + contract-version tripwire** after touching contracts/DTOs.

## Migration

One Alembic migration: `solution_connection_schema` table + `solutions.readme` column.
`setup_complete` column already exists.

## Open questions / deferred

- **README write-back conflict semantics** (in-app edit vs repo edit) follow the same
  last-sync-wins discipline as other code round-trips; no new conflict UI this arc.
- Per-integration **version/compat** of the template skeleton is out of scope (templates are
  best-effort skeletons, not versioned contracts).
- Data-provider-by-name resolution across bundles is best-effort (null + note when absent).
