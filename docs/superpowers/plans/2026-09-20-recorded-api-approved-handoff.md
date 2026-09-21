# B1 — Recorded evaluation API and CLI, exact checks

Primary-owned contract decision. Start after A2 acceptance. This is an intermediate implementation slice of Phase 2, not acceptance of the whole feature. Semantic execution and automatic applicability classification remain explicit follow-on work; exact mode must expose their absence honestly.

## Contract

- Reuse accepted enabled cases from published suites belonging to the selected agent and tenant. Freeze case ID/version/name/assertions at admission. Do not mutate existing suites or cases, flatten named suites, or silently choose a scenario.
- `POST /api/agent-evaluations/recorded-evaluations`: agent_id, deduplicated run_ids, either explicit case_ids or all_tests=true, applicability (default unknown), optional per-pair applicability overrides. Values applicable/not_applicable/unknown. Explicit caller applicability is recorded as caller-declared, never represented as an automated conclusion. No semantic inference or model call in this slice.
- Missing/hidden selected inputs are identical 404 responses; no partial admission with hidden IDs. Authorize agent with existing access policy, suites/cases by exact tenant and agent, and every source via A2. Non-superuser org=None is denied. Selected run must belong to selected agent. Include terminal production runs only; simulations must use existing simulation results rather than masquerade as historical production.
- Admit at most 20 runs, 100 tests and 1000 pairs, with a total 4 MiB UTF-8 JSON frozen-input ceiling. Reject before dispatch; no silent truncation. A2's individual evidence bounds also apply. Freeze accepted evidence once per selected run; do not copy it for every pair.
- Return `202 PlatformJobAccepted`, shared Location header, and X-Recorded-Evaluation-Id. Generic PlatformJob is sole lifecycle/status/cancel authority. No bespoke status/cancel route, scheduler loop, worker, transport or browser polling.
- Add one domain record with frozen inputs, agent/org/requester, platform_job_id, creation time and completed result aggregate, plus per-pair result records keyed uniquely by evaluation/case/run. Domain record has NO parallel queued/running/cancelled state machine or active-dedupe index. Keep case/run original IDs and versions in immutable snapshots if source rows are deleted.
- `GET /api/agent-evaluations/recorded-evaluations/{id}/results` returns bounded paginated pair results, aggregate, evaluation ID and job ID. Read requires requester or superuser AND current tenant/agent/source-run access. Revoked/deleted sources deny access to frozen private evidence; snapshots are not an authorization bypass. Generic job result contains only evaluation ID and non-sensitive counts/verdict, never frozen evidence.
- Active dedupe uses shared enqueue lock and includes requester, tenant, agent, frozen case versions/content, canonical run selection/evidence hashes and applicability. Reused job resolves its existing domain ID; no orphan duplicate domain rows. Same request by another user must never reuse an unreadable job.

## Execution and persistence

Register `agent.evaluation_recorded`, payload containing domain ID only. Handler reads frozen inputs, calls accepted A1, persists pair outcomes and rollup, and returns IDs/counts. Exact mode leaves llm_judge as pending_judge; complete/gate_passed remain false. A fully evaluated failed assertion does not fail the PlatformJob. Actual handler/infrastructure failures use structured PlatformJobFailure.

Every result transaction must lock/check current PlatformJob lease token, running status, expiry and cancellation before writing domain results. `context.report` before a separate transaction alone is NOT fencing. Use idempotent pair uniqueness/upsert under that fence; retries may reuse verified existing pairs. Cancellation/runner loss must not publish stale results. Do not hold a transaction while calling a model (there are no model calls in B1).

Initial policy: 5 minute timeout, two attempts, runner-loss retry allowed for deterministic idempotent work, running cancellation allowed, existing default memory floor/ratios. Measure representative bounded handler duration/memory before final integrated acceptance; do not claim calibrated capacity from these initial values.

## CLI

Add `agent-tests evaluate --agent REF --tests ID,ID|all --runs ID,ID --applicability applicable|not_applicable|unknown [--applicability-file PATH] [--wait]` using existing group JSON behavior. Default unknown is clearly described in help. No fictitious semantic flag. Without wait, return shared job ID and domain ID. With wait, follow shared job then fetch results.

