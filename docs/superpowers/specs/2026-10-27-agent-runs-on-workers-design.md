# Agent Runs on Workers — Design

Status: **Draft for Jack's review.** Phase 1b of the performance benchmark
program (`2026-10-27-performance-benchmark-suite-design.md`, section 13).

## 1. Why

Before #1066 (2026-10-25), worker executors claimed `agent.run`. #1066 moved
autonomous agent runs, their summaries and summary backfills into the API
process (`api/src/worker/api_agent.py`, `ApiAgentExecutor`). The goal was to
keep the agent runtime's imports out of every idle worker. The cost moved to
the API instead:

- **Production memory.** Since 2026-10-25 the API reaches its ~750–800 MiB
  ceiling in 1–4 hours instead of 12–16. Its memory grows in 25–80 MiB jumps,
  and 54% of jump minutes follow an agent-run start, against 8% of quiet
  minutes. The allocator never returns that high-water memory.
- **Throughput.** Every agent loop, CodeMode sandbox and tool result shares the
  API's one event loop and one CPU with the UI, SDK calls from every workflow,
  and worker control. In the Phase 1 baseline (fake model, one API at 1 CPU),
  an `agent-tools` run takes about 0.6 s on its own but about 4.4 s when ten
  run at once. `agent-codemode` takes about 0.6 s alone and about 5.2 s in a
  burst of ten.
- **Design intent (Jack).** Agents were meant to run on workers. Only simple
  AI requests belong in the API.

## 2. What we're doing

1. **Autonomous agent runs (`agent.run`, including CodeMode) execute on
   workers.** Each run executes in a **short-lived child process** that the
   worker launcher starts for the run and that exits when the run ends.
2. **Idle worker memory stays where it is** (75–78 MiB per worker Job in the
   baseline). The launcher never imports the agent runtime; only the child
   does, and only while it runs.
3. **Summaries stay in the API** (`agent.summarize`). They are single model
   calls, Jack's "simple AI request" exception. `agent.summarize_backfill`
   stays in the API unless the benchmark shows it is heavy.
4. **Proven by the benchmark:** the same Phase 1 profile, before and after.

## 3. What we're not doing

- No credential or trust redesign. The agent child gets its database, Redis
  and model access the same way platform-job children on workers do today.
  No new credential endpoint. Moving secrets out of workers, and running
  workers elsewhere (for example Cloudflare), is a later design.
- No change to interactive chat (`routers/chat.py`). It has always run in the
  API. The benchmark will show whether it should follow later.
- No topology change, no new feature flag, and no dual path. The API stops
  claiming `agent.run`, and workers start.
- No allocator tuning.

## 4. Design

### 4.1 Ownership

- `ApiAgentExecutor` handlers become `agent.summarize` and
  `agent.summarize_backfill` only.
- The per-worker `ApiExecutor` in `api/src/worker/remote/host.py` gains the
  `("agent.run", 1)` handler next to `workflow.execute`. Claims keep going
  through `work_dispatches`, owned by the API session for that worker, with
  the same database-time leases and generation fencing as today.

### 4.2 Execution: same shape as platform jobs

Platform jobs on workers already follow the pattern we want
(`launcher.py` action `platform_job`, which spawns
`python -m src.jobs.platform.runner <job_id> <lease_token>`, then reports
`job_exit` with inventory). Agent runs mirror it:

- **API side:** when the `ApiExecutor` claims an `agent.run` dispatch, it
  sends the launcher an `agent_run` request: route ID, dispatch ID, lease
  token and memory request. Routes are kept until inventory proves them absent,
  exactly like `platform_routes`, so a lost reply never causes a second attempt.
- **Launcher side:** the `agent_run` action applies the existing memory-pressure
  check, then spawns `python -m src.jobs.agent_run_child <dispatch_id>
  <lease_token>` in its own session (process group). On exit it reports
  `agent_exit` with inventory kind `agent_run`.
- **Child:** a new module, `src/jobs/agent_run_child.py`, initialises the
  database, loads the claimed dispatch's message, and runs the existing
  `AgentRunConsumer.process_message(body)`. It completes the dispatch under the
  lease it was given, then exits. No agent logic changes; the consumer that
  workers ran before #1066 runs again, now in a child.

### 4.3 Cancellation, loss and reconnect

- **Cancellation:** unchanged inside the child (the consumer's Redis cancel
  watcher). A `terminate_job`-style launcher action kills the child's process
  group when a cancel deadline passes or the owner fences the lease.
- **Worker loss:** unchanged. Lost-worker recovery
  (`services/worker_pool/recovery.py`) already marks a lost worker's agent runs
  failed rather than replaying them.
- **Reconnect and API restart:** inventory carries `agent_run` routes. The new
  API owner adopts live children the same way it adopts platform jobs, and
  replays unacknowledged `agent_exit` events.

### 4.4 Memory and concurrency

- A child costs about 1.9 s to import the agent runtime and about 170 MiB
  resident before the run's own growth (measured on the production image,
  2026-10-27). All of it is returned when the child exits.
- Agent dispatches carry a memory request; workers admit them through the
  existing pre-claim admission, so a 2 GiB worker runs as many concurrent
  agents as fit, and the API's Job controller scales workers for agent backlog
  as it does for workflows. The initial request is 256 MiB, adjusted from the
  benchmark's measured child peak.

### 4.5 Start-up cost

Each run pays the child start-up (about 2 s). That's small against real model
turns, which take seconds each, but it dominates fake-model timings. The
builder measures it first. If it matters, the one allowed optimisation is
**drain-then-exit**: a child that finishes a run takes the next agent run
already claimed for that worker, and exits as soon as none is waiting. It
never lingers idle, so memory is still released the moment work runs out.

## 5. Acceptance

Run `./test.sh bench run --profile baseline` with both backends, before
(the committed baseline) and after:

| Check | Pass condition |
|---|---|
| API memory | Settled API memory after the agent classes falls by at least the agent-runtime import cost (about 30 MiB in the fake-model baseline), and does not grow during agent bursts |
| Worker idle | Worker working set at idle stays within ±5 MiB of the baseline (75–78 MiB) |
| Release | After each agent class, worker working set returns to idle (no child left) |
| Agent latency | Sequential p50 rises by no more than the measured child start-up; burst p50 for `agent-tools` and `agent-codemode` improves |
| Workflow classes | No regression beyond run-to-run noise |
| Correctness | Every agent run completes; tool calls and CodeMode nested calls execute (the harness checks status; the existing E2E agent tests cover behaviour) |

## 6. Left to the builder

- Names of the launcher action, events and inventory kind (suggested:
  `agent_run`, `agent_exit`).
- How the child receives the dispatch: by ID with the lease, reading the
  message itself, following `platform.runner`.
- Test placement follows the existing remote-runner and thin-launcher tests
  (`tests/unit/dev/test_thin_launcher.py`,
  `tests/e2e/platform/test_remote_runner_lifecycle.py`,
  `tests/e2e/platform/test_agent_runtime_boundary.py`).
