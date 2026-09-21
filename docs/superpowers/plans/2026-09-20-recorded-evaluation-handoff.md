> Primary review: this is an audit proposal. Where it conflicts, `2026-09-20-recorded-core-approved-handoff.md` governs. Simulator-state absence is insufficient evidence; proven failures remain visible alongside incompleteness; PlatformJob completion is separate from verdict. Core business logic goes in shared. Backend admission/DTO/job design is not yet accepted.

# Phase 2 Handoff — Recorded-Run Evaluation, First Implementation Packet(s)

Bounded internal handoff. Primary reviews contracts (`2026-09-20-workbench-contracts.md`)
then authorizes code writes. No code in this packet; no commits/push/install/credentials.
Product judgments in `2026-09-20-unified-agent-quality-workbench.md` are binding.

## Packet split decision

Deliver as TWO slices (core correctness first; endpoint/job/CLI second) unless the
implementer shows both fit one bounded review. Slice A is independently useful
(pure evaluation semantics + tripwires, no transport). Slice B wires admission/results
through the shared PlatformJob + CLI.

- **Slice A — recorded-evidence core + aggregation (no routes/jobs/CLI).**
- **Slice B — admission/results API + `agent.evaluation_recorded` job + CLI + MCP thin wrappers.**

Slice B starts only after Slice A is accepted. Do not silently add a job system:
Slice B extends the shared registry/transports per platform-jobs.md steps 1–9.

---

## Slice A — recorded-evidence core (allowed files + tests)

Allowed files (exact):
- NEW `api/src/services/agent_evaluations/recorded_evidence.py` — `load_recorded_run_evidence(session, run_id)`, `RecordedEvidenceError`, bounds constants (mirror `evidence.py` bounds; new names).
- NEW `api/src/services/agent_evaluations/recorded_outcomes.py` — per-assertion recorded evaluator (reuse `validate_assertions`; new completeness/applicability wrapper), `aggregate_recorded_pair()`, `aggregate_recorded_evaluation()` per contracts §3.1–§3.2.
- TOUCH `api/src/services/agent_evaluations/quotas.py` — additive recorded-evidence bound constants ONLY (no behavior change to existing checks).
- NEW tests: `api/tests/unit/services/agent_evaluations/test_recorded_evidence.py`, `test_recorded_outcomes.py`.

Behavioral gates (must hold; each has a named test):
1. Incomplete tool trace cannot pass `tool_not_called`/`forbidden_tool` (gap → `insufficient_evidence`).
2. `simulator_state` on recorded evidence → `not_applicable`, never evaluated against output.
3. `llm_judge` unresolved → `pending_judge`, never `passed`; aggregation treats pending as non-terminal.
4. Unknown applicability → `insufficient_evidence`; known-false → `not_applicable`; neither passes nor implicitly excludes.
5. Deleted/missing run → per-pair `error run_deleted`; oversized → `error evidence_oversized`; no truncation, no invented fixtures (`simulator_state` absent, `real_tool_executions` counted from durable invocations, never forced 0).
6. `AIUsage` sequence-0 rows excluded; usage-missing budget checks → `insufficient_evidence`.
7. Hidden-child omission → affected assertions `insufficient_evidence` (admitted-IDs-only fixtures in tests).
8. Aggregation order: error > insufficient/pending > all-N/A > failed > passed; roll-up preserves every pair; all-inapplicable sets `all_inapplicable: true`.

Representative schemas (Slice A implements the outcome half; DTOs land in Slice B but shapes are frozen here):
```python
RecordedAssertionOutcome = {code, type, label?, outcome: "passed|failed|not_applicable|insufficient_evidence|pending_judge|error",
    expected, actual, evidence_refs: [{run_id, sequence?, kind?}], detail?, judge_usage?}
RecordedPairResult = {test_id, test_version, run_id, outcome: "passed|failed|not_applicable|insufficient_evidence|error",
    assertion_outcomes: [RecordedAssertionOutcome], evidence_ref, error?}
RecordedRollup = {total, passed, failed, not_applicable, insufficient_evidence, error, pending, all_inapplicable: bool}
```

Commands (stack must be up; state auto-resets):
```bash
./test.sh tests/unit/services/agent_evaluations/test_recorded_evidence.py tests/unit/services/agent_evaluations/test_recorded_outcomes.py -v
./test.sh tests/unit/services/agent_evaluations/ -v
./test.sh quality api
```
Broader suites NOT run in-slice: e2e, client, pre-pr (merge queue is the broad gate).

## Slice B — admission/results + job + CLI (allowed files + tests)

