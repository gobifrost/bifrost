# Workflow Caller Identity & Org Scoping Refactor — Design

> **Status:** Design document. No code changes yet. Companion: `2026-05-01-org-scoping-inventory.md` (current-state reference).

## Why this exists

Three problems show up together in the codebase and we keep tripping over them because we conflate them. They are:

1. **The API doesn't know the caller during workflow execution.** Workflows make SDK calls that arrive at the API authenticated as a synthetic engine superuser (`ENGINE_USER_ID = 00000000-0000-0000-0000-000000000001`). The original caller (the person who triggered the workflow, or the webhook payload, or the cron schedule) is preserved in the worker's `ExecutionContext` but never reaches the server. Every SDK call therefore evaluates as god-mode regardless of who actually triggered the work.

2. **The cross-org scope gate uses the wrong concept.** `resolve_target_org` and `resolve_org_filter` gate on `is_superuser` (the platform-admin role bit). The intended rule, expressed correctly in `ExecutionContext.set_scope()` and `resolve_scope()`, is `organization.is_provider`. The two correlate today only because of migration history (PLATFORM users were swept into the provider org). A provider-org member who isn't a superuser cannot scope across orgs today, which is wrong by intent.

3. **The CLI handler path has no server-side scope gate at all.** `_get_cli_org_id` in `api/src/routers/cli.py:357` accepts whatever `scope` arrives on the wire. Inside workflow runtime this is invisible because the SDK pre-gates with `resolve_scope`. From a developer shell running `bifrost.tables.insert(scope=...)` against the deployed API, it's a cross-org bypass.

The CLI/REST consolidation we set out to do (the original blocker on `feat/table-access`) is *downstream* of all three. Pointing the SDK at REST endpoints today would just move the bypass from one router to another, because both routers see the same engine-superuser token.

## Core design

The framing is: **the engine has its own identity (which we keep), and the caller travels alongside as "on behalf of" context.** Each identity has one job; we stop trying to make either do the other's job.

### Engine identity (transport)

The worker is a long-lived service running inside the platform. It needs to authenticate its TCP connection to the API. That's it. The engine token is a transport credential — the API uses it to verify "this request came from a real worker process" and nothing else. It does not grant authorization to do specific work; it grants the right to *carry* an "on behalf of" payload that does.

**Mechanically, the engine token stays roughly what it is today** — a long-lived JWT minted by `authenticate_engine()`, identifying the worker. What changes is what the API does with it: instead of "engine token = `is_superuser=True`, trust everything," the API treats it as "request is from a worker; the actual authorization comes from the on-behalf-of payload."

### On-behalf-of identity (authorization)

The worker, when making an SDK call, attaches a payload identifying the caller. The API reconstructs a `UserPrincipal` from the payload and evaluates authorization against *that* principal — its org, its `is_provider` membership, its policies, its audit trail. The engine token's job is to attest that the on-behalf-of payload is genuine; the principal in the payload is the one that gets gated.

The payload is signed (or carried in a way the API can trust) by the engine. Concretely we have two reasonable transports:

- **Header-with-signed-claims**: The worker mints a short-lived (per-execution) JWT for the caller and attaches it as `X-Bifrost-On-Behalf-Of: <jwt>`. The bearer token is the engine token; the on-behalf-of header is the caller principal. The API verifies both, prefers the on-behalf-of for authorization, falls back to the engine principal when the header is absent (legacy / non-workflow callers).

- **Single token, dual claims**: The worker mints a single JWT that contains both engine claims and on-behalf-of claims — e.g., `sub = caller_user_id`, `engine_attested = true`, `engine_jti = <engine_token_id>`. One token on the wire; the API reads `engine_attested` and treats the principal as the caller.

Either is fine. The header form is easier to roll out incrementally (the API can keep accepting legacy engine-only tokens during migration); the dual-claims form is cleaner once everything's migrated. **Recommendation: header-with-signed-claims**, because the migration is the whole story here — we want the cleanest possible "old request shape still works, new request shape is preferred" pivot.

### System-triggered workflows

Cron triggers, webhook triggers, and similar have no human caller. We do not want them to fall back to engine-superuser; that's the bug we're fixing. Instead, **a synthetic system principal** is constructed at trigger time:

- `user_id = <trigger-specific synthetic UUID>` — well-known per trigger type (specific UUIDs to be assigned during implementation), or generated per-execution if we want each trigger occurrence to be distinguishable in audit logs
- `email`, `name` describing the trigger source
- `organization_id = <workflow's organization_id>` (the org the workflow lives in — *not* the provider org by default)
- `is_superuser = False`
- `is_provider = (workflow.organization.is_provider)` — whatever's true for the workflow's home org
- A trigger-source claim (`trigger_type: "cron" | "webhook" | "agent_run" | ...`) for audit and policy purposes

The system principal is **bounded**: it can do whatever the workflow's org could do (which, if the workflow lives in the provider org, includes cross-org scope; if it lives in a tenant org, does not). It is *not* god-mode.

This addresses the "what's the user for a scheduled workflow that fires at 3am" question: the user is the workflow's organization, represented as a synthetic principal. The same authorization rules apply.

### Caller-scoped vs worker-trusted endpoint classes

Even after the caller principal reaches the API, some operations should *not* be authorized against the caller — operations the workflow needs to do as part of being a workflow, that the caller wouldn't have permission to do directly. Examples: writing to a global knowledge namespace, upserting an integration's OAuth mapping, reading internal files. The platform's trust model is "an admin wrote the workflow, so the workflow is trusted to do these things."

We split endpoints into two classes, **per-endpoint at the API**, not per-workflow:

| Class | Authorization | Examples |
|---|---|---|
| **Caller-scoped** | API enforces against the caller principal (org gate, policies, role checks) | `tables.documents.*`, `ai.*`, `executions.*`, `forms.*`, user-facing things |
| **Worker-trusted** | API requires only that the request comes from a real worker (engine token valid); the workflow author's deploy was the authorization | `integrations.upsert_mapping`, `integrations.delete_mapping`, `knowledge.store` (global), `files.write`, `email.send`, system-side effects |

**There is no per-workflow flag.** The split is drawn once, at API design time, by which endpoint is which. Caller-scoped endpoints are the user-facing REST surface (the `/api/...` paths that the web UI also hits). Worker-trusted endpoints are a privileged subset (probably under `/api/internal/` or kept under `/api/cli/` for now), accessible only to authenticated worker requests.

This means the SDK gets a clean answer to the question "what does X do when called from a workflow":

- `tables.documents.insert("customers", {...})` — caller-scoped. Caller's table policies apply. If the caller can't insert into this table, the workflow can't insert on their behalf, and the workflow author needs to either re-think the operation or do it via a worker-trusted method (like a privileged "system insert" that bypasses caller policy with audit).
- `integrations.upsert_mapping(...)` — worker-trusted. The workflow can do it regardless of caller, because the workflow author's act of writing the code was the authorization.

The trust model **doesn't move**. What moves is which endpoints honor caller policy and which don't. Today everything bypasses caller policy because the API never sees the caller.

### `is_provider` on `UserPrincipal`

Once the caller principal reaches the API, the existing scope helpers (`resolve_target_org`, `resolve_org_filter`) need to evaluate `is_provider` instead of `is_superuser`. Plumbing:

- Add `is_provider: bool` to `UserPrincipal` (default `False`).
- Add `is_provider` to JWT claims at token creation time (login, OAuth, MFA, embed). Looked up once from `Organization.is_provider` at login.
- Existing `is_superuser` claim/property stays — they're orthogonal concepts now (`is_superuser` = platform-admin role; `is_provider` = member of provider org).
- New helper `can_target_org(user, target_org_id) -> bool`:
  ```
  if user.is_provider: return True
  if user.is_superuser: return True   # safety bridge during migration
  return target_org_id in (user.organization_id, None)
  ```
- `resolve_target_org` and `resolve_org_filter` use `can_target_org`. The `is_superuser OR is_provider` clause is the additive shim: it preserves every allow-case the current rule has, plus the cases the current rule wrongly denies (provider-org non-superusers).
- After divergence-logging shows the safety bridge is unused (no requests hit `is_superuser=True, is_provider=False`), we narrow to provider-only.

