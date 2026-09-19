# Agent Evaluation Studio Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provide backend and CLI support for reproducible synthetic Agent evaluation, generated test cases, unpublished candidate snapshots, coherent simulated tools, and baseline-versus-candidate comparison.

**Architecture:** Suites/cases/candidates/results are durable feature records. A suite execution is one canonical PlatformJob that dispatches synthetic AgentRuns and waits without occupying its worker slot. Each case owns a versioned stateful simulator seeded from a frozen fixture; all tool calls in that case read and mutate the same state. Candidate snapshots overlay Agent configuration only inside test runs and cannot invoke real tools.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, Alembic, PlatformJob, durable AgentRun runtime, Pydantic AI, JSON Schema, Click CLI, pytest.

---

## Task 1: Model suites, cases, candidates, executions, and results

**Files:**
- Create: `api/src/models/orm/agent_evaluations.py`
- Modify: `api/src/models/orm/__init__.py`
- Create: `api/src/models/contracts/agent_evaluations.py`
- Create: `api/alembic/versions/20260918_agent_evaluation_studio.py`
- Create: `api/tests/unit/models/test_agent_evaluation_models.py`

- [ ] Add tenant-scoped `AgentEvaluationSuite` with target Agent, name/description, draft/published version, and timestamps.
- [ ] Add ordered `AgentEvaluationCase` with immutable versioned input, fixture, simulator policy, assertion list, tags, provenance (`manual`, `generated`, `historical_inspiration`), and enabled flag.
- [ ] Add immutable `AgentCandidateSnapshot` containing base Agent/version plus prompt/model/profile/tool/delegate/limit overlays and hashes. It references published tools but does not mutate their attachment to the Agent.
- [ ] Add `AgentEvaluationExecution` as a feature projection referencing its authoritative `platform_job_id`, suite/candidate versions, status/counters, and timestamps. Do not create a feature job/lease/retry system.
- [ ] Add per-case `AgentEvaluationResult` referencing baseline/candidate AgentRun IDs, assertion results, comparison, usage/cost/duration, simulator final-state hash, and error.
- [ ] Add per-case `AgentSimulationSession` and append-only synthetic tool records/state versions. Encrypt or redact fixtures using the same policy as durable job payloads.
- [ ] Constrain version uniqueness and active execution deduplication; index suite/execution/result lookups.
- [ ] Add additive migration and contract tests; confirm one Alembic head.
- [ ] Commit: `feat: add agent evaluation studio persistence`

## Task 2: Create candidate snapshots without publishing Agents

**Files:**
- Create: `api/src/services/agent_evaluations/candidates.py`
- Create: `api/tests/unit/services/agent_evaluations/test_candidates.py`
- Modify: durable AgentRun snapshot builder only through a documented test-run entry point

- [ ] Build a candidate from a readable base Agent plus validated overlays for prompt, model/profile, published tool IDs, delegated agents, and execution limits.
- [ ] Resolve and freeze the complete candidate snapshot and hashes at creation. Later Agent/tool edits do not change it.
- [ ] Enforce ordinary tenant and role permissions for every referenced object. Reject inactive/unpublished tools that the caller could not attach normally.
- [ ] Mark snapshots as evaluation-only. Production enqueue paths must reject them; only the Evaluation service may create a synthetic AgentRun from one.
- [ ] Test prompt-only, tool-only, model-only, combined overlays, cross-tenant denial, and no mutation of the base Agent.
- [ ] Commit: `feat: add ephemeral agent candidate snapshots`

## Task 3: Implement a coherent stateful tool simulator

**Files:**
- Create: `api/src/services/agent_evaluations/simulator.py`
- Create: `api/src/services/agent_evaluations/simulator_models.py`
- Create: `api/tests/unit/services/agent_evaluations/test_simulator.py`

