# Idle Database Churn — Plan

Status: **Draft for Jack's review.**

## Problem

An idle tenant — one API, one connected worker, nothing running, nobody
signed in — performs about **31 PostgreSQL transactions per second**
(benchmark Kind stack, 2026-10-27, `pg_stat_database` / `pg_stat_user_tables`
deltas over 45 s). For hosting, that is a per-tenant cost paid around the
clock in Postgres CPU, WAL and connection churn, before a single workflow
runs. Measured idle rates:

| Table | Idle rate | Main source |
|---|---:|---|
| `execution_workers` | ~12 scans/s, ~0.8 updates/s | Claim polling's correlated subqueries (1 s, per executor), fence on every WebSocket frame (1/s per worker), heartbeats (5 s), lease renewal (10 s), dispatch reconcile (5 s), Kubernetes controller (2 s) |
| `work_dispatches` | ~3.6 scans/s | Claim polling (1 s, per executor) and dispatch reconcile (5 s) |
| `platform_jobs` | ~3.5 scans/s | Platform-job polling (2 s): expired-lease recovery plus claim, per worker |
| `scheduler_task_states` | ~2.1 upserts/s | After **every** scheduled run, all 23 task rows are re-upserted; ~5.5 runs/min |
| `system_configs` | ~1.4 scans/s | `load_kubernetes_policy` re-read inside claim and reconcile loops |
| (all) | — | `pool_pre_ping=True` adds a round trip per connection checkout (effect unmeasured) |

None of this is needed to stay correct. The platform already has a Redis
wake-up for new dispatches (`bifrost:pool:wake`), but claim loops still poll
every second regardless.

## Rule

**Wake on events; sweep slowly for safety.** Every loop that looks for work
waits on a wake-up signal. A slow sweep catches anything a lost wake-up
missed, because Redis pub/sub doesn't guarantee delivery. Liveness writes
happen once per process per interval, not once per loop. Nothing reads
settings or ownership from the database when an in-memory copy is current.

## Target

| Measure | Today | Target |
|---|---:|---:|
| Idle transactions/s, one API + one worker | ~31 | **≤ 3** |
| `wf-noop` queue wait p50, idle stack | 25–45 ms | No worse |
| Worker-loss detection | Heartbeat-driven | Unchanged |
| Work started when a wake-up is lost | n/a | Within the sweep interval (proposed 15 s) |

## Changes

1. **Claim loops wait on the existing wake-up** (`worker/pool_main.py:_claim_loop`).
   They block on `bifrost:pool:wake` instead of polling every 1 s, with a 15 s
   safety sweep. Every path that makes work claimable already publishes the
   wake (`hand_off`, `wake_pool_workers`). Add it where it's missing: release
   of unstarted claims and freed admission capacity.
2. **Platform jobs get the same wake-up** (`services/platform_jobs.py:enqueue_platform_job`
   publishes it; `_platform_job_loop` waits on it). Expired-lease recovery
   moves from every 2 s to the 15 s sweep. A queued job that's blocked on
   concurrency or a resource lock stops re-reading the policy and recounting on
   every tick: it's re-evaluated when a job of that type or lock finishes
   (wake) or on the sweep.
3. **No database fence per WebSocket frame** (`worker/remote/host.py:905`).
   The session already knows its lease expiry, renewed every 10 s. Check it in
   memory for telemetry frames, and keep the database fence for
   state-changing frames (results, claims, terminal events), as today.
4. **One liveness write per worker per interval.** Today the remote executor
   writes its heartbeat (5 s), its owner-lease renewal (10 s) and a diagnostics
   heartbeat (10 s), and the API's own executors add more. Fold these into one
   update per worker every 10 s, and one diagnostics row per process. The
   lost-worker threshold is unchanged, because it is defined in seconds, not
   in writes.
5. **Scheduled-task bookkeeping writes only what changed**
   (`scheduler/control.py:publish_task_states`). It upserts the task that just
   ran, not all 23 rows.
6. **Reconcilers run when there's something to reconcile.**
   - Dispatch reconcile (5 s) and the Kubernetes worker controller (2 s) run on
     wake-ups (new dispatch, worker ended, capacity changed), plus a 15 s sweep.
   - A pass with no unassigned dispatch and no worker change does a single
     existence query and stops.
   - `largest_budget` is computed once per pass instead of once per handler.
7. **Settings are cached and invalidated by event.** `load_kubernetes_policy`
   and the other settings that loops read (memory policy, AI behaviour) are
   held in memory and refreshed when Settings publishes a change. The 15 s
   sweep re-reads them as a safety net.
8. **Measure `pool_pre_ping` before changing it.** If it's a large share of
   idle traffic, replace it with `pool_recycle` plus a retry on stale-connection
   errors. This is a reliability trade-off, so it comes back to Jack with
   numbers before any change.

Indexes aren't part of this. The idle tables are tiny, so their scans are the
planner's correct choice; the cost is how often they run, not how they run.

## Measurement (benchmark addition first)

Before any change, the benchmark gains an **idle phase** metric:
transactions/s and per-table operations/s from `pg_stat_*` counters, and API
and worker CPU millicores, over a fixed idle window. It also gains per-class
**transactions per run**. Each change above is landed with its before and
after numbers. The acceptance run is `./test.sh bench run --profile baseline`
with the targets above.

## Order

1. Benchmark: idle-phase metric and transactions per run (bench-only PR).
2. Changes 5 and 3: small and independent, together about 3 transactions/s.
3. Changes 1 and 2: wait on wake-ups. The bulk of the saving.
4. Changes 4, 6 and 7: liveness consolidation, reconcilers, cached settings.
5. Change 8: measure, then decide with Jack.

Each step is its own PR with focused tests: no lost work when a wake-up is
dropped (the sweep picks it up), no change to lost-worker detection, and no
duplicate claims.
