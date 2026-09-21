# Quality Scheduling Foundation — Approved Bounded Handoff

Primary owns acceptance; executor implements in `durable-agent-platform-backend`, preserving all existing work. S1 (approved below) covers shared recurring-trigger storage, fire fence, review-schedule admission, and scheduler wiring. S2 (approved 2026-09-21) adds evaluation-suite scheduling on the same fence: shared matrix admission extracted from the router plus suite fire admission. No API/CLI/UI in either slice.

## Gap decisions (primary)

1. **Table shape/registry**: shared tables `recurring_platform_job_triggers` + `recurring_trigger_fires`, registry `api/shared/recurring_trigger_registry.py` mapping `operation_type` → validate/admit. Generic, not quality-local. S1 registers `agent_review` only; `agent_evaluation_suite` arrives in S2 with the matrix-admission router extraction.
2. **Retention**: no deleter in S1. Fire rows are the idempotency fence and are retained; retention policy is a documented follow-up, not this slice.
3. **Receipts**: internal only. No routes, CLI, or status transport in S1.
4. **Overlap**: `skip` only; `queue`/`replace` rejected at validation time.

## Ownership

- `api/src/models/orm/recurring_triggers.py`: `RecurringPlatformJobTrigger`, `RecurringTriggerFire`, constraints (skip-only overlap, unique `(trigger_id, scheduled_for)`).
- `api/alembic/versions/20260920_recurring_triggers.py`: additive migration.
- `api/shared/recurring_trigger_registry.py`: operation registry; param validation (cron via `is_cron_expression_valid`, tz via `get_schedule_zone`, skip-only overlap, `agent_review` params: `review_id`, `lookback_days` 1–30 default 7).
- `api/shared/review_schedule_admission.py`: window selection (every TERMINAL_STATUSES state, exclude `evaluation_synthetic`, `completed_at` in `[scheduled_for - lookback, scheduled_for)`, order `completed_at DESC, id DESC`, cap 20) + admission reusing `build_review_evidence_input`, `freeze_review_profile_snapshot`, `review_request_fingerprint`, `enqueue_platform_job` (dedupe includes schedule id + scheduled_for + requester). Requester loaded and validated (real active non-system user; org users require trigger/requester org match; superusers governed by the same `authorize_review_agent` check as HTTP admission). Disabled trigger/target, missing target, zero eligible runs → skipped/failed fire receipt, no job.
- `api/src/jobs/schedulers/recurring_triggers.py`: `process_recurring_platform_job_triggers()` leader callback (due via `_latest_due_run_utc`, fence claim, overlap skip against latest admitted fire's non-terminal job, admit in same transaction). Register in `api/src/scheduler/main.py` + `registry.py` diagnostics.
- Tests: ORM/constraint unit tests; admission/scheduler unit tests (due, disabled, duplicate fire, overlap skip, lost permission, zero runs, missing target). No model calls: seed runs directly, never invoke the executor.

## Non-goals (both slices)

Evaluation-suite scheduling (S2), API/CLI/UI, retention deleter, queue/replace, system requester fallback. No changes to accounting, executor, or existing admission paths.

## S2 — evaluation-suite scheduling (approved 2026-09-21)

- `api/shared/evaluation_matrix_admission.py`: single/batch orchestration moved verbatim from the router (domain errors, same locks/quotas/dedupe/membership); router endpoints are thin translators. Behavior parity proven by the existing matrix/batch suite.
- `agent_evaluation_suite` trigger params: `suite_id`, `candidate_ids` (0–10), `profile_ids` (1–10), `repetitions_override` (1–10); registered in `recurring_trigger_registry.py`.
- `api/shared/suite_schedule_admission.py`: fire admission via the shared batch call inside a savepoint (mid-batch failure rolls back matrix/executions/jobs before the skipped receipt). Fire receipt records the matrix id; `platform_job_id` is the first admitted cell's job as an audit witness — the matrix membership holds every cell. Overlap consults member executions, not the witness job.
- Same requester rule as reviews; unpublished/missing suite, bad candidate/profile, empty cases, and quota all fail closed with no rows.
- Tests admit-then-cancel immediately (no live queued jobs) except the overlap case, which needs one active job across a processor run — the same exposure long accepted by on-demand admission tests, with cancel-then-delete cleanup.

## S3 — schedule API/CLI (approved 2026-09-21)

Trigger CRUD over the S1 storage; fires read-only. No schedule-status transport (PlatformJob stays canonical), no retention changes.

- DTOs in `shared/models.py`: `RecurringTriggerCreate` (operation_type, operation_id, operation_params, cron_expression, timezone, overlap_policy default skip, organization scoping like reviews: non-admin fixed to caller tenant incl. explicit-global rejection; admin may target an existing tenant or global null), `RecurringTriggerUpdate` (cron/timezone/enabled/operation_params only — operation identity immutable), `RecurringTriggerPublic`, `RecurringTriggerPage`, `TriggerFirePublic`, `TriggerFirePage`.
- Router `api/src/routers/recurring_triggers.py`: `POST/GET` collection (org-scoped list, operation filter), `GET/PATCH` item (disable via `enabled=false`; no delete — disable preserves the fence history), `GET` item fires (read-only receipts, newest first). All writes validate through the registry (cron/tz/skip-only/params); requester is always the caller (stored snapshot), never caller-supplied — create stores the creator, and any update rebinds to its editor so future fires run under whoever last authorized the request.
- CLI `recurring-triggers` group: create/list/get/update/disable/fires with `--json` parity, exit 2 usage errors, no provenance/status flags.
- Contract fingerprint gains the new DTOs (additive refresh). Types + appendices regenerated.
- Tests: auth matrix (tenant isolation, admin scoping, requester-is-caller), validation rejections, disable-stops-processor, fire receipt visibility; CLI unit tests. No model calls.

## Phase 4a — test identity + default collection foundation (approved 2026-09-21)

Decisions on the inventory's five open questions: (1) stable identity via Option A — `logical_test_id` UUID on cases, each existing row backfilled as its own logical test, `UNIQUE(logical_test_id, version)`; (2) hidden server-managed default suite per (org, agent) marked by new `is_default` with partial-unique `(org_id, agent_id) WHERE is_default` plus a CHECK requiring org+agent when default (the existing `(org,name,version)` uniqueness cannot scope per-agent defaults); (3) default suites auto-published at creation with append-only immutable version inserts through the future agent-wide service — ordinary suite case CRUD stays blocked on published suites; (4) edits to published named-suite tests copy forward as new versions into the default collection under the same `logical_test_id` (no in-place mutation); (5) candidate-apply endpoint deferred — no apply-path changes in this slice.

This slice is storage + helper only (Phase 3A precedent): migration, ORM, `shared/agent_test_collection.py` (`get_or_create_default_suite`, race-safe via integrity-conflict reselect), constraint/backfill/idempotency tests. No API/CLI/UI/routes, no DTO or fingerprint changes, no behavior changes to existing suite/case flows.

## Phase 4c — latest-results read model (approved 2026-09-21)

Per-test latest across both modes, keyed by logical_test_id (joins through the concrete case row; no migration): simulation latest = most recent non-pending result on a terminal execution, with execution/profile/candidate/case-version context; recorded latest = most recent complete result with evaluation/run/case-version/outcome/judge-mode context. In-progress/pending reads as unknown (null), never passing. Unauthorized executions/evaluations are omitted, never fail the list. Recorded grouping decision: latest per logical test overall (not per source run). Read-only: service + GET routes + CLI list command; no new ledger, worker, or status surface.
