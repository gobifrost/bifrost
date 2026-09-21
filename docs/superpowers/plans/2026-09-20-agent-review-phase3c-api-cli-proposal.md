# Agent Review Phase 3C API and CLI Proposal

> **For agentic workers:** This is a primary-review proposal only. Do not implement until primary issues an approved bounded implementation packet.

**Goal:** Add the public API and CLI surface for versioned agent review definitions, review admissions/results/usage reads, and additive finding fields/search on top of the accepted Phase 3A data foundation and Phase 3B service/executor.

**Architecture:** HTTP handlers stay thin and call shared business/admission/read helpers. PlatformJob remains the only lifecycle/status/cancel surface; review-domain rows carry immutable definition/version/run facts and parsed results only. Reads of review-derived content are fail-closed against current source authorization, while global finding lists omit unauthorized derived items without leaking hidden counts.

**Tech Stack:** FastAPI, SQLAlchemy/PostgreSQL, Pydantic models in `api/shared/models.py`, existing `PlatformJob` enqueue/status APIs, existing `agent-tests` Click CLI patterns, existing `shared.quality_usage_reporting` operation reports.

---

## Scope and non-goals

Phase 3C owns the review definition CRUD/version/admission/read/results/usage API, matching CLI commands, and additive finding DTO/router extensions needed to expose review-derived findings safely. It does not add UI, schedules, MCP tools, background pollers, new job lifecycle routes, automatic fixes, prompt application, test creation, or reporting aggregation beyond per-review-run usage reads through the accepted quality reporting service.

The accepted data foundation already provides ORM tables for review definitions, versions, review runs, and review provenance columns on `AgentFinding`. Phase 3B provides executor/parser/evidence/profile/read-authorization primitives. This packet must not redesign those internals. If a Phase 3B helper name differs at implementation time, keep the public semantics below and adapt the route layer to the accepted helper name.

## Existing surfaces to preserve

Existing finding API paths keep working:

- `POST /api/agent-findings`
- `GET /api/agent-findings?agent_id=...` returning the existing bare list shape
- `GET /api/agent-findings/{finding_id}`
- `PATCH /api/agent-findings/{finding_id}`

Existing manual/run/external finding creation remains accepted. Additive finding fields are optional/defaulted, so old clients can ignore them. Manual findings may set `finding_kind` and `evidence_markdown`, but manual/API callers cannot set or spoof `source_review_id`, `source_review_version_id`, `source_review_run_id`, `source_review_version`, `source_run_refs`, or `source_ordinal`; those fields are service-owned for review-created findings.

Existing recorded evaluation APIs and CLI commands remain unchanged. If any finding commands already exist under `agent-tests`, preserve them. New review commands should live in a top-level `agent-reviews` CLI command group, not under `agent-tests`. New global finding search/create commands should live in a top-level `agent-findings` CLI command group or extend an existing finding group without moving old command names.

## Shared Pydantic models

Add new DTOs in `api/shared/models.py`, not in `api/src/models/contracts/*`. Existing repository instructions and Phase 3B corrections require new Pydantic request/response models here.

### Review definition and version DTOs

```python
AgentReviewStatus = Literal["active", "disabled"]
AgentReviewFindingKind = Literal["problem", "opportunity"]

class AgentReviewDefinitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    organization_id: UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    review_statement: str = Field(min_length=1, max_length=8000)
    evidence_format_instructions: str | None = Field(default=None, max_length=4000)
    model_profile_id: UUID | None = None

class AgentReviewDefinitionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: AgentReviewStatus | None = None

class AgentReviewVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_statement: str = Field(min_length=1, max_length=8000)
    evidence_format_instructions: str | None = Field(default=None, max_length=4000)
    model_profile_id: UUID | None = None

class AgentReviewVersionPublic(BaseModel):
    id: UUID
    review_id: UUID
    version: int
    review_statement: str
    evidence_format_instructions: str | None = None
    model_profile_id: UUID | None = None
    created_by: UUID | None = None
    created_at: datetime

class AgentReviewDefinitionPublic(BaseModel):
    id: UUID
    agent_id: UUID
    org_id: UUID | None = None
    name: str
    status: AgentReviewStatus
    latest_version: int
    latest_version_id: UUID
    latest_version_created_at: datetime
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime
```

