# Quality Scheduling Proposal

Status: **UNAPPROVED proposal**. This is reconnaissance for the next Phase 3 scheduling slice. It is not an implementation packet.

## Existing patterns to reuse

`docs/architecture/platform-jobs.md` makes `PlatformJob` the canonical lifecycle for durable work. Scheduled quality work must enqueue the existing review and evaluation platform jobs and use the generic platform-job status, cancellation, notifications, retry policy, and runner pool. It must not add a review/evaluation-specific worker, status table, status endpoint, WebSocket channel, or polling loop.

The scheduler already has two reusable trigger patterns:

- `api/src/scheduler/main.py` runs APScheduler only on the elected scheduler leader through `Scheduler._run_scheduled_task(...)`. Recurring callbacks can either do short work directly or return `ScheduledTaskOutcome(platform_job_id=...)` after enqueueing a durable job.
- `api/src/jobs/platform/system_maintenance.py` shows the recurring-to-PlatformJob pattern: `enqueue_automatic_oauth_refresh`, `enqueue_automatic_webhook_renewal`, `enqueue_automatic_solution_update_check`, and `enqueue_automatic_file_index_reconciliation` all call `enqueue_platform_job(...)` with a stable dedupe/resource lock and return `ScheduledTaskOutcome`.

The workflow schedule implementation is reusable for cron semantics, not for quality domain storage:

- `api/src/models/orm/events.py::ScheduleSource` has `cron_expression`, `timezone`, `enabled`, and `overlap_policy`.
- `api/src/models/enums.py::ScheduleOverlapPolicy` currently defines `skip`, `queue`, and `replace`.
- `api/src/services/cron_parser.py::is_cron_expression_valid` accepts only standard 5-field cron expressions and also requires human-readable parsing. `api/src/services/cron_parser.py::get_schedule_zone` resolves IANA time zones plus the existing `America/Indianapolis` alias. `api/src/jobs/schedulers/cron_scheduler.py::_latest_due_run_utc` uses `croniter` in the configured zone and returns the latest due occurrence only inside the polling window. `process_schedule_sources` ignores disabled schedules and treats `QUEUE`/`REPLACE` as logged `SKIP` behavior today.
- `api/tests/unit/jobs/schedulers/test_cron_scheduler.py` already covers due windows, timezone evaluation, disabled schedules, invalid cron/timezone, and overlap fallback.

Quality execution already has the durable job handlers needed by scheduled triggers:

- Reviews use `api/src/jobs/platform/agent_review.py::AGENT_REVIEW_DEFINITION` with `shared.models.AgentReviewJobPayload(review_run_id=...)`. Execution lives in `api/shared/agent_reviews.py::execute_agent_review_job`.
- Review evidence admission logic already exists in `api/shared/agent_reviews.py::build_review_evidence_input`. It accepts explicit run IDs, reuses recorded evidence loading/redaction, rejects nonterminal selected runs, carries completeness limitations for dimensions that remain unproven, rejects synthetic source runs, enforces the review agent/org, freezes delegated source references, limits to `MAX_REVIEW_RUNS`, and enforces `MAX_REVIEW_INPUT_BYTES`.
- Synthetic evaluation suites use `api/src/jobs/platform/agent_evaluation.py::AGENT_EVALUATION_SUITE_DEFINITION`. Current admission is in `api/src/routers/agent_evaluations.py::_admit_execution_cell`, with active reuse through `_find_reusable_execution(...)` and dedupe material from `api/src/services/agent_evaluations/executions.py::build_dedupe_key`.

## Smallest proposed scheduling contract

Add a quality scheduling admission layer that runs under the elected scheduler and enqueues the existing platform jobs. The scheduler layer owns recurring trigger evaluation and idempotent admission; the review/evaluation job handlers continue to own execution.

There is no generic persisted recurring PlatformJob schedule table today. `api/src/models/orm/scheduler_diagnostics.py::SchedulerTaskState` and `SchedulerTaskRun` track code-registered scheduler tasks for diagnostics, while `ScheduleSource` is workflow event-source storage. The smallest foundation should therefore add a shared recurring PlatformJob trigger storage layer, then use quality-specific admission handlers as its first consumers. The recurring-trigger rows store trigger configuration and immutable admission policy; they are not job lifecycle records. Required fields:

- durable schedule ID
- organization ID
- operation type: `agent_review` or `agent_evaluation_suite`
- operation ID: review definition ID or evaluation suite ID
- operation params JSON validated by the registered trigger definition
- cron expression and timezone, using the same parser/timezone validation as `process_schedule_sources`
- `enabled` boolean; disabled schedules are ignored by the scheduler and never enqueue jobs
- overlap policy, with only `skip` approved initially; `queue` and `replace` must be rejected at create/update time until their behavior is implemented
- durable requester UUID plus requester email/name snapshot for PlatformJob attribution
- review run-selection policy or evaluation matrix policy in validated operation params, described below
- audit timestamps

The implementation should not add feature-local scheduler tables as the first choice. Add a small shared recurring-trigger table plus a shared fire/admission fence table keyed by trigger definition. Quality scheduling can register `agent_review` and `agent_evaluation_suite` trigger definitions that validate operation params and call shared admission. It should not reuse `ScheduleSource` directly: `ScheduleSource` produces workflow `Event`/`EventDelivery` rows for subscribed workflows, while quality schedules must call review/evaluation admission and enqueue PlatformJobs.

## Run selection windows

Scheduled reviews need a stored selector because recurring work cannot rely on caller-supplied explicit run IDs. The initial selector should be intentionally narrow:

- source is terminal production `AgentRun` rows for the reviewed agent and organization
- exclude `trigger_type='evaluation_synthetic'`
- window is `[scheduled_for - lookback, scheduled_for)` in UTC, using the due cron occurrence rather than wall-clock processing time
- use `api/src/models/orm/agent_runs.py::AgentRun.completed_at` as the canonical terminal timestamp; require it to be non-null and order by `completed_at DESC, id DESC` for a stable cap
- cap at the existing review evidence limit of 20 selected runs
- if the selector finds zero eligible runs, record a skipped fire and do not enqueue a review job
- each selected run still goes through `build_review_evidence_input(...)`; revoked access, missing rows, nonterminal selected rows, oversized input, or wrong-agent data fail closed for that fire. Incomplete evidence dimensions on otherwise terminal runs remain in the evidence completeness/limitations payload and do not automatically reject admission.

Scheduled evaluation suites do not need source-run selection. They need a frozen matrix policy equivalent to current batch admission: suite version at trigger time, zero or more candidate IDs, one or more provider profile IDs, and repetitions override. The scheduler should call the same `plan_matrix_cells(...)` fan-out used by the batch API, admit one PlatformJob-backed execution per `(candidate-or-baseline, profile)` cell, persist matrix membership, and reuse the same authorization, snapshot freezing, accepted/enabled case checks, quota checks, and PlatformJob enqueue behavior as `_admit_execution_cell(...)`. A single-cell helper is insufficient for the scheduled suite expectation.

## Idempotency and overlap dedupe

Active PlatformJob dedupe is necessary but not sufficient for recurring schedules: once a job reaches terminal state, the same due occurrence could be admitted again after scheduler failover or retry. Add a narrow shared recurring-trigger fire/admission fence with strict unique `(schedule_id, scheduled_for)` before creating domain rows or enqueueing a job. Do not include `selection_fingerprint` in the unique key; allowing a second row for the same due occurrence would duplicate spend. This is trigger provenance, not a job status system.

For each due occurrence:

1. The scheduler computes `scheduled_for` from the cron expression/timezone using the same due-window approach as `process_schedule_sources`.
2. In one database transaction, it inserts/claims the schedule fire fence. If the fire already exists, it does not admit duplicate work.
3. It checks overlap for the same schedule. If an active PlatformJob/domain run already exists for that schedule, `skip` records the fire as skipped with reason `overlap` and does not enqueue a second job.
4. It resolves the requester and target, selects/freeze inputs, creates the review run or evaluation matrix/executions, and enqueues the existing PlatformJob rows in the same admission transaction.
5. If review run selection returns zero eligible terminal source runs, it records the fire as skipped with reason `zero_source_runs` in that same transaction and does not enqueue a PlatformJob.
6. It stores the platform job ID(s) and domain run/execution/matrix ID(s) on the fire record for audit/navigation only. PlatformJob remains the authoritative status.

