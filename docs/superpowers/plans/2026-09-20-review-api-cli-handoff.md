# On-Demand Review API and CLI — Approved Bounded Handoff

Primary owns acceptance. Executor: native worker (GPT-5.5), worktree durable-agent-platform-backend, preserving all prior changes. Follow the reviewed `2026-09-20-agent-review-phase3c-api-cli-proposal.md` for **review routes and agent-reviews CLI only**. Findings search/additive DTO/CLI and scheduling are separate packets.

## Ownership and boundaries

- New `api/shared/agent_review_admission.py`: definition/version CRUD, atomic admission, authorized run/result reads; UserPrincipal and domain errors, no router imports.
- New `api/src/routers/agent_reviews.py`, router registration in __init__/main.
- `api/shared/models.py`: new review public/request DTOs only, preserve all existing models and executor payload.
- New `api/bifrost/commands/agent_reviews.py`, command registration; focused review CLI tests.
- New `api/tests/e2e/api/test_agent_reviews.py`, focused admission unit tests.
- Contract fingerprint and generated CLI/OpenAPI appendices/client v1 types after functional verification. No manual API TypeScript types.
- No writes to agent_reviews.py executor, finding visibility/router/contracts, ORM/migrations, synthetic code, scheduler or UI. Contract gaps return to primary.

## Required decisions

1. Versioned definition create/get/list/update/version(s), immutable org selection per proposal, admin-only explicit profile selection, no public delete. Trim/reject blank text; explicit null must not set required name/status null. Version increments serialized under definition lock.
2. Admission uses accepted evidence/profile helpers, actual requester UUID, canonical fingerprint function with actual frozen profile fingerprint (never the provisional blank-profile fingerprint on ReviewEvidenceInput). Use separate dedupe material including requester and immutable org scope. Atomic advisory/definition lock + enqueue + domain creation; same caller active duplicate returns same job/run, terminal intentional rerun new IDs. Validate reused row/job/requester/provenance, never invent missing domain row. Bounded 20 selected runs, 4 MiB; incomplete dimensions retained, nonterminal/synthetic roots rejected.
3. Accepted response is only review_run_id/job_id/reused/notification_id. Generic PlatformJob is lifecycle; Location header points there. Shared job payload only review_run_id. Publish only after commit. No derived content in generic progress/result/name.
4. Run detail/results/usage reauthorize ALL source refs using accepted shared helper before returning any derived data. ReadSnapshotDbSession for multi-query derived reads and usage. Existing profile snapshot/config/evidence must not leak in public DTOs.
5. Results DTO can use existing FindingPublic fields for this slice. Do not move or extend Finding DTOs yet; do not call finding router serializer from shared code. Return existing fields/linked cases with current authorization. The additive Markdown/provenance DTO extension is next integration, not an excuse to duplicate domain models.
6. Top-level agent-reviews CLI supports --file JSON/YAML for create/version/run, short literal flags, parseable stdout and generic PlatformJob wait/status/cancel. Preserve existing agent-tests and usage commands. Exit 0 success (including findings), 2 invocation, 3 unsuccessful job, 4 wait timeout/incomplete authorized result; ensure stdout remains parseable. Implement flags exactly supported by DTOs, preserve distinction omitted org vs explicit global null.
7. Read scope and profile rules must match proposal exactly: nonadmin own org only, global agent can host tenant-scoped review; admin chooses existing tenant for global agent or global null, nonglobal agent org fixed. Selected and descendant contributor org all exact scoped match. Disabled review cannot admit or version; old readable results remain available.

## Verification and monitoring

Primary owns stack until explicit transfer, then one worker at a time. Tests: new review admission/API/CLI plus existing recorded CLI and contract/DTO tripwires as relevant. Include concurrent active duplicate, terminal rerun, wrong requester, invalid profile policy, tenant/private/source revocation and usage read scope. No paid models in these tests. Regenerate API types using already-running debug stack after routes settle; no stack teardown. Report exact commands, failures and fixes. Primary reviews source and independently checks results before acceptance. No commit/push/merge.
