# Solution Inactive-Lifecycle — Design

**Status:** Approved (brainstorming, 2026-06-24)
**Supersedes:** the orphan-to-org uninstall model in `2026-06-22-solution-scoped-files.md` (Tasks 17/18/21) and the existing table/config orphan-to-org + reattach-by-slug behavior.
**Branch:** `codex/files-sdk-policies`

## Problem

Solution uninstall currently **destroys the Solution row** and **re-homes its owned data to the org namespace** (`solution_id → NULL`, `organization_id` set, `origin_solution_slug`/`orphaned_at` provenance stamped), with reattach-by-slug on reinstall. This applies to tables and configs today; the solution-scoped-files work (Tasks 17/18/21) mirrored it for files, adding an S3 byte-move + an orphan background job.

This model is wrong for the intent. Re-homing solution data into the org breaks the core expectation that **reinstalling a solution brings its data back with the install**. It also created real bugs (the capstone e2e surfaced an S3-sweep-before-byte-move data-loss ordering issue) and an unresolved inline-vs-background tension. The orphan-to-org subsystem (provenance columns, reattach-by-slug, byte-move, orphan jobs, "show orphaned" toggles) is being **removed entirely**.

## The model

A solution-owned entity is any row carrying `solution_id` (workflows, tables, configs, files/file-metadata/file-policies, forms, agents, apps, custom claims, event sources, config schema). All of them follow one lifecycle, keyed off **Solution state**, not off the data rows.

### Solution state

Add `status` to `Solution`: `active | inactive` (default `active`). Uninstall no longer deletes the Solution row.

### Three operations

1. **Uninstall (`active → inactive`) — status flip only.**
   - The Solution row's `status` flips to `inactive`. **Nothing else changes.**
   - All owned entities are **frozen in place** under their existing `solution_id`. No re-home to org, no S3 move, no provenance stamping, no cascade-avoidance.
   - Replaces today's `delete_solution` orphan-to-org behavior.

