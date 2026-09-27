# Supervised Workflow Services — Implementation Plan

Status: plan + Slice 1 implemented (2026-09-20, branch `services-plan`).
Worktree: `.claude/worktrees/services-plan`, base `origin/main`.
No code changed by the plan itself; Slice 1 changes are uncommitted in the worktree.

## 1. Agreed scope

Services are user-authored `@service` functions that run continuously to hold a
connection, subscription, or listener (Telegram long-poll, Discord gateway,
MQTT subscriber, IMAP IDLE, feed watcher). Finite work stays where it is:
one-shot handling → workflow, periodic finite work → scheduled workflow,
durable finite platform work → `PlatformJob`, public HTTP callback → webhook.

**In for MVP:**
- `@service` discovery alongside workflows, `type='service'` on the workflows table.
- Importable service runtime SDK: `service.ready()`, `service.is_stopping()`,
  `await service.wait_until_stopping()`, plus cooperative SIGTERM/task-cancel propagation.
- Desired state (`running`/`stopped`) + automatic startup and recovery across
  exceptions, child crashes, worker restarts, container replacement.
- Singleton execution across multi-worker deployments via fenced DB leases.
- Explicit start/stop/restart/enable/disable; health, uptime, attempts, exit reasons, logs.
- Bounded restart backoff + crash-loop protection.
- Graceful shutdown on user request, deployment, worker drain (SIGTERM → bounded SIGKILL).
- Explicit per-worker service capacity that preserves ordinary workflow slots.
- `events.emit` from services (producer-only; services trigger existing
  topic/webhook/schedule subscriptions).
- Service logs: permanent-per-service view, retention, streaming.
- Portable definitions: manifest + git sync + Solution capture/install.
- UI under Workflows → Services; CLI + REST controls.

**Explicitly out:**
- Service inbox / `subscribe()` SDK, `target_type='service'` deliveries.
- Codex-harness-as-consumer (no consumer direction for services in MVP).
- Public ingress, arbitrary ports, TLS/routing, MCP/HTTP server exposure.
- Replica counts > 1, rolling multi-replica deploys, service-to-service discovery.
- User-defined health probes (MVP: process-alive + lease-healthy + `ready()`).
- Immortal `PlatformJob`s, permanently unacknowledged RabbitMQ messages,
  in-memory-only restart loops, duplicate execution engines.
- Variable tracing for services.

## 2. User model

```python
from bifrost import config, events, service

@service(startup="automatic", restart="always")
async def telegram_bridge():
    async with connect(config.get("telegram_token")) as client:
        service.ready()
        async for message in client.messages():
            await events.emit("telegram.message.received", message.to_dict())
```

- Decorator carries identity only (name/description/category/tags), matching the
  `@workflow` identity-only rule (`api/bifrost/decorators.py:14-26`).
  `startup`/`restart` in the sketch above are authoring intent; durable policy
  lives in DB (UI/API-owned like `timeout_seconds`), not decorator params.
- No `context` parameter required; SDK context stays importable (ContextVar),
  same as workflows.
- `service.ready()` separates "function started" from "connection established".
- Stop/deploys propagate via task cancellation + SIGTERM so `async with` blocks
  clean up naturally.

## 3. State model

Durable desired state (small, PostgreSQL source of truth):

```text
running | stopped
```

Derived observed state from the current attempt:

```text
stopped → starting → running → stopping → stopped
starting | running → restarting → starting
restarting → crash_loop (desired stays running, no new attempts)
unknown → restarting
```

- `startup=automatic` sets initial desired `running` on enable/install-adopt;
  it never means "invoke during every API/worker boot".
- User stop writes `desired=stopped` first, then terminates — the reconciler must
  not restart it.
- Crash-loop retains `desired=running`, stops launching, surfaces last failure;
  manual restart or source/config change clears it.

Health signals stay distinct: **process alive** (worker observes child),
**lease healthy** (owner renews), **service ready** (`ready()` called within
`startup_grace_seconds`).

## 4. Runtime architecture: reuse data plane, add control plane

```text
service source → discovery/indexer → durable definition + desired state
  → service reconciler → fenced lease claimed by a worker
  → import template fork → isolated child → execution engine (service mode)
  → SDK context (emit, config, logging)
```