Allowed files (exact):
- NEW `api/src/models/orm/agent_recorded_evaluations.py` + `alembic` migration (NEW tables `recorded_evaluations`, `recorded_evaluation_results`; no edits to existing tables/enums).
- TOUCH `api/src/models/orm/__init__.py` (register new models only).
- TOUCH `api/src/models/contracts/agent_evaluations.py` (ADDITIVE new DTOs only: `RecordedEvaluationCreate/Public`, `RecordedEvaluationResultPublic`, `RecordedOutcome`; no edits to existing classes).
- NEW `api/src/routers/agent_recorded_evaluations.py` + TOUCH `api/src/routers/__init__.py` + TOUCH `api/src/main.py` (router registration only): `POST /api/agent-evaluations/recorded-evaluations` (202 + `Location` + `X-Recorded-Evaluation-Id`), `GET /.../recorded-evaluations/{id}`, `GET /.../recorded-evaluations/{id}/results`, `POST /.../recorded-evaluations/{id}/cancel`.
- NEW `api/src/jobs/platform/agent_evaluation_recorded.py` + TOUCH `api/src/jobs/platform/registry.py` (one registry line). Payload v1 `{recorded_evaluation_id}`; policy proposal: timeout 15min, attempts 2, retry_on_runner_loss True, headroom 64MB, cancellable True (scheduler confirms).
- TOUCH `api/bifrost/commands/agent_tests.py` (ADDITIVE commands only: `evaluate`, `recorded-status`, `recorded-results`, `recorded-cancel`; existing commands untouched) + generated skill appendix refresh via `python api/scripts/skill-truth/generate.py` if CLI help changes.
- NEW thin MCP wrappers (only if MCP parity is requested in-slice; else deferred with names reserved) + extend `PARITY_HANDLERS` in `tests/unit/test_mcp_thin_wrapper.py`.
- NEW/TOUCH tests: `api/tests/unit/services/agent_evaluations/test_recorded_admission.py`, `api/tests/e2e/api/test_agent_recorded_evaluation_api.py`, TOUCH `api/tests/unit/bifrost/commands/test_agent_tests.py` (additive cases), refresh `EXPECTED_CONTRACT_FINGERPRINT` in `api/tests/unit/test_contract_version.py` (additive → no MIN_CLI_VERSION bump; reviewer confirms), `api/tests/unit/test_dto_flags.py` disposition (new DTO fields exposed as flags or added to `DTO_EXCLUDES` with reason).

Admission/results semantics (from contracts §2.1 + §3.5): tenant + `_authorized_agent` + per-run `agent_run_visibility_conditions` + same-agent + admitted-IDs-only + dedupe; frozen test/run/evidence/judge refs at admission; dedupe key `recorded:{agent_id}:{tests_hash}:{runs_hash}:{judge_hash}`; reused active evaluation returned (never re-admit); `Location` header; shared cancel; terminal/cancelled survive restart via reconciler-adjacent finish (reuse existing reconciliation loop — NO new scheduler callback; recorded finish rides the shared deferred-finish path).

CLI contract (exact spellings per contracts §2.5; `--wait` via `poll_platform_job`; exit map §2.5 incl. all-inapplicable → exit 1 with `{"all_inapplicable": true}`):
```text
bifrost agent-tests evaluate --agent AGENT --tests TEST[,TEST...]|all --runs RUN[,RUN...] [--judge exact|semantic] [--wait] [--json]
bifrost agent-tests recorded-status EVALUATION_ID [--json]
bifrost agent-tests recorded-results EVALUATION_ID [--json]
bifrost agent-tests recorded-cancel EVALUATION_ID
```
Live happy-path acceptance: CLI evaluates selected existing runs, returns documented exit status, no real tool/agent call occurs (assert via invocation-count fixtures), cross-tenant run IDs denied (404), duplicate runs evaluated once, missing/deleted evidence reported per-pair, exact vs semantic outcomes differ correctly, existing simulation results unchanged (run existing eval suites untouched — assert no synthetic rows created).

Commands:
```bash
./test.sh tests/unit/services/agent_evaluations/test_recorded_admission.py -v
./test.sh tests/e2e/api/test_agent_recorded_evaluation_api.py -v
./test.sh tests/unit/bifrost/commands/test_agent_tests.py tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v
./test.sh quality api
```
Regenerate types after DTO changes against this worktree's debug stack (`./debug.sh status`, `cd client && npm run generate:types`) — note: no UI in Phase 2, so type regen is contract hygiene only.

## True unresolved choices (Primary decides; implementer does not guess)

1. Test-version source for `test_refs`: recommended reuse of accepted `AgentEvaluationCase` versions (frozen `case_definitions` pattern). Alternative (new test-version store) deferred to Phase 4.
2. Default-collection pointer (§2.3): `agent_id` filter + default-suite resolution rule.
3. Final CLI spellings (§2.5) and job policy numbers (§4).
4. Whether the thin MCP wrappers land in Slice B or ride Phase 3.

## Stop condition

Report completion with: files written (these two docs only), exact symbols inventoried, compatibility statement (additive; existing simulation/finding/CLI untouched), unresolved choices listed above, and await Primary review. No code writes until authorized. No user notifications (internal handoff).
