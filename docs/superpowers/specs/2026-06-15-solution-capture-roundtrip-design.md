# Spec: Solution capture → pull → deploy round-trip (pending-capture queue)

**Date:** 2026-06-15
**Status:** design, awaiting user review → writing-plans
**Branch:** `solutions/connection-references` · worktree `solutions-success-criteria`
**Discovered by:** the build-skill validation loop (Track A run 1, `docs/plans/2026-06-15-build-skill-validation-log.md`).

## 1. Problem

`bifrost solution capture` (exposed in BOTH the CLI and the UI via `client/src/components/solutions/SolutionCaptureDialog.tsx` → `POST /api/solutions/{id}/capture`) flips `solution_id` / `is_solution_managed` on a DB entity record **server-side only**. It writes nothing to the source workspace.

`bifrost solution deploy` is **manifest-driven full-replace**: the server reconcile sweep DELETES any solution-managed entity not present in the incoming `.bifrost/*.yaml` manifest (`api/src/services/solutions/git_sync.py:99` — "a type missing from the bundle gets DELETED by deploy's reconcile sweep").

**Consequence (verified, reproduced twice with a table):** capture a table/form/agent/config → next deploy-from-source **deletes it**, because source's manifest never knew about it. Only workflows round-trip, and only because the user manually adds a UUID-keyed entry to `.bifrost/workflows.yaml`. This blocked Track A of the skill validation: there is no working capture→deploy round-trip for table/form/agent/config.

**The hard part:** a *genuine delete* (dev removes form Y from `.bifrost/forms.yaml` and deploys to delete it) and an *un-pulled UI capture* (form Z captured in the UI, never pulled to source) are **indistinguishable** from `solution_id` alone — both are "solution-managed in DB, absent from the incoming manifest." A naive "block if absent" guard would block every legitimate delete forever.

## 2. Constraints / principles (from the user)

- **Source is the only writer.** The UI is read-only for solution-managed entities; people MUST deploy from source. A UI capture must therefore be forced back into source before it is "real."
- **Never silently delete.** Deletion is the scary failure mode. Deletion must only ever touch entities source has *demonstrably seen*. When in doubt, **block the deploy** — never delete.
- **Don't wipe the dev's local work.** Pulling captured entities back to source must NOT touch hand-authored source (`apps/`, `functions/`) — only the generated `.bifrost/` manifest.

## 3. Design — pending-capture queue

A separate queue table makes "captured but not yet pulled to source" a first-class, single-source-of-truth state, distinguishable from a genuine delete, without mutating the (heavily guarded, org-scoped) entity tables.

### 3.1 `pending_captures` table (new — the only schema change)

```
pending_captures
  id           UUID PK
  solution_id  UUID   (FK → solutions install; indexed)
  entity_type  str    ("table" | "form" | "agent" | "config" | "event" | "claim")
  entity_id    str    (the captured entity's id; for config, its key)
  captured_at  timestamptz
  captured_by  UUID | None  (user who captured; null for system)
  UNIQUE(solution_id, entity_type, entity_id)
```

No columns added to `tables` / `forms` / `agents` / `configs` / `events` / `custom_claims`. Answering "does this install have un-pulled captures?" is a single `SELECT ... WHERE solution_id = ?` (no 6-table union). The UI can render the queue ("3 captured entities pending pull").

### 3.2 Capture enqueues

`POST /api/solutions/{id}/capture` (the existing endpoint, used by both CLI and UI): after it sets `solution_id` on each captured entity, INSERT a `pending_captures` row per captured entity (idempotent via the UNIQUE constraint — re-capturing is a no-op). No behavior change to what capture does to the entity itself.

### 3.3 `bifrost solution pull` (new CLI command) drains the queue

