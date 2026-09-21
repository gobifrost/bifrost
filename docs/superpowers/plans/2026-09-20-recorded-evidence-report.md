# A2 Recorded Evidence Report

Status: implemented for primary review.

## Changed Files

- `api/shared/agent_recorded_evidence.py`
- `api/tests/unit/services/agent_evaluations/test_recorded_evidence.py`
- `api/tests/e2e/api/test_agent_recorded_evidence.py`
- `docs/superpowers/plans/2026-09-20-recorded-evidence-report.md`

## Implementation Notes

`load_recorded_run_evidence(session, run_id, *, user)` now projects one authorized durable `AgentRun` tree into the A1 recorded-assertion shape without writes, external calls, synthetic-session lookup, or route/API changes.

The loader enforces:

- mandatory `UserPrincipal`;
- canonical `agent_run_visibility_conditions(user)` plus explicit tenant equality for non-superusers, including `organization_id=None` isolation;
- descendant-only traversal from the selected run through bounded parent links;
- bounded tree, journal, invocation, and usage collections, with overflow errors rather than truncation;
- generic withholding limitations for hidden descendants without leaking hidden IDs, counts, or content, including large hidden fanouts and nullable-tenant hidden children;
- no traversal through a withheld parent;
- durable terminal and completion-journal proof for output and call-history completeness;
- contiguous journal sequences from 1 for every included node;
- journal/invocation correspondence by run, provider call ID, tool identity, and arguments;
- deduplication of repeated observations of the same operation while preserving distinct same-name calls;
- engine-tool calls proven from journal only;
- in-flight or uncertain dispatch marked incomplete and never replayed;
- real-tool execution counts only from completed/failed dispatch ledger rows;
- argument completeness invalidated by stored redaction markers and by final sensitive-key redaction;
- explicit null output as recorded null only when durable completion proof exists;
- bounded delegation rendering with cycle detection that invalidates tree-derived completeness;
- sequence-0 model usage excluded from observed usage rows;
- nonnegative run iteration counters required before reporting `usage.iterations` complete.

## Conservative Unsupported Dimensions

`usage.tokens` and `usage.cost_usd` remain incomplete even when valid `AIUsage` rows are present. The rows are returned as observational amounts, but the current runtime contract does not prove every model call has a persisted usage row, and usage persistence can fail independently of run completion. This means budget assertions over token or cost evidence fail closed until a later runtime recording contract can provide authoritative per-call coverage.

`simulator_state` remains absent. Cross-run `tool_order` is incomplete for multi-run projections because parallel children have no durable global causal order.

## Verification

- `./test.sh tests/unit/services/agent_evaluations/test_recorded_evidence.py tests/e2e/api/test_agent_recorded_evidence.py -v` -> 42 passed.
- `./test.sh tests/unit/services/agent_evaluations/test_recorded_outcomes.py tests/unit/services/agent_evaluations/test_assertions.py -v` -> 64 passed.
- `./test.sh quality api` -> pyright 0 errors/warnings/informations; Ruff passed.

Broader backend, client, Playwright, API contract, CLI, and manifest suites were not run for this bounded shared-service packet.