Principle: one underlying user-code engine; two lifecycle modes in the pool:

```python
route_execution(...)  # finite, one-shot (unchanged)
route_service(...)    # long-lived, supervised (new)
```

### 4.1 Reuse as-is

- Decorator parsing/discovery (`api/bifrost/decorators.py`,
  `api/src/services/file_storage/indexers/workflow.py`, `module_loader.py`).
- Module loading + dependency resolution + import template fork
  (`template_process.py:639 fork`, `:410 _run_forked_child`).
- SDK context injection, secrets/config access, import restrictions.
- Worker registration/heartbeat, memory-pressure admission
  (`process_pool.py:815-824`), crash detection (`:1358-1425`),
  cancel listener (`:1106-1148`), WebSocket fan-out, horizontal scaling.

### 4.2 Semantics that must change for service mode

Verified finite assumptions (all must be handled, not inherited):

1. **One-shot result pipe.** Child sends one dict then `break`s + exits
   (`template_process.py:509,519-520`); parent `pop`s the handle and frees the
   `max_workers` slot on result (`process_pool.py:1572-1622`). Service children
   must not hold a workflow slot for months → separate service-slot accounting.
2. **Terminal log flush.** Engine streams to Redis but Postgres flush happens
   parent-side post-completion (`workflow_execution.py:421-434,610-623`).
   Services need bounded periodic flush + rotation (see §7).
3. **Variable tracing.** `sys.settrace` captures on return/exception only
   (`engine.py:994-1016`); `variables`/`execution_context` persist once at
   completion (`workflow_execution.py:384-397`, dropped on failure `:578-586`).
   Disabled for services (§7).
4. **Timeout kill.** `_monitor_loop` 1s tick kills at `timeout_seconds`
   (`process_pool.py:960-1042`); engine has no in-function timer. Services use
   `graceful_shutdown_seconds` + cancel, not execution timeout. The stuck
   sweeper TIMEOUTs RUNNING past workflow-timeout+5min (30min default) every
   5min (`execution_cleanup.py:32-36,89-105`) — service attempts must be exempt
   by construction (dedicated attempt model, not `executions` rows) or the
   sweeper kills every service within the hour. Only escape hatch today is
   `timeout_seconds == 0` (`:97-100`); do not rely on it.
5. **Template recycling.** `drain_and_restart_template` waits bounded 60s for
   `processes` to empty, then kills survivors (`process_pool.py:466-499`).
   Long-lived children block recycling → mark affected service revisions stale
   on template refresh, gracefully stop old attempt, start new revision (§8).
6. **RabbitMQ ownership.** `BaseConsumer` acks at dispatch, before the child
   completes (`rabbitmq.py:272-307`; `workflow_execution.py:1024-1058`).
   Never hold a delivery open for a service lifetime — RabbitMQ only carries a
   wakeup hint; the DB lease owns the lifetime (§6).
7. **Cooperative cancel gap.** Child SIGTERM handler only sets
   `shutdown_requested`, checked at the work-loop top
   (`template_process.py:442-458`); a stuck `await` only dies by parent SIGKILL
   (`process_pool.py:1071-1078`). Service mode needs the stop signal plumbed
   into the user coroutine (cancel scope / `wait_until_stopping`) — kill-only
   is insufficient for graceful disconnect.

## 5. Durable data model (new tables, not PlatformJob rows)

Service attempts are intentionally non-terminal → they are not `PlatformJob`s
(which would permanently occupy the 2 scheduler claim slots) and not
`executions` rows (which the sweeper terminalizes). Finite service management
ops (dep install, env rebuild, revision deploy) may still use `PlatformJob`.

**`service_definitions`** (one per `@service` function):

```text
id, workflow_id (source identity: path+function_name),
organization_id, solution_id, name, description,
enabled, startup_policy (automatic|manual), restart_policy (always|on_failure|never),
desired_state (running|stopped), current_revision,
graceful_shutdown_seconds, startup_grace_seconds,
restart backoff (initial/max/jitter), crash-loop (max_restarts/window/cooldown),
created_by, created_at, updated_at
```

**`service_attempts`** (many per service, immutable once terminal except final
bookkeeping):