2. **Reinstall over an inactive install (slug match, same org) — prompted, never silent.**
   - Installing a bundle whose slug matches an existing **inactive** install in the same org does **not** silently reactivate and does **not** silently create a second install.
   - It **prompts**: *"An inactive install of `<slug>` exists. Reinstalling redeploys over its retained data (tables, files, configs come back). For a clean install instead, permanently delete the inactive one first."*
   - On confirm → flip `status → active` and redeploy entities **atop the retained data** (entity upsert over the frozen rows; data the bundle doesn't touch survives).
   - No clean-install-alongside path while an inactive install exists for that slug — clean install requires an explicit hard-delete first.

3. **Hard-delete (`active | inactive → gone`) — the single destructive path.**
   - Reachable **directly** from either `active` or `inactive` (no mandatory uninstall-first step), gated by a confirmation modal that:
     - lists exactly what will be destroyed (X files, Y tables, Z configs, …),
     - requires the user to **type the solution name** to enable the confirm button.
   - On confirm: drop the `Solution` row → the `ondelete=CASCADE` FKs on `solution_id` remove all owned DB rows (tables + their documents, file-metadata, file-policies, config-schema, etc.) → sweep the `solutions/{id}/` S3 prefix for file bytes.
   - This is the **only** place cascade + S3 sweep run.

### Dormancy enforcement

Inactive-solution data stays resolvable by `solution_id` for **browsing and export**, but is **not servable/executable**:
- The own-solution resolver arms (named-rules resolver Task 4; file scope/policy resolvers Tasks 15/16) and the execution/serve paths add a `Solution.status == active` gate.
- Inactive ⇒ workflows can't read its tables/files, its app is down, its forms/agents don't execute. It is cold storage you can browse, export, reactivate, or hard-delete.

### "Show inactive" surface

Solution-level (not a per-entity orphan filter). Replaces the existing per-page "show orphaned" toggles (Tables, Config):
- The solutions list gains a "show inactive" toggle; inactive installs render distinctly.
- The Files-on-Contents browser (Task 25) and the table/data browsers, when scoped to an inactive install, show its retained data read-only with an "inactive" indicator.

## What gets deleted (existing machinery, all entity types)

- Columns: `origin_solution_slug`, `origin_solution_id`, `orphaned_at` on tables, configs, file_metadata, file_policies (and any other entity that gained them). Greenfield migration — **dropped outright, no reconciliation** (pre-release; no deployed orphaned-to-org data).
- Uninstall orphan-stamping logic in `delete_solution` (tables detach block, config orphan-stamp block, the files restamp/orphan path).
- Reattach-by-slug in deploy: `_upsert_tables` orphan adoption, `_reattach_orphan_configs`, any files reattach.
- Files orphan service: `orphan_solution_files`, `orphan_solution_files_by_ids`, `restamp_solution_files_metadata`, `delete_solution_files_metadata`, and the `orphan` kind of `SolutionFileJob` (Tasks 17/18/21). The inline-vs-background byte-move problem disappears — there is no byte-move.
- The capstone's stopgap inline-orphan-move in `delete_solution` (commit `254107107`) — replaced by the status flip.
- "Show orphaned" toggles + `include_orphaned` repo params on Tables/Config.

## What gets added

- `Solution.status` column + migration.
- `POST .../uninstall` (or repurposed DELETE) → status flip; `POST .../reactivate` (or reinstall-over path); a separate confirmed **hard-delete** endpoint.
- Reinstall-over-inactive prompt logic (deploy detects an inactive slug match → returns a "needs confirmation" signal the CLI/UI surface).
- Status gate in resolvers + execution/serve paths.
- Hard-delete cascade + S3 sweep (the only destructive path) + the type-the-name confirmation modal (frontend) + the "what will be destroyed" count endpoint.
- "Show inactive" in the solutions list + scoped data browsers.

## Components / boundaries

| Unit | Responsibility |
|------|----------------|
| `Solution.status` (ORM + migration) | active/inactive state; drop orphan provenance columns |
| `uninstall` (router/service) | flip status → inactive; no data mutation |
| `reactivate` / reinstall-over (deploy) | inactive slug-match detection + prompt signal; redeploy atop retained data on confirm |
| `hard_delete` (router/service) | confirmed cascade-drop of Solution row + S3 prefix sweep; "what will be destroyed" summary |
| status gate (resolvers + execution) | inactive-solution data is browse/export only, never servable |
| "show inactive" (solutions list + browsers) | surface inactive installs + their retained data read-only |

## What is preserved (unaffected by this redesign)

- **Plan 1 — named policy rules (Tasks 1–13):** entirely unaffected. The `$ref`/cascade/rules feature is orthogonal to uninstall lifecycle. (The status gate is added to its resolver, but the rules feature itself stands.)
- **Plan 2 non-uninstall parts:** solution file scope resolution (15), file policy cascade (16), bundle capture (19), deploy writes files (20), manifest round-trip (22), CLI/MCP scope (23/24), Files-on-Contents UI (25). These remain valid; they get the status gate and lose the orphan assumptions where they reference them.

## Testing

- Unit: `Solution.status` defaults/transitions; the "what will be destroyed" summary; resolver status-gate (inactive → not servable, still browsable).
- E2E (the new capstone): install → use → **uninstall (status=inactive, data frozen in place, not servable, still browsable/exportable)** → **reinstall over inactive (prompt → confirm → reactivate, data intact)** → **hard-delete (type-name confirm → all rows + S3 gone)** → assert no cross-solution leakage throughout. Replaces the orphan-survival arc in the current Task-26 capstone.
- Migration test: orphan columns dropped; no reconciliation; existing solution data resolves under `solution_id`.
- Cross-entity: tables, configs, AND files all follow the one lifecycle (no entity still orphans-to-org).

## Open mechanical notes (resolved in the plan, not choices)

- The `solution_id` FK `ondelete=CASCADE` is now the hard-delete mechanism (was previously avoided). Cascade-avoidance code is removed.
- Reinstall-over redeploys entities via the existing upsert (atop frozen rows); data the new bundle omits survives (no mirror-delete — consistent with O1 for files; extend the same non-destructive upsert to all entities on reactivate).
- "Type the solution name" confirmation is a frontend gate; the backend hard-delete endpoint still validates auth + that the caller passed an explicit confirm token (defense in depth, not relying on the modal alone).
