# Agent review Phase 3B service/executor handoff

Status: review packet for primary approval. Do not implement until approved. This packet sits on top of the accepted Phase 3A data foundation in `docs/superpowers/plans/2026-09-20-agent-review-findings-phase3-proposal.md` and intentionally excludes API routes, CLI commands, UI, scheduling, MCP, and public reporting changes.

## Primary approval and corrections (takes precedence over proposal below)

Approved for the bounded service/executor ownership below after Phase 3A data tests and integrated API quality passed. Executor: native evidence_worker; primary retains code review and acceptance. No paid calls, commits, UI, or extra public contracts. The prose below contained symbol/semantics errors corrected here:

- Use fastapi-free `src.core.principal.UserPrincipal`, never `CurrentActiveUser`/`src.core.auth` in this service. Non-superuser null-org principals fail closed. Agent visibility must cover private ownership, role-based access and external users just as existing evaluation authorization does; no tenant-only substitute.
- Require terminal selected production runs, but **do not reject every incomplete evidence dimension**. The accepted reader intentionally marks historical usage incomplete, and reviews can still identify observed problems/opportunities. Preserve completeness/limitations, and instruct the model that missing evidence cannot prove an absent action. Selected synthetic runs remain excluded.
- Freeze the identity of every authorized selected/descendant source contributing evidence. Follow `_freeze_runs` in recorded admission for the shape and authorized descendant ID traversal; do not import that API-coupled module into the worker. Identity-only ORM reads of already-authorized projection IDs are permitted. Reads validate all frozen identities without rereading/rebuilding evidence. Missing refs must never trigger a permissive legacy fallback.
- The real LLMResponse exposes `input_tokens`, `output_tokens`, cache counts and `provider_cost` directly; there is no `response.usage`. `get_llm_client` accepts a DB session, not an LLMConfig. Resolve config in a short session, close it, then instantiate lazy-imported `PydanticAIClient(config)` and call `complete` outside transactions, matching accepted judges. Use only arguments actually supported by that method. Measure monotonic provider-call duration for accounting.
- LLMConfig has no profile ID/name. Resolve the actual AIModelAssignment('testing')/AIModelProfile identity and freeze the same explicit profile config in one consistent read; no invented identity or second mutable assignment resolution. Explicit profile ID selection policy belongs to later admission, not this internal executor.
- Before beginning the attempt, validate actual PlatformJob type/payload/run ID, exact nullable org, requester UUID against stored review requester and live active User, review/version/agent binding, and frozen input/profile fingerprints. Reauthorize frozen sources for the current requester before dispatch using a fastapi-free principal. Deleted/revoked sources or disabled/deleted review fail before a paid call. Reacquire fresh result fence and domain binding after the response; accounting is persisted independently even if this fails.
- No extra started domain marker: AIUsageAttempt is the durable marker. A duplicate started/observed attempt never calls the provider again; preserve existing findings/results. All field lengths, strict JSON types and allowed source-ID subsets must be validated before inserting any findings. No schema-coercion repair or model retry.
- One model request, no tools, no remote link fetch. Formatting instructions may define canonical ticket URL templates using evidence IDs. They do not authorize external actions.
- This packet implements executor, strict parser, authorized evidence/profile helpers and read authorization only. Enqueue/admission transactions, definition CRUD, public DTOs, routes and CLI follow a separate reviewed packet. Do not implement unused optional entry points or raw status mirrors.

Focused checks must include current import-hygiene tests as well as new service/DB tests and API quality. Test fixtures flush FK dependencies in order and clean up committed rows by scoped primitive IDs. Coordinate test-stack ownership with primary.

## Goal

Add the on-demand review service and one PlatformJob executor that turns an already-admitted review definition/version plus selected authorized production evidence into at most one model call and up to 20 review-derived findings. The implementation must reuse the Phase 3A data tables and the accepted shared quality usage foundation. It must not import routers into worker code or duplicate router-only authorization helpers.

## Ownership for this packet

Own only these new/touched backend service/executor files and focused tests:

- New `api/shared/agent_reviews.py` for admission helper utilities that are safe to call from future routers and the PlatformJob worker.
- New `api/src/jobs/platform/agent_review.py` for the thin job definition and handler.
- Touch `api/src/jobs/platform/registry.py` for one registry entry.
- Touch `api/shared/models.py` only for `AgentReviewJobPayload` if not already present.
- New focused tests under `api/tests/unit/services/agent_evaluations/test_agent_reviews.py` or `api/tests/unit/services/test_agent_reviews.py`, plus DB-backed executor tests under `api/tests/e2e/api/test_agent_review_executor.py`.