### Review run DTOs

```python
class AgentReviewRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_ids: list[UUID] = Field(min_length=1, max_length=20)

class AgentReviewRunAccepted(BaseModel):
    review_run_id: UUID
    job_id: UUID
    reused: bool
    notification_id: UUID | None = None

class AgentReviewSourceRef(BaseModel):
    run_id: UUID
    agent_id: UUID | None
    org_id: UUID | None = None
    root_run_id: UUID | None = None
    parent_run_id: UUID | None = None
    trigger_type: str | None = None

class AgentReviewRunPublic(BaseModel):
    id: UUID
    review_id: UUID
    review_version_id: UUID
    review_version: int
    agent_id: UUID
    org_id: UUID | None = None
    platform_job_id: UUID | None = None
    selected_run_ids: list[UUID]
    source_refs: list[AgentReviewSourceRef]
    result_summary: str | None = None
    created_at: datetime

class AgentReviewRunResults(BaseModel):
    review_run: AgentReviewRunPublic
    findings: list[FindingPublic]
    usage_operation_type: Literal["agent_review"] = "agent_review"
    usage_operation_id: UUID
```

`AgentReviewSourceRef` is identity-only: selected/descendant run IDs, nullable agent/org identity, root/parent IDs, and trigger type. Frozen projection content, summary text, prompt text, raw model response, and provider config are intentionally omitted from public run DTOs. Generic `PlatformJob.result` remains counts/verdict only and must never be used to derive evidence or review summary because source permissions may be revoked after execution.

### Additive finding DTO fields

Move the existing `FindingCreate`, `FindingUpdate`, `FindingPublic`, `FindingStatus`, and `FindingSourceKind` definitions into `api/shared/models.py` with their original class names, fields, `ConfigDict` settings, defaults, and validation preserved. Then change `api/src/models/contracts/agent_findings.py` into an authorized compatibility shim that imports and re-exports those names. This satisfies the shared-model rule for new public DTO work without breaking existing imports.

Add new optional fields in `api/shared/models.py`.

Extend `FindingCreate` additively:

```python
finding_kind: Literal["problem", "opportunity"] = "problem"
evidence_markdown: str | None = Field(default=None, max_length=20000)
```

Do not add review provenance fields to `FindingCreate`; clients cannot provide them.

Extend `FindingUpdate` additively:

```python
finding_kind: Literal["problem", "opportunity"] | None = None
evidence_markdown: str | None = Field(default=None, max_length=20000)
```

`FindingUpdate.evidence_markdown=None` is ambiguous under the existing optional-update pattern. Use `model_fields_set` in the router so explicit `null` clears evidence markdown and omitted leaves it unchanged.

Extend `FindingPublic` additively:

```python
finding_kind: str = "problem"
evidence_markdown: str | None = None
source_review_id: UUID | None = None
source_review_version_id: UUID | None = None
source_review_run_id: UUID | None = None
source_review_version: int | None = None
source_run_refs: list[dict[str, Any]] = Field(default_factory=list)
source_ordinal: int | None = None
```

When serializing review-derived findings, the read layer must include these fields only after current authorization verifies the review run and every frozen source reference. The field names match the accepted ORM columns; no compatibility aliases are needed. Review provenance remains read-only at the HTTP and CLI layer.

## Review authorization rules

Review definitions use the same agent visibility policy accepted in Phase 3B: a `UserPrincipal`-compatible check that covers private ownership, role-based access, external users, and global/shared agents. Do not replace it with tenant-only logic.

Review organization scope is immutable and is chosen only when the definition is created. It is never inferred from later selected runs.

For non-superusers:

- `user.organization_id is None` fails closed.
- `AgentReviewDefinitionCreate.organization_id` must be omitted or equal to `user.organization_id`; any other value, including explicit `NULL` for a tenant user, is rejected.
- Review `org_id` is the caller tenant, even when the agent definition is global/shared.
- Runs admitted to a review must be visible to the caller and belong to the same selected agent and tenant.

For superusers/platform admins:

- For a non-global agent, review `org_id` is fixed to `agent.organization_id`; an explicit `organization_id` must match it.
- For a global agent (`agent.organization_id IS NULL`), the admin may choose an existing tenant UUID with `organization_id`, or omit it / pass `NULL` to create a global review scope. This enables provider-admin review definitions for tenant runs of a global agent without mixed-tenant operation.
- Admission requires every selected source and every frozen descendant contributor to match the definition `org_id` exactly, including `NULL`. Mixed tenant selections are rejected.
- Explicit `model_profile_id` on a review version is allowed only for platform admins. Non-admin explicit profile selection returns 403 because the caller is known but lacks permission.

Default profile policy:

- Omitted `model_profile_id` resolves the existing global `testing` assignment at admission/execution snapshot time.
- Explicit `model_profile_id` is platform-admin-only and freezes the accepted non-secret profile snapshot.
- Review execution validates profile drift before dispatch as Phase 3B requires.

## API routes

Create top-level `api/src/routers/agent_reviews.py` and register it in `api/src/routers/__init__.py` and `api/src/main.py`. Review routes use `/api/agent-reviews`, matching the approved master/Phase 3A direction.

### Definition CRUD and versions

`POST /api/agent-reviews` → `AgentReviewDefinitionPublic`

- Body: `AgentReviewDefinitionCreate`.
- Authorize agent.
- Validate explicit profile policy before writing version 1.
- Resolve and persist immutable review `org_id` from `organization_id` according to the organization-scope rules above. Validate any admin-supplied tenant UUID exists.
- Create definition and version atomically; `latest_version=1`.
- No PlatformJob.

`GET /api/agent-reviews?agent_id=...&status=active|disabled|all&limit=&offset=` → paged list

- Agent ID is required in Phase 3C to keep scope bounded.
- Non-superusers see only their tenant-scoped reviews for visible agents.
- Superusers see reviews for the visible agent, with optional `organization_id` filtering when needed.
- Default status filter is active.
- Return a small page DTO with `items`, `total`, `limit`, `offset` rather than an unbounded list.

`GET /api/agent-reviews/{review_id}` → `AgentReviewDefinitionPublic`

- 404 on missing/unauthorized.
- Include latest version metadata only, not all historical version bodies.

`PATCH /api/agent-reviews/{review_id}` → `AgentReviewDefinitionPublic`

- Body: `AgentReviewDefinitionUpdate`.
- Updates name/status only.
- No delete route. Disable via `status='disabled'`.

`POST /api/agent-reviews/{review_id}/versions` → `AgentReviewVersionPublic`

- Body: `AgentReviewVersionCreate`.
- Reject disabled/missing/unauthorized review.
- Validate explicit profile policy.
- Lock review definition, increment `latest_version`, insert immutable version, update review `updated_at`.
- Existing runs keep their old version.

`GET /api/agent-reviews/{review_id}/versions?limit=&offset=` → paged list of `AgentReviewVersionPublic`

- Ordered newest first or version descending; document the chosen order.
- Same review read authorization.

### Admission and lifecycle linkage

`POST /api/agent-reviews/{review_id}/runs` → `202 AgentReviewRunAccepted`

- Body: `AgentReviewRunCreate`.
- Calls `shared.agent_review_admission.admit_agent_review_run`; route does not build frozen evidence itself. Phase 3B's `shared.agent_reviews.py` executor/auth module remains owned by that service packet, so admission lives in a separate shared module.
- Response body is `AgentReviewRunAccepted` with `job_id`, `review_run_id`, `reused`, and optional `notification_id`. It does not include a domain lifecycle status; clients use the generic PlatformJob for lifecycle.
- Response sets `Location: /api/platform-jobs/{job.id}` and `X-Agent-Review-Run-Id: {review_run.id}`.
- Publish platform job update only after commit, matching recorded evaluation route.
- No bespoke review status endpoint is introduced.

Admission must be atomic:

1. Lock review definition and current latest version.
2. Build canonical request fingerprint from review/version, selected run IDs, frozen source identities/evidence hashes, profile snapshot fingerprint, requester UUID, and the immutable definition organization scope.
3. Use an advisory/transactional guard plus `enqueue_platform_job` active dedupe. Dedupe key must include requester UUID so a different user never reuses an unreadable active job.
4. Generate review run UUID before enqueue and put only that ID in the shared job payload.
5. For a new active job, create one review run row with `platform_job_id` in the same transaction.
7. For a reused active job, load the existing review run by unique `platform_job_id`/resource ID and return it. Missing reused domain row is an invariant error; do not repair by inventing a row.
8. If the matching prior job is terminal, create a new review run and a new PlatformJob even for the same request fingerprint.

