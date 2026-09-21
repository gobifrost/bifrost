# Quality usage accounting handoff

Status: architectural proposal. The primary has approved the bounded foundation in `2026-09-20-quality-usage-foundation-approved.md`; that packet supersedes conflicting schema, placement, and lifecycle suggestions below. Reporting and caller integration still need their own bounded packets. Do not execute this entire proposal as one assignment.

## Scope

Testing, recorded semantic judging, reviews, and future quality/designer operations must be accounted in the same `AIUsage` system as production agent and workflow usage. The accounting must support per-operation and global provider/MSP breakdowns, token/cache/cost totals, explicit coverage gaps for unknown attempts, and durable provenance without attaching overhead costs to the production source runs being evaluated.

This packet covers the shared accounting contract only. UI, CLI presentation, recorded semantic judge behavior, and review scheduling remain separate packets, except where this accounting contract defines acceptance for those later surfaces.

## Existing source inventory

- `api/src/models/orm/ai_usage.py`
  - Defines `AIUsage` and `AIModelPricing`.
  - Current `AIUsage` requires one of `execution_id`, `conversation_id`, or `agent_run_id`.
  - Current tokens/cache/cost columns are already the canonical global usage ledger and must remain the ledger.
- `api/src/services/ai_usage_service.py`
  - `record_ai_usage()` canonicalizes provider/model, calculates cost, inserts `AIUsage`, invalidates caches, and logs failures without raising.
  - This behavior is correct for legacy best-effort call paths and should remain unchanged for existing callers.
- `api/src/routers/usage_reports.py`
  - `GET /api/reports/usage` is the current global admin report.
  - It aggregates existing `AIUsage` rows by source, trend, workflow, conversation, agent, and organization.
  - It is platform-admin scoped today and should remain the global/admin accounting surface.
- `api/src/models/contracts/ai_usage.py`
  - Holds the report DTOs consumed by API clients.
  - Existing fields should keep their semantics; quality accounting should add new breakdown fields.
- `api/src/routers/agent_runs.py` and execution detail routes
  - Per-run detail already reads `AIUsage` by `agent_run_id` / `execution_id` and returns token/cache/cost totals.
- `api/src/services/agent_evaluations/executions.py`
  - Existing synthetic semantic judge calls persist `judge_usage` JSON inside assertion results but do not insert `AIUsage`.
  - This is the current gap for synthetic judge global accounting.
- `api/shared/agent_recorded_admission.py`
  - Recorded evaluation admission/result handling already owns domain authorization and lease-fenced result writes.
  - Recorded semantic judge accounting must integrate through shared accounting and must not add a parallel job lifecycle.

## Approved direction

Extend `AIUsage`. Do not create a separate cost ledger. Do not add feature-specific usage tables for recorded evaluations, reviews, designers, or synthetic judges.

Add a stable generic quality operation identity to `AIUsage`:

- `quality_operation_type`: server-owned string enum-like value, for example `recorded_evaluation`, `synthetic_evaluation`, `agent_review`, `quality_designer`.
- `quality_operation_id`: server-owned durable operation id. It points to the domain object by convention and authorization, not by a feature-specific FK.
- `quality_operation_item_id`: optional stable child reference for assertion/case/review-item granularity.
- `usage_purpose`: stable purpose string, for example `recorded_semantic_judge`, `synthetic_semantic_judge`, `test_designer`, `agent_review_judge`.
- `frozen_profile`: JSON snapshot of the model/profile identity used for the quality call. It must include non-secret identity needed for audit and grouping, such as profile id/name when present, provider, model, endpoint family, config fingerprint, and relevant generation options.
- `platform_job_id`: optional link to the shared `PlatformJob` that caused the call.
- `idempotency_key`: required for new strict quality writes and globally unique.

Existing legacy rows stay valid and unchanged. Existing `execution_id`, `conversation_id`, and `agent_run_id` semantics stay intact.

Do not add per-feature foreign key columns to `AIUsage`. Domain authorization must resolve through `quality_operation_type` and `quality_operation_id` in the relevant domain router/service.

