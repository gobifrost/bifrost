# Agent Runs on Workers — Design

Status: **Draft for Jack's review.** Phase 1b of the performance benchmark
program (`2026-10-27-performance-benchmark-suite-design.md`, section 13).
Revised after a builder review against the code. Calls marked *(my call)* are
for Jack to check.

## 1. Why

Before #1066 (2026-10-25), worker executors claimed `agent.run`. #1066 moved
autonomous agent runs, **chat turns** (also `agent.run` dispatches, with
`trigger_type="chat"`, `services/chat_runs.py:316`), their summaries and summary
backfills into the API process (`api/src/worker/api_agent.py`). The goal was to
keep the agent runtime out of every idle worker. The cost moved to the API:

- **Production memory.** Since 2026-10-25 the API reaches its ~750–800 MiB
  ceiling in 1–4 hours instead of 12–16. It grows in 25–80 MiB jumps, and 54%
  of jump minutes follow an agent-run start, against 8% of quiet minutes. The
  allocator never returns that high-water memory.
- **Throughput.** Every agent loop, CodeMode sandbox and tool result shares the
  API's one event loop and one CPU with the UI, SDK calls from every workflow,
  and worker control. In the Phase 1 baseline (fake model, one API at 1 CPU),
  an `agent-tools` run takes about 0.6 s alone but about 4.4 s when ten run at
  once. `agent-codemode` goes from 0.6 s to about 5.2 s.
- **Design intent (Jack).** Agents were meant to run on workers. Only simple
  AI requests belong in the API.

## 2. What we're doing

1. **`agent.run` executes on workers again: autonomous runs, CodeMode and chat
   turns.** Each run executes in a **child forked from a warm agent template**
   on the worker, and the child exits when the run ends (Jack, 2026-10-27).
2. **The agent template exists only while agents are active.** It starts on
   the first agent dispatch (or when a user opens a chat) and retires after a
   quiet period. With no recent agent activity, worker idle memory stays where
   it is (75–78 MiB per worker Job in the baseline).
