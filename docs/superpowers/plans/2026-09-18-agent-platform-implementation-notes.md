# Agent Platform Implementation Notes

Evidence log for architectural contradictions and cross-cutting hazards found
during backend implementation. Both entries below were found during final
verification, root-caused, and fixed in-tree (see commits).

## 1. RabbitMQ publisher singleton deadlocks across pytest event loops (FIXED)

**Symptom.** `./test.sh all` hung forever in
`test_agent_workflow_tool_execution.py` after `test_agent_fanout_join.py`
passed. Coroutine-stack capture showed the workflow test stuck in
`enqueue_workflow_execution → publish_message → ensure_publish_topology →
aio_pika declare_exchange → connection.ready → Lock.wait`, with the
`workflow-executions` topology lock orphaned in the held state.

**Root cause.** `RabbitMQConnection` is a process-wide singleton whose pools
bind to the first asyncio loop that touches them, but pytest-asyncio runs
each test on a fresh function-scoped loop. The fan-out tests called
`notify_parent_of_completion` outside any publish mock, so a REAL
`publish_message("agent-runs")` built the singleton pools on that test's
loop. The next test's loop reused the stale pooled connection (whose
ready-future belongs to the dead loop) and blocked forever; the
per-queue topology lock stayed held, poisoning every later publish.

**Fix** (`5e4fd37e2`).
- `RabbitMQConnection.init_pools()` records the building loop and rebuilds
  (`reset_pools()`) when the running loop differs. Single-loop worker and
  scheduler processes never trigger the rebuild. Unit test:
  `test_init_pools_rebuilds_on_loop_change`.
- The five new durable e2e files that execute runs in-process gained a
  file-level autouse fixture mocking `src.jobs.rabbitmq.publish_message`,
  so queue nudges never reach the shared stack worker mid-suite.

**Residual risk.** The same singleton pattern exists for any other
loop-bound client held process-wide. `reset_db_state` (DB) and manual
`reset_pools` call sites (`test_builtin_events.py`,
`test_workflow_scheduling_flow.py`) are the established workarounds. Any
future e2e test that publishes in-process on a pytest loop must mock the
transport or reset the pools at the loop boundary.

## 2. Pydantic AI imports break the worker import-time closure (FIXED)

**Symptom.** `test_worker_app_closure_has_no_heavyweights` failed with
`{'mcp', 'uvicorn', 'starlette'}` after the runtime work landed.

**Root cause.** In pinned `pydantic-ai==2.35.3`, even a bare
`import pydantic_ai` eagerly pulls `mcp`/`uvicorn`/`starlette`. The durable
work added top-level `from src.services.agent_runtime import ...` imports to
the agent-run consumer; importing that package runs its `__init__`, which
re-exported Pydantic AI-backed helpers (`toolset`, `budgets`,
`observed_model`, …) eagerly — dragging the whole chain into the worker
entry closure.

**Fix** (`554d6a7ab`).
- `agent_runtime/__init__.py` now imports only loop-safe helpers eagerly
  (`errors`, `types`, `settings`) and resolves everything else through PEP
  562 `__getattr__`, with a `TYPE_CHECKING` mirror so pyright still sees
  real types (keeps `quality api` green).
- The consumer imports `resume` (checkpoint codec) lazily inside
  `process_message`, mirroring the existing executor pattern.

**Rule for future work.** No module reachable from `src.worker.app` at
import time may import `pydantic_ai` (or anything that does) at module
level. Defer such imports into function bodies, as the consumer already
does for `AutonomousAgentExecutor` and `AgentExecutor`.

## Pre-existing issues observed (NOT introduced, NOT fixed here)

- `test_agent_run_enqueue` fails in this environment because no LLM
  `primary` profile is configured (admission now resolves the model at
  enqueue, so it fails fast with 422). Verified identical on the stashed
  baseline.
- `./test.sh all` in a single process carries order pollution: pre-existing
  e2e files (e.g. `test_chat.py` creating `E2E Chat …` model profiles) leak
  DB state into later unit tests (`test_chat_model_profiles`,
  `test_ai_model_service`, `test_model_selection`, `test_media_generation`;
  reproduced without any new test files in the run). The separate
  `./test.sh unit` and `./test.sh e2e` lanes are green. `test_mcp_solution_managed`
  likewise passes in isolation and in the unit lane but fails in the mega-run;
  the polluter was not isolated — none of the new test files touch
  files/S3/solutions (verified by search).
