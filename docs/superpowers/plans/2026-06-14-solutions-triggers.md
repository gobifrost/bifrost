# Plan — Solutions ship event/schedule triggers (manifest section)

**Date:** 2026-06-14
**Design:** `docs/superpowers/specs/2026-06-14-solutions-v1-coverage-design.md` (§2)
**Branch:** `solutions/connection-references` (worktree solutions-success-criteria), draft PR #347, NOT pushed.
**Goal:** A Solution can ship its triggers. An `EventSource` (+ child `ScheduleSource`/`WebhookSource`)
and its `EventSubscription`s deploy as part of the bundle, scoped to `solution_id`, read-only, swept on
reconcile, round-tripping through capture/export/install — reusing the existing forms machinery.

This is the **highest-reuse** coverage item: the generic `_capture_model`, `_reconcile_one`, the
always-on read-only guard, the remap machinery (`_remapped_bundle`/`_remap_ref`), and the H1 git-sync
exclusion (`_spare_solution_managed`, central in `_bulk_delete`) all apply for free once `solution_id`
exists on the rows. Net-new is mechanical and templated off `_upsert_forms`.

## Scope decision: which sources are portable

| Source type | V1 treatment | Why |
|-------------|--------------|-----|
| **schedule** | Fully portable | `cron_expression`/`timezone`/`overlap_policy` are pure definition. The flagship case (nightly-sync solution). |
| **topic** | Fully portable | `event_type` string only. Pure definition. |
| **webhook** | Definition carried, instance-state SCRUBBED | `adapter_name`/`config` are definition; `external_id`/`state` (secrets/tokens)/`expires_at`/rate-limit counters + `integration_id` are instance-specific. Carry the shell (like connection declarations); the instance re-establishes the external subscription + binds the integration after install. |

Rationale: schedule is the V1 headline and trivially portable; refusing webhooks would block the common
"shipped integration + its webhook trigger" shape, but carrying their live external state would leak
secrets and point at a foreign environment's subscription. Scrub-and-carry mirrors the
`build_integration_template` pattern already used for connection declarations.

## Ownership model

- `solution_id` goes on **`EventSource`** and **`EventSubscription`** (the two top-level rows).
- `ScheduleSource` / `WebhookSource` are **child rows cascaded from `EventSource`** (FK `ondelete=CASCADE`).
  They are owned transitively — no `solution_id` of their own. Reconcile deletes the `EventSource`;
  the child + subscriptions cascade. (Confirms the audit/ledger: column on the two top-level tables only.)

## Tasks

### T1 — Migration + ORM `solution_id` (keystone)
- Alembic migration: add nullable `solution_id UUID FK(solutions.id) ON DELETE CASCADE` + index to
  `event_sources` and `event_subscriptions`. Mirror the existing `Table.solution_id` column exactly
  (`api/src/models/orm/tables.py`).
- ORM: add `solution_id` mapped_column to `EventSource` and `EventSubscription` in
  `api/src/models/orm/events.py`.
- Apply to debug + test stacks: restart `bifrost-init` then `api` (per memory: migrations run by init).
- **Free wins to VERIFY after this lands (no code):**
  - Read-only guard: `is_solution_managed` keys off `getattr(obj, "solution_id")` (`guard.py:59`) — fires for these rows automatically.
  - Git-sync H1 exclusion: `_spare_solution_managed` is central in `_bulk_delete` (`manifest_import.py:1697`), and EventSource/EventSubscription go through `_bulk_delete` (`manifest_import.py:1830,1833`) — so the `_repo/` sweep spares managed rows the moment the column exists. Add a regression assertion to the H1 test (extend `test_sweep_spares_managed_entities` with EventSource).

### T2 — Manifest model + capture
- `ManifestEventSource`/`ManifestEventSubscription` already exist in `api/bifrost/manifest.py` (git-sync).
  Confirm they carry what we need; add a `solution_id` env-field only if the pattern requires it (forms
  carry it as an env field — match). Add a `schedule`/`webhook` child sub-model if not already nested.
- Capture: add `events: list[UUID]` to `SolutionCaptureSelectors` (`capture.py:46`) and a
  `_capture_model(EventSource, solution, selectors.events)` call (`capture.py:120` block). `_capture_model`
  is generic (stamps `solution_id`, restamps org) — reused as-is for the EventSource row. Child rows
  (schedule/webhook) and subscriptions need a small bespoke capture step (stamp `solution_id` on
  subscriptions; children cascade by ownership) — model on how a captured entity with children is handled
  if one exists, else inline.
- `_event_entries(...)` for the export bundle (analog of `_form_entries`, `capture.py:435`): serialize
  EventSource + nested schedule/webhook + subscriptions to a portable dict. **SCRUB** webhook
  `state`/`external_id`/`expires_at`/rate-limit counters and null `integration_id` (instance binds it).