- Regenerates the local `.bifrost/*.yaml` for this solution from current server state, **reusing the existing serializers** (`api/src/services/manifest_generator.py::serialize_table/serialize_form/serialize_agent/serialize_config/...`, the same ones `build_workspace_zip` uses).
- **Writes ONLY the `.bifrost/` directory.** It MUST NOT touch `apps/`, `functions/`, `lib/`, or any hand-authored source — those are the dev's working tree. (`pull` rewrites the machine-generated manifest, which is safe by construction; the dev's code edits are untouched.)
- After a successful local write, the server DELETES the `pending_captures` rows that were materialized (the entities are now in source). Mechanism: pull calls a server endpoint that returns the manifest delta + clears the queue rows it covered, OR pull POSTs the set of materialized entity ids to a `pull/ack` endpoint that clears them. (Implementation detail for the plan — keep the clear server-authoritative so two concurrent pulls don't double-clear incorrectly.)
- **Agent-runnable.** `pull` only rewrites generated `.bifrost/`, so the agent may run it when a deploy 409s (low blast radius, like regenerating types) — UNLIKE watch/push/sync which stay user-driven.

### 3.4 Deploy guard — block, never silently delete

On `POST /api/solutions/{id}/deploy`, BEFORE the reconcile/delete sweep:

1. Load `pending_captures` rows for this install.
2. For each pending row whose `entity_id` is **absent from the incoming manifest** (`body.tables/forms/agents/config_schemas/...`): collect it as an unpulled-capture blocker.
3. If any blockers → **HTTP 409**, body naming them: *"N entities were captured (UI/CLI) and are not yet in your source: {type:id, …}. Run `bifrost solution pull`, then deploy."* No reconcile, no delete.
4. If no blockers → proceed to reconcile exactly as today. An entity absent from the manifest with **no pending_captures row** is a **genuine delete** → deleted as today (source pulled it at some point, then deliberately removed it; pull had drained its queue row).

**This is the core distinction:**

| Entity state | In manifest? | pending_captures row? | Deploy action |
|---|---|---|---|
| Pulled, then kept | yes | no | upsert (normal) |
| Pulled, then deliberately removed | no | no | **DELETE** (genuine) |
| Captured (UI/CLI), not yet pulled | no | yes | **409 BLOCK** (pull first) |

Deletion only ever happens for entities with no pending row — i.e. entities source has demonstrably seen. That is the "never delete what source hasn't seen" guarantee.

### 3.5 Edge cases

- **Dangling queue row** (entity hard-deleted out from under a stale row): the deploy guard resolves each pending row's `entity_id`; a row pointing at a no-longer-existing entity is ignored (and cleaned up — pull/deploy can prune pending rows whose entity is gone). It never blocks on a nonexistent entity.
- **Capture then immediately deploy (same CLI session):** still 409s until `pull` runs. Uniform across UI/CLI — capture and pull are distinct steps. (We deliberately do NOT auto-pull inside capture: keeping them separate keeps the UI and CLI paths identical and the "source is the writer" boundary crisp.)
- **Concurrent pulls / deploys:** the existing solution write-lock (`api/src/services/solutions/write_lock.py`) serializes deploys; queue-row clearing is server-authoritative so a stale client can't clear rows it didn't materialize.
- **Config quirk:** A1 found config capture reports success but doesn't set `solution_id` on the config record (configs use `solution_config_schema` + `origin_solution_id`). The plan must verify the config capture path actually enqueues + round-trips, or scope configs explicitly.

## 4. Components / files

| Unit | File(s) | Responsibility |
|---|---|---|
| `pending_captures` ORM + migration | `api/src/models/orm/` + `api/alembic/` | the queue table |
| capture enqueue | `api/src/routers/solutions.py` (`capture_solution_entities`) + the capture service | insert queue rows on capture |
| deploy guard | `api/src/routers/solutions.py` (`deploy_solution`) + reconcile service | 409 on pending+absent; genuine delete otherwise |
| `solution pull` CLI | `api/bifrost/commands/solution.py` | regenerate `.bifrost/` from server, ack/clear queue |
| pull server support | `api/src/routers/solutions.py` | serve manifest delta + clear queue rows (server-authoritative) |
| manifest serializers (reuse) | `api/src/services/manifest_generator.py` | already exist — pull reuses |
| UI surfacing (optional, follow-up) | `client/src/components/solutions/` | show "N pending captures — pull to source" |

## 5. Testing

- **Unit:** capture inserts a queue row (idempotent on re-capture); deploy guard returns 409 when a pending+absent entity exists; deploy proceeds + deletes when an absent entity has NO queue row (genuine delete); pull clears queue rows for materialized entities only.
- **Guard-faithful:** solution-managed writes use Core statements per the always-on read-only `before_flush` guard (memory `project_solution_managed_guard_deploy_core`) — install the guard in tests so they're prod-faithful, not false-green.
- **E2E (the real proof):** UI/CLI capture a table+form+agent+config → deploy → assert 409 names them → `solution pull` → assert `.bifrost/*.yaml` now contains them AND `apps/`/`functions/` untouched → deploy → assert success + entities survive → remove one from manifest → deploy → assert it deletes (genuine). This e2e replaces the broken round-trip the validation loop found.
- **The validation loop itself (Tasks 11–12) is the ultimate check** — once this fix lands, Track A should reach a clean from-scratch solution-with-entities build.

## 6. Constraints carried in

- Worktree only; never two concurrent `./test.sh`; full pre-completion verification.
- Solution-managed writes from deploy/sync/capture/pull MUST use Core statements (the read-only guard 500s on ORM-object mutation in prod but passes in isolated unit tests — install the guard in the test).
- Test-stack API container must be restarted after deploy-path code changes (long-running; HTTP API reads source at boot).
- No client specifics in the public repo.