```text
id, service_id, revision, worker_id, lease_token,
state, ready_at, started_at, heartbeat_at,
stop_requested_at, stopped_at, exit_code, exit_reason, error,
restart_number, created_at
```

- At most one valid active lease per service; all attempt updates carry the
  lease token and are rejected after reassignment (fences stale workers after
  network partition).
- Attempt history retained longer than raw logs (e.g. attempts 90d, logs 30d —
  finalize numbers in implementation).

Alembic migration required. Partial-unique precedent for scoping:
`workflows` `(path,function)` / `(path,function,solution_id)`
(`api/src/models/orm/workflows.py:162-176`) — service definitions follow the
same per-source scoping.

## 6. Reconciliation and assignment

Controller loop (leader-elected scheduler or worker-coordinated; PostgreSQL is
truth either way — decide in implementation, default: scheduler leader reusing
`src/scheduler/leadership.py`, since only the leader runs APScheduler triggers
today):

```text
desired=running + no valid attempt → eligible for worker claim
desired=stopped + active attempt → graceful termination (set stop first)
lease expired → mark lost → apply restart policy/backoff → eligible again
child exit → record terminal attempt → restart policy/backoff
revision change → stop old attempt → start new revision
```

- Workers advertise service capacity alongside execution capacity.
- Claim path: atomically acquire durable lease → resolve current revision →
  build SDK context → fork from import template → invoke `@service` in engine
  service mode → renew lease while owned → report `ready()` → record exit.
- RabbitMQ may carry a claim-wakeup hint; acknowledge it after durable claim,
  never hold it open.
- Worker shutdown: stop accepting claims → mark owned draining → SIGTERM →
  `graceful_shutdown_seconds` → SIGKILL → release/expire leases for takeover.

## 7. Logging, streaming, retention (agreed)

- One log stream per **service**, not per attempt: new `service_logs` table
  `(service_id, sequence)` ordered, `attempt_id` as correlation column —
  mirrors `execution_logs(execution_id, sequence)`
  (`api/src/models/orm/executions.py:126-144`).
- Write path mirrors today: child `log_and_broadcast` → Redis stream (bounded,
  `MAXLEN ~10000` precedent in `api/bifrost/_logging.py:125`) + PubSub live
  delivery → **new** periodic bounded flush to Postgres (every N sec/lines),
  not terminal-only. New `publish_service_log` on `service:{id}`, reusing
  `manager.broadcast` (`api/src/core/pubsub.py:160-200`).
- System rows (restart, `ready()`, stopping, crash-loop) are first-class log
  entries with distinct level/source, separable from user output.
- UI: continuous service view by default, attempt filter; level filter, search,
  cursor pagination (same contract as `ExecutionLogRepository`); late
  subscribers need replay like chat events have (execution pubsub is
  fire-and-forget — do not copy that gap).
- Retention, not permanent storage: address-stable per-service view, raw rows
  ~30d or per-service cap via the cleanup-scheduler pattern
  (`execution_cleanup.py`, `event_cleanup.py`); attempts outlive logs.
- Variables/`execution_context` snapshots: disabled. Buffered SDK writes via
  `WriteBuffer`/`flush_pending_changes` (`api/bifrost/_write_buffer.py`,
  `api/bifrost/_sync.py`) are currently caller-less (SDK writes go direct to
  REST) — if any buffered writer returns, it needs periodic flush + TTL
  heartbeat, not completion flush.
- Control actions (who started/stopped/restarted) go to audit, not service logs.
- Fix the read-path inconsistency found during inspection: `GET /{id}` treats
  CANCELLING as live-stream (`routers/executions.py:331-355`) but
  `GET /{id}/logs` falls through to DB (`:517-541`). New service log endpoints
  must have one rule.

New REST (state endpoints update durable desired state; never launch user code
in the API process):

```text
GET  /api/services
GET  /api/services/{id}
POST /api/services/{id}/start
POST /api/services/{id}/stop
POST /api/services/{id}/restart
GET  /api/services/{id}/attempts
GET  /api/services/{id}/logs
```

CLI mirrors it (`bifrost services list/get/start/stop/restart/logs/attempts`).
No browser polling: state over the existing notification WebSocket channel.

## 8. Source and dependency updates

- Build/refresh the import template as usual on source/dep change.
- Mark affected running service revisions stale → graceful-stop old attempt →
  start new attempt from refreshed template → preserve attempt history.
