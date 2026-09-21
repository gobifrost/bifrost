# Agent Review Findings Phase 3A Contract Proposal

> **For agentic workers:** This is a reviewed design proposal only. Do not implement until primary approves a bounded implementation packet.

**Goal:** Define the smallest backend contract for versioned review statements and on-demand review runs that create dismissible problem/opportunity findings from authorized recorded production evidence.

**Architecture:** Review definitions are versioned instructions; review runs are immutable admission/evidence/result records tied to the canonical `PlatformJob` for lifecycle. Review execution reuses the accepted recorded evidence reader, shared quality accounting, and existing findings authorization. There are no automatic fixes, no schedules in Phase 3A, and no review-specific job lifecycle.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, shared Pydantic models in `api/shared/models.py`, `shared.agent_recorded_evidence.load_recorded_run_evidence`, `shared.quality_usage`, canonical `PlatformJob`, existing Python CLI patterns.

---


## Approved Phase 3A data-foundation ownership

Primary approval for this implementation is limited to the data foundation slice. This slice owns only the proposal note, the additive ORM/migration/export changes for review definitions, review versions, review runs, and review-generated finding provenance, plus focused database tests. It does not add shared DTOs, routers, CLI commands, platform-job registry entries, workers, services, UI, or model/provider calls.

The implementation must keep `PlatformJob` as the sole lifecycle record. `agent_review_runs` stores durable admission, evidence, result, and accounting linkage facts only; it must not add status, error, start, or completion mirrors. Active admission dedupe and terminal rerun behavior belong to the later service layer, so the data model must allow multiple terminal reruns with the same review/version/request fingerprint/requester snapshot.

Requester identity is a required UUID snapshot on review runs. Later admission logic must validate the user before creating the row, but the data foundation must not use a requester foreign key that erases historical identity or makes users undeletable. Agent and organization deletion must cascade review-domain rows through the review/agent/org relationships, while existing `AIUsage` and `AIUsageAttempt` rows retain their immutable quality operation UUIDs without domain foreign keys. Review-generated findings must use all-or-none review provenance, including source ordinal, positive source review version, and nonempty source run refs. Finding review provenance columns are immutable UUID snapshots, not foreign keys; deleting review-domain rows must not erase provenance or convert fail-closed reads into apparently unreviewed findings.

## Scope

Phase 3A includes only:

- versioned review definitions with one plain-English review statement and separate Markdown evidence formatting instructions;
- explicit selected production run IDs only;
- one on-demand review run per active dedupe key;
- one bounded model call per review run;
- creation of review-derived findings with Markdown evidence and structured multi-run source references;
- per-review-run quality accounting as `quality_operation_type='agent_review'`, `usage_purpose='agent_review'`;
- read/list/get/run/results/usage/cancel/status CLI/API contracts needed to exercise the backend.

Phase 3A excludes:

- recurring schedules;
- latest-run selectors;
- automatic fixes, prompt application, candidate creation, test creation, or finding dismissal;
- semantic dedupe across review runs;
- UI/MCP work;
- feature-specific jobs, status endpoints, workers, WebSocket channels, or browser polling.

## Existing source facts to reuse

- Existing findings are in `api/src/models/orm/agent_findings.py`, contracts in `api/src/models/contracts/agent_findings.py`, and routes in `api/src/routers/agent_findings.py`.
- Existing finding dismissal is independent of tests and must remain independent.
- Existing finding tenant policy is caller-tenant scoped. For non-superusers, `AgentFinding.org_id` must equal `user.organization_id`, including findings for global/shared agents.
- Existing source-run authorization helper is `src.services.execution.agent_run_access.agent_run_visibility_conditions(user)`.
- Accepted recorded evidence reader is `shared.agent_recorded_evidence.load_recorded_run_evidence(session, run_id, user=user)`. It performs visibility checks, terminal evidence checks, completeness reporting, redaction, descendant traversal limits, and bounded evidence projection without writes or external calls.
- Existing `PlatformJob` lifecycle, dedupe, cancellation, and status are in `src.services.platform_jobs` and `src.jobs.platform.base`. Review runs must not mirror this lifecycle.
- Existing model assignments include `testing` but not `review` in `AIModelAssignment.assignment_key`. `get_llm_config(..., assignment_key='testing')` is available through `src.services.llm.factory` and explicit profiles can be resolved by `profile_id`.
- Admin model profile APIs are guarded by `RequirePlatformAdmin` in `api/src/routers/ai_models.py`. There is no tenant-level profile permission model visible in this pass.

