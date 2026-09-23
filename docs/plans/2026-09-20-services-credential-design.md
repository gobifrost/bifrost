# Service credentials + autonomous identity — Slice 3 design (§18.5)

Parent plan: `docs/plans/2026-09-20-supervised-workflow-services-plan.md` §18.5.
Status: design accepted as Slice 3 build spec; implementation follows in this slice.

## Problem

`mint_engine_token` (`api/src/core/security.py:414`) mints a **superuser**
token lasting workflow-timeout+5min (24h when timeout=0). Services outlive both
bounds and must not run as superuser. The standard `/auth/refresh` rotation
does not apply: it requires `type=refresh` + a Redis JTI (`routers/auth.py`),
while execution tokens are `type=access` with no JTI. Slice 3 must define a
service-scoped, renewable credential plus a rotation channel **before any
long-lived child runs**, and in the same pass specify the autonomous execution
identity (stable caller/org/role for config, secrets, tables, files, emit).

## Decision D1 — service token shape (`mint_service_token`)

New minter in `api/src/core/security.py`, next to `mint_engine_token`.
A service token is an engine token **minus superuser, plus org scope** —
every existing engine gate keys on `sub`/claims, never on `is_superuser`,
so no auth-path changes are needed:

| Claim | Value | Why |
|---|---|---|
| `sub` | `SYSTEM_USER_UUID` (engine sentinel) | Keeps `_engine_module_scope` (`routers/sdk_modules.py:59` — requires sentinel sub + `engine_execution_id`) and `is_engine_user` attestation (`services/solution_scope.py:42`, `routers/tables.py:106`) working unchanged |
| `is_superuser` | `False` | Least privilege: `get_current_superuser` gates and every `is_platform_admin` check reject it |
| `org_id` | definition `organization_id` | `get_execution_context` (`core/auth.py:295`) scopes data access to this org automatically |
| `email` / `name` | `service-<shortid>@bifrost.internal` | `email` claim is mandatory (`core/auth.py:150`); deterministic per definition |
| `engine_execution_id` | attempt id | Marks the request as engine-attested; `resolve_trustworthy_caller` trusts the signed `engine_solution_id` for own-install inbound (same as workflows) |
| `engine_solution_id` | definition `solution_id` (nullable) | Signed install scope; `None` = ad-hoc execution (outside), same convention as workflows |
| `engine_global_repo_access` | resolved as for workflows | Cold module-fetch scope parity |
| `service_id` / `service_attempt_id` | definition / attempt ids | Audit attribution (the `sub` is shared with workflow executions) |
| `type` | `"access"` | Passes `expected_type="access"` gates; deliberately unusable at `/auth/refresh` — rotation is parent-driven (D2) |

Lifetime: **15 min** (`SERVICE_TOKEN_LIFETIME_SECONDS = 900`). Returns
`(token, expires_at_iso)`, mirroring `mint_engine_token`.

Rejected alternative: per-service `sub` (uuid5 of definition id). It would
give distinct audit identity and allow per-service role grants, but it breaks
`_engine_module_scope` and every `is_engine_user` mirror, requiring auth-path
changes across routers. Deferred to Slice 6 hardening if the role limitation
(D3) proves binding in practice.

## Decision D2 — rotation channel: parent-driven Redis handoff

The child cannot mint (no `SECRET_KEY`); the worker parent holds it
legitimately (the consumer already calls `mint_engine_token`).

- **Key**: `bifrost:service:{attempt_id}:token` → JSON `{token, expires_at}`.
  `SETEX` with TTL = lifetime + 300s.
- **Writer**: the owning worker parent, piggybacked on the existing lease
  heartbeat — every successful `heartbeat_attempt` also mints a fresh token
  and rewrites the key. No new loop, no timing drift: live lease ⇒ fresh
  token exists. (Heartbeat cadence already renews a 60s lease; a JWT sign +
  SETEX per beat is negligible.)
- **First token**: handed via `context_data` over the private work pipe at
  dispatch, exactly like `engine_token` today.
- **Reader**: child-side helper (`ensure_service_credentials`). It carries
  `expires_at` explicitly (the process-`EnvBackend` has no `expires_at`, so
  `is_token_expired` alone cannot drive it) and re-reads the Redis key when
  within a 5-min margin. Driven by a periodic asyncio task owned by the
  engine service-mode runner (60s tick) — no threads, no 401-retry coupling.
- **Fencing / revocation**:
  - Parent deletes the key when it stops owning the attempt: heartbeat
    `StaleLeaseError` (child is killed anyway), terminal completion, worker
    shutdown/drain, stop-requested teardown.
  - Parent death ⇒ key TTL expiry bounds exposure to ≤20 min; token lifetime
    bounds API abuse to ≤15 min post-fence. A fenced child can never renew:
    renewal requires the live owner's heartbeat.