- Restart only affected services unless the template architecture forces
  broader; any broader restart is explicit and observable.

## 9. Capacity and scaling

- Extend the pool capacity model with an explicit per-worker service-child max;
  ordinary `max_workers` workflow slots are never consumed by services.
  Admission still consults cgroup memory pressure (same
  `has_sufficient_memory_cgroup` gate as `route_execution`).
- Singleton ownership via DB lease → adding worker replicas adds service
  capacity without duplicate instances.
- Pending-for-capacity is a visible state, not failed-attempt oscillation.

## 10. Restart policy

- `always`: unexpected return/exception/crash/lost worker → new attempt while
  `desired=running`. Manual stop changes desired first → no restart.
- `on_failure`: clean return stays stopped-but-visible (operator action
  required to restart); exception/crash restarts.
- `never`: any exit stays stopped.
- Exponential backoff with jitter (`1s→2s→4s→…→max`); rolling-window
  accounting sheds old penalties after sustained healthy run; crossing
  threshold → `crash_loop` (desired stays `running`, launches stop, last
  failure prominent).

## 11. Manifest, git sync, Solutions portability

Services ride the workflow manifest path as `type='service'` — no new
top-level entity file.

1. `api/bifrost/decorators.py:38` — widen `ExecutableType` with `"service"`;
   add `@service` decorator attaching `_executable_metadata` (identity-only).
   Mirror in `api/src/sdk/decorators.py:129` and
   `api/src/services/execution/module_loader.py:39,70,124` (three literal copies
   that must stay in sync).
2. `api/src/services/file_storage/indexers/workflow.py` — `extract_metadata`
   regex `:68,85`, `decorator allowlist :362-398`, new `service` branch in
   `index_python_file` (`:137-234` pattern), honoring the enrich-only contract.
   Decide gating: is `service` agent-callable (`tool_registry.py:101`,
   `agent_router.py:97`), form-bindable (`shared/form_publication.py:47`),
   endpoint-executable, event-triggerable. Default: not a tool, not endpoint —
   producer via `events.emit` only.
3. `Workflow` ORM: `type` docstring `:37-40` + new policy columns + migration.
   `WorkflowUpdateRequest` (`contracts/workflows.py:287-396`, today no `type`
   field) gains policy fields if UI/CLI-editable.
4. `api/bifrost/manifest.py` — `ManifestWorkflow.type :110` widens; policy
   fields as `CONTENT` via `classify()`; org/roles stay `ENVIRONMENT`;
   `from_row :135-156` + both `to_orm_values` dicts (GIT_SYNC `:158-190`,
   INSTALL `:191-209`).
5. `api/src/services/manifest_generator.py:93-95` — new columns surface via
   `from_row` automatically once added.
6. Import: `_resolve_workflow` (`manifest_import.py:1187-1249`,
   `github_sync.py` is a thin wrapper) + `_index_workflows_from_manifest`
   `:933-963` + deletions `:1416-1419,1540-1545` — same natural-key
   `(path,function_name)` upsert. Secrets stay out per existing scrub
   (Config SECRET `value=None`, OAuth `client_secret` never read, webhook
   instance state never serialized — `manifest.py:741-774,608-640`,
   `manifest_generator.py:227-236`).
7. Solutions: `capture.py:397-416` (`_workflow_entries`) carries service rows +
   `source` files; `deploy.py:828-890` (`_upsert_workflows`) remaps IDs per
   install (`uuid5`), stamps install org/solution, infers params from bundled
   source. **Install default: `desired_state=stopped`, `enabled` per operator**
   even when source says `automatic` — installing a bundle must not auto-dial
   with missing creds into a crash loop.
8. Validation + determinism: extend `validate_manifest`, `get_all_entity_ids`,
   `get_all_paths`, `filter_manifest_by_ids`; manifest-codec goldens will change
   (`test_manifest_codec.py`).