## Schema contract

### New table: `agent_review_definitions`

Fields:

- `id UUID primary key`
- `agent_id UUID not null FK agents.id ondelete CASCADE`
- `org_id UUID nullable FK organizations.id ondelete CASCADE`
- `name varchar(200) not null`
- `status varchar(20) not null default 'active'`, check `active|disabled`
- `latest_version int not null default 1`
- `created_by UUID nullable FK users.id ondelete SET NULL`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Indexes:

- `(agent_id)`
- `(org_id)`
- `(status)`
- no name uniqueness constraint; repeated review names are allowed.

Delete policy:

- Deleting an agent cascades definitions.
- Phase 3A should not expose a delete route. Disable via `status='disabled'` so review/version/run history remains available.

### New table: `agent_review_versions`

Fields:

- `id UUID primary key`
- `review_id UUID not null FK agent_review_definitions.id ondelete CASCADE`
- `version int not null`
- `review_statement text not null`
- `evidence_format_instructions text nullable`
- `model_profile_id UUID nullable`
- `created_by UUID nullable FK users.id ondelete SET NULL`
- `created_at timestamptz not null`

Constraints/indexes:

- unique `(review_id, version)`
- check `length(btrim(review_statement)) > 0`
- check `length(review_statement) <= 8000`
- check `evidence_format_instructions is null or length(evidence_format_instructions) <= 4000`

Semantics:

- A version selects the review statement, formatting instructions, and optional profile ID.
- It does not freeze provider/model settings. Profile settings are frozen at run admission so a changed/deleted profile does not brick old definitions before they are run, and old runs remain reproducible/accountable.

### New table: `agent_review_runs`

This table is not a job-status mirror. It must not contain mutable lifecycle fields such as `status`, `error`, `started_at`, or `completed_at`. The canonical lifecycle is the linked `PlatformJob`.

Fields:

- `id UUID primary key`
- `review_id UUID not null FK agent_review_definitions.id ondelete CASCADE`
- `review_version_id UUID not null FK agent_review_versions.id ondelete CASCADE`
- `review_version int not null`
- `agent_id UUID not null FK agents.id ondelete CASCADE`
- `org_id UUID nullable FK organizations.id ondelete CASCADE`
- `platform_job_id UUID nullable unique FK platform_jobs.id ondelete SET NULL`
- `requested_by_user_id UUID not null`
- `requested_run_ids UUID[] not null`
- `selected_run_ids UUID[] not null`
- `source_evidence jsonb not null`
- `source_refs jsonb not null`
- `profile_snapshot jsonb not null`
- `profile_fingerprint varchar(128) not null`
- `request_fingerprint varchar(128) not null`
- `input_bytes int not null`
- `result_summary text nullable`
- `created_at timestamptz not null`

Constraints/indexes:

- check `cardinality(selected_run_ids) between 1 and 20`
- check `review_version > 0`
- check `input_bytes > 0 and input_bytes <= 4194304`
- index `(review_id)`
- index `(agent_id)`
- index `(org_id)`
- index `(platform_job_id)`
- no permanent uniqueness on `(review_id, review_version, request_fingerprint, requested_by)`. Intentional reruns after terminal jobs must be allowed. Active duplicate admission is handled by an advisory lock, canonical active PlatformJob dedupe, and a reverse lookup from active job `resource_id` to the existing `AgentReviewRun`.