The PlatformJob definition remains from Phase 3B or its follow-up registry packet. Payload contains only `review_run_id`. The job result/progress contains only non-sensitive counts such as selected run count, finding count, and parse verdict. It never contains source evidence or summary text.

`GET /api/agent-reviews/runs/{review_run_id}` → `AgentReviewRunPublic`

- Requires current review/run read authorization.
- Revalidates every frozen source ref against current requester permissions. If any source is missing/deleted/revoked, return 404 for the whole run item.

`GET /api/agent-reviews/runs/{review_run_id}/results` → `AgentReviewRunResults`

- Same whole-run fail-closed read rule.
- Returns `review_run` and review-created findings for that run, ordered by `source_ordinal`.
- If source access has been revoked for any frozen source, return 404 for the whole derived result. Do not return partial summary/evidence because the review content is derived from the full source set.

`GET /api/agent-reviews/runs/{review_run_id}/usage` → `QualityUsageBreakdownResponse`

- Same run read authorization first.
- Calls `summarize_quality_operation_usage(... operation_type='agent_review', operation_id=review_run.id ...)`.
- Uses `ReadSnapshotDbSession` like recorded evaluation usage.

Do not add review-specific status/cancel endpoints. CLI conveniences use generic `/api/platform-jobs/{id}` and cancel endpoints.

## Finding API and search extensions

### Single finding reads

`GET /api/agent-findings/{id}` keeps the old path. For non-review findings, behavior is unchanged. For review-derived findings, `_to_public` must verify:

- finding tenant/agent visibility;
- review provenance is complete;
- referenced review definition/version/run rows exist;
- current caller may read the review run;
- every contributor frozen on the referenced `AgentReviewRun.source_refs` is still authorized for this caller, not only the subset cited by this finding. The review model saw the whole selected/descendant input set, so revoking an uncited source must hide every derived finding from that run.
- `AgentFinding.source_run_refs` is a nonempty subset of `AgentReviewRun.source_refs`, with complete provenance matching the same review/version/run/agent/org rows.

If any check fails, return 404 for this finding. This is a deliberate fail-closed rule: frozen derived evidence is not an authorization bypass.

### Agent-scoped list

Existing `GET /api/agent-findings?agent_id=...` remains available. Add optional filters:

- `status=open|dismissed`
- `finding_kind=problem|opportunity`
- `source_kind=run|manual|external`
- `review_id=UUID`
- `review_run_id=UUID`
- `q=...` simple case-insensitive search over description, expected behavior, and evidence markdown
- `limit`, `offset`

For compatibility, if the existing route must continue returning a bare list, add a second paged route instead of changing the response shape:

- `GET /api/agent-findings/search` → `FindingSearchPage`

Keep existing list as a bare list for old CLI/UI and add `/search` for global/search pagination. Register `/search` before `/{finding_id}` in `api/src/routers/agent_findings.py` so the literal path is not captured as a UUID.

### Global searchable findings

`GET /api/agent-findings/search` supports no required `agent_id`. It returns findings across all agents visible to the caller. Non-superusers are limited to `AgentFinding.org_id == user.organization_id` plus current agent visibility. Superusers can search across orgs with optional `organization_id` filter.

Review-derived authorization behavior for global lists:

- Do not fail the entire list because one derived item has revoked/missing source access.
- Omit unauthorized derived items before total/count/pagination are computed.
- Counts and pages reflect only visible items, so hidden derived findings do not leak existence through `total` or empty gaps.
- Use a concrete SQL visibility predicate before count/offset; do not fetch candidate pages in application code and do not count the whole ledger first.

Concrete SQL predicate shape:

1. Base finding visibility:
   - Non-superuser: `AgentFinding.org_id == user.organization_id`.
   - Superuser: optional `organization_id` filter, otherwise no tenant restriction.
   - Join `Agent` on `Agent.id == AgentFinding.agent_id`. Implement a reusable SQL predicate helper in the review/finding read layer with the same semantics as `api/src/routers/agent_evaluations.py::_authorized_agent` and `_entity_access_allowed`: non-superusers require `Agent.organization_id IN (user.organization_id, NULL)` for shared/global visibility, except private owner access can still match `Agent.owner_user_id == user.user_id`; `everyone` is visible; `authenticated` is visible when `user.is_external` is false; `private` is visible only to owner; `role_based` is visible via `EXISTS (SELECT 1 FROM agent_roles ar JOIN user_roles ur ON ur.role_id = ar.role_id WHERE ar.agent_id = Agent.id AND ur.user_id = :user_id)`. Superusers bypass access-level/role restrictions. Do not call the Python router helper from SQL paging code; extract or duplicate the SQL form in shared read helpers so count/page use the same predicate.
2. Non-review findings are visible when base finding visibility passes and all review provenance columns are null.
3. Review-derived findings require complete provenance and a matching `AgentReviewRun`, `AgentReviewDefinition`, and `AgentReviewVersion` row. Missing domain rows fail closed.
4. `AgentReviewRun.source_refs` is the authoritative contributor set for derived finding visibility. It must be a nonempty JSON array and every contributor element must be authorized. `AgentFinding.source_run_refs` must also be a nonempty JSON array and a subset of the run's contributor set, but visibility is not limited to the subset.
5. Guard every `jsonb_array_elements` call with `CASE WHEN jsonb_typeof(refs) = 'array' THEN refs ELSE '[]'::jsonb END`; SQL boolean evaluation order cannot be relied on to protect malformed JSON shape before expansion. Guard length checks the same way: `jsonb_array_length(CASE WHEN jsonb_typeof(refs) = 'array' THEN refs ELSE '[]'::jsonb END) > 0`. Derived rows also require `jsonb_typeof(refs) = 'array'`, so malformed/empty refs fail closed instead of silently authorizing.
6. Every contributor/finding ref element must contain a text UUID `run_id`, an `agent_id` key whose value is either text UUID or explicit JSON null, an `org_id` key whose value is text UUID or JSON null matching the review org, and a `trigger_type` key. Missing `agent_id` is not the same as explicit JSON null and fails closed. Selected root source refs must have `agent_id == review.agent_id` and `trigger_type != 'evaluation_synthetic'`; descendant refs may use explicit JSON null for deleted historical descendant agents preserved by the accepted recorded evidence identity freeze.
7. Compare IDs as text when possible (`AgentRun.id::text = elem->>'run_id'`) or use a regex-guarded `CASE` before UUID casts. Never perform an unchecked UUID cast from JSON text. Compare nullable `agent_id` and `org_id` null-safely: explicit JSON null must match SQL NULL; text UUID must match the current row text value. Invalid JSON shape, missing keys, invalid UUID text, or mismatched agent/org identity fail closed.
8. For every contributor in `AgentReviewRun.source_refs`, require an existing `AgentRun` row with the same ID, null-safe agent ID, org ID matching the review org, matching root/parent identity when present, and matching `trigger_type`. Reuse `api/src/services/execution/agent_run_access.py::agent_run_visibility_conditions(user)` directly in the contributor `EXISTS` subquery so the read policy matches recorded evidence and 3B source reads. Missing/deleted/revoked runs fail closed.
9. For every finding ref in `AgentFinding.source_run_refs`, require a matching contributor element in `AgentReviewRun.source_refs`; otherwise the finding provenance is inconsistent and the item is hidden.
10. The same visibility predicate is used for the old agent-scoped bare list, the global search `SELECT count(*)`, and the global search page query, so pagination and counts cannot leak hidden derived findings. Run count and page under the same read snapshot/session so a permission change between queries cannot make the returned `total` inconsistent with the page.

`FindingSearchPage` in `api/shared/models.py`:

```python
class FindingSearchPage(BaseModel):
    items: list[FindingPublic]
    total: int
    limit: int
    offset: int
```

## CLI commands

Create a new CLI command module such as `api/bifrost/commands/agent_reviews.py` and register a top-level `agent-reviews` group. Preserve any existing `agent-tests findings` commands if they exist; do not add review commands under `agent-tests`. Keep JSON stdout parseable on nonzero exits. Human chatter goes to stderr.