3. **Each run costs only its own memory.** Forked children share the template's
   imports copy-on-write. A run's footprint is what it allocates and touches,
   measured, not a fixed 128 or 256 MiB reservation (Jack: "I don't want to eat
   128 MB for every agent thing that runs").
4. **Summaries stay in the API** (`agent.summarize`), Jack's "simple AI
   request" exception. `agent.summarize_backfill` stays unless the benchmark
   shows it is heavy.
5. **Proven by the benchmark:** the same Phase 1 profile, before and after.

## 3. What we're not doing

- No credential or trust redesign. The child gets database, Redis and model
  access the same way platform-job children on workers do today. No new
  credential endpoint.
- No topology change, no feature flag and no dual path. The API stops claiming
  `agent.run`, and workers start.
- No allocator tuning and no database migration.

## 4. Design

### 4.1 Ownership and claiming

- `ApiAgentExecutor` handlers become `agent.summarize` and
  `agent.summarize_backfill`.
- The per-worker `ApiExecutor` (`worker/remote/host.py`) adds
  `("agent.run", 1)`. `claim_next` also requires the worker row's
  `supported_handlers` to include it (`services/worker_pool/claims.py:174-200`).
  Both places that create those rows add `agent.run`: Job rows
  (`services/worker_pool/controller.py:560`) and static remote rows
  (`worker/remote/host.py:817`).
- The fence stays as it is today: the API owner's `execution_workers` lease
  around `claim_next` (`worker/remote/fencing.py`). Claiming sets
  `assigned_worker_id` and inserts the `ExecutionWorkerClaim`.

### 4.2 Execution

- **API side:** for a claimed `agent.run`, the `ApiExecutor` sends its launcher
  an `agent_run` request with a route ID, the run ID and the memory request.
  Routes are kept until inventory proves them absent, so a lost reply never
  causes a second launch.
- **Launcher side:** the `agent_run` action applies the memory-pressure check,
  ensures the agent template is running, forks a child from it for the run,
  and reports `agent_exit` (return code) with inventory kind `agent_run`. Unlike platform-job processes, agent children are **memory-sampled
  and eligible for the dangerous-pressure kill**, like workflow children
  (`launcher.py:526-576`) *(my call)*.
- **Agent template:** a second template process next to the launcher's existing
  workflow template (`services/execution/template_process.py`), built the same
  way: credentials handled as the workflow template does today, the agent
  runtime imported (`jobs/consumers/agent_run.py`, Pydantic AI, providers,
  CodeMode, and the REST bridge's `src.main.app` if a run can reach it), no
  event loop, threads or connections open at fork time, and `gc.freeze()`
  called just before it starts accepting forks so the collector doesn't copy
  shared pages into every child.
- **Template lifecycle:** started lazily by the first `agent_run` request, or by
  a pre-warm request when a user opens a chat. It retires after
  `agent_template_idle_seconds` with no agent child alive (initial value set
  from the benchmark), and an active conversation keeps it warm. Package
  installs recycle it with the workflow template.
- **Child:** forked from the template. It initialises its database and Redis
  connections after the fork and runs the existing `AgentRunConsumer.process_message(body)` for that run. As
  today, the consumer deletes the dispatch at the start, in the transaction that
  locks the `AgentRun` row (`jobs/consumers/agent_run.py:205-221`). The run's
  outcome is the `AgentRun.status` update, which applies only while the run is
  `running`. That guard is what makes a duplicate delivery harmless. No agent
  logic changes; the consumer workers ran before #1066 runs again, in a child.
- **Callers stay connected:** sync results (Redis list push), cancellation
  (Redis key poll), summaries (`stage_summary` + Redis publish) and UI updates
  already go through Redis. One gap: `core/pubsub.py` falls back to local
  sockets when Redis publishing isn't available. In a child that would silently
  drop UI updates, so the child must publish through Redis or fail loudly
  *(my call)*.
- **In-process REST bridge:** thin-wrapper system tools call REST through an
  in-process transport that imports `src.main.app`
  (`services/mcp_server/tools/_http_bridge.py`). The child must support that
  path, and its memory must be measured with it loaded (see 4.4).

### 4.3 Failure, cancellation and handoff *(my calls)*

- **Abnormal child exit** (non-zero `agent_exit`, OOM, kill): the API side fails
  the run immediately if it is still `running`. It wakes sync callers and chat
  streams the same way a normal failure does, instead of leaving them to the
  stale-run sweep (`jobs/schedulers/execution_cleanup.py`).
- **Cancellation:** unchanged inside the child. A launcher `terminate`
  action kills the child's process group when a cancel deadline passes.
- **Lost worker:** existing recovery fails the lost worker's agent runs
  (`services/worker_pool/recovery.py`). It must also wake their callers, as
  above, which it doesn't today for agents.
- **API drain or handoff:** children are **not** killed when the API-side wait
  is cancelled. They are adopted by the next owner from inventory, like
  workflow children. Adoption is validated against the existing
  `ExecutionWorkerClaim(worker, "agent_run", run_id)` plus
  `AgentRun.status == "running"`; no new lease column.
- **Re-queue race:** `release_unstarted_claims` can re-queue a dispatch whose
  child hasn't yet started (`recovery.py:167-183`), so two workers could launch
  the same run. The `AgentRun` row lock and the status guard keep it from
  executing twice. A test pins this.

### 4.4 Memory, capacity and scaling

- **Template cost:** about 170 MiB resident and 1.9 s to build (measured on
  the production image, excluding `src.main.app`), paid once per worker while
  agents are active, and released when the template retires.
- **Per-run cost:** the forked child's **unique** memory (its private, dirtied
  pages, measured from `smaps_rollup`), not its resident size, which counts the
  shared template pages. The benchmark records each class's peak child unique
  memory.
- **Memory request:** agent dispatches request 32 MiB today
  (`services/memory_requests.py:31`). It becomes the measured per-run unique
  peak plus margin, so admission reserves only what a run really adds.
- **Scaling:** the Job controller today counts only waiting `workflow.execute`
  dispatches, and capacity only on workers that support it
  (`services/worker_pool/controller.py:337-370`, `services/work_dispatch.py:219-241`).
  It must count `agent.run` backlog and capacity too, so agent work launches
  workers like workflow work does. With the warm-worker policy at its default
  (1), agents always have somewhere to start.

### 4.5 Start-up cost and chat

A fork from a warm template starts in milliseconds. Only the first agent run
after a quiet period pays template build (about 2 s). For chat, opening the chat
sends a pre-warm so the first message normally finds the template ready. Fresh
per-run children (2 s on every chat message) were rejected by Jack as too slow.

## 5. Acceptance

Run `./test.sh bench run --profile baseline` with both backends, before
(the committed baseline) and after:

| Check | Pass condition |
|---|---|
| API memory | Settled API memory after the agent classes falls by at least the agent-runtime import cost (about 30 MiB in the fake-model baseline), and does not grow during agent bursts |
| Worker idle | After the template retires, worker working set stays within ±5 MiB of the baseline (75–78 MiB) |
| Release | No agent child outlives its run; the template retires after its idle period |
| Per-run memory | Peak unique memory per agent child is reported per class. Target: tens of MiB for the bench classes, nowhere near a 128 MiB fixed cost. A higher number is a finding to explain before merge |
| Agent latency | With the template warm, sequential p50 for every agent class is no worse than the baseline; burst p50 for `agent-tools` and `agent-codemode` improves; a cold start costs no more than the template build |
| Chat latency | New class `agent-chat-session`: first message with the template pre-warmed, and a follow-up message, are each no slower than the baseline `agent-chat` p50 plus 100 ms |
| Workflow classes | No regression beyond run-to-run noise |
| Correctness | Every agent run completes; tools and CodeMode nested calls execute; the abnormal-exit, handoff-adoption and re-queue-race tests pass; existing agent and chat E2E tests pass |

## 6. Decisions (Jack, 2026-10-27)

- Chat moves with agent runs, using the forked-template model. Fresh per-run
  children were too slow; keeping chat in the API was not chosen.
- Fork from a warm template is the preferred model "for most jobs", as long as
  RAM doesn't grow much. Applying it to platform jobs is a later change.
- No fixed 128 MiB-per-run cost: per-run memory is measured and admitted at
  its measured size.

## 7. Left to the builder

- Names of the launcher action, events and inventory kind (suggested:
  `agent_run`, `agent_exit`, `agent_prewarm`).
- The benchmark additions this needs: per-class peak child unique memory on
  workers, and the `agent-chat-session` class.
- Test placement follows the existing remote-runner, thin-launcher and agent
  boundary tests (`tests/unit/dev/test_thin_launcher.py`,
  `tests/e2e/platform/test_remote_runner_lifecycle.py`,
  `tests/e2e/platform/test_agent_runtime_boundary.py`).