Semantics:

- Duplicate active admission returns the same `agent_review_runs` row and same active PlatformJob for the same review/version/request fingerprint/requester UUID snapshot. After the prior PlatformJob is terminal, the same review/version/run selection creates a fresh review run and fresh PlatformJob.
- The row freezes only admission facts, selected evidence, profile snapshot, and eventual parsed summary. It does not mirror job state.
- There is no public delete route; normal product behavior is disabling a review. Database delete behavior must not make agents or organizations undeletable because reviews exist: deleting an agent or organization cascades review definitions, versions, review runs, and review-domain finding provenance FKs according to their relationships. Existing AIUsage/AIUsageAttempt rows keep immutable operation UUIDs and survive domain deletion according to the accepted accounting foundation.

### Extend `agent_findings`

Add fields:

- `finding_kind varchar(20) not null default 'problem'`, check `problem|opportunity`
- `evidence_markdown text nullable`, max 20000 enforced in the database and DTO/service
- `source_review_id UUID nullable` immutable snapshot of the source review definition ID
- `source_review_version_id UUID nullable` immutable snapshot of the source review version ID
- `source_review_run_id UUID nullable` immutable snapshot of the source review run ID
- `source_review_version int nullable`
- `source_run_refs jsonb not null default '[]'`
- `source_ordinal int nullable`

Constraints/indexes:

- unique `(source_review_run_id, source_ordinal)` where both are not null
- check review-generated findings use all-or-none review provenance: review ID, review version ID, review run ID, positive source review version, nonnegative source ordinal, and nonempty source run refs
- index `(source_review_id)`
- index `(source_review_run_id)`
- index `(finding_kind)`

Semantics:

- Review-created findings set `source_kind='run'`, `source_review_*`, `source_ordinal`, `source_run_refs`, and may set legacy `source_run_id` to the first source ID for older displays. Review provenance IDs and source run refs remain stored even if review-domain rows are deleted; read services must require the referenced review/run records to exist and authorize them, returning 404 on missing or revoked sources rather than silently exposing derived evidence.
- Existing manual/run/external findings remain valid.
- Dismissed findings are never reopened, deleted, or modified by review execution.
- No cross-run or semantic finding dedupe is promised in Phase 3A.

## DTO contract in `api/shared/models.py`

Add these shared DTOs.

```python
class AgentReviewDefinitionCreate(BaseModel):
    agent_id: UUID
    name: str = Field(min_length=1, max_length=200)
    review_statement: str = Field(min_length=1, max_length=8000)
    evidence_format_instructions: str | None = Field(default=None, max_length=4000)
    model_profile_id: UUID | None = None

class AgentReviewDefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal['active', 'disabled'] | None = None

class AgentReviewVersionCreate(BaseModel):
    review_statement: str = Field(min_length=1, max_length=8000)
    evidence_format_instructions: str | None = Field(default=None, max_length=4000)
    model_profile_id: UUID | None = None

class AgentReviewRunCreate(BaseModel):
    run_ids: list[UUID] = Field(min_length=1, max_length=20)

class AgentReviewRunAccepted(BaseModel):
    review_run_id: UUID
    job_id: UUID
    reused: bool

class AgentReviewSourceRef(BaseModel):
    run_id: UUID
    agent_id: UUID
    org_id: UUID | None
    root_run_id: UUID | None = None
    parent_run_id: UUID | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

class AgentReviewRunPublic(BaseModel):
    id: UUID
    review_id: UUID
    review_version: int
    agent_id: UUID
    org_id: UUID | None
    platform_job_id: UUID | None
    selected_run_ids: list[UUID]
    source_refs: list[AgentReviewSourceRef]
    result_summary: str | None = None
    created_at: datetime

class AgentReviewFindingPublic(BaseModel):
    finding_id: UUID
    ordinal: int
    finding_kind: Literal['problem', 'opportunity']
    source_run_ids: list[UUID]

class AgentReviewRunResults(BaseModel):
    review_run: AgentReviewRunPublic
    findings: list[FindingPublic]
    usage_operation_type: Literal['agent_review'] = 'agent_review'
    usage_operation_id: UUID
```