Review scheduled dedupe material should include schedule ID, scheduled_for, review ID/version, selected run IDs, request fingerprint, and requester UUID. Evaluation scheduled dedupe material should include schedule ID, scheduled_for, suite ID/version, candidate IDs, profile IDs, repetitions, and requester UUID. On-demand terminal reruns remain possible because their admission does not use the schedule fire uniqueness key.

## Requester and permission rules

A schedule must store a durable requester UUID and a display snapshot. At trigger time, the scheduler must load that user and validate current permissions before admitting work. If the user is deleted, inactive, in a different tenant, or no longer authorized for the review, suite, baseline agent, candidate, model profile, or selected source runs, the fire fails closed and no model call or PlatformJob is created.

This requires one shared admission seam for each quality target:

- Review scheduling should factor the on-demand review admission path so both HTTP and scheduler callers use the same review/version/profile snapshot, `build_review_evidence_input(...)`, request fingerprint, quality accounting identity, and `AGENT_REVIEW_DEFINITION` enqueue.
- Evaluation scheduling should move the reusable parts of `_authorize_matrix_cell(...)`, `_find_reusable_execution(...)`, `_admit_execution_cell(...)`, matrix membership creation, and `plan_matrix_cells(...)` orchestration out of `api/src/routers/agent_evaluations.py` into shared service code. The scheduler should not import router functions or bypass current baseline/candidate/profile authorization.

Scheduled jobs should use the requesting user identity in `PlatformJob.requested_by_*` fields. There is no approved system-superuser fallback for private data.

## Disabled schedules and deleted targets

Disabled schedules are skipped before permission checks and should not enqueue jobs. If the target review/suite/agent/profile/candidate is missing or disabled at trigger time, the scheduler should record a failed/skipped fire with a short reason and no PlatformJob. It should not resurrect deleted targets or fall back to frozen private data.

Existing domain delete semantics should decide whether schedule rows cascade with their target or become inert. Either way, an enabled schedule with a missing target must fail closed before source evidence is read or any provider call is attempted.

## Proposed files for an implementation packet

Likely source files, subject to final approval:

- `api/src/models/orm/...`: new shared recurring PlatformJob trigger ORM record and strict `(schedule_id, scheduled_for)` fire/admission fence record
- `api/alembic/versions/...`: additive migration for schedule/fire records and constraints
- `api/shared/...`: registered recurring-trigger definitions plus shared admission helpers for review schedules and extracted evaluation matrix admission helpers
- `api/src/jobs/schedulers/...`: `process_recurring_platform_job_triggers(...)` leader callback, or equivalently named shared processor, that computes due fires and calls registered admission handlers
- `api/src/scheduler/main.py`: register the shared recurring PlatformJob trigger processor through `_run_scheduled_task(...)`
- `api/src/scheduler/registry.py`: scheduler diagnostics definition for the new shared processor
- `api/tests/unit/jobs/schedulers/...`: due window, disabled schedule, duplicate fire, overlap skip, lost requester permission, and zero eligible run coverage

No UI, CLI, route, or status transport is required for the scheduling foundation. Later API/CLI work can expose create/update/disable/list operations after the storage and scheduler contract is approved.

## Open contract gaps before approval

- Exact generic table shape and registry API for shared recurring PlatformJob triggers. Source inspection found no existing generic persisted recurring PlatformJob schedule storage; this proposal recommends adding that shared foundation instead of feature-local schedule tables.
- Whether skipped/failed fires need a bounded retention policy in the first slice. The proposal requires a fire fence for idempotency, but does not approve a user-facing history UI or unbounded detail API.
- Whether the first implementation exposes only disable/update APIs or also exposes fire receipts. The storage must preserve skipped `zero_source_runs` and `overlap` receipts internally either way.
- Whether later `QUEUE`/`REPLACE` support belongs in the shared recurring-trigger registry or a future per-operation policy. The first scheduler behavior should reject both and support `SKIP` only.
