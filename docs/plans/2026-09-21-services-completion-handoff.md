# Handoff — ship supervised Services (all remaining work)

Owner: Jack. Worktree: `.claude/worktrees/services-plan`, branch
`services-plan` (uncommitted Slices 1–3 + UI rounds + PARTIAL backend edits —
see §0). Base: `origin/main` @ `033b358f2`. Never commit unless asked; all
changes in this worktree only.

## §0 — tree state (read this first)

Verification status of what's in the tree is MIXED — do not assume green:

- Backend Slice 3 (claim loop, worker service mode, credentials, engine,
  pool service slots, worker wiring) — reviewed by Codex, findings fixed,
  unit + e2e green at the time.
- UI mock rounds (Services page + detail + RunPanel gate + panel date
  filter): 94 UI tests + tsc/lint green at last run. Browser panel died
  mid-verification — last rounds were NOT eyeball-verified live.
- PARTIAL, UNVERIFIED backend edits from the live-wiring attempt (do not
  trust; run quality + units first, or revert):
  - `api/src/services/execution/process_pool.py`: `get_service_memory_mb`
    + `memory_mb` in heartbeat `service_children`.
  - `api/src/services/service_claim.py`: `_report_memory` /
    `_clear_reported_memory` (registration-hash publish). NOTE: an edit
    dropped the `_rotate_token` def line mid-file and it was repaired —
    re-read that region before trusting it.
  - `api/tests/unit/execution/test_process_pool_services.py`: memory
    assertion. `api/src/config.py` + pool default: `max_service_workers`
    5 → 20. `api/tests/e2e/platform/test_service_owner_loss.py`:
    `STACK_SERVICE_SLOTS` 5 → 20.
  - UI: `ServiceListItem.memory_mb` (contract), fixtures with memory
    values — all currently consumed by NOTHING live (mock only).

## §1 — what's done and proven

- Renewable service credentials (design doc:
  `docs/plans/2026-09-20-services-credential-design.md` D1–D5), service
  tokens work live (org-scoped emit proven e2e; admin gates reject).
- Worker-pull claim loop, engine service mode (cooperative stop, no
  settrace/timeout kill, attempt-scoped bounded Redis streams
  `bifrost:service-logs:{attempt_id}` + Redis pubsub on the same key),
  crash/lease-expiry recovery, startup-grace enforcement — all e2e
  (`test_service_worker_mode.py`, `test_service_owner_loss.py`).
- UI mock: `/services` + `/services/:serviceId`, table/cards, state+org
  filters, search, grid/table, detail header facts, icon actions,
  Open in editor (REAL via fileService), RunPanel service gate, shared
  `ExecutionLogsPanel` + native date filter, unified timeline (attempts
  live as system rows — no tabs, no attempt picker, by owner decision).

## §2 — ship list (mock → shippable, in order)

### 2.1 Control actions live + confirmations (pure frontend)

Endpoints exist and are e2e-proven. Replace mock toasts in
`pages/Services.tsx` + detail `onAction` with real `services.ts` calls;
**confirm modals on Stop/Restart/Disable** (AlertDialog precedent:
`pages/Applications.tsx` delete dialog); Start/Enable direct. Success →
invalidate live queries; failure → `getErrorMessage` toast. If unsure
whether stops need confirm, ASK — do not invent.

### 2.2 Live list + detail data (needs the API decision below)

- Definitions: `GET /api/services` exists.
- **Contract gap — DECIDE, do not invent:** `ServiceResponse` carries NO
  worker/uptime/readiness/last-exit/memory. Recommended: expand the
  endpoint to embed `active_attempt` summary + `last_exit_reason` +
  `memory_mb` (one round trip, backend-owned). Alternative: UI fans out
  to `GET .../attempts` per row (N+1 — only if backend resists).
- Memory has NO HTTP source today (worker-local heartbeat data). Two
  options, recommended first: (a) claim loop already publishes
  `{attempt_id: {memory_mb, updated_at}}` into the pool registration hash
  (see §0 partial work) — API reads hashes across workers
  (`bifrost:pool:*`, 2-colon keys only; precedent: `get_packages_from_workers`
  in `api/src/routers/packages.py`), accepts entries < 90s old;
  (b) persist a `memory_mb` column on attempts (migration — heavier,
  smells like telemetry in durable rows; avoid unless (a) fails).
- Detail attempts: `GET .../attempts` exists — use directly.
- Retire fixtures: delete `serviceFixtures.ts`; `getServiceHref`
  resolves workflow→definition from the live list.
