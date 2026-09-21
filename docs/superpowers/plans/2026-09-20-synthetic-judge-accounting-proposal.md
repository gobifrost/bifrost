# Synthetic semantic judge accounting proposal

Owner: primary review. This is a read-only design packet for the existing synthetic `llm_judge` path. It proposes the smallest correction that brings synthetic semantic judges onto the accepted shared quality-usage foundation while preserving today’s non-authoritative synthetic observation semantics and existing AgentRun evidence/usage APIs.

## Current gap

`api/src/services/agent_evaluations/executions.py::resolve_semantic_assertions` is reached from two durable paths:

- `api/src/jobs/platform/agent_evaluation.py::apply_synthetic_terminal`, after a terminal synthetic AgentRun callback applies persisted evidence to a result row.
- `api/src/jobs/platform/agent_evaluation.py::reconcile_agent_evaluation_jobs`, when the scheduler heals terminal run state for an execution whose deferred PlatformJob transition was not finalized.

Both callers currently hold the execution/result fence while `resolve_semantic_assertions` calls `evaluate_assertions_async`, which calls `execute_semantic_judge(session, ...)`. That helper resolves the frozen judge profile and calls the provider under the same database session/transaction. It stores observed usage only inside `assertion_results[].judge_usage`; it does not write `AIUsageAttempt` or `AIUsage`.

The normal mixed exact/semantic path is already protected in `_score_result`: it stores only `llm_judge` entries in `_semantic_pending.definitions`, so the replacement queue is semantic-only. Keep that behavior covered with a regression test while changing orchestration, but do not implement a speculative alignment fix for a bug that is not present in the current normal path.

## Platform coordination choice

The existing deferred parent job cannot safely own long-running judge calls from callback/reconciliation code. `PlatformJob` has a canonical runner lease, heartbeat, phase/progress, and deferred `waiting` state, but terminal callbacks and scheduler reconciliation do not hold a live runner lease while they are invoked. A plain `AIUsageAttempt.state="started"` marker also cannot distinguish “the original provider call is legitimately still running” from “the process died after committing the marker.” Marking started attempts ambiguous from reconciliation would race and corrupt legitimate in-flight calls.

Use one shared PlatformJob definition for semantic postprocessing, registered in the existing PlatformJob registry:

- job type: `agent.evaluation_synthetic_semantic`
- payload v1: `{execution_id, result_id}` or `{execution_id, result_ids}`; choose per-result unless batching clearly reduces complexity without weakening fences.
- policy: `max_attempts=1`, `retry_on_runner_loss=False`, `allow_running_cancellation=True`, bounded timeout consistent with the current judge call envelope, normal runner lease/heartbeat.
- dedupe key: stable hashed canonical identity for the result or result set.
- organization/resource policy: same tenant/resource as the parent evaluation suite execution.
- notification policy: do not call `ensure_platform_job_notification` for the child job. `enqueue_platform_job` does not create notification records by itself, so this avoids a duplicate user-facing card while retaining normal internal PlatformJob state and WebSocket update broadcasts.

This is not a new worker, table, status endpoint, WebSocket transport, browser poller, or bespoke lifecycle. It is the canonical shared PlatformJob system applied to the missing durable unit of work. The parent `agent.evaluation_suite` job remains deferred until synthetic runs and semantic postprocessing are terminal. Callback/reconciliation wake or enqueue this child job, then finalization is based on persisted result rows.

If primary prefers extending shared “resume deferred phase” support instead, it must provide equivalent canonical lease/heartbeat ownership for postprocessing outside the callback transaction. Without that, a postprocessing PlatformJob is the smaller reviewed correction.

## Shared business logic placement

New substantive orchestration belongs in `api/shared`, not `api/src/services`. Add a focused shared module such as `api/shared/agent_synthetic_judge.py` that owns:

- deriving synthetic judge call plans from a result’s `_semantic_pending` payload and current `assertion_results`;
- preserving the existing semantic-only pending-definition invariant;
- canonical request fingerprinting and bounded idempotency-key hashing;
- started/completed/ambiguous outcome projection;
- strict usage extraction for shared accounting;
- sanitized reason metadata while preserving today’s failed, non-authoritative outcome shape.

