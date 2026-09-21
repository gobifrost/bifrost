# Quality Usage Reporting: Public Contract

Status: approved implementation packet; execution awaits accounting inventory handback.
Owner: primary agent. Executor: native worker assigned through handoff.
Worktree: `/home/jack/GitHub/bifrost/.worktrees/durable-agent-platform-backend`.
Preserve all current dirty work. No commits, paid calls, or production UI edits.

## Intent

Expose the verified shared usage ledger aggregation so providers can distinguish normal agent execution from testing and review overhead. Do not rebook historical source-run spend. Unknown usage or cost is visible, never represented as a complete zero. This packet exposes reporting, not claims that every legacy caller already records complete usage.

## API

- Add `GET /api/reports/usage/breakdown` to the existing usage reports router, with its existing platform-admin authorization and organization-context behavior. Preserve the existing `/usage` response and semantics. Query: required inclusive UTC `start_date` and `end_date`, optional `org_id` UUID, `source` all/executions/chat/agents, purpose/provider/model/profile_id/profile_fingerprint, and bounded limit/offset (50 default, 200 max). Reject reversed dates. Translate to UTC half-open timestamps. Explicit org filter takes precedence over existing request organization context. Without either, platform scope.
- Add `GET /api/agent-evaluations/recorded-evaluations/{evaluation_id}/usage`. Load the domain record and call `assert_recorded_evaluation_readable` before any aggregation. Operation filter is server-selected `recorded_evaluation` plus that ID. Aggregate all-time operation usage; bounded pagination only. Unknown or unauthorized record returns 404. Do not expose an arbitrary operation-ID lookup.
- Synthetic execution and designer reporting follow after the execution-authorization correction and inventory review; do not add a weak tenant-only route here.
- Both routes return a shared typed response matching `summarize_quality_usage`: overall totals, coverage, and paginated by-purpose/provider-model/profile/organization/operation groups. Put DTOs in `api/shared/models.py`. Use concrete dimension row types and a reusable typed page, not arbitrary JSON. Decimal monetary values serialize as strings. Preserve null profile identity; do not substitute current mutable profile metadata.
- Reflect missing-cost counts consistently in each group's coverage as well as totals. Expose legacy coverage uncertainty honestly. No cache hit-rate percentage without a justified denominator. No secrets, prompt snapshots, internal idempotency keys, or raw accounting attempts in the response.
- Keep new business logic in shared modules and handlers thin. Read-only request-scoped work, no jobs or new polling.

## CLI

- Add `bifrost usage report` with the same filters/date validation and existing org targeting/JSON output conventions. Register the group using existing CLI patterns.
- Add `bifrost agent-tests recorded-usage EVALUATION_ID` with limit/offset and standard JSON output. Reads only; no inference/provider work or waits.
- Human output labels known cost, provider-reported vs estimated cost, tokens/cache, and unresolved coverage clearly. Preserve decimal precision in machine output.

## Ownership and verification

Own the usage report router, recorded evaluation router, shared reporting DTO additions, CLI usage group/registration and recorded-usage command, their focused endpoint/CLI tests, generated API types, and additive contract fingerprint if required. Coordinate any overlapping file ownership before edits. Do not modify accounting writer or synthetic authorization router.

Tests: platform-admin gate and organization filtering, recorded requester/current-source authorization denial, actual provider/estimated/missing cost with unresolved attempt groups, date bounds and pagination, decimal JSON, CLI argument forwarding/error/JSON behavior. Use existing owned DB fixtures and cleanup. Run focused tests via `./test.sh`, DTO and CLI contract tripwires when touched, API quality, regenerate types against the worktree debug API, client tsc. Coordinate the shared test stack with primary. Return exact results and blockers; no user notifications or broad feature-completion claims.

## Consistent live-read correction

Primary review confirmed default PostgreSQL READ COMMITTED lets a concurrent usage write land between overall totals and dimension queries. Public reports must use one REPEATABLE READ snapshot. Approved: a new generic API read-snapshot dependency in `src/core/db_deps.py` creates an independent session through the existing DB lifecycle and sets isolation before its first SQL; only the two new report routes use it. The global route retains existing Context/org-header behavior. Recorded record load, current source authorization, and aggregation share the snapshot. Internal aggregation helpers and legacy `/usage` stay unchanged. Verify with a real second-session commit between report queries: current report remains coherent, next report sees the new row. No sleep-based race tests or global isolation change.