- Dependency walker: add `events` arg (`dependency_walker.py:121`). A captured EventSubscription pulls in
  its referenced workflow/agent (forward closure) — surface in the preview like other refs.

### T3 — Bundle + deploy `_upsert_events` (the core net-new)
- `SolutionBundle.events: list[dict]` field (`deploy.py:237`).
- `_remapped_bundle`: add EventSource to the typed_entries id-remap (pass 1), and in pass 2 rewrite each
  subscription's `workflow_id`/`agent_id` and the child/subscription `event_source_id` through `id_map`
  (`deploy.py:502-524`). The referenced workflow/agent is in the same bundle → in `id_map`. Refs outside
  the bundle are left untouched (resolve by scope at runtime), same rule as forms.
- `_upsert_events(solution, events)`: for each EventSource entry — `_guard_owner(EventSource, id, sid)`,
  Core-upsert the EventSource row (stamp `solution_id` + `organization_id`), upsert its child
  schedule/webhook row, then upsert its subscriptions (stamp `solution_id`, remapped FKs). Use Core
  insert/update (NOT ORM-object mutation) per the always-on guard convention
  (`project_solution_managed_guard_deploy_core` memory) — install the guard in the unit test to be
  prod-faithful.
- Reconcile: `_reconcile_one(EventSource, sid, {ids})` in the deploy reconcile block (`deploy.py:1545`).
  Subscriptions + child rows cascade via FK on EventSource delete — verify the cascade, don't double-sweep.
- Export: write `.bifrost/events.yaml` (mirror the forms block, `export.py:111`).

### T4 — Tests (TDD throughout)
- **Unit** (`tests/unit/test_solution_*`):
  - capture stamps `solution_id` on EventSource + subscriptions; webhook state scrubbed.
  - deploy `_upsert_events` upserts source+child+subs, remaps `workflow_id`/`agent_id`/`event_source_id`,
    stamps scope (guard installed in the test → prod-faithful).
  - reconcile sweep deletes a stale EventSource (and cascades subs) scoped to the install.
  - round-trip: capture → export dict → deploy → rows match (minus scrubbed webhook state).
  - H1 regression: extend `test_sweep_spares_managed_entities` with a managed EventSource.
- **E2E** (`tests/e2e/platform/`):
  - deploy a solution with a schedule trigger → EventSource+ScheduleSource+EventSubscription exist, scoped.
  - read-only: REST/MCP mutation of a managed EventSource/Subscription 409s (extend
    `test_solution_readonly_full.py`).
  - roundtrip re-install (extend `test_solution_roundtrip.py`).
- **Parity:** if any `*Create`/`*Update` DTO changes, run `test_dto_flags.py` + `test_contract_version.py`;
  bump CONTRACT_VERSION if a CLI/SDK-consumed DTO changed (likely NOT — these are server/manifest-side).

### T5 — Full pre-completion verification
pyright (changed files) · ruff · tsc · lint · `generate:types` only if a response contract changed ·
backend unit + e2e (serial, per worktree flake) · client unit. All green before desloppify.

## Reuse ledger (from grounded investigation)
| Piece | reuse | file:line |
|-------|-------|-----------|
| `_capture_model` (stamp solution_id) | as-is for EventSource | `capture.py:136` |
| `_reconcile_one` (scoped stale sweep) | as-is | `deploy.py:1568` |
| read-only `before_flush` guard | as-is (generic on solution_id) | `guard.py:59` |
| git-sync H1 exclusion | as-is, auto-covers once column exists | `manifest_import.py:1697` |
| `_remapped_bundle` / `_remap_ref` | extend (add events to typed_entries + pass-2 FK rewrite) | `deploy.py:502` |
| `ManifestEventSource` / `serialize_event_source` / `_resolve_event_source` | exist (git-sync); reuse/confirm | `manifest.py:330`, `manifest_generator.py:320`, `manifest_import.py:2507` |
| `_upsert_events` | net-new, templated off `_upsert_forms` | new (near `deploy.py:1164`) |
| `_event_entries` (scrub webhook state) | net-new, analog of `_form_entries` | new (near `capture.py:435`) |
| migration + ORM column | net-new (keystone) | `events.py:38,227` |

**Honest verdict:** ~70% reuse. The one trap absent in forms: subscriptions reference workflow/agent by
**UUID FK**, so the pass-2 remap MUST run on them or a fresh install's triggers point at the wrong (or
no) workflow. The remap machinery exists; the work is registering events in it.

## Build order
T1 (migration/ORM, unblocks everything) → T2 (capture/manifest) → T3 (deploy/reconcile/export) →
T4 (tests, interleaved TDD) → T5 (verify). Then desloppify the new code.

## Out of scope (this plan)
Knowledge own-first (separate plan, NOT built tonight). Storage (post-V1). Running webhook external
re-subscription automatically (the instance re-establishes it; we carry the definition only).
