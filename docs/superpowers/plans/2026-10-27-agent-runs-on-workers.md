# Agent Runs on Workers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `agent.run` dispatches (autonomous runs, CodeMode, chat turns) execute on worker launchers in children forked from a lazily started agent template, instead of in the API process. Measured before and after with the benchmark.

**Architecture:** The API-side `ApiExecutor` (one per connected worker) claims `agent.run` and hands the claimed message to its launcher. The launcher forks a child from a warm **agent template** (a second `TemplateProcess` profile that preloads the agent runtime and keeps credentials). The child runs the existing `AgentRunConsumer.process_message` and exits. The template starts on first use or on a chat pre-warm and retires after an idle period. Summaries stay in the API.

**Tech Stack:** Python 3.14, asyncio, `multiprocessing` fork template (`services/execution/template_process.py`), SQLAlchemy, Redis pub/sub, the remote launcher protocol (`worker/remote/`).

**Spec:** `docs/superpowers/specs/2026-10-27-agent-runs-on-workers-design.md`

## Global Constraints

- No feature flag and no dual path: after the switch PR the API never claims `agent.run`.
- No migration.
- Worker idle memory (no agent activity) stays within ±5 MiB of the baseline (75–78 MiB per worker Job).
- No fixed per-run reservation: `AGENT_RUN_REQUEST_BYTES` is set from the measured per-child unique peak.
- The workflow template's credential scrub is unchanged; only the agent template keeps credentials.
- Summaries (`agent.summarize`, `agent.summarize_backfill`) stay in `ApiAgentExecutor`.
- Verification follows CLAUDE.md: `./test.sh quality api`, focused unit tests, the named E2E tests, then the benchmark.
- Never edit `scripts/bench/` while a benchmark run is in progress.

## Review Focus

1. A child that dies (OOM kill, `SIGKILL`, crash) must leave the run `failed` and wake its caller within seconds: the sync waiter, the chat stream and the run-update subscribers. → Tasks 4 and 6.
2. API restart or handoff while an agent child runs: the next owner adopts the child from inventory; the run completes once. → Task 5.
3. `release_unstarted_claims` re-queues a run whose child hasn't started; a second worker claims it. Exactly one execution happens (row lock + status guard). → Task 6.
4. Package install while agent children run: the template recycle doesn't kill live children. → Task 3.
5. Agent backlog with no worker free: the controller launches a Job for it. → Task 6.

---

### Task 1: Benchmark additions and re-baseline (PR 1, bench only)

**Files:** `scripts/bench/run.py`, `scripts/bench/incluster/driver.py`, `scripts/bench/report.py`, `scripts/bench/cluster.py`, `scripts/bench/profiles/baseline.json`, tests.