Do not touch routers, CLI, client types, public DTOs beyond the job payload, usage reporting, scheduler, UI, findings routes, or Phase 3A migration/ORM unless a test exposes a direct service-layer blocker and primary approves scope expansion.

## Existing symbols to reuse

Data foundation already exists:

- `src.models.orm.agent_reviews.AgentReviewDefinition`
- `src.models.orm.agent_reviews.AgentReviewVersion`
- `src.models.orm.agent_reviews.AgentReviewRun`
- `src.models.orm.agent_reviews.MAX_REVIEW_INPUT_BYTES`
- `src.models.orm.agent_reviews.MAX_REVIEW_RUNS`
- review provenance fields on `src.models.orm.agent_findings.AgentFinding`

Authorization/evidence helpers:

- `shared.agent_recorded_evidence.load_recorded_run_evidence(session, run_id, user=user)` is the only production-evidence reader. It already applies `src.services.execution.agent_run_access.agent_run_visibility_conditions(user)`, terminal checks, descendant bounds, hidden-node handling, redaction, completeness, evidence refs, and limitation projection.
- `src.services.execution.agent_run_access.agent_run_visibility_conditions(user)` is the source-run visibility predicate for read reauthorization and tests.
- Do not import `_authorized_agent`, `_scope_check`, `_entity_access_allowed`, or other private helpers from `api/src/routers/agent_evaluations.py`; move any required shared authorization into `api/shared/agent_reviews.py` using ORM queries and existing lower-level predicates.

Profile/model helpers:

- Default review profile resolution must use `src.services.llm.factory.get_llm_config(db, assignment_key='testing')`.
- Explicit profile execution must use `get_llm_config(db, profile_id=...)` and validate non-secret frozen fields against the Phase 3A allowlist before dispatch.
- Existing profile-freeze shape to mirror: `src.services.agent_evaluations.test_designer.designer_testing_model(profile_id, config)` and `src.services.agent_evaluations.executions.apply_profile_override(...)` freeze `profile_id`, `profile_name`, `provider`, `model`, `endpoint`, `openai_transport`, `anthropic_prompt_cache_supported`, `default_max_tokens`, and `extra_params` into snapshots. Review service should implement its own small `freeze_review_profile_snapshot(...)` in `shared.agent_reviews` using that same allowlist rather than importing router code.
- Provider accounting identity must use `src.services.model_pricing.canonical_provider(snapshot['provider'], snapshot.get('endpoint'))`, matching recorded/synthetic judge accounting.

PlatformJob/accounting helpers:

- `src.services.platform_jobs.enqueue_platform_job(...)` for admission in later API packet, but this service packet should only include executor and pure admission helpers if primary wants them in service now.
- `src.services.platform_jobs.lock_running_platform_job_for_result_write(db, job_id=context.job_id, lease_token=context.lease_token)` is the result fence. It must be acquired before any domain write and reacquired after the provider response before inserting findings/result summary.
- `src.jobs.platform.base.PlatformJobDefinition`, `PlatformJobPolicy`, `PlatformJobContext`, and `PlatformJobCancelled` are the job integration types.
- `shared.quality_usage.begin_quality_usage_attempt(...)`, `record_quality_usage_observation(...)`, and `mark_quality_usage_unobserved(...)` are the only accounting writers.

LLM helpers:

- `src.services.llm.factory.get_llm_client(config)` and `client.complete(...)` are the provider boundary.
- `src.services.llm.base.LLMResponse.usage` is normalized by the Pydantic client, but `input_tokens`/`output_tokens` can be missing. Missing token counts must call `mark_quality_usage_unobserved(..., reason='missing_usage')` and insert no zero/fake `AIUsage` row.

## New service DTOs and signatures

In `api/shared/agent_reviews.py`:

```python
@dataclass(frozen=True)
class AgentReviewServiceError(Exception):
    code: str
    public_detail: str = "Review run not found."
    http_status: int = 404

@dataclass(frozen=True)
class ReviewProfileSnapshot:
    profile_id: UUID
    profile_name: str | None
    provider: str
    model: str
    endpoint: str | None
    openai_transport: str | None
    anthropic_prompt_cache_supported: bool | None
    default_max_tokens: int | None
    extra_params: dict[str, Any]
    fingerprint: str

@dataclass(frozen=True)
class ReviewEvidenceInput:
    input: dict[str, Any]
    source_refs: list[dict[str, Any]]
    selected_run_ids: list[UUID]
    input_bytes: int
    request_fingerprint: str

@dataclass(frozen=True)
class ReviewFindingDraft:
    ordinal: int
    finding_kind: Literal["problem", "opportunity"]
    description: str
    expected_behavior: str | None
    evidence_markdown: str | None
    source_run_ids: list[UUID]

@dataclass(frozen=True)
class ReviewModelResult:
    summary: str | None
    findings: list[ReviewFindingDraft]
    usage: dict[str, Any] | None
    unobserved_reason: Literal["missing_usage", "provider_error"] | None = None
```

Use plain dataclasses internally. Public API DTOs stay out of this packet.

Required service functions:

```python
async def authorize_review_agent(
    db: AsyncSession,
    user: CurrentActiveUser,
    *,
    agent_id: UUID,
    org_id: UUID | None,
) -> None: ...
```

Shared authorization helper for future routes/admission. Non-superusers must require `org_id == user.organization_id` for review definitions/runs, including reviews of global/shared agents. It must also verify current agent visibility without importing router helpers. If no lower-level shared agent policy exists, implement the minimal ORM equivalent in this module and cover it with tests.

```python
async def build_review_evidence_input(
    db: AsyncSession,
    user: CurrentActiveUser,
    *,
    review: AgentReviewDefinition,
    version: AgentReviewVersion,
    requested_run_ids: Sequence[UUID],
) -> ReviewEvidenceInput: ...
```

This dedupes explicit run IDs, enforces 1..`MAX_REVIEW_RUNS`, calls `load_recorded_run_evidence` once per selected run, rejects evidence for a different `review.agent_id`, rejects incomplete/nonterminal evidence, preserves `evidence_refs` and `limitations`, canonical-sorts by selected run ID for fingerprint stability, builds a canonical JSON input, and rejects over `MAX_REVIEW_INPUT_BYTES` before any PlatformJob or model call. It must use only the projected recorded evidence, never raw run rows or hidden descendants.

```python
async def freeze_review_profile_snapshot(
    db: AsyncSession,
    *,
    profile_id: UUID | None,
) -> ReviewProfileSnapshot: ...
```

If `profile_id` is `None`, resolve `assignment_key='testing'`. If non-null, resolve that profile ID. The future router/admission packet must enforce platform-admin-only explicit profile selection; the service should still fail closed if `get_llm_config` cannot resolve the profile. The snapshot stores the non-secret allowlist above and a canonical fingerprint.

```python
async def assert_review_sources_readable(
    db: AsyncSession,
    user: CurrentActiveUser,
    *,
    review_run: AgentReviewRun,
) -> None: ...
```

Future route/finding readers use this helper. It must batch-check every `source_refs[].run_id` / `selected_run_ids` using `agent_run_visibility_conditions(user)`, require all source IDs present, require original source agent/org identity to match the frozen `source_refs`, and fail closed if review/domain rows or any source row is missing or revoked. It must not call `load_recorded_run_evidence` on reads because evidence may have grown after freezing.

```python
async def execute_agent_review_job(context: PlatformJobContext, review_run_id: UUID) -> None: ...
```

This is the only executor entry point. It loads and validates `AgentReviewRun`, `AgentReviewDefinition`, `AgentReviewVersion`, requester `User`, frozen profile snapshot, and PlatformJob binding, then performs the durable attempt/call/usage/fence/finding write sequence below.

## Job payload/definition

In `api/shared/models.py`:

```python
class AgentReviewJobPayload(BaseModel):
    review_run_id: UUID
```

In `api/src/jobs/platform/agent_review.py`:

```python
agent_review_job = PlatformJobDefinition(
    job_type="agent.review",
    payload_version=1,
    payload_model=AgentReviewJobPayload,
    handler=handle_agent_review_job,
    policy=PlatformJobPolicy(
        timeout_seconds=10 * 60,
        max_attempts=1,
        retry_on_runner_loss=False,
        allow_running_cancellation=True,
    ),
)
```