Support `--file PATH` for create/version/run workflows. The file is YAML or JSON and maps directly to the bounded request DTO fields. Literal flags remain available for short statements and run IDs, but long review statements/evidence formatting should use `--file` rather than mandatory shell-escaped text.

Exit codes:

- `0`: command/admission/read succeeded. A review that produced findings is still success; findings are not test failures.
- `2`: local invocation/validation error.
- `3`: server job failed, was cancelled, or infrastructure status is terminal unsuccessful.
- `4`: local wait timeout or completed review results are incomplete/unavailable because derived source access is now denied.

### Review definition commands

`agent-reviews create --agent REF --name NAME --statement TEXT [--evidence-format TEXT|--evidence-format-file PATH] [--profile-id UUID] [--org-id UUID|global] [--file PATH]`

- Prints `AgentReviewDefinitionPublic` JSON.
- `--profile-id` surfaces server 403/422 for non-admin explicit profile.
- `--org-id` maps to `organization_id`; `--org-id global` sends explicit null. Non-admin callers normally omit it; server validation enforces ownership/scope. DTO parity should cover this field like other command flags.

`agent-reviews list --agent REF [--status active|disabled|all] [--limit N] [--offset N]`

`agent-reviews get REVIEW_ID`

`agent-reviews update REVIEW_ID [--name NAME] [--status active|disabled]`

`agent-reviews version REVIEW_ID --statement TEXT [--evidence-format TEXT|--evidence-format-file PATH] [--profile-id UUID] [--file PATH]`

`agent-reviews versions REVIEW_ID [--limit N] [--offset N]`

### Review run commands

`agent-reviews run REVIEW_ID --runs ID,ID [--wait] [--file PATH]`

- Without wait: prints `AgentReviewRunAccepted` including job ID and review run ID.
- With wait: polls generic PlatformJob. On terminal success, fetches `/api/agent-reviews/runs/{id}/results` and prints results JSON.
- Local wait timeout exits `4` and explains on stderr that server operation may continue.

`agent-reviews results REVIEW_RUN_ID`

- Fetches results route.
- If source access revoked, prints JSON error envelope or existing CLI error behavior for 404 without leaking hidden details.

`agent-reviews usage REVIEW_RUN_ID [--limit N] [--offset N]`

`agent-reviews status JOB_ID`

- Thin wrapper over generic platform job get.

`agent-reviews cancel JOB_ID`

- Thin wrapper over generic platform job cancel.

### Finding commands

Additive only:

`agent-findings create --agent REF --description TEXT [--expected TEXT] [--kind problem|opportunity] [--evidence-markdown TEXT|--evidence-markdown-file PATH] [--source-run ID --source-sequence N | --external-ref TEXT]`

Manual source remains allowed. This command cannot send review provenance fields.

`agent-findings search [--agent REF] [--status open|dismissed] [--kind problem|opportunity] [--review REVIEW_ID] [--review-run REVIEW_RUN_ID] [--q TEXT] [--limit N] [--offset N]`

Use `/api/agent-findings/search` so global searchable findings work through the CLI.

## File ownership for implementation packet

Likely files to modify/create:

- Create `api/src/routers/agent_reviews.py` for top-level review routes.
- Modify `api/src/routers/__init__.py` and `api/src/main.py` to register the router.
- Modify `api/shared/models.py` for review DTOs and `FindingSearchPage`.
- Move existing finding DTO definitions to `api/shared/models.py` with additive fields, and change `api/src/models/contracts/agent_findings.py` into a compatibility re-export shim.
- Modify `api/src/routers/agent_findings.py` for additive fields, global search, and review-derived read filtering.
- Create `api/shared/agent_review_admission.py` for API admission/read helpers. Keep Phase 3B executor/auth helpers in their accepted module; do not merge route admission into the executor.
- Create `api/bifrost/commands/agent_reviews.py` and register it in `api/bifrost/commands/__init__.py`.
- Create `api/bifrost/commands/agent_findings.py` for the new top-level `agent-findings` group and register it in `ENTITY_GROUPS`. Current CLI inspection shows no existing finding group, so no legacy finding CLI aliases are required.
- Modify `api/bifrost/dto_flags.py` only if DTO parity tests require an explicit include/exclude decision. Additive shared DTOs used by FastAPI may affect contract fingerprint tests.
- Tests:
  - `api/tests/e2e/api/test_agent_reviews.py`
  - `api/tests/unit/services/test_agent_review_admission.py`
  - extend `api/tests/e2e/api/test_agent_findings.py`
  - extend `api/tests/unit/test_dto_flags.py` only if parity expectations need new references
  - `api/tests/unit/test_agent_reviews_cli.py` and focused top-level finding CLI tests