- [ ] Define a versioned fixture containing entity collections, deterministic ID/time sources, allowed tool names, initial state, tool-specific rules, and optional historical examples.
- [ ] Execute calls against one locked per-case state. A synthetic `create_ticket` must affect later `get_ticket`, `list_tickets`, update, and delete calls; equivalent relationships apply to generic CRUD tools.
- [ ] Validate every call against the snapshotted published tool schema before simulation. Return schema-valid results or a controlled synthetic tool error.
- [ ] Implement generic state operations plus fixture rules; do not hardcode Halo-specific production behavior in the runtime core.
- [ ] Store call/result/state transition records and canonical hashes. Seeded time/IDs make reruns byte-stable except explicitly ignored fields.
- [ ] Historical tool results may inspire generated fixture values, but are copied only after tenant authorization and redaction. They are never replayed as real calls.
- [ ] The simulation dispatcher must have no import/path to real integration execution. Add a test that a malicious fixture/tool name cannot escape to a live tool function.
- [ ] Commit: `feat: add stateful synthetic tool simulator`

## Task 4: Add assertions and deterministic comparison

**Files:**
- Create: `api/src/services/agent_evaluations/assertions.py`
- Create: `api/src/services/agent_evaluations/comparison.py`
- Create: `api/tests/unit/services/agent_evaluations/test_assertions.py`

- [ ] Support assertions for terminal status, output JSON Schema/path/value, tool called/not-called/count/order/arguments, forbidden tool, final simulator state, delegation tree, max iterations/tokens/cost, and no real-tool execution.
- [ ] Each assertion produces stable code, pass/fail, expected/actual redacted values, and evidence journal sequence IDs.
- [ ] Comparison reports regressions, improvements, unchanged failures, tool-trajectory differences, output differences, and usage deltas. It must not turn subjective prose into a silent pass/fail gate.
- [ ] Add optional LLM-judge assertions only behind an explicit assertion type/model snapshot; label them nondeterministic and store rationale/usage. Core v1 suites work without a judge.
- [ ] Test malformed assertions fail at case save time and secrets are redacted in failure evidence.
- [ ] Commit: `feat: add agent evaluation assertions`

## Task 5: Run synthetic AgentRuns through the durable engine

**Files:**
- Create: `api/src/services/agent_evaluations/runner.py`
- Modify: `api/src/services/agent_runtime/execution_snapshot.py`
- Modify: `api/src/services/agent_runtime/toolset.py`
- Create: `api/tests/e2e/api/test_synthetic_agent_run.py`

- [ ] Add explicit execution mode `evaluation_synthetic`; default production mode remains unchanged.
- [ ] Synthetic runs use normal model loop, checkpointing, limits, output contracts, debugger journal, child runs, and terminal events, but dispatch every non-engine tool through the simulator.
- [ ] Store suite/case/candidate/execution IDs in bounded AgentRun correlation. Never tag an evaluation run as a production event trigger.
- [ ] Baseline and candidate start from independent copies of the exact same frozen fixture and input.
- [ ] Prohibit timers that sleep beyond a configurable synthetic maximum; simulate deterministic wake progression rather than wall-clock waiting.
- [ ] Test restart recovery, delegation, output contract failure, candidate-only tool attachment, and proof that zero real tool executors were called.
- [ ] Commit: `feat: run synthetic evaluations through agent runtime`

## Task 6: Orchestrate suites with PlatformJob

**Files:**
- Create: `api/src/jobs/platform/agent_evaluation.py`
- Modify: `api/src/jobs/platform/registry.py`
- Create: `api/src/services/agent_evaluations/executions.py`
- Modify: scheduler reconciliation registration following summary-backfill pattern
- Create: `api/tests/unit/jobs/platform/test_agent_evaluation.py`

- [ ] Define payload v1 containing execution ID only; durable feature rows carry the frozen suite/candidate references. Register `agent.evaluation_suite` with explicit timeout, retry, memory, cancellation, and concurrency policy.
- [ ] Enqueue baseline and candidate case runs with a configurable concurrency ceiling, report progress, then raise `PlatformJobDeferred` so the scheduler slot is released.
- [ ] Agent terminal events update case results idempotently and dispatch the next bounded batch. A reconciler closes event-before-wait and lost-notification races.
- [ ] When all results are terminal, compute assertions/comparison and finish the deferred PlatformJob through the shared service. Respect cancellation by cancelling only unfinished synthetic AgentRuns.
- [ ] Use PlatformJob shared status, notification, retry, and cancellation APIs. Do not add Studio polling/status transports.
- [ ] Test duplicate events, child completion before defer, runner loss, partial failure, cancellation, concurrency bounds, and deterministic rerun.
- [ ] Commit: `feat: orchestrate agent evaluation suites as platform jobs`