Add `agent-tests recorded-results EVALUATION_ID` (pagination flags) and `recorded-status JOB_ID`/`recorded-cancel JOB_ID` as thin conveniences over generic shared job endpoints; help distinguishes job ID from evaluation ID. Preserve existing commands.

Document exits: 0 gate passed (or successfully admitted without wait); 1 observed assertion failure; 2 invalid invocation; 3 job/infrastructure error or cancellation; 4 completed evaluation with incomplete required evidence, pending judge or all-inapplicable. Failure takes precedence over incompleteness, matching A1, while JSON retains both. A local wait deadline exits nonzero and explains server operation may continue. JSON stdout must remain parseable even on nonzero verdict exits; chatter stderr only.

## Ownership and tests

Allowed: new ORM `api/src/models/orm/agent_recorded_evaluations.py`, one additive Alembic migration and necessary ORM exports; new shared admission/execution helper `api/shared/agent_recorded_admission.py`; new DTO source `api/shared/models.py` (repository instruction explicitly requests this location; existing file absent, create only needed recorded DTOs, no migration of existing contracts); new router `api/src/routers/agent_recorded_evaluations.py` and minimal registration; new platform job definition and registry import; existing CLI agent_tests.py; focused unit/e2e/CLI tests and applicable DTO/version/generated skill-truth outputs. A1/A2 files are dependencies, not owned. No product UI changes. Preserve all other dirty work.

Acceptance tests: authorized exact happy path through HTTP/CLI; no agent/model/tool dispatch; incomplete evidence not pass; unknown applicability not pass; immutable snapshots across source edits; duplicate and concurrent enqueue/requester isolation; cross-tenant/private/revoked-source reads denied; pair pagination; failed assertions complete job successfully but nonzero CLI; all-inapplicable/pending distinct; stale/expired/cancelled lease cannot write; idempotent retry; limits reject rather than truncate. Exercise shared scheduler handler boundary, not only a mocked outcome function.

## Review clarifications

- Globally shared agent definitions remain usable by tenant users under existing access policy. Evaluation tenant comes from the caller tenant (non-superuser) or one common authorized selected-root tenant (superuser), not blindly from Agent.organization_id. Reject mixed-tenant selections, and require suites/cases and all included evidence in that target tenant. Agent definition scope may be global. This preserves existing tenant-owned suites for globally shared agents without adding a new org selector.
- Generate domain UUID before enqueue and put it in payload; enqueue first within the transaction, create the domain row only for a new job. For reuse, load the domain row by unique platform_job_id and return its ID, ignoring the newly generated UUID. Missing reused domain is an invariant error, never repaired by inventing a replacement. Commit both records atomically, then publish notification.
- A small reusable transaction-fence helper in `api/src/services/platform_jobs.py` is an approved shared extension: lock matching job ID/token with status exactly running, no cancel_requested_at, unexpired lease compared against database time. Test it and use the caller's transaction for result persistence. Never permit cancel_requested writes. Existing shared behavior must remain unchanged for existing consumers.
- Frozen-input byte limit covers the entire canonical stored admission document, including all evidence, case definitions, provenance and applicability, not just request JSON.
- Generic job status remains requester/admin-visible and contains only counts/verdict, not evidence. Result access may be denied after source deletion/revocation even while generic job status stays visible; this is intentional fail-closed evidence behavior for B1. Snapshot IDs retain provenance, not an access grant.
- CLI uses Click context exit after output_result for verdict exit codes; do not convert all nonzero verdicts into ClickException exit 1 or corrupt JSON with prose.
- The explicit repository instruction controls DTO placement for this additive slice. Import new shared/models.py DTOs in router response/request signatures so ordinary FastAPI OpenAPI generation discovers them; there is no filename-based OpenAPI discovery requirement. Do not relocate existing contracts.

Run focused tests, A1/A2 regression, DTO/CLI contract tripwires, API quality, generate OpenAPI types against existing debug stack and relevant client type check. Report exact checks, failures and unrun full suites. No commits/push/secrets/notifications. Contract gap returns to primary before changing semantics.