9. Tripwires that will fire (checklist, not blockers): `test_dto_flags.py`
   (new PATCH fields → CLI flags or `DTO_EXCLUDES` entry), `test_contract_version.py`
   (fingerprint refresh; `MIN_CLI_VERSION` bump only if CLI-parsed shape breaks;
   `CONTRACT_VERSION=10` frozen), `test_mcp_thin_wrapper.py` (new MCP tools as
   HTTP wrappers only), `test_skill_appendix_fresh.py` (regenerate via
   `api/scripts/skill-truth/generate.py`), `client/src/lib/v1.d.ts` regenerate
   via `npm run generate:types` against the running worktree stack (never
   hand-edit).
10. Tests per AGENTS.md manifest rule: round-trip unit (`tests/unit/test_manifest.py`)
    + E2E (`tests/e2e/platform/test_git_sync_local.py`).

## 12. PlatformJob relationship

Service attempts are not `PlatformJob`s. Finite service-adjacent work with
durable progress (dep install, env rebuild, revision deploy, complex uninstall)
may use `PlatformJob` via the standard enqueue/status/cancel contract.
Start/stop/restart/lease/reconciliation stay in the service controller + worker.

## 13. Security

Inherit workflow isolation + org scope. Additionally: only authorized users
change desired state/config; attempts get minimum SDK context; secrets never in
attempt payloads/logs (dynamic-secret scrubbing already in
`_execution_context.py:302-315`); fenced attempts can't write after
reassignment; restarts resolve the currently authorized revision; service logs
follow execution-log visibility (hide DEBUG/TRACEBACK from non-admins,
`routers/executions.py:364-367` precedent); no public network exposure in MVP.
Harness-style subprocess/network needs (deliberately out of MVP) get no
backdoor here — that discussion happens separately with explicit allowlists.

## 14. Product placement

```text
Workflows
├── Workflows
├── Services
├── Data providers
└── Tools
```

Services view: name, source location, desired/actual state, readiness, assigned
worker, uptime, restart count, current revision, latest exit reason, actions
(Start/Stop/Restart/Enable/Disable/Logs/Attempts).

## 15. Verification (from Codex list, adapted to agreed scope)

1. `@service` discovery + validation; importable SDK context without a function parameter.
2. Automatic startup after creation/enablement; manual services stay stopped.
3. Restart after unhandled exception; after child-process crash; after
   worker/container loss + lease expiry.
4. Stale-worker fencing after reassignment (old token writes rejected).
5. No restart after user-requested stop.
6. `always`/`on_failure`/`never` differences (incl. clean-return semantics).
7. Exponential backoff + crash-loop transition + manual clear.
8. Workflow capacity preserved while services run (service slots separate).
9. Graceful shutdown then SIGKILL when over grace; revision rollover restarts
   only affected services.
10. Continuous bounded log persistence + streaming + retention; attempt history
    survives log expiry.
11. Singleton under multi-worker claim races; worker scale-out adds capacity
    without duplicates.
12. AuthZ + org isolation (incl. solution-managed services).
13. Manifest round-trip + git-sync E2E + Solution install-lands-stopped.
14. UI/CLI state matches durable server state (no polling loops).

## 16. Implementation order

- **Slice 1 — definition + discovery:** decorator, `ExecutableType` x3,
  indexer branch, ORM columns + migration, `WorkflowUpdateRequest` fields,
  gating decisions. Unit: discovery/validation.
- **Slice 2 — control plane:** definition/attempt tables, reconciler,
  fenced claim/renew/release, start/stop/restart endpoints. Unit + multi-worker
  race tests (singleton, fencing, no-restart-on-stop).
- **Slice 3 — worker service mode:** `route_service`, engine service-mode
  invocation, cooperative stop plumbing, `service.*` SDK, crash/exit reporting,
  drain integration. Tests: exception/crash/worker-loss recovery, graceful vs
  forced shutdown.
- **Slice 4 — logs + observability:** `service_logs`, periodic flush, streaming
  channel, retention job, Services UI + CLI logs/attempts. Tests: bounded
  persistence, stream continuity across attempts, retention sweep.
- **Slice 5 — portability:** manifest fields, generator/import, git-sync E2E,
  Solution capture/install (lands-stopped), DTO/contract/skill tripwires,
  type regeneration.
- **Slice 6 — hardening:** backoff/crash-loop tuning, capacity limits,
  revision rollover on template refresh, authZ matrix, full verification list.

Do-not-build reminders (enforced in review): zero-timeout workflows masquerading
as services, unacked RabbitMQ lifetimes, immortal platform jobs, separate worker
containers, in-memory restart loops, forked engine copies, public ingress.