`api/src/services/agent_evaluations/executions.py` should keep pure scoring/result helpers and call the shared module where needed. Do not add wrappers solely for tests.

## Public semantics to preserve

- Deterministic assertion scoring and result status remain authoritative.
- Synthetic semantic judge outcomes remain non-authoritative/nondeterministic observations.
- Provider/config failures continue to appear as failed `llm_judge` observations, matching today, but add sanitized reason metadata such as `reason="judge_provider_error"` instead of raw provider exception text.
- AgentRun source usage and existing `comparison["usage"]` projections remain unchanged.
- Shared `AIUsage` rows carry synthetic judge accounting. Do not attach judge spend to source AgentRuns and do not add a parallel JSON cost subtotal.
- Absence of historical attempts means coverage unknown; do not backfill.

## Smallest corrected flow

1. `_score_result` still writes deterministic outcomes and `_semantic_pending` when semantic assertions exist.
2. `apply_synthetic_terminal` and `reconcile_agent_evaluation_jobs` do not call a provider. When a result has semantic pending work, they enqueue or reuse the `agent.evaluation_synthetic_semantic` child PlatformJob under a stable dedupe key, persist the child job id under `result.comparison["semantic_judge_job_id"]`, and leave the parent deferred. This stored child id is required because shared PlatformJob dedupe is active-only; once a child is terminal, dedupe alone cannot prevent accidental reenqueues.
3. The child job handler locks the current PlatformJob lease through the canonical `PlatformJobContext`. It loads the execution/result/suite with row locks, validating that the child job tenant matches `AgentEvaluationSuite.org_id`, the child job requester is the canonical parent `PlatformJob.requested_by_user_id`, the requester parses as a UUID for accounting, and the parent evaluation execution/result still match the payload. Parent job display fields come from the existing parent `PlatformJob` row; `AgentEvaluationExecution.created_by` is an email string and must not be treated as canonical requester identity.
4. Under the result lock, it converts `_semantic_pending` into bounded per-assertion started markers and calls `begin_quality_usage_attempt` for each semantic call. It commits before provider dispatch. The call identity uses `quality_operation_type="synthetic_evaluation"` and `usage_purpose="synthetic_semantic_judge"`.
5. Only attempts newly created by this child job are dispatched. Existing completed semantic outcomes are reused. Existing started markers are not replayed.
6. The handler calls the provider outside any database transaction.
7. Immediately after each provider response, the handler records factual usage through `record_quality_usage_observation` in a fresh short transaction. If the response lacks strict input/output token counts, call `mark_quality_usage_unobserved(..., reason="missing_usage")`. If provider/config dispatch fails before observable usage, call `mark_quality_usage_unobserved(..., reason="provider_error")`. If process death makes dispatch ambiguous, leave the committed attempt as `started`; the platform runner-loss path settles the domain observation without replay.
8. The handler reacquires the job/result fence before writing semantic outcomes. If cancellation or stale ownership wins, accounting remains settled and the domain result is not mutated.
9. When all semantic observations for the result are completed or marked ambiguous, reconciliation/finalization recomputes execution status and calls `finish_deferred_platform_job` for the parent only after every result is terminal and no semantic postprocessing child remains active.
10. If `apply_terminal_event` returns `False` because the source side was already durably advanced, callback/reconciliation still checks the existing result for `_semantic_pending` or started markers and enqueues/reconciles semantic postprocessing. Durable source advancement must not strand semantic accounting.

Parent and child jobs must not share a `resource_lock_key` that makes the child unclaimable while the parent is `waiting`. The scheduler treats `running` and `cancel_requested` jobs as resource-busy, not `waiting`, but using a distinct child lock such as `agent-evaluation-semantic:{execution_id}` keeps the relationship explicit and avoids future deadlock if parent locking changes. The result row lock remains the domain fence for one result.

## Runner-loss recovery