## Task 7: Add the Test Designer agent

**Files:**
- Create: `api/src/services/agent_evaluations/test_designer.py`
- Create: `api/src/services/agent_evaluations/prompts/test_designer.md`
- Create: `api/src/services/agent_evaluations/schemas/test_designer_output.json`
- Create: `api/tests/unit/services/agent_evaluations/test_test_designer.py`

- [ ] Run Test Designer as an evaluation-only ephemeral AgentRun snapshot so it receives normal tracing, budgeting, output enforcement, and debugger support without requiring a mutable system Agent row.
- [ ] Inputs are target Agent snapshot, selected authorized/redacted historical examples, published tool schemas, suite goal, and requested count. Historical content is inspiration, not a frozen replay.
- [ ] Enforce a caller-owned schema returning proposed cases with coherent initial state, inputs, simulator rules, and assertions. Validate tool chains against schemas and reject unresolved entity references.
- [ ] Generated cases remain drafts until explicitly accepted. Acceptance freezes a new case version; reruns never regenerate it.
- [ ] Add deduplication and coverage labels so the designer proposes materially distinct success, failure, safety, and edge cases.
- [ ] Test coherent create/get chains, unauthorized history omission, malformed repair, deterministic acceptance, and no automatic publication.
- [ ] Commit: `feat: add agent evaluation test designer`

## Task 8: Expose Studio REST APIs

**Files:**
- Create: `api/src/routers/agent_evaluations.py`
- Modify: API router registration
- Create: `api/tests/e2e/api/test_agent_evaluation_api.py`

- [ ] Add tenant-authorized CRUD/version endpoints for suites, cases, candidates, generated drafts, executions, and results.
- [ ] `POST /api/agent-evaluations/executions` returns `202 PlatformJobAccepted` with the shared `Location` header and active dedupe behavior.
- [ ] Add designer enqueue/accept endpoints; generation itself is asynchronous and observable through AgentRun/PlatformJob as appropriate.
- [ ] Result detail links baseline/candidate debugger run IDs and returns assertion/comparison summaries, not duplicated full journals.
- [ ] Use optimistic version checks on mutable drafts. Published/frozen versions are immutable.
- [ ] Test role/tenant boundaries, candidate isolation, historical provenance, dedupe, pagination, and shared job contract.
- [ ] Commit: `feat: expose agent evaluation studio api`

## Task 9: Add Studio CLI workflows

**Files:**
- Create: `api/bifrost/commands/agent_tests.py`
- Modify: CLI command registration
- Create: `api/tests/unit/bifrost/commands/test_agent_tests.py`

- [ ] Add commands for suite list/get/create, case import/export, candidate create, designer generate/accept, run, status, and result comparison.
- [ ] Support `@file.json`/`@file.yaml` for fixtures/assertions/candidate overlays using existing safe loaders. Resolve Agent/tool/delegate refs through `RefResolver`.
- [ ] `run --wait` polls the shared PlatformJob endpoint with a client-side deadline and clearly reports that server work continues if the wait expires.
- [ ] JSON output is automation-safe; human output summarizes regressions, failures, usage deltas, and linked AgentRun IDs.
- [ ] Test every request shape and failure exit code.
- [ ] Commit: `feat: add agent evaluation studio cli`

## Task 10: Studio security, scale, and final verification

**Files:**
- Create: `docs/architecture/agent-evaluation-studio.md`
- Modify: generated contract/truth artifacts as required
- Modify: implementation notes if any deviation remains

- [ ] Add quotas for fixture size, cases per suite, generations per request, active suites per tenant, fan-out, journal size, and synthetic wall time.
- [ ] Add retention/cascade behavior for drafts, immutable versions, simulator state, and AgentRuns. Never silently delete published fixtures or evidence.
- [ ] Document synthetic-only guarantees, provenance/redaction, candidate isolation, PlatformJob lifecycle, Test Designer review, assertion semantics, CLI flows, and the Astra UI handoff.
- [ ] Run all Studio/runtime/debugger focused tests, DTO/contract guards, migration-head check, `git diff --check`, and then `./test.sh api/tests`.
- [ ] Confirm searches show no authored frontend changes and no real tool dispatcher reachable from simulation mode.
- [ ] Commit: `docs: document agent evaluation studio backend`

