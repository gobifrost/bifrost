> Primary review: this is an audit proposal. Where it conflicts, `2026-09-20-recorded-core-approved-handoff.md` governs. Simulator-state absence is insufficient evidence; proven failures remain visible alongside incompleteness; PlatformJob completion is separate from verdict. Core business logic goes in shared. Backend admission/DTO/job design is not yet accepted.

# Workbench Contracts — Phase 1 Readiness (Reviewed-Ready Design)

Scope: bounded READ-ONLY audit for `2026-09-20-unified-agent-quality-workbench.md` Phase 1.
Worktree: `/home/jack/GitHub/bifrost/.worktrees/durable-agent-platform-backend`.
Writes authorized ONLY to this file and `2026-09-20-recorded-evaluation-handoff.md`.
No product/prototype edits. Dirty worktree changes (durable agent platform feature) preserved and untouched.

Product judgments in the main plan are binding. This document resolves only routine
implementation details with the smallest compatible proposal and marks true choices for Primary.

Read: `AGENTS.md` (§ Long-running platform jobs, CLI/MCP/manifest sync, testing),
`docs/architecture/platform-jobs.md`.

---

## 1. Exact existing symbols (verified by reading source)

### 1.1 Routers (existing)

`api/src/routers/agent_evaluations.py` — prefix `/api/agent-evaluations`, tag `agent-evaluations`:

| Symbol | Method/route | Notes |
|---|---|---|
| `create_suite` | `POST /suites` → `EvaluationSuitePublic` | body `EvaluationSuiteCreate`; org via `_org_id_for`; baseline agent via `_authorized_agent` |
| `list_suites` | `GET /suites?status&limit&offset` | tenant-scoped unless superuser; **no `agent_id` filter today** |
| `get_suite` | `GET /suites/{suite_id}` | `_scope_check` (tenant) |
| `update_suite` | `PUT /suites/{suite_id}` | 409 on published; optimistic `expected_version` |
| `publish_suite` | `POST /suites/{suite_id}/publish` | idempotent; draft→published |
| `create_case` | `POST /suites/{suite_id}/cases` | locks suite; quota + `validate_fixture` + `freeze_semantic_judges`; 409 on published; finding link via `_finding_id_or_422` |
| `list_cases` | `GET /suites/{suite_id}/cases?limit&offset` | ordered by position,name |
| `update_case` | `PUT /suites/{suite_id}/cases/{case_id}` | 409 if suite published **or** `case.accepted`; only draft-suite unaccepted cases editable |
| `create_candidate_endpoint` | `POST /candidates` | full baseline/tool/delegate/profile authorization; `create_candidate()`; 422 `CandidateError` |
| `get_candidate` | `GET /candidates/{candidate_id}` | tenant-scoped |
| `designer_drafts` | `POST /suites/{suite_id}/designer/drafts` | body `DesignerDraftRequest`; dedupes `historical_run_ids`; visibility via `agent_run_visibility_conditions` + `AgentRun.org_id == suite.org_id`; testing assignment `testing` (`TESTING_ASSIGNMENT_KEY`); admits synthetic designer run via `admit_synthetic_run` + `AgentSimulationSession(side="designer")`; publishes `agent-runs` |
| `accept_case` | `POST /suites/{suite_id}/cases/accept` body `{"draft_id"}` | explicit accept; freezes new version (`max_version+1`); 409 if already accepted or duplicate content |
| `create_execution` | `POST /executions` → `202 PlatformJobAccepted` + `Location: /api/platform-jobs/{job_id}` + `X-Evaluation-Execution-Id` | single-cell; advisory lock; requires published suite; quota; `_admit_execution_cell` |
| `get_execution` | `GET /executions/{execution_id}` | tenant scope via suite |
| `list_results` | `GET /executions/{execution_id}/results?limit&offset` | returns `list[EvaluationResultPublic]` |
| `cancel_execution` | `POST /executions/{execution_id}/cancel` | `cancel_evaluation_execution` + shared `request_platform_job_cancel` (best-effort) |
| `create_execution_batch` | `POST /executions/batch` → `202 EvaluationBatchResult` | matrix fan-out; atomic cells (continues past line 1334; matrix grouping semantics per ORM docstring) |
| helpers | `_org_id_for`, `_scope_check`, `_entity_access_allowed`, `_authorized_agent`, `_suite_or_404`, `_locked_suite_or_404`, `_finding_id_or_422`, `_authorize_matrix_cell`, `_admit_execution_cell`, `_find_reusable_execution`, `_matrix_cells_aggregate` | single source of truth for cell auth/snapshot/dedupe |

`api/src/routers/agent_findings.py` — prefix `/api/agent-findings`:

| Symbol | Route | Notes |
|---|---|---|
| `create_finding` | `POST ""` → `201 FindingPublic` | agent via `_authorized_agent` (imported from `agent_evaluations` router); source run via `_resolve_source_run` (canonical visibility + `run.agent_id == agent.id`); `source_sequence` requires `source_run_id`; org = caller tenant (admin: agent's org) |
| `list_findings` | `GET ""?agent_id&status` | requires `agent_id`; tenant-scoped; `status ∈ {open,dismissed}` |
| `get_finding` | `GET /{finding_id}` | dual gate: agent visibility + `_require_finding_tenant` |
| `update_finding` | `PATCH /{finding_id}` | body `FindingUpdate`; dismissal needs no test |
| helpers | `_to_public` (linked cases from `AgentEvaluationCase.finding_id` filtered by visible suites), `_require_agent`, `_require_finding_tenant`, `_resolve_source_run` | — |

No general persisted review-statement router exists (confirms main-plan § Reviews row).

`api/src/routers/platform_jobs.py` — generic `GET /api/platform-jobs`, `GET /api/platform-jobs/{job_id}`, `POST /api/platform-jobs/{job_id}/cancel` (shared contract; see §1.5).
Scheduler diagnostics: `api/src/routers/scheduler_diagnostics.py`, `GET /api/platform/scheduler`.

### 1.2 Contracts (existing, `api/src/models/contracts/`)

`agent_evaluations.py`:
`Provenance` (`manual|generated|historical_inspiration|finding`), `SuiteStatus`,
`ExecutionStatus` (`queued|running|waiting|succeeded|failed|cancelled`),
`ResultStatus` (`pending|running|passed|failed|error`),
`EvaluationAssertion{type,params,label}` (`extra="forbid"`),
`AssertionOutcome{code,type,label,passed,expected,actual,evidence_sequences,detail}`,
`EvaluationSuiteCreate/Update/Public`, `EvaluationCaseCreate/Update/Public`
(note: `assertions: list[dict]` on Public; `finding_id` forces provenance `finding` at route layer),
`CandidateOverlay` (system_prompt, llm_profile_id, llm_max_tokens, tool_ids, delegated_agent_ids, system_tools, max_iterations, max_token_budget, max_run_timeout, output_schema),
`CandidateCreate/Public`, `DesignerDraftRequest{suite_goal,requested_count,historical_run_ids}`,
`DesignerDraftAccepted{run_id,status="queued"}`,
`EvaluationExecutionCreate{suite_id,candidate_id,repetitions_override}`,
`EvaluationExecutionBatchCreate{suite_id,candidate_ids[≤10],profile_ids[1..10],repetitions_override}`,
`MatrixCellPublic`, `EvaluationMatrixPublic`, `EvaluationBatchResult` (live aggregate + `cost_note` stating no spend estimate),
`EvaluationExecutionPublic`, `EvaluationResultPublic` (`assertion_results: list[dict]`, `comparison: dict|None`, usage/cost/simulator_state_hash),
`EvaluationComparison{regressions,improvements,unchanged_failures,tool_trajectory_differences,output_differences,usage_delta,verdict}`.

`agent_findings.py`: `FindingStatus`, `FindingSourceKind`, `FindingCreate` (agent_id, description, expected_behavior, source_kind, source_run_id, source_sequence, external_ref), `FindingUpdate`, `FindingPublic` (+ `linked_case_ids` computed, `org_id`, timestamps).

`platform_jobs.py`: `PlatformJobStatus` (queued/running/waiting/cancel_requested/succeeded/failed/cancelled), `PlatformJobProgress`, `PlatformJobError{code,message,retryable}`, `PlatformJobAccepted{job_id,status,reused,notification_id}`, `PlatformJobPublic` (single HTTP+WS contract incl. `execution_backend`, memory fields), `PlatformJobListResponse`, `PlatformJobCancelResponse`.

`agent_runs.py`: run request/response incl. `trigger_type`, `parent_run_id`; `agent_debugger.py`: debugger projections incl. `parent_run_id`, `root_run_id`.

### 1.3 ORM (existing)

`api/src/models/orm/agent_evaluations.py`:
`AgentEvaluationSuite` (`agent_evaluation_suites`, uq org/name/version, statuses draft/published/archived);
`AgentEvaluationCase` (`agent_evaluation_cases`, uq suite/name/version, `finding_id FK agent_findings.id SET NULL`, `accepted` bool, frozen fixture/assertions);
`AgentCandidateSnapshot` (`agent_candidate_snapshots`, immutable, `evaluation_only` default true);
`AgentEvaluationMatrix` (`agent_evaluation_matrixes`; grouping only; `cell_execution_ids`; never reparented; aggregate computed live);
`AgentEvaluationExecution` (`agent_evaluation_executions`; projection over one PlatformJob; `platform_job_id` unique FK `SET NULL`; `dedupe_key` with partial unique index while active; frozen `baseline_snapshot/candidate_snapshot/case_definitions`; `matrix_id/profile_id`);
`AgentEvaluationResult` (`agent_evaluation_results`; uq execution/case/repetition; `baseline_run_id/candidate_run_id FK agent_runs.id SET NULL`; `assertion_results` JSONB incl. `{definition}` wrappers at admission; `comparison` JSONB doubles as semantic-pending staging `_evidence_*`/`_semantic_pending` then final comparison);
`AgentSimulationSession` / `AgentSimulationToolRecord` (per-side locked fixture state, append-only tool history, `uq result/side`, `uq session/sequence`, `uq session/operation_id`).
Status check constraints: executions `queued|running|waiting|succeeded|failed|cancelled`; results `pending|running|passed|failed|error`.

`api/src/models/orm/agent_findings.py`: `AgentFinding` (`agent_findings`; `agent_id CASCADE`, `org_id CASCADE`, `source_run_id SET NULL`, `source_sequence`, `external_ref(500)`; statuses open/dismissed; kinds run/manual/external). Case linkage lives on the case side only.

`api/src/models/orm/agent_runs.py`: `AgentRun` (incl. `trigger_type`, `status`, `input/output/output_schema`, `org_id`, `caller_user_id`, `parent_run_id CASCADE`, `root_run_id CASCADE`, `execution_snapshot`, `contract_valid`, usage scalars, verdict/summary fields); `AgentRunStep` (compat projection); `AgentRunCheckpoint`; `AgentRunJournalEntry` (`uq run/sequence`, `kind`, `data`, `provider_invocation_id`); `AgentToolInvocation` (PK `operation_id`, `run_id`, `provider_tool_call_id`, `tool_name`, `arguments/result/error`, `state planned|running|completed|failed|uncertain`, `idempotency_key`).

`api/src/models/orm/ai_usage.py`: `AIUsage` (`agent_run_id CASCADE`, tokens incl. cache read/write, `provider_cost/cost`, `duration_ms`, `sequence`; **sequence 0 = post-run summarization**, excluded from evaluation usage evidence).

### 1.4 Services (existing, `api/src/services/agent_evaluations/`)

- `assertions.py`: `KNOWN_ASSERTION_TYPES` (17: terminal_status, output_schema, output_path, tool_called, tool_not_called, forbidden_tool, tool_count, tool_order, tool_args, simulator_state, delegation_tree, max_iterations, max_tokens, max_cost_usd, max_latency_ms, no_real_tools, llm_judge); `AssertionDefinitionError`; `validate_assertions` (fail-closed at save); `freeze_semantic_judges(session, assertions, is_superuser)` — admin-only, resolves `judge_profile_id`→`judge_snapshot{profile_id,provider,model,endpoint,openai_transport,anthropic_prompt_cache_supported,default_max_tokens,extra_params,prompt_version}`, rejects client `judge_snapshot`; `evaluate_assertions(assertions, evidence)` sync deterministic (llm_judge → `_pending_judge_outcome` **passed=True/actual="pending"** — see §3.4 warning); `evaluate_assertions_async(..., judge_fn)`; `execute_semantic_judge(session, params, evidence)` (verifies live config == frozen snapshot incl. `extra_params`; redacted evidence; returns score/rationale/usage/`evidence_hash`; failure → `passed=False, actual=None`); per-type `_ev_*` evaluators; `_EVALUATORS` map (llm_judge handled separately).
- `evidence.py`: `load_persisted_evaluation_evidence(session, run_id)` — **synthetic-only projector** (requires `AgentSimulationSession` for `root_run_id`; fails closed on oversized trees/journals/records/usage via `MAX_EVIDENCE_TREE_RUNS=128`, `MAX_EVIDENCE_JOURNAL_ENTRIES=4000`, `MAX_EVIDENCE_USAGE_ROWS=4000`, `quotas.MAX_SIM_RECORDS_PER_RUN`, `check_evidence_size`). Emits `terminal_status/output/malformed_output/tool_calls/simulator_state(+hash)/delegation/usage(tokens/cost/latency)/real_tool_executions=0/output_schema/journal_sequences/journal_references`. **Do NOT claim this is a historical evaluator** — it hard-requires a simulation session and sets `real_tool_executions=0` by construction.
- `executions.py`: `build_dedupe_key(suite,version,candidate,profile,reps)`; `plan_matrix_cells` (baseline-per-profile + candidate×profile; `MAX_MATRIX_CELLS`); `apply_profile_override` (copies snapshot, freezes full non-secret model config; secrets stay server-side); `plan_work_items` / `plan_result_work_items` / `next_batch` (bounded, ceiling); `apply_terminal_event` (idempotent per-side; frozen scored results reject late duplicates); `_score_result` (evidence-error → `error`; deterministic eval; `_semantic_pending` staging with redacted snapshots; usage; status passed/failed/error); `resolve_semantic_assertions`; `finalize_execution` (counters; terminal only when completed==total>0); `unfinished_run_ids`; `create_execution_objects` (freezes `case_definitions`; wraps `assertion_results` as `{definition}`; expands `expected_tools→tool_called`, `forbidden_tools→forbidden_tool`); `_case_definition`.
- `runner.py`: `EVALUATION_SYNTHETIC_MODE="evaluation_synthetic"`; `is_engine_tool` (`delegate_to_*`, `delegate_agents`, `sleep_until`); `build_synthetic_correlation`; `assert_no_production_trigger`; `admit_synthetic_run` (only writer of synthetic rows; rejects production keys); `SimulatorToolRouter`/`PersistentSimulatorToolRouter` (real_executor proof hook; operation-id idempotency; locked session); `load_synthetic_router`; `collect_assertion_evidence`.
- `simulator_models.py`: `FIXTURE_VERSION=1`; `validate_fixture`; `redact_value` (key-name `secret|token|password|api_key|apikey|credential`, token counters exempt); `canonical_hash`; `fresh_state`.
- `simulator.py`: `Simulator`, `SyntheticToolError` (referenced by runner/tests).
- `test_designer.py`: `TESTING_ASSIGNMENT_KEY="testing"`; `DESIGNER_VERSION=1`; `DETERMINISTIC_ASSERTION_CATALOG` (no llm_judge — proposals with llm_judge rejected); `build_designer_snapshot/input`, `designer_testing_model`, `load_designer_history` (selected runs only, no descendant expansion; invocations-first, legacy steps fallback; per-run/total byte budgets, `truncated` marking), `validate_designer_output`, `accept_proposal`, `materialize_designer_drafts` (server-owned; bounded batch in reconciler).
- `comparison.py`: `compare_runs` (deterministic; occurrence-qualified labels; verdict regression/improvement/unchanged/mixed; prose never gates).
- `quotas.py`: `MAX_FIXTURE_BYTES=256KiB`, `MAX_CASES_PER_SUITE=200`, `MAX_DESIGNER_PROPOSALS_PER_REQUEST=10`, `MAX_ACTIVE_EXECUTIONS_PER_ORG=5`, `MAX_REPETITIONS_PER_CASE=10`, `MAX_MATRIX_CELLS=10`, `MAX_SYNTHETIC_TIMER_SECONDS=300`, `MAX_SIM_RECORDS_PER_RUN=2000`, `MAX_EVAL_JOURNAL_BYTES=4MiB`; `QuotaExceeded(status_code)`.
- `candidates.py`: `CandidateError`, `create_candidate`, `build_candidate_snapshot` (used in e2e).

### 1.5 Platform jobs (existing)

- Job type `agent.evaluation_suite` (`api/src/jobs/platform/agent_evaluation.py`): `JOB_TYPE`, `PAYLOAD_VERSION=1`, `AgentEvaluationSuitePayload{execution_id}` (payload carries ID only; frozen inputs live on durable rows); `AGENT_EVALUATION_SUITE_DEFINITION` with `PlatformJobPolicy(timeout 30min, max_attempts 2, retry_on_runner_loss True, min_memory_headroom 128MB, allow_running_cancellation True)`; `run_agent_evaluation_suite` (dispatch bounded batch → `PlatformJobDeferred` releasing slot); `apply_synthetic_terminal` (reloads **persisted** evidence, never trusts callback payload); `cancel_evaluation_execution` (runtime-owned cancellation via `run_store.request_cancellation` + `cascade_cancel`); `reconcile_agent_evaluation_jobs` (heals event-before-wait/lost-notification; materializes designer drafts bounded `DESIGNER_RECONCILIATION_BATCH=50`; finishes deferred jobs).
- Registry: `api/src/jobs/platform/registry.py` (`_DEFINITIONS`, `get_platform_job_definition`, `list_platform_job_definitions`) — the **only** dispatch map. Adding a job type = new definition module + registry entry (per platform-jobs.md steps 1–9).
- Scheduler: `api/src/scheduler/main.py:264-275` — `agent_evaluation_reconciliation` every 60s (`IntervalTrigger(seconds=60)`). No other eval trigger.
- Shared contract: enqueue → `202 PlatformJobAccepted` + `Location`; observe via `GET /api/platform-jobs/{job_id}`; cancel via `POST .../cancel`; browser via `platform_job_updated` WS (`PlatformJobPublic` shape), never bespoke polling (platform-jobs.md §§ Shared caller contract; UI/CLI/MCP behavior).

### 1.6 CLI (existing — exact spellings)

`api/bifrost/commands/agent_tests.py`, group `agent-tests` ("Manage agent evaluation suites."), `_BASE="/api/agent-evaluations"`:

`suites-list` (GET /suites) · `suites-get SUITE_ID` · `suites-create --name --description --agent --org/--global` · `suites-publish SUITE_ID` · `cases-list SUITE_ID` · `cases-create SUITE_ID --name --input --fixture(required) --assertions --expected-tools --forbidden-tools --output-schema --repetitions` · `cases-export SUITE_ID [--output]` · `candidates-create --agent(required) --name --overlays --system-prompt` · `candidates-get CANDIDATE_ID` · `designer-drafts SUITE_ID --goal(required) --count --historical-run(multiple)` · `designer-accept SUITE_ID DRAFT_ID` · `run --suite(required) --candidate --repetitions --wait/--no-wait --timeout(1200s)` (POST /executions; stderr enqueue line; `--wait` via `poll_platform_job`) · `status EXECUTION_ID` (GET execution + results) · `results EXECUTION_ID` (GET results) · `compare EXECUTION_ID` (**client-side** summary from results: regressions/failures/usage_deltas/runs; no server compare route) · `cancel EXECUTION_ID` (POST cancel).

Not existing: `suites-update`, `cases-update/delete`, `candidates-list`, matrix/batch admission, recorded-evaluation, review/finding, profile-matrix, definition edit/import commands. The main plan's `agent-reviews … / agent-tests evaluate|simulate|draft-* …` spellings are **proposals only** — §2 reconciles them (none of `evaluate`, `simulate`, `draft-create/diff/apply`, `generate`, `accept --file` exist today; `designer-accept` takes a draft ID, not `--file`).

Shared CLI behavior (`commands/base.py`, `bifrost/platform_jobs.py`):
`--json` on group and every subcommand (either position); stdout JSON has no progress chatter (progress → stderr); `run_async` exit map: ref-not-found/ambiguous → 2; guarded file 409 → 4; other 4xx → 1; 5xx → 3. `poll_platform_job`: returns body on `succeeded`; raises `ClickException` (exit 1) on `failed|cancelled` and on client-side timeout — with explicit "durable operation may still be running; check status before retrying / retry command to follow existing job" wording. **No assertion-aware exit codes exist today** (see §2.5 proposal).

### 1.7 MCP / scheduler / manifest (existing)

- MCP: no agent-evaluation/review/finding tools identified in this inventory. New MCP mutations MUST be thin HTTP wrappers (`tools/roles.py`, `tools/configs.py`, `tools/_http_bridge.py` pattern); enforced by `api/tests/unit/test_mcp_thin_wrapper.py` (AST check rejecting `src.repositories.*`, `src.models.orm.*`, `AsyncSession` in parity handlers). Parity introspection: `tests/e2e/mcp/test_mcp_parity.py` (DTO `model_fields` vs tool signatures).
- Scheduler除了 `agent_evaluation_reconciliation`: trigger leader + housekeeping callbacks listed in platform-jobs.md § Runtime; eval has no other schedule. Review/test schedules (Phase 3) must enqueue deduplicated platform jobs, not add callbacks that do work inline.
- Manifest/git-sync: `api/src/services/manifest.py`, `manifest_generator.py`, `github_sync.py` (`_resolve_*` upsert-by-natural-key); portable exports via `api/bifrost/manifest.py` + scrub `api/bifrost/portable.py`. **No agent-evaluation/finding/review manifest models exist.** `.bifrost/` is export-only; general `export/import --portable` removed (Solutions are the packaging path). DTO changes require `tests/unit/test_dto_flags.py` (union of flags + `DTO_EXCLUDES` in `api/bifrost/dto_flags.py` — note: eval/finding DTOs are NOT in `DTO_REF_LOOKUPS`/`DTO_EXCLUDES` today) and `tests/unit/test_contract_version.py`.
- Contract tripwire: `EXPECTED_CONTRACT_FINGERPRINT="af7a0e6f…"`, `CONTRACT_VERSION` frozen at 10, `MIN_CLI_VERSION="1.2.3"` (`api/shared/version.py`). Fingerprint covers `_COMMAND_DTOS` + programmatic `contracts.cli` SDK DTOs + `("/api/version",)`. New CLI-parsed response shapes are fingerprint-relevant; additive optional fields refresh fingerprint only, breaking changes raise `MIN_CLI_VERSION` first.

### 1.8 Evidence/permission primitives reused by the proposal

- Run visibility: `agent_run_visibility_conditions(user)` (`api/src/services/execution/agent_run_access.py`) — superuser bypass; else `org_id == user.organization_id` AND (`trigger_type != "delegation"` OR has parent OR caller is owner). Used by designer drafts + findings source-run validation.
- Runtime statuses: `api/src/services/agent_runtime/types.py` — `TERMINAL_STATUSES={completed,failed,cancelled,timeout,budget_exceeded,contract_failed}`; journal kinds (`tool_call/tool_result/tool_error/delegation/…`); transitions explicit.
- Redaction: `redact_value` everywhere evidence crosses trust boundaries; fixtures redacted at save; judge evidence redacted + hashed.

---

## 2. Minimal additive contracts (proposal; Primary decides)

Compatibility rule: additive only. No renames, no enum removals, no changes to existing routes/DTOs/ORM columns/enums, no new required fields on existing DTOs. New tables/routes/DTOs/job types are new names.

### 2.1 Recorded evaluation (Phase 2 scope; new, does not reuse synthetic rows)

New names (reserved; created in Phase 2 packet):
`RecordedEvaluationCreate{agent_id, name?, test_refs: list[{test_id,test_version}], run_ids: list[UUID], judge: {mode: "exact"|"semantic", judge_snapshot?}, repetitions_note?}` →
`POST /api/agent-evaluations/recorded-evaluations` → `202 PlatformJobAccepted` + `Location` + `X-Recorded-Evaluation-Id`.
`RecordedEvaluationPublic{id, agent_id, org_id, status, test_refs, run_ids(admitted, deduped), evidence_refs, judge_snapshot, platform_job_id, created_by/at, completed_at}`.
`RecordedEvaluationResultPublic{id, evaluation_id, test_id, test_version, run_id, outcome: RecordedOutcome, assertion_outcomes, evidence_ref, error}` with
`RecordedOutcome ∈ {passed, failed, not_applicable, insufficient_evidence, error}` — **new enum on new tables only**; existing `ResultStatus` untouched.
New job type `agent.evaluation_recorded`, payload v1 `{recorded_evaluation_id}` (ID only; frozen refs on the row — same pattern as suite payload). Registry + policy declared explicitly (see handoff packet). No synthetic dispatch, no simulator writes, no agent/model invocation except the configured semantic judge.

Admission (all fail-closed before enqueue):
1. `agent_id` visible via `_authorized_agent`; evaluation `org_id` = caller tenant (admin: agent's org).
2. Each `run_id`: `agent_run_visibility_conditions(user)` AND `AgentRun.org_id == eval org` AND `run.agent_id == agent_id` (same-agent rule mirrors findings `_resolve_source_run`). Dedupe `run_ids` preserving order; each evaluated once.
3. Each test ref: resolve frozen test version (Phase 2 packet defines the test-version read source — either existing accepted `AgentEvaluationCase` versions or the new test-version store if Primary chooses; default: reuse accepted case versions, see §2.3). Missing version → 422, never silent skip.
4. Judge: `exact` needs no model; `semantic` requires admin-frozen `judge_snapshot` via the existing `freeze_semantic_judges` path (same admin-only gate, same snapshot-vs-live-config verification at judge time).

Evidence (new projector, does NOT extend `evidence.py`):
`load_recorded_run_evidence(session, run_id) -> {evidence, completeness}` built from durable rows only: `AgentRun` (+ `root_run_id` tree, bounded like synthetic), `AgentRunJournalEntry` (tool_call/result/error + delegation refs), `AgentToolInvocation` (authoritative tool calls incl. `uncertain` state surfaced, never replayed), `AIUsage` (sequence>0 only), `AgentRun.output/output_schema/contract_valid/status`. No `AgentSimulationSession` lookup; no invented fixtures; `simulator_state` explicitly absent (`completeness.simulator_state="absent"`); `real_tool_executions` = count of non-engine invocations (NOT forced 0). Oversized/missing/deleted → `error`/`insufficient_evidence`, never truncation. Deleted run (`db.get` None) → per-pair `error run_deleted`. See completeness matrix §3.

Tenant/root-child rule: only explicitly admitted `run_ids` are read; descendants are included in a run's tree **only** when they satisfy visibility AND share the admitted run's `root_run_id` (observability grouping, never an auth grant — mirrors `cancel_evaluation_execution` root-scoping comment). A hidden child is omitted and the affected assertions become `insufficient_evidence` (same "never smuggle" precedent as `load_designer_history`).

### 2.2 Reviews / findings (reserved for Phase 3; no Phase 2 writes)

Existing `AgentFinding` stays single-`source_run_id` + text + `linked_case_ids` (computed). Additive Phase 3 names (reserved, not created now): `AgentReview{statement, scope, evidence_instruction, schedule, version}`, `AgentReviewRun{review_version, run selection window, admitted run_ids}`, finding extensions `source_run_ids[]`, `review_id/review_version`, `kind ∈ {problem, opportunity}`, `evidence_markdown`. Finding dismissal stays independent; test pass never dismisses (binding product rule — no contract can violate it).

### 2.3 Default collection (smallest compatible proposal)

Today: named suites with versions; `list_suites` has no agent filter; no default/last-result read contract.
Proposal (Primary confirms one): **(a)** additive `GET /suites?agent_id=` filter + additive `GET /suites/default?agent_id=` resolving the agent's default suite (server-owned pointer, e.g. agent-level `default_suite_id` or newest published suite for the agent — Primary picks exactly one) + additive per-test last-result read (`GET /suites/{id}/tests/last-results?mode=recorded|simulation&profile_id=`); **(b)** named suites and version snapshots preserved; no silent flattening. Phase 2 packet does NOT create this; it only guarantees recorded results carry the `(test_id, test_version, mode="recorded")` keys the future read needs.

### 2.4 Hypothetical / proposed tools (reserved for Phase 4; no Phase 2 writes)

`CandidateOverlay.tool_ids/system_tools` today reference only real, authorized workflows. Reserved additive names: `proposed_tools[] {name, input_schema, output_schema, behavior, simulation_only: true}` on the candidate/test definition; namespace rule (proposal): proposed names MUST NOT collide with real workflow tool names or engine tools, and resolution order is simulator-first with a hard "never real dispatch" fence (reuse `route_real` proof-hook pattern). Apply guard: candidate with unresolved `proposed_tools` is rejected at apply time with an explicit implementation/binding requirement. Simulation proves contract usefulness only — separate integration coverage required before binding (binding product rule).

### 2.5 CLI parity (proposal → reconciled spellings; implemented in Phase 2b only)

Keep every existing `agent-tests` command byte-compatible. New commands (exact spellings proposed; Primary approves before implementation):

```text
bifrost agent-tests evaluate --agent AGENT --tests TEST[,TEST...]|all --runs RUN[,RUN...] [--judge exact|semantic] [--wait] [--json]
bifrost agent-tests recorded-status EVALUATION_ID [--json]
bifrost agent-tests recorded-results EVALUATION_ID [--json]
bifrost agent-tests recorded-cancel EVALUATION_ID
```

Mapping: `evaluate` → `POST /recorded-evaluations` (202 + `Location`); `--wait` reuses `poll_platform_job` (same short-request polling, same timeout wording, same safe-retry via dedupe); `recorded-status/results` → `GET` detail + results; `recorded-cancel` → shared cancel. No `agent-reviews`/`agent-findings` commands in Phase 2 (Phase 3). Definition editing/import (`--file` accept flows) stays Phase 4; `designer-accept` keeps its draft-ID shape.

Stable exit behavior (documented before implementation; new, CLI-only):
0 = success (all required checks passed; zero `failed/error/insufficient_evidence` on required pairs; `not_applicable` reported separately).
1 = invocation/job/HTTP failure (existing `run_async`/`poll_platform_job` map: 4xx→1, 5xx→3, ref→2, file-conflict→4 preserved) OR assertion failure (`failed`) OR incomplete required evidence (`error/insufficient_evidence` on required pairs).
All-inapplicable selection (every pair `not_applicable`, none failed/error) → exit **1** with machine-readable `{"all_inapplicable": true}` (never a green gate), stderr explains no applicable evidence. `pending_judge` (job still `waiting`/judge unresolved) is an execution state: `--wait` keeps polling; non-wait prints job + evaluation IDs and exits 0 for admission success (admission ≠ pass).

### 2.6 MCP / manifest / scheduling (no new systems)

- MCP: Phase 2 adds at most thin wrappers over the three recorded-evaluation REST endpoints (same `_http_bridge` pattern); AST guardrail test extended with the new handler names. No direct ORM/repository access.
- Manifest: no manifest models for recorded evaluations in Phase 2 (execution records, not portable definitions). If a later phase exports test definitions, it follows `manifest.py` + `portable.py` scrub + round-trip tests.
- Scheduling: no new scheduler callbacks, workers, tables-with-leases, status endpoints, WS events, or polling loops. Recorded evaluation reuses the trigger-leader + shared claim loops; Phase 3 review schedules enqueue the same deduplicated platform jobs.

---

## 3. Semantics the contracts enforce (binding detail)

### 3.1 Completeness matrix by assertion kind (recorded evidence)

`complete` = verdict counts; `incomplete` = `insufficient_evidence` (never pass, never implicit exclude); `N/A` = `not_applicable` (reported, never green — see §2.5).

| Assertion | Recorded-historical evidence required | Incomplete → | Notes |
|---|---|---|---|
| terminal_status | run row `status` terminal | `error` if run missing/in-flight (`queued|running|cancelling|waiting_child*|sleeping`) | in-progress/cancelled/execution-error are execution states, never pass |
| output_schema / output_path | `output` + applicable schema (`params.schema` or run `output_schema`); `contract_valid is False` → fail (malformed), not insufficient | missing output+schema → `insufficient_evidence` | — |
| tool_called | ≥1 matching invocation in `AgentToolInvocation` ∪ journal `tool_call` for the admitted tree | coverage gap (uncertain invocations, journal overflow vs bounds) → `insufficient_evidence` | `uncertain` state is surfaced, never replayed |
| tool_not_called / forbidden_tool | **complete** tool history required (bounded-full journal + invocations for the whole tree, no truncation, no `uncertain`) | any gap → `insufficient_evidence`, **never pass** (incomplete trace cannot prove absence — Phase 2 acceptance) | mirrors synthetic fail-closed bounds |
| tool_count / tool_order / tool_args | complete ordered tool history as above | gap → `insufficient_evidence` | order uses journal sequence + invocation timestamps, documented |
| simulator_state | — | always `not_applicable` on recorded runs (no equivalent recorded state; never silently fill fixtures) | simulator checks only run in simulation |
| delegation_tree | durable parent/child links + delegation journal refs for admitted tree | hidden-child omission → `insufficient_evidence` | descendant inclusion rule §2.1 |
| max_iterations/max_tokens/max_cost_usd/max_latency_ms | `AIUsage` (seq>0) + run scalars; missing usage → `insufficient_evidence` (budget detail cites missing side) | — | seq-0 summarizer rows excluded (spend attribution unchanged) |
| no_real_tools | count of non-engine invocations from durable rows | gap → `insufficient_evidence` | historical runs normally fail this (they ran real tools) — correct, not a bug |
| llm_judge | frozen `judge_snapshot` + redacted evidence; judge runs as its own durable step with usage persisted | judge unrun/failed → outcome stays `pending_judge` (execution state, **never pass**); judge exception → `error` on that pair, not silent pass | sync path keeps `_pending_judge_outcome` shape but recorded aggregation treats `pending` as non-terminal (see §3.4) |

Applicability unknown vs false: unknown (cannot determine from complete evidence whether the check applies) = `insufficient_evidence`; known-false (e.g. simulator_state on recorded, or test's `applicable_situation` provably absent) = `not_applicable`. Neither is an implicit exclusion and neither passes.

### 3.2 Outcome aggregation algorithm (recorded evaluation)

Per admitted (test_version, run_id) pair, in order:
1. Load recorded evidence (or record `error`: run_deleted / evidence_unavailable / oversized).
2. Evaluate each assertion → `passed|failed|not_applicable|insufficient_evidence|pending_judge|error` (exact assertions deterministic; semantic via frozen judge or `pending_judge` if unresolved).
3. Pair outcome = `error` if any `error`; else `insufficient_evidence` if any `insufficient_evidence` or `pending_judge` remains at finalization; else `not_applicable` if all `not_applicable`; else `failed` if any `failed`; else `passed`.
4. Evaluation roll-up preserves every pair (bulk jobs never collapse non-applicability/insufficiency). Counters: `{total, passed, failed, not_applicable, insufficient_evidence, error, pending}`. Job `succeeded` ⇔ zero `failed/error/insufficient_evidence/pending` AND ≥1 `passed` (all-inapplicable ⇒ job `succeeded` with `all_inapplicable: true` in result payload, but CLI exits 1 per §2.5 — job success and CI gate are deliberately different signals).

### 3.3 No simulated state for historical runs

The recorded projector has no fixture input, no `fresh_state`, no `Simulator` call, no `route_real`. `simulator_state_hash` is absent (not null-filled, not hashed from output). Any future "backfill fixture" would be a separate, explicit, versioned proposal — not silent invention.

### 3.4 Semantic judge: pending isn't pass

Existing `_pending_judge_outcome` returns `passed=True/actual="pending"` as a **staging placeholder** inside `_score_result` (resolved later by `resolve_semantic_assertions`). Recorded aggregation MUST NOT reuse that boolean: pairs with unresolved judges stay non-terminal (`pending_judge`), the job goes `waiting` (reuses the deferred pattern, not a new state machine), and finalization waits for `resolve_semantic_assertions`-equivalent or a judge timeout → `error`. This is stated explicitly so no implementer mistakes the staging `True` for a verdict.

### 3.5 Permissions recap

Admission AND evidence-read both enforce: caller-tenant org match + `_authorized_agent` + per-run `agent_run_visibility_conditions` + same-agent + admitted-IDs-only. Cross-tenant run IDs → 404 (not 403, matching findings/eval precedent). Superuser bypass preserved. Evidence never expands descendants beyond admitted roots; hidden children degrade to `insufficient_evidence`.

---

## 4. Migration choices and risks

- **New tables** (`recorded_evaluations`, `recorded_evaluation_results`) + new job type + new routes. Chosen over reusing `AgentEvaluationExecution/Result` because those rows' status enums, counters, snapshot columns, and reconciler semantics are synthetic-coupled (frozen snapshots, simulator sessions, candidate pairing). Risk of reuse: silent semantic drift of existing simulation results (explicit Phase 2 acceptance: existing simulation results unchanged). New-table risk: more tables — accepted because they are feature records, not a job system (the job system stays shared).
- **New job type vs reuse `agent.evaluation_suite`**: new type `agent.evaluation_recorded`. Reuse would force synthetic fan-out branches into the suite handler; a separate handler keeps both paths auditable. Not a parallel system: same registry, same claim loops, same status/cancel/WS/CLI-poll transports.
- **Fingerprint**: new response DTOs parsed by the CLI refresh `EXPECTED_CONTRACT_FINGERPRINT`; additive ⇒ no `MIN_CLI_VERSION` bump (decision recorded in the packet; reviewer confirms).
- **Judge cost**: semantic mode spends model budget per pair; admission requires explicit `--judge semantic`, and results persist `judge_usage` (same shape as `execute_semantic_judge` output). Default is `exact`.
- **Run deletion**: `SET NULL`/missing rows degrade per-pair, never fail the whole evaluation opaquely.
- **Unresolved choices for Primary**: §2.1 test-version source (recommended: reuse accepted case versions); §2.3 default-collection pointer; §2.5 exact CLI spellings; new job policy numbers (recommended in handoff: timeout 15min, attempts 2, retry_on_runner_loss True — idempotent projector — headroom 64MB, cancellable True; scheduler team confirms from representative volume).

## 5. Existing commands / quick reference (for the packet author)

Routers: `agent_evaluations.py` (suites/cases/candidates/designer/executions/batch), `agent_findings.py` ("", /{id}), `platform_jobs.py`, `scheduler_diagnostics.py`. CLI group `agent-tests` (16 commands, §1.6). Tests: `tests/unit/services/agent_evaluations/` (assertions, evidence, executions, simulator, test_designer, comparison, quotas, candidates, matrix, finding-case seam), `tests/unit/jobs/platform/test_agent_evaluation*.py`, `tests/e2e/api/test_agent_evaluation_*.py` + `test_agent_designer_history.py` + `test_agent_findings.py` + `test_synthetic_agent_run.py`, `tests/unit/bifrost/commands/test_agent_tests.py`, tripwires `tests/unit/test_dto_flags.py` + `tests/unit/test_contract_version.py` + `tests/unit/test_mcp_thin_wrapper.py` (+ `tests/e2e/mcp/test_mcp_parity.py`), scheduler `tests/e2e/platform/test_scheduler_diagnostics.py`.

## Additional primary review requirement: requested versus completed actions

Existing `tool_called`/`tool_args` assertions establish requests and arguments, not successful side effects. Product copy must not present those alone as proof a private note was created or a ticket was closed. The test designer must pair positive action expectations with supported result/state evidence (or an explicit judged result). If the common assertion contract needs a `tool_succeeded`/result predicate, design and review that additive change in Phase 4; do not silently redefine existing tool_called semantics. Recorded evidence should retain execution state/results distinctly from request presence, and insufficient outcome evidence must not become success.