## 17. Resolved open decisions (from Codex list)

1. Definition storage: share `workflows` row (`type='service'`) for source
   identity + separate `service_definitions` table for desired state/policy.
2. Attempts: dedicated `service_attempts` model, not `executions` extension
   (sweeper + terminal-result coupling).
3. Capacity: new per-worker service-slot interface in the pool; workflow slots
   untouched.
4. Claim: DB lease + lightweight broker wakeup (no long-lived delivery).
5. SDK writes: none block MVP — `WriteBuffer` is currently caller-less; direct
   REST writers need no change; periodic flush only if a buffered writer returns.
6. Template recycling: revision-staleness marking; only affected services restart.
7. Clean return under `on_failure`: visibly stopped, desired stays `running`,
   explicit operator restart required.
8. Readiness timeout: restartable failure per policy (not silent `degraded`).
9. Browser contract: existing notification WebSocket channel; no new polling.
10. Backoff defaults: `1s→2s→4s→…→cap` with jitter + rolling window; exact cap
    and crash-loop thresholds tuned in Slice 6 with live measurements.

## 18. Codex review reconciliation (accepted 2026-09-20)

An independent Codex read-only audit verified the plan's core claims and found
it directionally sound but not implementation-ready. All corrections below are
accepted and normative where they conflict with earlier sections.

### 18.1 Corrections to plan text

- RabbitMQ ack wording (§4.2.6): ack happens after dispatch to the process
  pool, not after user code completes (`api/src/jobs/rabbitmq.py:281`,
  `api/src/jobs/consumers/workflow_execution.py:1024`). Never hold a delivery
  open for a service lifetime — unchanged conclusion, corrected mechanism.
- `ExecutableType` literal: single source in `module_loader.py:39`;
  `src/sdk/decorators.py:129` only chooses tool-vs-workflow. Slice 1 widens the
  one literal and the two decorator copies plus the contracts enum.
- `manifest_generator` surfaces only `Workflow`-row fields via
  `ManifestWorkflow.from_row`. Policy owned by `service_definitions` (§18.2)
  requires explicit composition in the generator — not automatic.
- Solution UUID remapping lives in `_remapped_bundle` (`deploy.py:544`), not
  `_upsert_workflows` (`:828`), which consumes remapped IDs.
- `get_all_entity_ids` / `get_all_paths` / `filter_manifest_by_ids` need no
  service branch: they iterate all `manifest.workflows` entries type-blind.
- `test_mcp_thin_wrapper.py` fires only if service MCP tools are added
  (manually maintained `PARITY_HANDLERS`). MVP adds none → no MCP surface.
- Scheduler capacity wording: an immortal job holds runner capacity and a lease
  indefinitely (not "2 slots" specifically).

### 18.2 Policy ownership (resolves §5 vs §11 tension)

Policy lives **solely in `service_definitions`**. `Workflow` keeps source
identity (`path`, `function_name`, `solution_id`) + the `type='service'`
discriminator. No restart/desired-state columns on `Workflow`. Manifest and
Solution serialization explicitly compose `Workflow + ServiceDefinition`.
`ManifestWorkflow.type` becomes a constrained literal (validation rejects
misspellings). Restart/startup policy is portable CONTENT; `desired_state`,
`enabled`, crash-loop status, lease, and current attempt are environment/runtime
state and never serialize.

### 18.3 Reconciler invariant (blocks Slice 2 schema)

"Desired running + no valid attempt → launch" is wrong for `on_failure` clean
returns, `never`, and crash-loop. Durable launch-suppression state is required
on the definition row from the start:

```text
service_definitions += blocked_reason (null | 'policy' | 'crash_loop' | 'disabled')
                     += restart_eligible_at (timestamptz, backoff gate)
```

Eligibility = `desired=running AND enabled AND blocked_reason IS NULL AND
restart_eligible_at <= now AND no live attempt`. Terminal policy outcomes
(clean return under `on_failure`/`never`, crash-loop entry) set
`blocked_reason`; manual restart/source change clears it.

### 18.4 Lease and claim protocol (blocks Slice 2)

- `service_attempts += lease_expires_at` + partial unique index permitting at
  most one live (non-terminal) attempt per service.
