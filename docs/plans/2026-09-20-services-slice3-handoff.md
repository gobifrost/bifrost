# Slice 3 handoff — worker service mode + Services UI mock

Parent plan: `docs/plans/2026-09-20-supervised-workflow-services-plan.md` (§1–§19).
Worktree: `.claude/worktrees/services-plan`, branch `services-plan` (uncommitted).
Base: `origin/main` @ `033b358f2`. Test + debug stacks running for this worktree.

## Where things stand

- Slice 1 (definition + discovery) and Slice 2 (control plane) are implemented,
  reviewed by Codex twice, and green (unit + e2e + quality + client checks).
- New tables `service_definitions` / `service_attempts` exist with migration
  `20260920_service_tables`. REST at `/api/services/*` works live.
- No runner exists: definitions sit eligible, attempts are never claimed.

## Slice 3 scope (do this next)

1. **Renewable service credentials** (§18.5 — the load-bearing design). The
   engine token (`api/src/core/security.py:414`) is superuser and short-lived.
   Design service-scoped, renewable credentials + rotation channel before any
   long-lived child runs. No placeholder schema was added; this slice owns it.
2. **`route_service(...)`** in `api/src/services/execution/process_pool.py`
   alongside `route_execution`, with separate per-worker service slots
   (never consumes `max_workers` workflow capacity). Same template fork,
   same engine — no second execution implementation.
3. **Engine service mode**: invoke the `@service` coroutine, plumb stop as
   cancellation (SIGTERM today only flips a loop-top flag —
   `template_process.py:442`), continuous bounded log flush (today flushes
   only at completion — `workflow_execution.py:421`), variables stay disabled.
4. **`service.*` SDK** (`ready()`, `is_stopping()`, `wait_until_stopping()`)
   on the `service` decorator namespace from Slice 1.
5. **Claim loop**: `expire_leases` + `claim_eligible_service` from
   `api/src/services/service_lifecycle.py`, worker-pull (workers claim
   eligible rows; scheduler only advances eligibility — §18.4).
6. **Autonomous execution identity** (Codex deferral): stable caller/org/role
   policy for config, secrets, tables, files, emit — specified with (1).

## Services UI mock (do early — de-risks the state contract)

Mock before wiring, against fixture data:
- Placement: **Workflows → Services tab** (same area, separate list — reuse
  `WorkflowListSurface` patterns, not a fork). Badge + `workflowTypeLabel`
  already handle `type='service'`; execute navigation already disabled.
- List columns from `ServiceResponse`: name, source path, desired/observed
  state, readiness, worker, uptime, restart count, revision, last exit reason.
- Detail view: simplified executor page, **logs-focused** — continuous
  per-service timeline (attempt filter), attempt history, start/stop/restart/
  enable/disable actions. No variables panel, no result panel.
- Mock first with fixtures; wire to `/api/services/*` after (§14, Slice 4
  will add `service_logs` streaming + retention).

## Rehydrate checklist (new thread)

1. Read the parent plan §5, §6, §7, §18, §19.
2. Read `api/src/services/service_lifecycle.py` (claim/heartbeat/complete/expire).
3. Read `api/src/services/execution/process_pool.py` (`route_execution`,
   `_dispatch_to_child`, `_monitor_loop`, drain) + `template_process.py`
   (child entry, SIGTERM flag) + `engine.py` (`_execute_workflow_with_trace`).
4. Check `git status` + `git log --oneline -5` in the worktree.
5. Boot stacks only if down: `./test.sh stack status`, `./debug.sh status`.

## Verification bar (same as Slices 1–2)

Focused suites via `./test.sh` (unit + related e2e + tripwires
`test_dto_flags`/`test_contract_version`/skill freshness), `./test.sh quality
api`, client `tsc`/`lint`/vitest for UI, one live debug-stack pass per
milestone, Codex review at the milestone. Merge queue is the broad gate;
never run full suites locally by default.

## Slice 3 built (2026-09-21, uncommitted on `services-plan`)