- Refresh: refetch on action + focus + explicit Refresh buttons. NO
  browser polling loops (plan invariant).

### 2.3 Log persistence — SHIPS (trailing log, not 30 days)

Owner decision: persist, bounded trailing — not the 30-day plan §7.
- New `service_logs` table mirroring `execution_logs` (id PK,
  service_id, attempt_id, level, message, timestamp, index on
  (service_id, timestamp or id)). Alembic migration required.
- Flush: claim-loop beat drains owned attempts' streams
  (`XRANGE` from a Redis cursor key per attempt, bounded ~500/flush)
  into Postgres. Cursor in Redis (survives owner takeover; memory-only
  would duplicate after failover).
- Retention WITHOUT a new scheduler: trim inside the flush (delete
  beyond newest ~2000 rows per service). Cap size is a guess — note as
  Slice 6 tuning input.
- Read: `GET /api/services/{service_id}/logs` over POSTGRES (params:
  `attempt_id?`, `levels?`, `start/end_date?`, `limit` default 200 max
  1000; mirror `ExecutionLogRepository.list_logs` filter style in
  `api/src/repositories/execution_logs.py`).
- UI flips `MockServiceLogLine` → response DTO; panel stays (it already
  renders this shape). Date filter passes dates to the query AND the
  panel (same predicate, harmless).

### 2.4 Live log streaming over the standard WebSocket

Child publishes to Redis pubsub `bifrost:service-logs:{attempt_id}`
today; API `ResilientPubSubListener` pattern-subscribes `bifrost:*`
(`api/src/core/pubsub.py:193` — VERIFY the routing first, it was not
fully traced). Needed: bridge pubsub → `manager.broadcast(
`service:{service_id}`, …)`; WS allowlist branch for `service:` channels
(platform-admin only) next to the `execution:` branch in
`api/src/routers/websocket.py` (~:954); UI subscribes on detail
(`useExecutionStream.ts` is the pattern; `ExecutionLogsPanel` already
takes live `StreamingLog` entries + `isConnected`).

### 2.5 Mechanical tail (with ANY api change)

Regenerate `v1.d.ts` against the worktree stack (never hand-edit; API
needs restart for new routes — hot reload does not register routers).
Run skill-truth `generate.py` (the `--check` tripwire fires on new
endpoints). `test_contract_version.py` disposition: services DTOs are
currently UNfingerprinted — confirm still true after edits, else follow
the file's bump protocol. `test_dto_flags.py` for any CLI-relevant DTO.

## §3 — explicitly OUT (follow-ups, not this slice)

Services CLI (`bifrost services ...` — still missing since Slice 2),
config/secrets/tables service auth (Slice 6 authZ matrix), revision
rollover + backoff/crash-loop tuning (Slice 6, including the provisional
`max_service_workers=20` default), manifest/Solutions portability
(Slice 5), Playwright happy-path spec for Services (none exists — add
when the page is live; needs seeded services).

## §4 — verification (per AGENTS.md, focused)

`./test.sh quality api`, backend unit for touched domains, services e2e
additions (live action round-trips, logs-endpoint reads, persistence
across owner failover), client `tsc`/`lint`/vitest for touched surfaces,
`test_dto_flags` + `test_contract_version` + skill freshness, one
Playwright happy path if the harness allows, live debug-stack pass
(`/debug.sh status` for URL; demo services under
`workflows/demo_services/`), Codex review at the milestone. Full backend
e2e + `./test.sh pre-pr` are merge-queue territory, not local
prerequisites. Known pre-existing: one `no-console` lint warning in
untouched `client/e2e/support/seed-review-pack.ts`.

## §5 — resumption checklist (fresh session)

1. Read this file, then parent plan §7, §14, §16, §18.5–18.7, then the
   credential design doc (all under `docs/plans/`).
2. `git status --short` + `git log --oneline -5` in the worktree; confirm
   branch `services-plan`, base `origin/main`.
3. `./test.sh stack status` + `./debug.sh status`; boot whichever is down
   (`stack up` / `./debug.sh up`). Debug URL + login come from debug
   status (netbird credentials rotate — never copy them into docs).
4. Re-verify §0 partial work FIRST (`quality api` + named unit files)
   before building on it.
5. Standing rules: worktree only, never commit unless asked, no browser
   polling loops, no new polling/confirm inventions (ask), fixtures die
   with the mock (no split-brain live/mock states).