Token staleness during the migration window is a non-issue because adding `is_provider` only grants new capabilities (provider-org non-superusers gaining cross-org access); it never removes capabilities. Old tokens lacking `is_provider` continue to work via the `is_superuser` clause. After all tokens have rolled (24-48h), the safety bridge can come off in a follow-up.

### `_get_cli_org_id`

Two distinct fixes:

1. **Validation** — accept only valid UUID or `"global"` or null. Reject invalid input at 422.
2. **Authorization** — defer to `can_target_org` for the gate, layering the `DeveloperContext.default_org_id` fallback only on the "no scope provided" path. Preserves CLI ergonomics; closes the bypass.

For the CLI table-document endpoints specifically: those go away as part of consolidation (they become callable through the REST `/api/tables/{id}/documents/*` paths instead). For the CLI endpoints that survive (configs, integrations management, sessions), the gate gets corrected.

### Embed tokens

Embed tokens currently bypass the "non-superuser must have org" rule with an exemption in `auth.py:201-211`. The exemption exists because an embed user may interact with a form/app in an org they don't belong to — access was already verified via HMAC.

For caller-identity purposes, an embed token IS a caller. It carries `app_id` and `form_id`; the API can derive the relevant org from those. The rule for embed tokens is:

- `organization_id = <the form's or app's org>` (derived at token mint time and stored in the JWT).
- `is_provider = False` (embed users never get cross-org scope).
- The existing `embed=True` flag identifies the principal as embed-only; certain endpoints (anything outside the embedded form/app's data) reject embed principals.

This is a documentation + tightening pass on existing behavior, not a redesign. We pin it down in a test matrix.

## Migration model

Additive, surface-by-surface, with a divergence log between the additive change and the narrowing change. Each phase is independently shippable.

### Phase 0: Prep

- Build the **cross-org test matrix** (see below). Lock current behavior in the test suite. Most cases pass; the provider-org-non-superuser cases fail (correctly — that's the bug). Document which cases are "wrong but currently shipped."

### Phase 1: `is_provider` plumbing (no behavior change)

- Add `is_provider: bool = False` to `UserPrincipal`.
- Add `is_provider` to JWT claims at all token-mint sites. Default to `False` for missing.
- Introduce `can_target_org(user, target) -> bool` with the `is_superuser OR is_provider OR same-or-global` rule.
- Replace `resolve_target_org` and `resolve_org_filter` internals with `can_target_org`. Add divergence logging: log a warning if the new gate would have produced a different answer than the old gate (it shouldn't, given the safety bridge).
- Run for a stretch (a week, or until divergence logs go quiet). The safety bridge means nothing breaks; the new rule is a strict superset of the old.

### Phase 2: Caller-identity propagation

- Worker mints a per-execution caller JWT at workflow start. Attaches it on every SDK call as `X-Bifrost-On-Behalf-Of`.
- API parses the header (when present + engine-token-attested), constructs the caller `UserPrincipal`, uses it for authorization.
- Caller-scoped endpoints (the user-facing REST surface) start enforcing caller policies on workflow-driven requests. This is the breaking change for workflows that were inadvertently relying on engine-superuser to bypass caller policy. Roll out behind a feature flag; monitor; fix.
- System-triggered workflows construct their synthetic system principal at trigger time. Cron, webhooks, agent runs each get their `trigger_type` claim.

### Phase 3: CLI/REST consolidation

- `/api/cli/tables/documents/*` endpoints deleted. Python SDK points at `/api/tables/{name_or_id}/documents/*` (which already accept name-or-UUID).
- Auto-create-on-insert moves to the SDK (404 → POST `/api/tables` → retry).
- Web SDK and Python SDK share one endpoint set; policy / WS publish / audit happen uniformly.

This phase is small once Phase 2 is done. It's the original blocker that started this whole thread; it ships almost trivially on top of caller-identity.

### Phase 4: Tighten

- Remove the `is_superuser` clause from `can_target_org`. Provider membership becomes the sole cross-org gate.
- Delete `_get_cli_org_id`'s scope-trust path; survivors call `can_target_org`.
- Audit and migrate or annotate the raw `WHERE organization_id = ...` sites flagged in the inventory.
- Remove the "engine token = god mode" code path entirely; engine token is transport-only.

Each step in Phase 4 is independently revertable.

## Test matrix

Locked in before Phase 1, expanded as each phase lands. Every (user-type × scope-value) combination × every surface.

User types:

- Provider-org superuser
- Provider-org non-superuser (currently broken — gains capability in Phase 1)
- Tenant-org superuser (does this exist legitimately? confirm during Phase 0; today the migration says no, but check)
- Tenant-org non-superuser
- System account (`is_superuser=True, org_id=None`) — global scope
- System-trigger principal (cron) — bounded to workflow's org
- System-trigger principal (webhook) — bounded to workflow's org
- Embed principal (form/app session) — bounded to embed's app/form org

Scope values:

- `None` (no override)
- own org UUID
- other org UUID
- `"global"`
- invalid UUID
- malformed string

Surfaces:

- REST writes (`POST /api/tables`, `PATCH /api/tables/...`, document mutations)
- REST reads/lists (`GET /api/tables`, `GET /api/workflows`, etc.)
- CLI handlers (config, integrations, sessions — survivors of consolidation)
- SDK from workflow runtime (caller-scoped methods)
- SDK from CLI shell (developer running `bifrost` locally)

The matrix is the test directory we run before each phase ships. Every cell that flips behavior between phases gets an explicit annotation in the test ("changed in Phase 2 — was X under engine-superuser model, is Y under caller model").

## What this design explicitly does not include

- **Per-workflow capability sets / per-workflow scope flags.** No new identity attributes per workflow. The trust granted by deploying a workflow is global to the workflow surface; the split between caller-scoped and worker-trusted endpoints is at the API design level, drawn once, not per-workflow.

- **Per-call audit decisions.** Audit happens where it already happens (policy denial, write operations). The caller-identity change makes audits accurate — denials log the actual caller, not "engine" — but does not change what is or isn't audited.

- **Restructuring `OrgScopedRepository`.** Repository semantics stay the same; only the principal flowing through them changes. The flag-name confusion (`is_superuser` in repo init meaning "trust scope, skip role checks") is a separate cleanup that can wait.

## What you have to do to convince yourself this is correct

Three load-bearing claims:

1. **Engine token = transport, on-behalf-of = authorization.** This separation has to be airtight. Verify by checking that no API code path reads engine-token superuser status to make an authorization decision after Phase 2 lands. The grep test: `grep -n "is_superuser" api/src/` should not show authorization checks against the engine token's identity; it should only show checks against caller principals.

2. **The caller-vs-worker endpoint split is well-defined.** Each `/api/...` path is in exactly one bucket. The worker-trusted bucket is a finite, hand-curated list, documented at the API level. New endpoints get assigned at PR time. Verify by reviewing the bucket list during the design pass and confirming each entry's rationale.

3. **The migration is reversible at every phase.** Each of Phase 1-4 can be reverted with a single PR if something breaks in production. The additive shim ensures Phase 1 changes nothing observable; the on-behalf-of header in Phase 2 falls back to engine-only behavior if absent; Phase 3 is a routing change; Phase 4 narrows safely after divergence logs go quiet.

## Open questions for review

- **Do tenant-org superusers exist as a category we need to support?** The migration says all PLATFORM-type users are in the provider org; the model implies only platform admins are superusers. Confirm by querying production. If yes, design needs to handle them. If no, the test matrix can declare that combination invalid.

- **Confirm header-with-signed-claims as the chosen transport,** as recommended in the Core design section. The header form is easier to roll out incrementally; the dual-claims form is cleaner once migrated. If you'd rather collapse to single-token-dual-claims, decide before Phase 2 — the migration plan is built around the header form.

- **Webhook caller principal: identity of the webhook payload, or identity of the user who configured the webhook?** Probably the latter (configuring the webhook is the act of authorization, like writing the workflow), but worth pinning down before Phase 2.

- **Embed token org derivation: from `app_id`, `form_id`, or both?** Whichever yields a single org. Today's HMAC validation has the answer somewhere; pin it down during Phase 0 and write the test.