Do not attach quality overhead to the production source run that is being evaluated. A recorded or review judge can reference the source run as evidence in its domain result, but the `AIUsage` row must belong to the quality operation.

Existing synthetic `AgentRun` usage should be classified once in reporting when the run is synthetic/evaluation-owned. Do not copy those rows into new quality rows. Use the existing row as the accounting fact and add report classification/relationship metadata around it.

## Proposed schema

Future implementation should add one migration that extends `ai_usage`:

```text
ai_usage
  quality_operation_type        nullable varchar
  quality_operation_id          nullable uuid
  quality_operation_item_id     nullable varchar
  usage_purpose                 nullable varchar
  frozen_profile                nullable jsonb
  platform_job_id               nullable uuid references platform_jobs(id) on delete set null
  idempotency_key               nullable varchar unique
  attempt_id                    nullable uuid, only if shared call-attempt provenance is approved
```

Indexes:

```text
idx_ai_usage_quality_operation
  (quality_operation_type, quality_operation_id, timestamp)

idx_ai_usage_quality_purpose
  (organization_id, usage_purpose, provider, model, timestamp)

idx_ai_usage_platform_job
  (platform_job_id)

uq_ai_usage_idempotency_key
  (idempotency_key)
  where idempotency_key is not null
```

The existing context check should become:

```text
execution_id is not null
or conversation_id is not null
or agent_run_id is not null
or (quality_operation_type is not null and quality_operation_id is not null)
```

`idempotency_key` is nullable only for legacy rows. New strict quality usage writes must always provide it.

## Strict quality usage writer

Keep `record_ai_usage()` best-effort for legacy callers. Add a strict writer for new quality calls, either in `api/src/services/ai_usage_service.py` or in a small adjacent module such as `api/src/services/quality_usage.py`.

The strict writer should:

- require `organization_id`, `quality_operation_type`, `quality_operation_id`, `usage_purpose`, `frozen_profile`, and `idempotency_key`;
- accept observed response usage even when the semantic judgment is malformed or unparsable;
- canonicalize provider/model and calculate cost using existing local pricing/canonicalization helpers;
- avoid network pricing discovery while a result lock or lease fence is held;
- return the existing row on duplicate `idempotency_key`;
- raise or return a typed failure for validation/persistence failures instead of swallowing them;
- invalidate the same report caches that normal `AIUsage` writes invalidate;
- never infer zero usage for a call whose response usage is missing or ambiguous.

The implementation may refactor shared local pricing logic out of `record_ai_usage()` so both writers use one calculation path. That refactor must not change legacy best-effort behavior.

## Call-attempt provenance and unknown usage

Unknown or ambiguous attempts must appear as coverage gaps in both global reports and per-operation reads. They must not become invented zero-cost usage.

Recommended architecture, not yet approved: add a shared durable call-attempt provenance table, for example `ai_usage_attempts`. This table is not a job lifecycle, status endpoint, retry queue, or worker system. It is a ledger of model-call intent and outcome used to make accounting coverage auditable.

Proposed fields:

```text
ai_usage_attempts
  id                         uuid primary key
  organization_id             uuid not null
  user_id                     uuid nullable
  quality_operation_type      varchar not null
  quality_operation_id        uuid not null
  quality_operation_item_id   varchar nullable
  usage_purpose               varchar not null
  platform_job_id             uuid nullable references platform_jobs(id) on delete set null
  frozen_profile              jsonb not null
  request_fingerprint         varchar not null
  idempotency_key             varchar not null unique
  status                      varchar not null
  ai_usage_id                 uuid nullable references ai_usage(id) on delete set null
  started_at                  timestamptz not null
  completed_at                timestamptz nullable
  error_code                  varchar nullable
  error_message               text nullable
```

Allowed statuses should be small and accounting-specific, for example:

- `started`
- `accounted`
- `failed_before_response`
- `response_without_usage`
- `ambiguous_after_call`
- `cancelled_before_call`