- **Worker-pull claims**: workers atomically claim eligible rows with fenced
  leases (they know real process/cgroup pressure). The scheduler leader only
  advances durable eligibility/backoff and publishes wakeup hints — no
  scheduler-to-worker assignment protocol.

### 18.5 Credentials (blocks Slice 3)

`mint_engine_token` (`security.py:414-456`) mints a **superuser** token lasting
workflow-timeout+5min (24h when timeout=0). Services outlive both bounds and
must not run as superuser. Slice 3 designs renewable service-scoped credentials
with a rotation channel before any long-lived child runs. Autonomous execution
identity (stable caller/org/role policy for config, secrets, tables, files,
emit) is specified in the same slice.

### 18.6 Decisions changed per review

- Dependency updates are a **controlled all-service rollout**: requirements and
  template state are worker-global (`process_pool.py:1243`,
  `package_install.py:183`). Revision isolation is per-source, not per-dependency.
- Service logs use **attempt-scoped Redis streams** with a service-wide DB view:
  lease fencing, replay, cleanup, and stale-owner isolation stay per-attempt;
  `GET /services/{id}/logs` presents the continuous timeline. Service-wide
  sequence needs a DB/Redis allocator surviving attempts; stale attempts must
  not append after lease loss (log fencing).
- Revision pinning: define the immutable source identity (content hash pinned
  at claim; mismatch behavior specified) since today's `content_hash` is
  unset-and-warn-only.

### 18.7 Touch points added to slices

- Slice 1 also covers: `entity_detector.py:122` fast scan + AST allowlists,
  `ast_parser.py:16`, file-save deactivation scan (`file_storage/service.py:559`),
  registration endpoint + type derivation (`routers/workflows.py:1298`),
  `contracts/workflows.py:22` enum, public SDK exports
  (`bifrost/__init__.py:129`, `sdk/__init__.py:23`), explicit `service`
  exclusion at every type gate (`tool_registry.py:98`, `agent_router.py:94`,
  `form_publication.py:39`, MCP workflow tool, endpoint execution), Monaco
  decorator recognition (`monaco-setup.ts:203`) + list-surface isolation
  (`WorkflowListSurface.tsx:74`), ORM export registration (`models/orm/__init__`,
  `models/_exports.py`, `alembic/env.py`), manifest `type` literal constraint.
- Slice 2 also covers: removal/deactivation ordering (fence + graceful stop
  before deactivate/hard-delete, with failure recovery), enable/disable REST+CLI
  as distinct operations from start/stop, autonomous identity policy.
- Slice 4 also covers: WebSocket channel authorization for `service:{id}`
  (`routers/websocket.py:954,1090` — allowlist has no service path today).

## 19. Slice 2 milestone review reconciliation (2026-09-20)

Codex audited the Slice 2 control plane (lifecycle domain, REST, migration,
registration wiring, subscription guard). Four findings changed the code:

1. Lease expiry is now enforced on every owner write, not just the sweep:
   `_locked_live_attempt` rejects expired leases, so a stale owner can never
   resurrect one by renewing/reporting/completing late.
2. Launch safety is defense in depth: claims JOIN the workflow row
   (`type='service'` + active), AND every removal path parks definitions
   (explicit registration, indexer type-change, both deactivation methods,
   file-delete soft-delete, orphan cleanup). The indexer also ensures
   definitions (covering git sync/deploy, not just registration) and pins
   `current_revision = sha256(file)` for Slice 3 launch comparison.
3. Crash accounting counts terminal time (`stopped_at`), so a long run that
   fails now counts now. Operator flap (stop/start cycles) can inflate the
   window — accepted, documented; manual restart always clears it.
4. Subscription guard covers REST (400 + 404 on missing), both MCP
   direct-ORM paths, and a processor safety net that fails offending
   deliveries loudly instead of executing services one-shot.
5. Control-plane stop/disable/restart/park take the row lock and skip
   terminal attempts (either commit order converges to stopped).
6. ORM keeps a single index mechanism (explicit `Index`, no `index=True`
   duplicates).

Deferred with reasons: durable autonomous identity + credential renewal are
Slice 3 design (no placeholder schema); Pydantic 422s match platform
convention; creation-time form/agent guards already exact-match safe.