Do not edit Phase 3B executor internals unless a helper signature mismatch blocks route usage; return that to primary for a service-packet correction.

## Test plan for implementation approval packet

Focused tests must prove these contracts:

1. Definition create/get/list/update/version with agent authorization, immutable organization scoping, disabled reviews rejected for admission, and non-admin explicit profile rejected. Include cases: non-admin omitted/own org accepted, non-admin foreign/null-org denied, non-global agent explicit mismatch denied, admin global-agent explicit existing tenant accepted, admin global-agent omitted/null creates global scope, unknown tenant UUID denied.
2. Omitted profile uses default `testing`; explicit profile accepted for platform admin and frozen through existing service helper.
3. Admission with selected runs returns `202`, `Location`, `X-Agent-Review-Run-Id`, one domain row, one active PlatformJob, payload containing only review run ID.
4. Concurrent duplicate admission by same requester returns the same active job/run; terminal rerun creates a new job/run.
5. Duplicate admission by another user never reuses an unreadable job.
6. Hidden/missing/wrong-agent/synthetic/nonterminal selected roots fail without partial admission; selected root `agent_id` must match the review agent. Descendant source org IDs must exactly match the definition scope including `NULL`; descendant `agent_id` may be explicit JSON null only for preserved deleted historical descendant identity.
7. Review run read/results/usage deny the whole derived item if any frozen source is revoked/deleted/missing.
8. Generic PlatformJob result/progress does not contain evidence, prompt, source summary, or result summary.
9. Review-created findings expose complete provenance and cannot be spoofed through `FindingCreate`/`FindingUpdate`.
10. Manual findings can set `finding_kind` and `evidence_markdown` without review provenance.
11. Single finding get returns 404 for a review-derived finding when access to any contributor frozen on the review run is revoked, even if that source is not cited by the individual finding.
12. Old bare finding list and global finding search omit unauthorized derived items with the same SQL predicate before count/offset, reject malformed review provenance/source refs fail-closed per item, and never fail the whole list or leak hidden counts.
13. CLI create/list/version/run/results/usage/status/cancel produce parseable JSON; `--wait` uses generic PlatformJob then results route.
14. DTO/CLI contract tripwires pass; OpenAPI generation discovers shared DTOs through route signatures.

Run targeted commands in the implementation packet:

```bash
./test.sh tests/unit/services/test_agent_review_admission.py -v
./test.sh tests/e2e/api/test_agent_reviews.py -v
./test.sh tests/e2e/api/test_agent_findings.py -v
./test.sh tests/unit/test_agent_reviews_cli.py tests/unit/test_agent_findings_cli.py tests/unit/test_agent_tests_recorded_cli.py -v
./test.sh tests/unit/test_dto_flags.py tests/unit/test_contract_version.py -v
./test.sh quality api
```

If DTO shapes change the OpenAPI/CLI contract fingerprint, update only the fingerprint or minimum CLI version according to the repository contract-version rule. Regenerate client types only if the approved implementation packet explicitly includes generated OpenAPI/client files.

## Resolved inspection notes

- Agent visibility should use the SQL predicate shape above, derived from `api/src/routers/agent_evaluations.py::_authorized_agent` and `_entity_access_allowed`; current router helpers are Python authorization functions, so Phase 3C should add a shared SQL helper rather than page in application code.
- Agent-run contributor visibility should reuse `api/src/services/execution/agent_run_access.py::agent_run_visibility_conditions(user)`, matching existing recorded evidence and shared review source reads.
- Current CLI registration in `api/bifrost/commands/__init__.py` has `agent-tests` but no existing finding command group, so Phase 3C adds `agent-findings` without legacy finding aliases and leaves existing `agent-tests` commands in place.