The table gives recorded semantic judges and future reviewers a durable pre-call marker. The marker is written under the domain lease/result fence before the model call. The model request runs outside the write transaction. The final result write reacquires the domain fence. If the final fence is stale or cancelled, the caller may still record observed usage only when the durable pre-call marker matches the completed response. That accounting write has no authority to write a stale result, update assertion status, or make another model call.

Alternative architecture: use only existing domain markers such as recorded assertion outcomes. This is likely sufficient for the first recorded semantic judge but does not give shared reporting a reliable way to show unknown attempts across reviews, designers, and synthetic judges. If the primary owner rejects the shared table, each domain must expose equivalent attempt coverage to the report service before implementation starts.

Decision required before implementation: approve shared `ai_usage_attempts` or require each domain to provide equivalent durable attempt markers. Do not implement both.

## Idempotency and failure semantics

Every new quality call needs two idempotency layers:

1. Attempt idempotency, keyed by the durable pre-call marker.
2. Usage idempotency, keyed by the `AIUsage.idempotency_key`.

If a retry sees an existing completed `AIUsage` row for the same `idempotency_key`, it returns that row and must not create a duplicate.

If a retry sees a started attempt with no response usage, it must not call the model again unless the domain operation explicitly owns retry semantics and the billing ambiguity is accepted by that domain. For recorded semantic judges, the accepted direction is to avoid billable retry; a started-without-result attempt should be reported as uncertain/error.

Malformed judgment is not an accounting failure. If the provider returned token/cache/cost usage, persist that usage and let the domain result record the malformed judgment separately.

Provider/model errors before a response do not create `AIUsage`. They create or update an attempt gap.

Missing provider usage does not create a zero-cost row. It creates or preserves an attempt gap with enough provenance to show that the cost is unknown.

Cancellation and stale verdict fences do not erase observed usage. They only prevent stale result writes. Observed usage can be persisted after cancellation/staleness only when tied to a validated pre-call marker.

## Synthetic AgentRun classification

Existing designer/synthetic execution paths may already record usage against an `AgentRun`. Those rows remain the accounting source of truth.

Reporting should classify synthetic evaluation/designer `AgentRun` rows into quality/testing breakdowns by joining to the existing `AgentRun` metadata, such as trigger type and evaluation correlation, instead of duplicating rows. The report may annotate that a quality operation has descendant synthetic runs and include their costs in operation totals when the domain relationship is durable.

Production source runs being evaluated must not receive the overhead cost. Reports can show source-run relationships as metadata only.

## Report contract

Preserve current `GET /api/reports/usage` fields and source semantics. Add fields; do not reinterpret existing totals without an explicit migration note.

Add global/admin breakdowns:

- by provider/model with input/output/cache/cost totals and call counts;
- by MSP/organization with the same provider/model totals;
- by quality purpose;
- by quality operation type;
- by quality operation id where the requesting admin scope permits it;
- coverage gaps from attempts with missing/ambiguous usage.

Existing `source=all` should include all `AIUsage` rows, including quality rows. Existing `source=agents` should continue to mean agent-run usage. If new source filters are added, use additive values such as `quality` or `testing`; do not change the meaning of `executions`, `chat`, or `agents`.

Per-operation reads should be owned by domain routers/services and use domain authorization. For example, recorded evaluation detail may include a usage summary only after the requester is authorized to read that recorded evaluation. Global usage reports remain the existing platform-admin surface.

Suggested additive DTOs in `api/src/models/contracts/ai_usage.py`:

```text
UsageProviderBreakdown
  provider
  model
  input_tokens
  output_tokens
  cache_read_tokens
  cache_write_tokens
  cost
  provider_cost
  call_count

UsagePurposeBreakdown
  purpose
  provider_breakdown[]
  totals

QualityOperationUsage
  operation_type
  operation_id
  purpose
  platform_job_id
  provider_breakdown[]
  totals
  known_call_count
  ambiguous_attempt_count

UsageCoverageGap
  operation_type
  operation_id
  operation_item_id
  purpose
  attempt_id
  status
  reason
  started_at
```

Use existing totals DTOs where practical rather than inventing parallel token/cost shapes.

## File ownership for implementation

Expected source files for the implementation packet:

- `api/src/models/orm/ai_usage.py`
  - Add quality operation columns and, if approved, `AIUsageAttempt`.
- `api/alembic/versions/*`
  - Add migration for nullable quality fields, indexes, context check update, and optional attempt table.
- `api/src/services/ai_usage_service.py`
  - Keep existing `record_ai_usage()` behavior.
  - Extract shared local canonicalization/pricing helper if needed.
- `api/src/services/quality_usage.py` or `api/src/services/ai_usage_service.py`
  - Add the strict quality writer and attempt helpers.
- `api/src/models/contracts/ai_usage.py`
  - Add provider/MSP/purpose/profile/coverage DTOs.
- `api/src/routers/usage_reports.py`
  - Add additive global report breakdowns and attempt gap aggregation.
- `api/shared/agent_recorded_admission.py`
  - Future recorded semantic judge integration should write attempt markers and strict usage through the shared helper.
- `api/src/services/agent_evaluations/executions.py`
  - Existing synthetic semantic judges should use the shared strict writer for future calls, while legacy historical JSON-only results remain unchanged.
- Domain routers for recorded evaluations/reviews
  - Add per-operation usage summaries only behind existing domain authorization.

Do not place shared business logic in routers. The strict writer and attempt semantics belong in shared service code.

## Required tests for implementation packet

Use focused tests only for the changed surface.

Migration/model tests:

- legacy `AIUsage` rows with `execution_id`, `conversation_id`, or `agent_run_id` still insert;
- new quality `AIUsage` rows insert with `quality_operation_type` and `quality_operation_id`;
- rows with no legacy context and no quality operation still fail;
- duplicate quality `idempotency_key` is rejected or returns the existing row through the strict writer;
- attempt rows represent started/accounted/ambiguous states if the shared table is approved.

Strict writer tests:

- records provider/model/tokens/cache/cost for a well-formed observed response;
- records observed usage when the judgment payload is malformed;
- returns existing usage for duplicate idempotency key;
- does not call network pricing discovery under the strict path;
- does not create a zero-cost row when response usage is missing.

Fence/cancellation tests:

- stale domain result fence prevents result write but permits accounting when a matching pre-call marker exists;
- stale/cancelled operation without a matching marker cannot write usage;
- started-without-result attempt appears as a coverage gap.

Report tests:

- global admin report includes provider/model and organization/MSP breakdowns for quality rows;
- existing report totals and existing source filters keep their old meaning;
- quality source/purpose filters include quality rows without double-counting synthetic `AgentRun` usage;
- synthetic designer/evaluation `AgentRun` usage is classified once and descendant relationships are annotated rather than copied;
- coverage gaps appear globally and per operation.

Authorization tests:

- per-operation usage summary uses the domain object's read authorization;
- global report remains platform-admin scoped.

Contract tests:

- run the DTO/CLI contract tripwire if report DTOs change.
- regenerate TypeScript types only in the implementation packet that changes API schemas.

## Acceptance criteria

- All new quality LLM calls have either an `AIUsage` row with provider/model/token/cache/cost data or a durable coverage gap explaining why usage is unknown.
- Global reports can answer cost/tokens/cache by provider/model, purpose, operation type, and MSP/organization.
- Per-operation reads can answer the same breakdown for the authorized domain operation.
- Existing production source runs do not receive quality overhead costs.
- Existing legacy `AIUsage` rows, source filters, and report fields retain their current meaning.
- Recorded semantic judges, synthetic judges, review judges, and future designers use the same strict accounting writer.
- No code path invents zero cost for unknown or ambiguous usage.
- Accounting can persist observed usage after a stale/cancelled result fence only when tied to a validated pre-call marker, and that write cannot update stale domain results.

## Open decisions

The shared call-attempt provenance table is recommended but not approved by this packet. Implementation must not start until the primary owner chooses one of:

1. shared `ai_usage_attempts` as the durable, cross-domain coverage-gap source; or
2. domain-specific markers with a required shared read adapter for report aggregation.

The exact enum values for `quality_operation_type`, `usage_purpose`, and attempt `status` should be frozen in the implementation packet before migration.