1. **Credentials** — `docs/plans/2026-09-20-services-credential-design.md`
   (D1–D5): `mint_service_token` (sentinel sub, `is_superuser=False`,
   org scope, 15-min, `type=access`), parent-driven Redis handoff
   (`bifrost:service:{attempt}:token`, minted on every lease heartbeat),
   child 60s refresh task, key deletion on lost ownership. Org-less
   definitions never eligible (claim gate + test).
2. **Services UI mock** — Workflows → Services tab, `ServiceListSurface`
   (all state-contract columns) + `ServiceDetailView` (logs w/ attempt +
   level + search filters, attempt history, actions) on fixtures; verified
   live in the browser. **Contract gap locked**: `ServiceResponse` lacks
   worker/uptime/readiness/last-exit — `ServiceListItem` composes them from
   attempts; Slice 4 wiring expands `GET /api/services` or fans out.
3. **Worker service mode** — `route_service` (separate `max_service_workers`
   slots, never `max_workers`), engine service mode (cooperative stop via
   loop signal handlers + stop-event supervision, no settrace/timeout kill,
   attempt-scoped bounded streams `bifrost:service-logs:{attempt}` + live
   publish, 60s credential refresh, secret scrubbing), `service.*` SDK on
   the decorator namespace, claim loop (worker-pull: expire → beat owned →
   claim; ready drain; stop mirror; token rotation; `handle_service_result`
   → `complete_attempt` with fencing), worker-container wiring with
   graceful handover.
4. **E2E proof** — `test_service_worker_mode.py` (deployed worker: claim/
   ready/logs/stop-no-restart/rolling-restart/crash-loop/token scopes) and
   `test_service_owner_loss.py` (real forks: SIGKILL→restart,
   owner-death→lease-expiry→takeover; slot-filler exclusivity).
5. **Slice 2 updates forced by Slice 3** — `test_services.py` no-runner
   premise retired; lifecycle fixtures org-scoped.

Green: quality api, 300+ backend unit, services API e2e (7), service e2e
(5+2), full client vitest (3117), tsc/lint. Not run: full backend e2e,
`pre-pr` (merge-queue gate).

## Codex milestone review (2026-09-21) — 6 findings, all resolved

1. **P1 emit 403**: `POST /api/events/emit` required superuser, so service
   tokens (non-superuser by design) could never produce. Fixed narrowly:
   `service_id`/`service_attempt_id` carried onto `UserPrincipal`,
   `is_service_principal()` in `solution_scope.py`, emit admits
   superuser-or-service with org confinement (own org only, no GLOBAL).
   Config/secrets/tables stay superuser-gated → Slice 6 authZ matrix
   (design doc D3 corrected; it previously claimed they worked).
2. **P1 commit-before-fork**: claim committed after route, so an instant
   child outcome completed against an invisible attempt (dropped + zombie
   live attempt). `_claim_available` now commits the claim first;
   route failures complete `requested`/`route_failed` (no failure
   accounting). Covered by new unit tests.
3. **P1 crash-misclassification**: health loop skipped the queued-result
   drain for service handles (no `current_execution`) → clean returns
   reported as crashes. Drain condition now covers service handles.
4. **P1 orphan grace**: Case-B grace used the pool default (6s), orphaning
   gracefully-unwinding service children (e.g. 30s grace) mid-cleanup.
   Now per-handle (`graceful_shutdown_seconds + 1s`) for services.
5. **P2 startup grace unenforced**: never-ready attempts renewed forever.
   Beat path now fails them (`startup_grace_exceeded`, restartable) once
   past `startup_grace_seconds`. New unit test.
6. **P1 secret in errors**: service exception strings persisted raw into
   `ServiceAttempt.error`. Now redacted with the context's collected
   secrets (logs already were). New unit test.

Also fixed while verifying: `_execute_async` dropped the service identity
(`error_message` vs `error` shape mismatch — FastAPI-visible as
"service attempt failed" with no detail); `run_service` now returns the
internal shape with identity passthrough. Slice 2 `test_services.py`
no-runner premise retired (runner now claims).
