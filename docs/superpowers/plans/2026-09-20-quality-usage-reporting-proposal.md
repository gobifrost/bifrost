# Quality usage reporting proposal

Status: proposal only. This packet defines the smallest additive reporting contract on top of the accepted shared quality usage foundation. It does not approve UI implementation, CLI implementation, caller integration, or any new job/polling lifecycle.

## Current surfaces to preserve

- Global admin usage report: `GET /api/reports/usage` in `api/src/routers/usage_reports.py`.
- Existing query spellings:
  - `start_date`
  - `end_date`
  - `source=executions|chat|agents|all`
  - `org_id`
- Existing response fields in `UsageReportResponse`:
  - `summary`
  - `trends`
  - `by_workflow`
  - `by_conversation`
  - `by_agent`
  - `by_organization`
  - knowledge storage fields
- Existing client hook: `client/src/services/usage.ts::useUsageReport(startDate, endDate, source, orgId)`.
- Existing UI page: `client/src/pages/UsageReports.tsx`, with tabs `All`, `Executions`, `Chat`, `Agents`.
- There is no current `api/bifrost` CLI command for usage reports. Do not invent a CLI rename or change existing commands in this reporting packet.

The existing `source` dimension must remain the runtime source filter. Do not reinterpret `agents` to include quality overhead, and do not remove or rename current fields.

## Foundation this proposal builds on

The accepted foundation added:

- `AIUsage.quality_operation_type`
- `AIUsage.quality_operation_id`
- `AIUsage.quality_operation_item_id`
- `AIUsage.usage_purpose`
- `AIUsage.profile_id`
- `AIUsage.profile_name`
- `AIUsage.profile_fingerprint`
- `AIUsage.platform_job_id`
- `AIUsage.usage_attempt_id`
- `AIUsageAttempt` with `state=started|observed|unobserved`, `started_at`, `observed_at`, immutable operation identity, provider/model, profile identity, org/user, and optional platform job.

New quality `AIUsage.timestamp` is the attempt `started_at`, so late accounting settlement reports in the original call window. `AIUsageAttempt.observed_at` is the settlement time.

## Reporting model

Add a new mutually exclusive reporting dimension named `purpose`. It is separate from the existing `source` filter.

Recommended purpose buckets:

- `runtime_execution`
- `runtime_chat`
- `runtime_agent`
- `recorded_semantic_judge`
- `synthetic_semantic_judge`
- `agent_review`
- `test_designer`
- `legacy_unknown`

For quality rows, `purpose` comes from `AIUsage.usage_purpose`.

For legacy runtime rows, classify purpose on read:

- `execution_id is not null` → `runtime_execution`
- `conversation_id is not null` → `runtime_chat`
- `agent_run_id is not null` and not synthetic/designer-classified → `runtime_agent`

Rows that cannot be safely classified remain `legacy_unknown`; do not infer historical quality overhead from old rows without durable provenance.

Additive query parameters may include:

```text
purpose=runtime_execution|runtime_chat|runtime_agent|recorded_semantic_judge|synthetic_semantic_judge|agent_review|test_designer|legacy_unknown|all
provider=<provider>
model=<model>
profile_id=<uuid>
profile_fingerprint=<string>
quality_operation_type=<type>
quality_operation_id=<uuid>
```

Keep `source` unchanged. A `source=all&purpose=recorded_semantic_judge` request is valid. A `source=agents` request should still mean rows with `agent_run_id`; it should not include quality rows unless a future explicit product decision adds a separate `source=quality`.

## Cost subtotals and coverage

Every aggregate that reports money should distinguish:

- `observed_provider_cost`: sum of `provider_cost` where the provider supplied exact cost.
- `estimated_cost`: sum of rows where `provider_cost is null` and `cost is not null`.
- `known_cost`: `observed_provider_cost + estimated_cost`.
- `missing_cost_call_count`: observed `AIUsage` rows whose `cost is null`.
- `started_attempt_count`: attempts in `started` state with no usage row.
- `unobserved_attempt_count`: attempts in `unobserved` state with no usage row.
- `legacy_coverage_unknown`: boolean/count marker for legacy paths where no `AIUsageAttempt` existed, so absence of gaps does not prove completeness.

Token/cache totals should only aggregate observed `AIUsage` rows. Do not create a denominator from attempts. Do not report cache hit rates unless the denominator is an observed token count from actual rows.

For observed quality rows:

- input/output/cache tokens are known because the strict writer requires finite nonnegative counts;
- `provider_cost` is exact provider spend when present;
- `cost` is either provider cost, a complete local estimate, or null.

For attempts without usage rows:

- report count and reason/status only;
- never add zero tokens or zero cost.

## Global report extension

Keep `UsageReportResponse` additive. Add fields rather than changing current fields:

```text
purpose_breakdown: list[UsagePurposeBreakdown]
provider_breakdown: list[UsageProviderBreakdown]
profile_breakdown: list[UsageProfileBreakdown]
quality_operation_breakdown: list[QualityOperationUsageBreakdown]
coverage: UsageCoverageSummary
```

Place new DTOs in `api/shared/models.py` per repo rule. Existing `api/src/models/contracts/ai_usage.py` may import and re-export/use those shared models in `UsageReportResponse` so the OpenAPI response remains generated from the existing route contract.

Shared DTO shape should reuse one totals object:

```text
QualityUsageTotals
  input_tokens
  output_tokens
  cache_read_tokens
  cache_write_tokens
  observed_provider_cost
  estimated_cost
  known_cost
  missing_cost_call_count
  call_count
  duration_ms

UsageProviderBreakdown
  provider
  model
  purpose
  organization_id
  organization_name
  totals

UsagePurposeBreakdown
  purpose
  totals
  observed_attempt_count
  started_attempt_count
  unobserved_attempt_count

UsageProfileBreakdown
  profile_id
  profile_name
  profile_fingerprint
  provider
  model
  purpose
  totals

QualityOperationUsageBreakdown
  quality_operation_type
  quality_operation_id
  usage_purpose
  organization_id
  profile_id
  profile_name
  profile_fingerprint
  provider
  model
  totals
  started_attempt_count
  unobserved_attempt_count
  latest_observed_at

UsageCoverageSummary
  started_attempt_count
  unobserved_attempt_count
  missing_cost_call_count
  legacy_coverage_unknown
```

Keep global operation breakdown bounded, for example top 50 by known cost plus gap counts. Do not dump all attempts globally. If detailed attempt browsing is needed, add a paginated operation-detail endpoint owned by the domain surface.

## Per-operation authorized reporting

Per-operation usage reads should be owned by the domain service/router that already authorizes the operation. Do not expose a generic unauthenticated quality-operation lookup.

Recommended shared helper in `api/shared/quality_usage_reporting.py`:

```text
summarize_quality_operation_usage(
  session,
  operation_type,
  operation_id,
  *,
  purpose=None,
  limit_items=100,
  offset_items=0,
) -> QualityOperationUsageSummary
```

The helper should return:

- operation totals;
- provider/model/profile breakdown;
- item breakdown by `quality_operation_item_id`;
- bounded coverage gaps from `AIUsageAttempt`;
- pagination metadata for item/gap details.

Domain routers call this only after domain authorization:

- recorded evaluation detail/results surface for `quality_operation_type=recorded_evaluation`;
- synthetic evaluation execution/detail surface for `quality_operation_type=synthetic_evaluation`;
- test designer run/suite surface for `quality_operation_type=quality_designer`;
- future review surface for `quality_operation_type=agent_review`.

This helper must not create jobs, status rows, WebSocket events, polling contracts, or caller integrations.

## Existing synthetic AgentRun classification

Some existing synthetic/designer work records usage as `AIUsage.agent_run_id`. Do not duplicate those rows into quality rows.

Classify synthetic runtime rows once on read when:

- `AgentRun.trigger_type == "evaluation_synthetic"`, or
- `AgentRun.correlation["evaluation_mode"] == "evaluation_synthetic"`.

Then split:

- designer rows when `AgentRun.correlation["evaluation_designer"] == true` → `test_designer`;
- other synthetic evaluation rows → `synthetic_semantic_judge` only when the row is actually judge overhead; otherwise classify as synthetic runtime execution for the evaluation operation.

Descendant runs should be included once when they are durable descendants of a synthetic parent and carry the same synthetic correlation lineage. Use the existing `AgentRun` parent/root/correlation relationships. Do not also count the source production run being evaluated.

Never sum historical production source-run runtime into testing spend. Source runs may be displayed as related evidence in the domain UI, but their old `AIUsage` remains under its original runtime purpose.

Legacy synthetic rows without enough correlation should remain `legacy_coverage_unknown` or runtime agent usage; do not promote them into testing spend by heuristic.

## Query and aggregation placement

Business logic belongs in shared code, not in the router.

Recommended file ownership for implementation:

- `api/shared/models.py`
  - Add shared additive DTOs for report breakdowns and coverage.
- `api/shared/quality_usage_reporting.py`
  - Add aggregation helpers for global quality usage and authorized per-operation summaries.
- `api/src/models/contracts/ai_usage.py`
  - Import/use additive shared DTOs in `UsageReportResponse`.
- `api/src/routers/usage_reports.py`
  - Keep route thin; call shared aggregation helper and preserve existing summary/trend/table behavior.
- Recorded/synthetic/designer domain routers
  - Later packets only: add per-operation usage summaries after domain auth.
- `client/src/services/usage.ts` and `client/src/pages/UsageReports.tsx`
  - Later UI packet only: consume additive fields. No UI implementation in this packet.
- `api/bifrost/commands/*`
  - No current usage report CLI command. Later CLI packet may add one, but it must respect existing REST query spellings.

## Acceptance tests for implementation packet

Focused backend tests:

- existing `source=executions|chat|agents|all` filters keep current results for legacy rows;
- quality rows appear in `source=all` totals but not in `source=agents`;
- `purpose` filter is mutually exclusive and additive;
- provider/model/profile/org/date breakdowns aggregate observed rows correctly;
- provider-cost rows contribute to `observed_provider_cost`;
- estimated rows contribute to `estimated_cost`;
- `cost is null` rows increment `missing_cost_call_count` without becoming zero cost;
- started/unobserved attempts appear as coverage gaps without token/cost totals;
- legacy paths report coverage unknown rather than complete;
- cache read/write totals are sums only, with no fake hit-rate denominator;
- synthetic/designer `AgentRun` rows are classified once and not duplicated into quality rows;
- source production runs referenced by an evaluation are not counted as testing spend;
- per-operation helper returns only after domain authorization in domain tests.

Contract tests:

- DTO contract/version tripwire if `UsageReportResponse` changes.
- Client type regeneration only in the implementation packet that changes OpenAPI.

No full UI/CLI implementation is required for this reporting contract.

## Open decisions for primary

- Whether to add a new REST `source=quality` filter now or defer. This proposal does not require it.
- Exact treatment of synthetic non-judge evaluation runtime rows: keep separate as synthetic runtime or fold into `synthetic_semantic_judge` only when caller provenance proves the row is judge overhead.
- Whether operation detail should be embedded in existing domain detail responses or exposed as subroutes such as `/usage` under each domain object.