`handle_agent_review_job(context, payload)` only calls `shared.agent_reviews.execute_agent_review_job(context, payload.review_run_id)`.

The executor must verify `review_run.platform_job_id == context.job_id` and `review_run.org_id == context.organization_id` before any accounting or model call. For nullable `org_id`, require both sides exactly match; do not coerce `None` to a tenant. Mismatch fails before provider dispatch and writes no findings/usage.

## Strict execution sequence

1. Open a short DB transaction and acquire `lock_running_platform_job_for_result_write(...)` before any domain write.
2. Load `AgentReviewRun`, definition, version, and requester `User`. Missing rows or malformed/missing requester fail before provider dispatch. Do not import any router dependency to synthesize a user; the executor only validates stored UUIDs.
3. Validate profile snapshot against current profile/config. A deleted or materially changed profile fails before provider dispatch and before accounting attempt.
4. Compute `idempotency_key = f"agent-review:{review_run.id}:review"` and `request_fingerprint = review_run.request_fingerprint`.
5. Call `begin_quality_usage_attempt(...)` under the job fence with:
   - `quality_operation_type='agent_review'`
   - `quality_operation_id=review_run.id`
   - `quality_operation_item_id='review'`
   - `usage_purpose='agent_review'`
   - `organization_id=review_run.org_id`
   - `user_id=review_run.requested_by_user_id`
   - `platform_job_id=context.job_id`
   - `provider=canonical_provider(snapshot.provider, snapshot.endpoint)`
   - `model=snapshot.model`
   - `profile_id/profile_name/profile_fingerprint` from the frozen snapshot
6. If the attempt already exists, fail closed as ambiguous/no-replay before provider dispatch. Do not call the model, do not insert findings, and do not overwrite terminal results.
7. Persist a started domain marker only if needed for no-replay diagnostics. Phase 3A schema has no status field; the durable `AIUsageAttempt` is the pre-call marker.
8. Commit the attempt transaction before provider dispatch.
9. Call the provider exactly once outside any DB transaction. The call input must contain only the selected `review_statement`, optional `evidence_format_instructions`, and frozen recorded evidence projection.
10. Immediately after response returns, open a short transaction and record usage before parsing and before result fence:
    - If `response.usage` has valid nonnegative integer `input_tokens` and `output_tokens`, call `record_quality_usage_observation(...)`. Optional cache counts must be nonnegative integers; optional `provider_cost` may be recorded. Do not coerce `None`/bool/string invalid counts to zero.
    - If a response exists but usage counts are missing/invalid, call `mark_quality_usage_unobserved(..., reason='missing_usage')`.
    - If provider/config dispatch fails after the attempt exists and no response was observed, call `mark_quality_usage_unobserved(..., reason='provider_error')`.
    - Commit this accounting transaction independently of verdict/finding writes.
11. Parse strict structured output with the caps below. Invalid output does not create findings and does not make another model call.
12. Reacquire `lock_running_platform_job_for_result_write(...)`. If stale, cancelled, or lease lost, return/raise `PlatformJobCancelled` without writing findings or `result_summary`; the observed usage remains recorded.
13. Insert findings under the fence with deterministic `source_ordinal` 0..N-1 and `source_review_*` provenance. Let the unique `(source_review_run_id, source_ordinal)` constraint prevent duplicates. Treat an existing ordinal row as idempotent only if every persisted field matches the draft; otherwise fail closed.
14. Set `AgentReviewRun.result_summary` under the same fence. Do not write lifecycle status/error fields; PlatformJob owns lifecycle.
15. Commit and report completion through the PlatformJob context if the handler pattern requires it.

## Model request and response contract

Request content must be deterministic and bounded. Proposed JSON sent to the model:

```json
{
  "review_statement": "...",
  "evidence_format_instructions": "... or null",
  "selected_runs": [
    {
      "run_id": "...",
      "evidence": {...},
      "completeness": {...},
      "limitations": [...]
    }
  ],
  "instructions": "Return JSON only with summary and findings. Findings must cite source_run_ids from selected_runs."
}
```

Use `response_format={"type": "json_object"}` if supported by the existing client call pattern. Do not use tools.

Strict response shape:

```python
{
    "summary": str | None,          # max 2000 chars
    "findings": [                  # max 20 items
        {
            "kind": "problem" | "opportunity",
            "description": str,    # 1..4000 chars
            "expected_behavior": str | None,  # max 4000 chars
            "evidence_markdown": str | None,  # max 20000 chars
            "source_run_ids": list[UUID],     # nonempty subset of review_run.selected_run_ids
        }
    ]
}
```

Validation failures map to a job failure with redacted/generic error text. They do not insert findings. If usage was observed, usage stays recorded.

## Error mapping

`AgentReviewServiceError.code` values for tests and future routers:

- `review_not_found` -> 404
- `review_disabled` -> 409 for admission helpers; executor treats missing/disabled as terminal job failure before provider call
- `review_unauthorized` -> 404
- `source_run_not_found` -> 404
- `source_run_wrong_agent` -> 422 at admission
- `source_evidence_incomplete` -> 422 at admission
- `review_input_oversized` -> 413
- `requester_not_found` -> executor failure before provider call
- `profile_unavailable` -> 422 at admission / executor failure before provider call
- `profile_drift` -> executor failure before provider call
- `job_mismatch` -> executor failure before provider call
- `review_attempt_already_started` -> executor failure before provider call; no replay
- `provider_error` -> executor failure; mark usage attempt unobserved when attempt exists
- `invalid_review_response` -> executor failure after recording usage/unobserved
- `job_cancelled_or_stale` -> raise `PlatformJobCancelled` after preserving usage

Future HTTP routes can translate these directly; this packet should not add routes.

## Tests required for this service packet

Focused unit/helper tests:

- `build_review_evidence_input` dedupes/sorts run IDs, rejects >20, calls only `load_recorded_run_evidence`, rejects wrong-agent evidence, preserves limitations/source refs, and enforces 4 MiB canonical input.
- `freeze_review_profile_snapshot` resolves default `testing`, explicit profile by ID, stores only the non-secret allowlist, and produces stable fingerprint changes when material config changes.
- structured parser accepts exactly valid output and rejects: non-object, >20 findings, invalid kind, empty description, oversized summary/description/expected/evidence, missing/foreign `source_run_ids`, duplicate/empty source IDs.
- usage extraction rejects missing/negative/bool/noninteger token/cache counts without inventing zero.

DB/executor tests using actual handler path and deterministic fake client:

- wrong `platform_job_id` or `organization_id` writes no attempt, no usage, no findings.
- requester UUID not found writes no attempt, no usage, no findings.
- profile drift/deleted profile writes no attempt and makes no provider call.
- happy path makes exactly one provider call, inserts one `AIUsageAttempt`, one `AIUsage`, <=20 findings with complete review provenance, source refs, `source_kind='run'`, and `result_summary`.
- malformed response with nonzero usage records usage before parse failure and inserts no findings.
- provider error after started attempt marks unobserved `provider_error` and inserts no findings.
- response with missing usage marks unobserved `missing_usage` and inserts no zero `AIUsage` row.
- cancellation/stale fence after model response records usage but inserts no findings and leaves `result_summary` unset.
- duplicate executor run after a started attempt does not call the provider again and does not create findings.
- duplicate ordinal insertion is idempotent only for matching fields; conflicting existing ordinal fails closed.
- dismissed/manual findings are untouched.
- review operation accounting uses `quality_operation_type='agent_review'`, `usage_purpose='agent_review'`, `quality_operation_id=review_run.id`, and does not attach cost to production source `AgentRun` rows.
- `assert_review_sources_readable` denies whole result when any frozen source run is missing/revoked/wrong original identity and succeeds without invoking `load_recorded_run_evidence`.

Suggested commands for the implementer after stack release:

```bash
./test.sh tests/unit/services/test_agent_reviews.py -v
./test.sh tests/e2e/api/test_agent_review_executor.py -v
./test.sh quality api
```

If `AgentReviewJobPayload` changes the shared CLI/server contract fingerprint, also run the existing contract tripwire and refresh only the additive fingerprint if primary confirms no minimum CLI bump is needed.

## Non-goals and explicit exclusions

- No routes, CLI, generated OpenAPI/types, public DTOs except `AgentReviewJobPayload`.
- No scheduler or recurring review support.
- No review status endpoint; PlatformJob remains lifecycle.
- No semantic dedupe across review runs.
- No test generation, prompt application, candidate creation, or finding dismissal.
- No paid/model call in tests.
- No fallback from missing review provenance to legacy finding fields.