`FindingPublic` must be extended additively with `finding_kind`, `evidence_markdown`, `source_review_id`, `source_review_version_id`, `source_review_run_id`, `source_review_version`, `source_run_refs`, and `source_ordinal`.

## Profile selection and freezing rule

Phase 3A should use this precise rule:

- If `model_profile_id` is omitted on the review version, admission resolves the existing global `testing` assignment via `get_llm_config(db, assignment_key='testing')` and freezes the resulting config.
- If `model_profile_id` is supplied, only a platform admin may create that version for now, matching the existing `/api/admin/ai/profiles` permission boundary. Admission resolves the profile by ID and freezes the resulting config.
- The frozen snapshot stores the same accepted judge snapshot allowlist: `profile_id`, `profile_name`, `provider`, `model`, `endpoint`, `openai_transport`, `anthropic_prompt_cache_supported`, `default_max_tokens`, `extra_params`, and a canonical fingerprint. It must not store encrypted API keys or raw credentials.
- Execution uses the frozen snapshot identity for accounting and resolves/calls the current profile only if the frozen config still matches required non-secret settings, including `extra_params`. If the profile was deleted or materially changed between admission and execution, the job fails before dispatch with no provider call. This matches recorded semantic judge drift protection. Non-admin explicit profile selection is not part of Phase 3A.

## Admission algorithm

`shared.agent_reviews.admit_agent_review_run(db, user, review_id, body)`:

1. Load definition and latest version. Reject disabled definitions.
2. Authorize the target agent with the same policy used by findings/evaluation routes. Non-superuser org must scope the review to `user.organization_id` even for global agents.
3. Validate explicit `run_ids`: 1–20 unique IDs after dedupe. No latest selector in Phase 3A.
4. For each run ID, call `load_recorded_run_evidence(db, run_id, user=user)`.
5. Reject if selected run evidence is not for the same `agent_id`, if the evidence proves the root/selected run is not terminal, or if `load_recorded_run_evidence` raises not-found/oversized.
6. Build review input from frozen evidence, completeness, evidence refs, limitations, review statement, and formatting instructions. Redact through the existing recorded evidence projection; do not read raw hidden descendants.
7. Reject if canonical input JSON exceeds 4 MiB.
8. Resolve and freeze profile snapshot using the rule above.
9. Compute `request_fingerprint = hash(review_id, version, selected_run_ids, review_statement, evidence_format_instructions, evidence fingerprints, profile fingerprint)`.
10. Validate `user.user_id` exists in `users` and store that UUID as durable requester identity. Do not silently null malformed requester identity.
11. Under one transaction and advisory dedupe lock, call canonical `enqueue_platform_job` with an active-only dedupe key. If it reuses an active job, load the existing `AgentReviewRun` by `platform_job_id`/job `resource_id` and return that same row.
12. If no active job is reused, insert a fresh `AgentReviewRun`, enqueue `agent.review` PlatformJob, set `platform_job_id`, commit, and return the accepted response. Terminal prior jobs never block a fresh rerun.

## Execution algorithm

`shared.agent_reviews.execute_agent_review_job(context, review_run_id)`:

1. Lock the current running PlatformJob through `lock_running_platform_job_for_result_write` before any domain write.
2. Load immutable review run, review definition/version, and frozen profile snapshot. If the review/domain rows are missing because the agent/org/review was deleted, abort before any provider call. Findings with deleted review provenance must not fall back to returning derived evidence.
3. Validate `context.requested_by_user_id` parses as a UUID and the user still exists. If not, fail before any provider call.
4. Begin exactly one quality usage attempt with:
   - `idempotency_key=f'agent-review:{review_run_id}:review'`
   - `quality_operation_type='agent_review'`
   - `quality_operation_id=review_run_id`
   - `quality_operation_item_id='review'`
   - `usage_purpose='agent_review'`
   - `organization_id=review_run.org_id`
   - `user_id=UUID(context.requested_by_user_id)` after validating the user exists; malformed or missing requester identity fails before any provider call
   - `platform_job_id=context.job_id`
   - frozen profile identity/fingerprint