Because the child postprocessing job uses the shared PlatformJob lease and `retry_on_runner_loss=False`, there is a concrete recovery signal that is not a custom timeout:

- While the child job stored in `comparison["semantic_judge_job_id"]` is `queued`, `running`, or otherwise active with a valid lease/heartbeat, scheduler reconciliation must not convert started markers to ambiguous or enqueue a replacement child.
- If the platform job scheduler marks the child failed/lost, or finds the child terminal failed after runner loss, reconciliation loads the result and converts started semantic markers without completed outcomes to today-shaped failed non-authoritative observations with sanitized `reason="judge_attempt_ambiguous"`.
- It does not replay the provider call.
- If `AIUsageAttempt.state == "observed"` but the verdict write was lost after response accounting, the spend remains in `AIUsage`; the result observation becomes ambiguous.
- If `AIUsageAttempt.state == "started"` and no observation exists, reporting shows attempted coverage uncertainty; the result observation becomes ambiguous after the child job is terminal/lost.
- Parent finalization never assumes a live lease on the deferred parent. It finishes from persisted child/result state.
- If the stored child job id is terminal succeeded and semantic outcomes are already complete, reconciliation must not enqueue another child even though active dedupe no longer applies.

## Identity and idempotency

Do not store raw identity strings that can exceed `AIUsageAttempt.idempotency_key`’s 255-byte bound. Build a canonical JSON identity and hash it:

```json
{
  "kind": "synthetic_semantic_judge",
  "execution_id": "...",
  "result_id": "...",
  "case_id": "...",
  "case_version": 1,
  "repetition_index": 0,
  "side": "baseline",
  "assertion_index": 3,
  "assertion_hash": "...",
  "request_fingerprint": "..."
}
```

Use `synthetic-semantic:{sha256(canonical_identity)}` as the idempotency key.

Set accounting provenance:

- `quality_operation_id = execution.id`
- `quality_operation_item_id = sha256(canonical_identity)`, or a bounded prefix plus hash if human readability is useful
- `platform_job_id = child semantic PlatformJob id`, not the source AgentRun and not a missing parent lease
- `organization_id = AgentEvaluationSuite.org_id`
- `user_id = UUID(parent_platform_job.requested_by_user_id)` after validating it is a canonical user UUID; if the value is not parseable, fail closed for this new semantic child job rather than dropping requester provenance to null
- `profile_id`, `provider`, and `model` from the frozen `judge_snapshot`
- `profile_name = None` unless already frozen immutably in the assertion snapshot
- `profile_fingerprint = sha256(canonical non-secret frozen judge_snapshot)`
- `request_fingerprint = sha256(canonical redacted request payload)`

Never include API keys, raw provider exceptions, full mutable profile config, or unbounded evidence in accounting records.

## Mixed exact/semantic behavior

Do not treat mixed exact/semantic assertions as a confirmed bug in the current normal path. `_score_result` filters `semantic_definitions` to `llm_judge` before storing `_semantic_pending.definitions`, so `resolve_semantic_assertions` receives a semantic-only list. The implementation should preserve that invariant and verify it while moving provider orchestration.

Regression test: one case with definitions `[terminal_status, llm_judge]` for baseline-only evidence must leave the deterministic terminal outcome untouched and replace only the semantic row with the judge outcome. A paired baseline/candidate test should prove both side queues preserve the current behavior when exact assertions precede semantic assertions.


## Functions and files to change

Primary implementation surface:

- New `api/shared/agent_synthetic_judge.py`
  - Shared business logic listed above.
  - No DB provider call under transaction.
  - No test-only wrappers.

- `api/src/jobs/platform/agent_evaluation.py`
  - Register/handle `agent.evaluation_synthetic_semantic`, or add a small sibling job module if cleaner while still using the shared business module.
  - `apply_synthetic_terminal` and `reconcile_agent_evaluation_jobs` enqueue/reuse semantic child jobs and recover terminal/lost child jobs to ambiguous observations.
  - Parent deferred finalization waits for semantic child completion.
  - Persist `semantic_judge_job_id` in `result.comparison` and consult it before enqueueing, because active dedupe does not cover terminal children.
  - Do not call `ensure_platform_job_notification` for child jobs; they are internal sub-work of the parent evaluation card.

