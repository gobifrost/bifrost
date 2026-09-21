# Synthetic and designer usage reporting — proposed bounded packet

## Goal

Expose per-synthetic-execution and per-designer-run usage breakdowns, plus small CLI readers, using the accepted shared quality usage reporting path. The packet should let reviewers distinguish production/source run spend from testing overhead at operation scope and in the already-added global report. It should not add UI, mutate accounting, rerun models, or change the legacy `/api/reports/usage` endpoint.

## Current facts from source

Synthetic suite execution authorization already exists in `api/src/routers/agent_evaluations.py`:

- `_execution_and_suite_or_404(db, user, execution_id)` loads `AgentEvaluationExecution`, its `AgentEvaluationSuite`, applies `_scope_check`, and verifies the baseline agent remains accessible through `_assert_execution_accessible`.
- `GET /api/agent-evaluations/executions/{execution_id}` and `/results` use that helper today.

Synthetic source run attribution already exists in `api/shared/quality_usage_reporting.py`:

- Runtime `AIUsage` rows attached to `AgentRun` with `trigger_type == "evaluation_synthetic"` and no designer flag are classified as purpose `simulation`.
- Their operation type is inferred as `synthetic_evaluation` when `AgentRun.correlation["evaluation_execution_id"]` is a valid UUID.
- Their operation id is that execution id.
- This is source/model spend for the synthetic baseline/candidate runs, not judge overhead.

Synthetic semantic judge overhead now has explicit shared accounting in `api/shared/agent_synthetic_judge.py`:

- `begin_quality_usage_attempt` and `record_quality_usage_observation` use `quality_operation_type="synthetic_evaluation"` and `quality_operation_id=execution.id`.
- `usage_purpose="synthetic_semantic_judge"`.
- `quality_operation_item_id=plan.item_id`, with provider/model/profile/fingerprint frozen from the judge snapshot.
- These rows are independent of source `AgentRun` usage.

Designer run authorization and attribution are different:

- `POST /api/agent-evaluations/suites/{suite_id}/designer/drafts` authorizes via `_locked_suite_or_404`, source-run visibility, and agent access.
- It creates an evaluation-only synthetic `AgentRun` with correlation from `build_synthetic_correlation(...)`, then adds `evaluation_designer=True` and `designer_suite_id=str(suite.id)`.
- `quality_usage_reporting` classifies legacy runtime `AIUsage` attached to a designer `AgentRun` as purpose `test_designer` and operation type `quality_designer`.
- The operation id for designer legacy rows is `coalesce(AgentRun.root_run_id, AgentRun.id)`, so the natural per-run lookup key is the designer root/run id, not the suite id.
- There is no explicit `AIUsageAttempt`/quality ledger write for designer calls yet; coverage for designer run spend is therefore legacy/observational. Missing usage rows remain unknown, not zero.

Global reporting already covers both categories:

- `GET /api/reports/usage/breakdown` can filter by `purpose`, provider, model, profile, org, source, and date.
- Its operation breakdown can show `synthetic_evaluation` execution ids, `quality_designer` designer run ids, and explicit semantic judge overhead.
- This global route is platform-admin only, so per-operation routes are still needed for non-admin users reviewing their own test execution/designer spend.

## Proposed API contracts

Add these two read-only endpoints to the existing Agent Evaluation Studio router, not to the recorded router:

1. `GET /api/agent-evaluations/executions/{execution_id}/usage`

   - Authorization: call `_execution_and_suite_or_404(db, user, execution_id)` before aggregation.
   - DB session: use `ReadSnapshotDbSession` so execution load, authorization, and multi-query aggregation share one repeatable-read snapshot.
   - Operation filter: server-selected `quality_operation_type="synthetic_evaluation"`, `quality_operation_id=execution.id`.
   - Scope: `organization` when `suite.org_id` is present, otherwise `platform` for an admin/global execution.
   - Response: existing `QualityUsageBreakdownResponse`.
   - Query: `limit` and `offset` only, matching recorded usage. No arbitrary operation id, purpose, provider, or date filters on this operation endpoint.
   - Semantics: response includes synthetic source run spend (`simulation`) and semantic judge overhead (`synthetic_semantic_judge`) for that execution. It does not rebook production source run spend.