5. Commit the attempt before provider dispatch.
6. Execute one model call outside DB transactions. There is no automatic replay after a started attempt. PlatformJob policy is `max_attempts=1`, `retry_on_runner_loss=False`.
7. Immediately after response returns, in a short transaction, record strict observed usage before parsing/verdict fence. Missing/bad usage calls `mark_quality_usage_unobserved(..., reason='missing_usage')`. Provider/config failure before response calls `mark_quality_usage_unobserved(..., reason='provider_error')` if an attempt exists.
8. Parse structured response with hard caps:
   - top-level object;
   - `summary` string max 2000 chars;
   - `findings` list max 20;
   - each finding has `kind: problem|opportunity`, `description` max 4000, optional `expected_behavior` max 4000, `evidence_markdown` max 20000, `source_run_ids` nonempty subset of selected run IDs.
9. Reacquire current PlatformJob result fence. If fence fails, do not write findings; usage remains recorded because spend already occurred.
10. Insert findings atomically under the fence with deterministic ordinals. Use the unique `(source_review_run_id, source_ordinal)` constraint so a retry/stale duplicate cannot double-create findings.
11. Set `AgentReviewRun.result_summary` only under the same fence. Do not set lifecycle status/error timestamps.

## Read authorization and evidence leak rule

All review run, review result, and review-created finding reads must re-authorize every source run reference, not only the review/finding tenant.

- For each `source_run_refs[].run_id`, verify current visibility with `agent_run_visibility_conditions(user)` and same tenant for non-superusers.
- If any source run is missing or no longer authorized, deny the whole derived review result/finding content with 404. Do not return partial content, hidden links, redacted Markdown, or summary text. Hiding links alone is insufficient because generated Markdown and descriptions can leak evidence.
- Reads fail closed if the review definition, review run, source run, or agent needed to authorize the derived content is missing. No orphaned derived-evidence read path is included in Phase 3A.

## Finding insertion and preservation invariants

- Review execution only inserts new findings for that `review_run_id` and ordinals.
- It never updates existing findings except through explicit user PATCH routes. If review/domain provenance rows are deleted by cascade, review-derived findings must fail closed on derived evidence reads rather than falling back to legacy source fields.
- It never reopens dismissed findings.
- It never dismisses findings because a later review or test passes.
- It never creates tests.
- It never applies agent changes.
- It makes no semantic dedupe promise across review runs.

## API routes

Create `api/src/routers/agent_reviews.py`:

- `POST /api/agent-reviews` -> create definition + version 1.
- `GET /api/agent-reviews?agent_id=&status=` -> list definitions.
- `GET /api/agent-reviews/{review_id}` -> get definition/latest version.
- `PATCH /api/agent-reviews/{review_id}` -> update name/status only.
- `POST /api/agent-reviews/{review_id}/versions` -> create immutable next version.
- `POST /api/agent-reviews/{review_id}/runs` -> admit on-demand run, returns `AgentReviewRunAccepted`.
- `GET /api/agent-reviews/runs/{review_run_id}` -> get immutable run record after all source refs authorize.
- `GET /api/agent-reviews/runs/{review_run_id}/results` -> get created findings after all source refs authorize.
- `GET /api/agent-reviews/runs/{review_run_id}/usage` -> operation usage, reusing shared quality usage reporting with `agent_review` operation.

Extend `api/src/routers/agent_findings.py` additively:

- filters: `kind`, `source_review_id`, `source_review_run_id`;
- public fields above;
- read of review-derived findings must enforce the all-source-refs authorization rule before returning evidence/description. Simpler and safer: if `source_review_run_id` is nonnull, delegate to the review source authorization helper and 404 on failure.

## PlatformJob contract

Create `api/src/jobs/platform/agent_review.py`:

- `job_type='agent.review'`
- payload `AgentReviewJobPayload(review_run_id: UUID)` in `api/shared/models.py`
- `payload_version=1`
- policy: `timeout_seconds=10*60`, `max_attempts=1`, `retry_on_runner_loss=False`, `allow_running_cancellation=True`

Register in `api/src/jobs/platform/registry.py`.

Admission uses `enqueue_platform_job`; no custom status/cancel route.

## CLI contract

Create `api/bifrost/commands/agent_reviews.py`, register as `agent-reviews`:

- `create --agent AGENT --file review.yaml`
- `list --agent AGENT [--status active|disabled]`
- `get REVIEW_ID`
- `version REVIEW_ID --file review.yaml`
- `run REVIEW_ID --runs RUN1,RUN2 [--wait] [--timeout 1200]`
- `results REVIEW_RUN_ID`
- `usage REVIEW_RUN_ID`
- `status JOB_ID` -> `/api/platform-jobs/{job_id}`
- `cancel JOB_ID` -> `/api/platform-jobs/{job_id}/cancel`

Create/register `api/bifrost/commands/agent_findings.py` in Phase 3A:

- `list --agent AGENT [--status open|dismissed] [--kind problem|opportunity] [--source-review REVIEW_ID]`
- `get FINDING_ID`
- `dismiss FINDING_ID`

Use existing `agent_tests.py` wait behavior: terminal failed/cancelled under `--wait` exits 3 and outputs JSON when requested.

## Migration order

1. Create `agent_review_definitions`.
2. Create `agent_review_versions`.
3. Create `agent_review_runs`.
4. Add finding columns with nullable/default-safe values.
5. Backfill existing findings: `finding_kind='problem'`, `source_run_refs='[]'`.
6. Add finding check constraints and partial unique index `(source_review_run_id, source_ordinal)`.
7. Add ORM exports.
8. Register router/job only after models/migration are in place.

Downgrade policy: preserve data. If downgrade would drop nonempty review tables or review-derived finding metadata, explicitly refuse with a clear migration error rather than silently deleting review/finding evidence.

## Tests required before implementation acceptance

- Definition/version unit tests: immutable versions, disabled reviews reject runs, explicit profile requires platform admin, default uses `testing` assignment.
- Admission tests: explicit IDs only, max 20, duplicate active admission returns same review run, terminal prior run allows a fresh intentional rerun, requester UUID must reference an existing user, cross-agent/cross-tenant/inaccessible run rejected, oversized 4 MiB input rejected, recorded evidence limitations preserved.
- Read authorization tests: revoked/missing source run denies whole review result and whole review-derived finding content; deleted review provenance on a finding does not fall back to returning derived evidence.
- Execution tests: one model call; max 20 output findings; finding source IDs must be subset of selected; malformed response records usage before parse and creates no findings; no response marks unobserved; stale/cancelled job fence prevents finding insertion while preserving usage.
- Idempotency tests: duplicate execution cannot insert duplicate ordinal findings.
- Preservation tests: dismissed findings untouched; existing manual findings unchanged; no tests/agent changes created.
- Usage tests: review operation appears under `agent_review`; production source run cost is not added to review operation cost.
- CLI tests for create/run/status/results/usage and stable exit 3 on terminal failed/cancelled wait.
- Contract tests: `test_contract_version.py` and DTO/CLI parity tests if shared models affect CLI-consumed contracts.

## Open decisions for primary

1. Agent findings CLI timing: Phase 3A includes backend finding fields and routes. CLI finding commands can ship in the same packet if CLI-first review acceptance requires them; otherwise they must be a named follow-on, not an implied gap.