- `api/src/services/agent_evaluations/executions.py`
  - Preserve the existing semantic-only pending-definition behavior while using shared call-plan helpers.
  - Keep `_score_result`, `apply_terminal_event`, and `finalize_execution` semantics stable.

- `api/src/services/agent_evaluations/assertions.py`
  - Extract provider execution/parsing only if required to call it outside a DB transaction. Preserve the existing failed non-authoritative outcome shape and existing usage-preservation behavior. Do not alter deterministic evaluators or assertion validation beyond the minimal alignment need.

- `api/src/jobs/platform/registry.py`
  - Register the new shared PlatformJob definition.

No router, CLI, MCP, UI, public report endpoint, browser poller, custom worker, custom table, or source AgentRun usage writer belongs in this packet.

## Tests to add or adjust

Focused unit tests:

- `api/tests/unit/services/agent_evaluations/test_assertions.py`
  - Preserve current semantic judge behavior: malformed JSON/out-of-range scores keep observed `judge_usage`; no-response/provider failure does not invent usage.

- `api/tests/unit/services/agent_evaluations/test_executions.py`
  - Mixed exact+semantic definitions preserve current semantic-only replacement behavior for baseline-only and paired baseline/candidate results.
  - Existing deterministic result status remains authoritative after semantic observation replacement.

- New or existing shared-service tests for `api/shared/agent_synthetic_judge.py`
  - Canonical identity hashes are bounded and stable.
  - Started/completed/ambiguous projection preserves non-authoritative fields and sanitized reason metadata.
  - Strict usage extraction rejects missing/bool/non-integer token counts for shared accounting while preserving best-effort `judge_usage` projection.

Durability/race tests:

- `api/tests/unit/services/test_agent_run_evaluation_completion.py`
  - Callback and scheduler reconciliation racing the same terminal run enqueue one semantic child job.
  - `apply_terminal_event` returning `False` still leads to semantic pending recovery when the result already contains durable pending state.
  - A terminal child job id stored in `comparison["semantic_judge_job_id"]` prevents reenqueue after active dedupe no longer applies.
  - Cancellation after provider response prevents stale assertion-result mutation but does not erase observed accounting.

- `api/tests/unit/jobs/platform/test_agent_evaluation_completion_race.py`
  - Deferred parent does not finish while a semantic child job is active.
  - Runner-loss terminal child marks started semantic observations ambiguous without replay.
  - Observed accounting with missing verdict still finalizes to ambiguous observation and preserves the `AIUsage` row.
  - No-live-parent-lease deferred finalization succeeds after child terminal state is durable.
  - Child job enqueue uses distinct resource locking and does not create a duplicate notification record.

Accounting integration:

- Add caller-focused DB tests that prove a synthetic semantic response writes one `AIUsageAttempt` and one `AIUsage` row with `synthetic_evaluation` / `synthetic_semantic_judge`, suite org provenance, validated requester user id, child `platform_job_id`, provider/model/profile identity, and no source `agent_run_id`.
- Existing `api/tests/unit/services/test_quality_usage.py` should only change if the shared foundation needs a generic helper guarantee; prefer keeping foundation tests generic.

Targeted checks after implementation:

```bash
./test.sh tests/unit/services/agent_evaluations/test_assertions.py tests/unit/services/agent_evaluations/test_executions.py -v
./test.sh tests/unit/services/test_agent_run_evaluation_completion.py tests/unit/jobs/platform/test_agent_evaluation_completion_race.py -v
./test.sh tests/unit/services/test_quality_usage.py tests/e2e/api/test_quality_usage_accounting.py -v
./test.sh quality api
```

Run DTO/contract tripwires only if the new PlatformJob payload or public job contract changes the CLI/OpenAPI-consumed surface. No frontend suite is expected for this bounded backend packet.

## Risks and review points