2. `GET /api/agent-evaluations/designer-runs/{run_id}/usage`

   - Authorization: load the `AgentRun` by id; require `trigger_type == "evaluation_synthetic"`, `correlation["evaluation_designer"]` to be an actual JSON boolean `true`, and a valid `designer_suite_id` UUID.
   - Accept only the root designer run itself: reject a child run whose `root_run_id` points elsewhere; do not redirect from child to root.
   - Require exact NULL-safe tenant equality between `AgentRun.org_id` and `AgentEvaluationSuite.org_id`. A run with `org_id=NULL` and tenant suite, or tenant run and global suite, fails closed.
   - Load the referenced `AgentEvaluationSuite` and apply the same suite access gate as designer admission/read paths: `_scope_check(user, suite.org_id)` and current baseline visibility. Because the suite baseline is mutable, also authorize the original designer run's `agent_id` live through the canonical AgentRun visibility policy; missing/deleted/private original run fails closed for non-admin.
   - Unknown, unauthorized, malformed, non-root, or non-designer run returns 404.
   - DB session: use `ReadSnapshotDbSession` so run load, suite/current-agent authorization, original-run authorization, and aggregation share one repeatable-read snapshot.
   - Operation filter: `quality_operation_type="quality_designer"`, `quality_operation_id=run.id` from the loaded root designer run.
   - Scope: `organization` when the suite/run org is present, otherwise `platform` for a valid admin/global designer run.
   - Response: existing `QualityUsageBreakdownResponse`.
   - Query: `limit` and `offset` only.
   - Semantics: response exposes model spend of that designer AgentRun. Current coverage is legacy/observational unless/until designer calls are migrated to explicit `AIUsageAttempt`; absence of usage rows is unknown, not a proof of zero cost.

Do not add a route for arbitrary `quality_operation_type/id`. The server must derive the operation filter from an already-authorized domain object.

## Proposed CLI contracts

Add two commands under the existing `agent-tests` group:

1. `bifrost agent-tests usage EXECUTION_ID [--limit N] [--offset N] [--json]`

   - Calls `/api/agent-evaluations/executions/{execution_id}/usage`.
   - JSON emits the full `QualityUsageBreakdownResponse`.
   - Human output should reuse the compact usage formatter pattern from `usage report`: totals plus by-purpose and provider/model lines. The key user value is seeing `simulation` versus `synthetic_semantic_judge` and provider/model costs.

2. `bifrost agent-tests designer-usage RUN_ID [--limit N] [--offset N] [--json]`

   - Calls `/api/agent-evaluations/designer-runs/{run_id}/usage`.
   - JSON emits the full response.
   - Human output uses the same compact totals/breakdown formatter, normally showing `test_designer`.

After command changes, regenerate `.claude/skills/bifrost-build/generated/*.md` with `api/scripts/skill-truth/generate.py` through the Dockerized API image, and run the skill appendix freshness test via `./test.sh` in the final implementation packet.

## Tests

Focused service/router tests:

- Synthetic execution usage route happy path: seed an authorized suite/execution plus one legacy synthetic source `AIUsage` on an `AgentRun` correlated with that execution and one explicit `AIUsage`/attempt for `synthetic_semantic_judge`. Assert the route returns both purposes and separate provider/model rows.
- Synthetic execution usage with no ledger rows must still report historical runtime coverage unknown rather than implying a complete zero-cost evaluation.
- Synthetic execution usage denies cross-tenant/non-readable execution with the same 404 behavior as existing execution readers, and isolates operation ids from other executions.
- Designer usage route happy path: seed a suite and root designer `AgentRun` with boolean `evaluation_designer=True`, matching `designer_suite_id`, and visible original run/agent; attach one `AIUsage` row to that run. Assert operation-scoped report returns `test_designer` and the expected totals.
- Designer usage with no ledger rows must still report historical runtime coverage unknown rather than complete zero cost.
- Designer usage route denies malformed/string designer flag, non-designer run, child run, missing suite, exact-null-org mismatch, cross-tenant run, private original run, and changed suite baseline that would otherwise hide the original authorization problem.
- No model calls and no duplicate ledger rows are introduced by either read endpoint.
- Snapshot wiring: either reuse the public report snapshot regression pattern or assert both new route functions type/use `ReadSnapshotDbSession`; a DB concurrency test is only needed if the implementation adds nontrivial route logic around aggregation.

CLI tests:

- `agent-tests usage --json` forwards limit/offset and preserves decimal strings.
- `agent-tests usage` human output includes purpose/provider-model lines for `simulation` and `synthetic_semantic_judge`.
- `agent-tests designer-usage` same JSON and human behavior for `test_designer`.
- Malformed UUID behavior can remain server-side unless the command adds local UUID validation; do not expand CLI validation unless existing command patterns require it.

Contract/freshness tests:

- If no new DTOs are needed, `test_contract_version.py` should not require a fingerprint change because the response reuses `QualityUsageBreakdownResponse` already in the CLI-consumed contract surface.
- Run the skill appendix freshness test after generator output changes.
- Run focused API quality after source changes.

## Known gaps and non-goals

- Designer model calls are still recorded through legacy runtime `AIUsage`, not explicit `AIUsageAttempt` rows. The operation route can report observed spend but cannot prove completeness. Until migrated, these per-operation routes should force `legacy_coverage_unknown=true` for synthetic/designer runtime operation reports, including zero-row reports, so a zero total is not misread as complete zero cost.
- Existing synthetic source run usage may also be legacy runtime usage. The reporting layer can classify it by correlation, but source-run usage completeness still depends on durable runtime usage recording, not this API packet.
- Provider cost can be missing on legacy rows. Reports must surface missing-cost counts and must not coerce unknown cost to zero completeness.
- The API should not add production UI routes/components in this packet.
- The API should not expose arbitrary operation lookups or broaden platform-admin global reporting.