- **Threat note**: an exfiltrated token is worth ≤15 min of org-member-equivalent
  access (D3), non-renewable after stop/fence. It cannot be revoked before
  expiry (plain JWT) — accepted, bounded by the short lifetime. No
  `SECRET_KEY`, no persistent credential write, and no JTI revocation state
  in the child, ever.

## Decision D3 — autonomous execution identity per surface

Stable across operator changes: identity derives from the **definition row**,
never from the user who started/enabled it.

- **Org**: definition `organization_id`, always. `GLOBAL`-scope services are
  out of MVP: a non-superuser token without `org_id` is rejected at
  `core/auth.py:169`, so the claim loop must skip org-less definitions (never
  eligible). Installing a bundle keeps `desired_state=stopped` (§11.7), so no
  global service can auto-start into this gate.
- **Caller**: the engine sentinel (`user_id=SYSTEM_USER_UUID`,
  `email` = service address, `is_platform_admin=False`). Same as workflow
  executions today; audit attribution via the `service_id`/`service_attempt_id`
  claims plus `ExecutionContext` service fields (D4).
- **Roles**: empty. Token-only principals have no `user_roles` rows, so
  `get_execution_context` leaves `role_ids`/`role_names` empty and the
  table-policy `has_role` evaluator denies. **Policy: services get
  org-membership access, never role grants** (granting a role to the shared
  sentinel would grant it to every engine execution — forbidden). Resources a
  service needs must be reachable under org-level policy. The tables
  `created_by`/`updated_by` override stays available (engine-sentinel callers
  are permitted, `routers/tables.py:106`), so service writes attribute
  correctly.
- **Per-surface outcome (implemented)**:
  - `events.emit`: admitted by an explicit service-principal gate
    (`is_service_principal`: sentinel sub + service claims + non-superuser),
    confined to the token's own org (no GLOBAL, no cross-org scope),
    own-install attestation via the signed `engine_solution_id` unchanged.
    A fenced child can emit until its token expires (≤15 min bound —
    same as any other API abuse with a stolen token; rotation stops at
    fence, renewal is impossible).
  - module fetch (virtual-import cold path): sentinel sub + signed scope
    claims — works unchanged (D1).
  - files `/read` and other `CurrentActiveUser` surfaces: authenticate as
    an org member (verified live: non-401).
- **Deferred to the Slice 6 authZ matrix**: config, secrets, and tables
  routers are superuser-gated today, so service tokens 403 there. Each
  surface needs the same narrow treatment as emit (service-principal gate
  + org confinement + per-endpoint review) — tracked as the Slice 6
  "authZ matrix" item, not expanded here.
- **Forbidden (by construction)**:
  - launching executions / admin endpoints / user-account routers
    (`get_current_user_from_db` paths — `oauth_sso`, `mfa`): rejected (not
    superuser, no user row). A service token is worth exactly one org
    member's data-plane power, nothing more.
  - control actions (who started/stopped/restarted) go to audit, not service
    logs (plan §7, unchanged).

## Decision D4 — service `ExecutionContext`

Engine service mode builds the context with caller = service identity
(sentinel user id, service email), organization = definition org,
`is_platform_admin=False`, plus `service_id` / `service_attempt_id` fields
for attribution. `variables`/`execution_context` snapshots stay disabled
(plan §4.2.3). SDK context injection and direct-REST writers work unchanged.

## Lifetimes summary

| Item | Value |
|---|---|
| Service token lifetime | 15 min |
| Redis token-key TTL | 20 min |
| Child credential check tick | 60s, refresh margin 5 min |
| DB lease TTL / heartbeat | unchanged (60s) |

## Implementation checklist (this slice)

1. `mint_service_token` in `security.py` + unit tests (claims, org-required,
   15-min expiry, `type=access`).
2. Worker claim path: mint first token into service `context_data` with
   `expires_at`; heartbeat piggybacks mint + `SETEX`; key deletion on
   lost-ownership / completion / shutdown.
3. Child: `ensure_service_credentials` + engine service-mode 60s refresh task.
4. Engine service-mode `ExecutionContext` per D4.
5. Claim-loop org gate: skip org-less definitions.
6. Tests: rotation across 2+ lifetimes (e2e); fenced-child renewal failure
   after key deletion (unit/e2e); 401-after-expiry without renewal;
   non-superuser rejection at an admin gate; service token usable at an
   org-scoped data endpoint and at `/api/sdk` module scope.

## Explicit non-goals

- Per-service `sub` identity and per-service role grants (deferred, see D1).
- Global (org-less) services (blocked by auth invariant; revisit only with a
  dedicated scoped-global design).
- Refresh-token/JTI rotation for services (rejected: needs revocation state
  and survives fencing unless separately gated — the Redis handoff fences by
  construction).