- The main risk is premature ambiguity while a legitimate provider call is in flight. Review should verify ambiguity is only produced from terminal/lost child PlatformJob state, never merely from seeing a started accounting marker.
- The second risk is replay after a committed marker. Review should verify `retry_on_runner_loss=False`, `max_attempts=1`, stable enqueue dedupe, and no code path re-dispatches an existing started attempt.
- The third risk is holding a DB transaction during provider dispatch. Review should verify attempts/started markers commit before model calls, and verdict writes reacquire locks afterward.
- Active-only PlatformJob dedupe is insufficient after the child reaches terminal state. Review should verify the stored child job id in result comparison is the durable no-reenqueue fence.
- Mixed exact/semantic behavior is not a known current bug; review should verify the current semantic-only pending invariant is preserved with tests.
- Tenant and requester provenance must be durable: use `AgentEvaluationSuite.org_id` and validated parent job requester identity. Do not silently null those fields for convenience.
- Provider-reported cost and token counts must be factual. Do not coerce missing input/output counts to zero for accounting. Keep legacy `judge_usage` best-effort projection separate from strict shared `AIUsage`.

## Primary approval and implementation constraints

Approved for bounded native implementation after the above revisions. Executor: `synthetic_accounting_plan` native worker. Worktree: `/home/jack/GitHub/bifrost/.worktrees/durable-agent-platform-backend`; preserve existing dirty changes, no commits or paid calls. Primary reviews source, contracts, races, and independent checks before acceptance.

Use one child PlatformJob per result with payload exactly `execution_id: UUID, result_id: UUID`. Pydantic payload goes in `api/shared/models.py`; that file is currently owned by public-reporting worker `evidence_worker`, so coordinate the addition through that owner instead of concurrent writes. New business logic goes in `api/shared/agent_synthetic_judge.py`; thin job definition/handler may use `api/src/jobs/platform/agent_synthetic_judge.py`. Do not alter other public DTOs.

Persist the child ID atomically with enqueue under the execution/result fence. Active-only job dedupe is not a permanent replay fence: terminal or missing linked child must never silently authorize another job/call. Missing linked child resolves to an explicit unavailable/ambiguous observation without replay. Preserve requester and org provenance from the parent job and suite and validate existence for accounting FK; historical rows without valid owner/job cannot issue a paid call. Cancel child jobs through shared cancellation before parent cleanup completes; actual returned usage still settles independently.

Canonical lock ordering matters: retain execution-before-result ordering used by callbacks, avoid child-job/execution lock inversion with cancellation/reconciliation, and acquire the current lease fence before dispatch/commit. No provider or network pricing under locks. Admit one assertion attempt immediately before its call rather than precreating a batch of not-yet-attempted calls. Preserve existing synthetic verdict semantics, redact provider errors, canonicalize OpenRouter accounting provider as recorded judging does, and keep source AgentRun spend unchanged.

Do not create child notification cards. No custom scheduling, polling, status endpoints, or feature-specific lease fields. Every parent-finalization call site must respect active semantic child work, including callback duplicates and scheduler recovery. Report exact focused tests, boundary evidence, and remaining limitations; do not declare the overall workbench complete. Test stack is currently owned by `evidence_worker`, then summary worker `recorded_api_plan`; coordinate before tests.

### First implementation review — not accepted

Primary found lock inversion (child job locked before execution while cancellation locks execution first), missing parent/domain cancellation checks on paid starts and verdict writes, and unconditional overwrite of persisted child identity. Correct these before any acceptance. Move substantive orchestration out of the job module into shared business logic.

Also reject malformed persisted child IDs without re-enqueue; settle every unresolved observation after terminal child failure in one pass (no short-circuit leaving pending rows); preserve terminal invalid-response/error observations instead of relabeling them ambiguous; reject negative input/output token counts; validate paid marker index/hash/request fingerprint before admitting or settling calls; remove dead no-op/unused helper types. Pure helper/policy tests are insufficient: actual database callback/reconciliation/handler, concurrent enqueue, live-child, cancellation, lost-worker, paired-side, and permanent no-replay tests are required. These findings are assigned back to the same worker; source is not accepted yet.