**Produces:**
- Classes `chat-first` (GET `/api/chat/model-profiles`, then POST `/api/chat/runs` `{"content": ...}` with no conversation; poll `GET /api/agent-runs/{run_id}`) and `chat-followup` (the same with the previous run's `conversation.id`). Records match the `run_class` record shape.
- `Cluster.process_private_mib(pod: str) -> list[dict]`: one `kubectl exec` that reads `Private_Clean + Private_Dirty` from every `/proc/<pid>/smaps_rollup` with its command line.
- A background sampler in `run.py` (1 s) over worker pods during each class. The class result gains `worker_children: {"peak_child_private_mib", "peak_children"}`, excluding the launcher and the workflow template.
- Report: a "Worker children" column per class.
- [ ] Tests: `parse_private_rollups(text) -> list[dict]` on a fixture; the report shows the new column.
- [ ] Run `baseline` with `fake` and `deepseek` on today's build: the "before" numbers for the chat classes and per-child memory. Append them to `docs/benchmarks/2026-10-27-baseline.md`.
- [ ] Commit.

### Task 2: Agent child entry (PR 2a, unwired)

**Files:** `api/src/jobs/agent_run_child.py`, `api/tests/unit/jobs/test_agent_run_child.py`

**Produces:** `async def run_agent_message(message: dict) -> None`: `init_db()`, `await AgentRunConsumer().process_message(message)`, `close_db()` in `finally`. Exceptions propagate (the caller maps them to an exit status).
- [ ] Test: with a fake consumer, `run_agent_message` initialises and always closes the database, and propagates a consumer error.
- [ ] Commit.

### Task 3: Agent template profile (PR 2a, unwired)

**Files:** `api/src/services/execution/template_process.py`, `api/tests/unit/services/execution/test_agent_template.py`

**Produces:**
- `TemplateProcess(profile: Literal["workflow", "agent"] = "workflow")`; `start()`, `fork(worker_id) -> (pid, send_queue, recv_queue)`, `shutdown()`, `is_alive()`, `collect_child_exit_statuses()` unchanged.
- The agent profile's main imports `src.jobs.consumers.agent_run`, `src.services.agent_executor`, `src.services.agent_runtime.tool_policy`, `pydantic_ai.models.openai`, `pydantic_ai.providers.openai`, and `src.main` (the REST bridge needs `src.main.app`). It skips the credential scrub and calls `gc.freeze()` immediately before sending `ready`. It opens no event loop, threads or connections before forking.
- An agent child reads one work item `{"message": dict}`, runs `asyncio.run(run_agent_message(message))`, sends `{"ok": True}` or `{"ok": False, "error_type", "error"}`, and exits.
- [ ] Tests: the agent template reaches ready; `gc.freeze` was called (the frozen count is > 0 in the template, read over the control pipe); a fork with a stub `run_agent_message` returns `ok` and the child exits; a raising stub returns `ok: False` with the error type; the workflow profile still scrubs credentials (existing tests pass).
- [ ] Commit.

### Task 4: Launcher actions and lifecycle (PR 2a, unwired)

**Files:** `api/src/worker/remote/launcher.py`, `api/tests/unit/dev/test_thin_launcher.py`

**Produces:**
- `self.agent_template: TemplateProcess | None`, guarded by `_template_lock`.
- Action `agent_prewarm` `{}`: start the agent template if absent.
- Action `agent_run` `{route_id, run_id, message, memory_request_bytes}`: memory-pressure check, start the template if needed, fork, send `{"message": message}`, and track a `Child(kind="agent_run", subject_id=run_id, sampler=...)`. Agent children are sampled by `monitor()` and eligible for `stop_largest_when_dangerous`.
- Exit reporting: `send_terminal({"event": "agent_exit", "route_id", "ok", "error_type", "error"}, inventory={"route_id", "kind": "agent_run", "subject_id": run_id, "memory_request_bytes"})`. A child killed without a result reports `ok: False, error_type: "ProcessCrashError"`.
- `inventory()` lists live agent children with `kind: "agent_run"`.
- Retirement: `monitor()` shuts the agent template down when no agent child has been alive for `BIFROST_AGENT_TEMPLATE_IDLE_SECONDS` (setting, default 300, tuned from the benchmark). `recycle` restarts the agent template only if it was running and never kills live agent children.
- [ ] Tests: prewarm starts the template once; `agent_run` forks and reports `agent_exit ok`; a crashing child reports `ProcessCrashError`; idle retirement stops the template and a later `agent_run` restarts it; recycle keeps live agent children; inventory includes agent routes; dangerous pressure can stop an agent child.
- [ ] Commit, then open PR 2a (infrastructure only; nothing sends these actions yet).

### Task 5: API routing and adoption (PR 2b, the switch)

**Files:** `api/src/worker/pool_main.py`, `api/src/worker/remote/host.py`, `api/src/worker/remote/remote_pool.py`, `api/src/worker/remote/agent_route.py` (new), `api/src/worker/api_agent.py`, tests in `api/tests/unit/worker/remote/`.

**Produces:**
- `PoolWorker._make_agent_consumer(self) -> AgentConsumer` (a protocol with `async process_message(body: dict) -> None`), defaulting to `AgentRunConsumer()`. `_start_executor` uses the seam.
- `RemoteAgentConsumer(bridge)` in `agent_route.py`: `process_message` sends `agent_run` with a new route ID and waits for that route's `agent_exit`. On `ok: False` it calls `announce_ended_agent_run(run_id, error)` (Task 6).
- `ApiExecutor`: handlers `(("workflow.execute", 1), ("agent.run", 1))`; overrides `_make_agent_consumer`.
- `ApiAgentExecutor`: `API_AGENT_HANDLERS = (("agent.summarize", 1), ("agent.summarize_backfill", 1))`.
- `ThinRemoteProcessPool.reconcile_inventory` accepts `kind == "agent_run"`. It adopts the route if `ExecutionWorkerClaim(worker_id=self, family="agent_run", subject_id=run_id)` exists and `AgentRun.status in ("queued", "running")`; otherwise it sends `terminate_job`. An adopted route's `agent_exit` resolves the waiter as above.
- Cancelling the API-side wait (drain or detach) does not kill the child.
- Static remote rows register `["workflow.execute", "agent.run"]` (`host.py:817`).
- [ ] Tests: an `ApiExecutor` claim of `agent.run` sends exactly one `agent_run` and completes on `agent_exit ok`; `ok: False` announces failure; reconcile adopts a live claimed run and rejects one whose run is finished; detach doesn't kill the child; `ApiAgentExecutor` no longer lists `agent.run`.
- [ ] Commit.

### Task 6: Recovery, scaling and memory request (PR 2b)

**Files:** `api/src/services/worker_pool/recovery.py`, `api/src/services/worker_pool/controller.py`, `api/src/services/work_dispatch.py`, `api/src/services/memory_requests.py`, `api/src/services/execution/agent_run_service.py` (new helper), tests.

**Produces:**
- `async def announce_ended_agent_run(run_id: UUID, error: str) -> None` in `agent_run_service.py`. It fails the run if it is `queued` or `running`; LPUSHes the sync result (`bifrost:agent_run:{run_id}:result`, 300 s TTL, the same JSON shape as `_publish_sync_result`), unconditionally because the `sync` flag isn't recoverable; publishes the chat terminal `error` event for chat runs; and publishes the agent-run update.
- `end_runs_of_lost_worker` returns the lost agent run IDs, and its callers announce them with that helper.
- Controller: waiting work counts unassigned `agent.run` dispatches; free capacity counts workers supporting `agent.run`; Job rows created by `_reserve` list `["workflow.execute", "agent.run"]`; `largest_budget` adds the launchable key for `agent.run`.
- `AGENT_RUN_REQUEST_BYTES`: set in Task 8 from the measurement; keep 32 MiB until then.
- [ ] Tests: lost agent runs are announced (sync key pushed, chat error published); a dispatch re-queued by `release_unstarted_claims` and claimed by a second worker runs once (row lock + status guard); agent backlog with no free worker plans a launch; Job rows advertise `agent.run`.
- [ ] Commit.

### Task 7: Chat pre-warm (PR 2b)

**Files:** `api/src/routers/chat.py`, `api/src/worker/remote/host.py`, tests.

**Produces:** `RemoteControlHost.prewarm_agents() -> None` sends `agent_prewarm` to every connected session, at most once per 30 s per API replica. `GET /api/chat/model-profiles`, which the chat page calls on mount (`client/src/hooks/useChatAvailability.ts`), schedules it without awaiting *(my call: an existing request the page already makes, so no client change)*.
- [ ] Tests: the route triggers one pre-warm per throttle window; failure to reach a launcher never fails the route.
- [ ] Commit.

### Task 8: Verify, measure, tune (PR 2b)

- [ ] `./test.sh quality api`; the unit tests from Tasks 2–7; E2E `tests/e2e/platform/test_agent_runtime_boundary.py`, `tests/e2e/platform/test_remote_runner_lifecycle.py`, `tests/e2e/platform/test_chat_runtime_boundary.py`. Add one E2E test to the agent boundary file: an agent run's `ExecutionWorkerClaim.worker_id` is a remote worker, not the `api-agent` row, and a chat run streams to completion.
- [ ] Benchmark `baseline` with `fake` and `deepseek`. Set `AGENT_RUN_REQUEST_BYTES` to the measured `peak_child_private_mib` plus 50%, and `BIFROST_AGENT_TEMPLATE_IDLE_SECONDS` from the observed gaps between agent runs. Rerun the benchmark.
- [ ] Check every acceptance row in spec section 5. Write `docs/benchmarks/<date>-agent-runs-on-workers.md` with before and after numbers.
- [ ] Independent review of the whole branch before the PR (two independent reviews for big changes, per standing feedback). Commit, then open PR 2b.
