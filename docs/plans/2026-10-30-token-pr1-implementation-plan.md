# Token PR1 — Runs and Shared Pieces: Implementation Plan (revision 3)

Spec: `docs/superpowers/specs/2026-10-27-access-token-claims-design.md`
(approved; amended 2026-10-30 to match this plan). Branch `token/pr1-runs`,
based on `origin/main` `ad623e250`; line numbers are at that commit.

PR1 delivers spec §10 item 1: `issue_access_token`, the claims schema,
not-before stamps, run refresh tokens and the README rule. Every runtime kind
switches to claims; people stay on today's path until PR2. Decisions are
`D…` (§11).

### History

- **Revision 1** (`9ed3edfc9`): first plan.
- **Revision 2** (`f2e0a8222`): answered Astra's review (`astra-token-pr1-plan-1.md`).
- **Revision 3** (this): Jack's decisions of 2026-10-30.
  1. **Stamp check is Postgres only.** One indexed query per request; no Redis
     copy, certifier, warm lease, pending markers, resync fencing or
     publication. Revision 2's SC5 (partial Redis partition) is moot. No hooks
     for a later cache.
  2. **Accepted:** SC1 (service tokens expire at the committed lease), SC2
     (impersonation subject and end stamp), SC3 (run refresh tokens valid while
     the run is live), SC4 (Postgres triggers write stamps), SC6 (merge units
     with migration-enforced order).
  3. **Q1 (PR2):** compress the `usr` block, size warning on the encoded token
     (§2, D19).
  4. The spec is amended in the same branch.

Revision 2's answers to Astra's findings still hold, re-derived for
Postgres-only checks:

| Finding | Revision 3 | Where |
|---|---|---|
| 1. Stamp vs commit latency | Writers stamp **at commit, under an exclusive per-subject lock**; minters read the clock and facts **under a shared lock**. The locks order the two, so the database clock alone decides (proof §3.3). Slow-commit and skew tests | §3.2–§3.4, §9.1 |
| 2. Warm marker / Redis | No Redis | — |
| 3. Savepoints | Trigger bookkeeping is transaction-local (a transaction-scoped setting plus deferred trigger events), rolled back with its savepoint by Postgres | §3.2, §9.1 |
| 4. Uncommitted lease | Service tokens minted only after the heartbeat commits | §5.4 |
| 5. Rolling deploys | Three merge units; preconditions enforced by migrations | §8 |
| 6. Completeness / one-minter | Triggers cover every write path; registry ↔ trigger checks, read capture, reachability, negative tests; reserved claims refused by `create_access_token` | §3.2, §9.2–§9.3 |
| 7. Fractional seconds | `iat` is an integer; the minter waits until the database clock passes the newest stamp and re-reads it | §3.4 |
| 8. Acceptance additions | Kept | §9 |
| 10. References | Corrected in revision 2 | — |

---

## 1. Scope boundary

### 1.1 Mint sites

| Site | PR1 | PR2 / unchanged |
|---|---|---|
| `core/security.py:458` `mint_engine_token` (caller `jobs/consumers/workflow_execution.py:933-937`) | PR1b: removed; consumer mints access + run refresh token | — |
| `core/security.py:426` `mint_service_token` (callers `services/service_claim.py:463` `_rotate_token`, `:691` `_launch_claimed`) | PR1b: removed; both mint after commit (§5.4) | — |
| `services/mcp_server/tools/_http_bridge.py:75-86` runtime branch | PR1b: `issue_access_token` (D6) | — |
| `_http_bridge.py:88-104` human branch; `routers/auth.py:684,836,1126,1892`; `routers/passkeys.py:207`; `routers/oauth_sso.py:423`; `routers/mfa.py:254`; `services/mcp_server/auth.py:509,564` | — | PR2 |
| `core/security.py:245` `create_embed_access_token` (spec: `create_embed_token`) | — | unchanged |
| `shared/browser_impersonation.py:90` marker | PR1a: end stamp only (SC2) | format unchanged |
| `core/security.py:179` `create_mfa_token`; `shared/solution_dev.py:121`; `services/oauth_state_core.py:79` | — | unchanged (not access tokens) |
| `core/security.py:57` `create_access_token` | PR1a: refuses reserved runtime claims (§9.3) | PR2 |

### 1.2 Validation and authority sites

| Site | PR1 change |
|---|---|
| `core/app_wiring.py:221` middleware (runtime branch `:242-246`) | PR1b: decodes and schema-validates `ver:1` claims without a DB session (attribution only); legacy-format tokens keep today's path (bridge, §8.3) |
| `core/auth.py:135` `_get_current_user_optional` (`:184-192`) | PR1b: `authenticate_runtime_token(db, claims)` runs the stamp check in the **request's own DB session** (`db: DbSession`), then builds the principal |
| `core/auth.py:343` `get_current_user` | PR1b: refresh-required 401 (D11) |
| `core/auth.py:401` `get_execution_context` | PR1b: own-Solution `?solution=` from `run.sol_active` (`:448-465`); role read `:430-433` skipped for runtime principals. Browser `X-Bifrost-App` gate → PR2 |
| `services/mcp_server/auth.py:661` `verify_token` (`:689-745`) | PR1b: `authenticate_runtime_token` in its existing short session; tool-filter roles from `usr` |
| `routers/auth.py:999` `/auth/refresh` | PR1a: `run_refresh` branch |
| `core/auth.py:39`, `:591`; `runtime_auth.py:34` `load_runtime_principal` and in-process callers (`jobs/consumers/agent_run.py:274,376`, `services/execution/autonomous_agent_executor.py:1367`, `services/authorization/callers.py:58`, `services/mcp_server/authorization.py:22`) | unchanged (D6) |
| `services/authorization/enforce.py:118` human branch; `shared/browser_impersonation.py:130` | PR2 |

Not in PR1: policy evaluation's data reads, logout/revoke-all, people
lifetimes/cookies, MCP OAuth refresh JTIs, the human `roles` claim shape.

### 1.3 Gaps from `token-spec-gaps-20261029.md`

| Gap | Disposition |
|---|---|
| `create_embed_token` name | allowlisted by its real name |
| Agent runs mint no JWT | D6 |
| Run refresh endpoint; worker env (`worker.py:51-52`) | §5 |
| Service lifetime | D9 (SC1) |
| `iat`/`jti`; 1 s ambiguity | §3.4, D2 |
| Warm marker key | moot (no Redis) |
| Cookie limit, cookie max_age (`auth.py:126`), MCP `expires_in` (`mcp_server/auth.py:518,571`), MCP refresh JTI (`:510,:565`) | PR2; D19 |
| Browser App/Solution gates (`core/auth.py:459,490,493`) | runtime own-Solution gate PR1b; rest PR2 |
| `build_authorization_context` docstring (`context.py:120`) | fixed in A4 |
| `roles` claim shape | runtime claims carry ids + names; human shape PR2 |
| Completeness mechanism | §3.2, §9.2 |

---

## 2. Claims schema (`ver: 1`)

Pydantic models in `api/src/services/authorization/tokens.py`
(`frozen=True, extra="forbid"`; internal, not an HTTP contract).

```text
iss, aud, type="access", ver=1, iat:int, exp:int, jti:str
sub: str                      runtime: credential id (name kept, D1)
credential_kind               "workflow_execution" | "service_attempt" | "agent_run"
service_id: str | absent      service_attempt only
usr: str                      base64url(zlib(JSON)) of the run user block below (D19)
     { id, email, name, kind, org, ext, prov, active,
       base: {id, perm:[str]},
       roles: [ {id, name, perm:[str], bnd:[[kind, org|null]]} ] }
act: { id, email, name, org, ext, prov, roles:[{id, name}] } | absent   acting user ≠ run user
run: { org, wf, sol, sol_active, root, by, powers: {mode, grants:[str]} | null }
imp: { by, sid } | absent
```

`principal_from_claims` reproduces `runtime_auth._principal_from_authority`
(`runtime_auth.py:66-133`): `AuthorizationContext` from `usr` (role grants =
roles with a non-empty `bnd`, as `build_authorization_context` yields);
`RunUser(usr.id, ctx, usr.kind, usr.active)`; the actor (`act` or `usr`)
supplies email, name, `is_external`, `is_provider_org`, role ids/names and
`is_superuser = PLATFORM_ADMIN_ROLE_ID ∈ actor role ids`; `organization_id =
run.org`; `RuntimeAuthority` from `credential_kind/sub/service_id/run/imp` plus
`solution_active` (D13). Grants use `WorkflowGrant.model_construct`.

**Size** (synthetic payloads shaped like the spec's production numbers): with
plain JSON, production's largest user is 4,444 B and a largest user with a
distinct 12-role actor, impersonation and 10 grants is 6,308 B. Compressing
`usr` alone cuts the largest people token from 4,001 to 2,397 B (measured), so
run tokens drop by about 1.6 KB as well. Minting warns when the **encoded**
token exceeds 6,144 B and never refuses (spec §3).

---

## 3. Not-before stamps (Postgres only)

### 3.1 Table

Migration `20261030_authz_not_before.py` (PR1a):

```text
authz_not_before(
  subject_kind varchar(32) CHECK IN ('user','role','organization','run','workflow','solution','impersonation'),
  subject_id   uuid,
  not_before   timestamptz NOT NULL,
  PRIMARY KEY (subject_kind, subject_id))
INDEX ix_authz_not_before_not_before (not_before)
authz_protocol_state(protocol int PRIMARY KEY, certified_at timestamptz NOT NULL)
scheduler_replicas.authz_protocol int NULL;  execution_workers.authz_protocol int NULL
```

ORM in `api/src/models/orm/authz_not_before.py` (registered in
`models/orm/__init__.py`); protocol columns on
`models/orm/scheduler_diagnostics.py:13` and `models/orm/execution_workers.py:27`.

### 3.2 Writers: triggers stamp at commit

Every carried fact has triggers in the same migration (literal SQL; migrations
stay immutable; §9.2 keeps code and triggers in step).

1. **Collect.** An `AFTER … FOR EACH ROW` trigger on each table of the mapping
   below calls `authz_nbf_collect(kind, id)`. It appends `kind:id` to the
   transaction-local setting `bifrost.authz_nbf` (`set_config(…, true)`).
   Changes inside a savepoint that rolls back are undone with it, because
   Postgres reverts setting changes made in an aborted subtransaction.
2. **Flush at commit.** A `CONSTRAINT TRIGGER … DEFERRABLE INITIALLY DEFERRED
   FOR EACH ROW` on the same tables calls `authz_nbf_flush()`. Deferred events
   fire at COMMIT, and events from an aborted savepoint are discarded. The
   first firing in a transaction does the whole flush; later firings see the
   done flag and return:
   - dedupe the subjects and map each to a bucket,
     `abs(hashtextextended(kind||':'||id, 0)) % 256`;
   - take `pg_advisory_xact_lock(AUTHZ_LOCK_CLASS, bucket)` (**exclusive**)
     for each bucket **in ascending order**;
   - `INSERT … ON CONFLICT DO UPDATE SET not_before =
     greatest(authz_not_before.not_before, clock_timestamp())`, with the
     inserted value also `clock_timestamp()`, read after the locks are held;
   - prune at most 50 rows with `not_before < clock_timestamp() - 5400 s`
     (`FOR UPDATE SKIP LOCKED`);
   - set the done flag (`bifrost.authz_nbf_done`).
3. **Explicit events** with no table change (impersonation end, SC2):
   `SELECT authz_nbf_stamp_now('impersonation', sid)` in its own short
   transaction. It takes the same exclusive lock, upserts `clock_timestamp()`,
   and commits before the Redis session record is deleted
   (`shared/browser_impersonation.py:186,225`).

Locks are taken only during commit processing, so long transactions (for
example a Solution deploy) never hold them while they run. The lock class is
a fixed 32-bit constant distinct from the advisory keys already in use
(implementer greps `pg_advisory` at A1). With 256 buckets, a transaction holds
at most 256 locks however many rows it changes.

| Table | Fires on | Subject(s) |
|---|---|---|
| `users` | UPDATE OF email, name, organization_id, base_role_id, is_external, identity_kind, is_active (value changed); DELETE | `user:id` |
| `organizations` | UPDATE OF is_provider (changed); DELETE | `organization:id` |
| `user_roles`, `user_role_boundaries` | INSERT / UPDATE / DELETE (incl. FK cascades) | `user:user_id` (old and new) |
| `role_permissions` | INSERT / UPDATE / DELETE | `role:role_id` |
| `roles` | UPDATE OF name (changed); DELETE | `role:id` |
| `workflows` | UPDATE OF solution_id, permission_mode (changed); DELETE | `workflow:id` |
| `workflow_permission_grants`, `solution_workflow_permission_requests` | INSERT / UPDATE / DELETE | `workflow:workflow_id` (old and new) |
| `system_configs` | rows with category `workflow_permissions`, key `default_mode`, org NULL | `workflow:` every workflow with `permission_mode IS NULL` |
| `solutions` | UPDATE OF status (changed); DELETE | `solution:id` |
| `executions` | status leaves `'Pending'/'Running'/'Cancelling'`; DELETE of a live row; UPDATE OF run_user_id, executed_by, workflow_id, organization_id, started_by_user_id, root_execution_id, impersonated_by_user_id, impersonation_session_id (changed) | `run:id` |
| `agent_runs` | status leaves `'running'/'cancelling'`; DELETE live; UPDATE OF run_user_id, acting_user_id, agent_id, org_id, impersonation columns (changed) | `run:id` |
| `service_attempts` | state leaves `'starting'/'running'/'stopping'`; DELETE live; lease_expires_at decreased; UPDATE OF run_user_id, service_id (changed) | `run:id` |
| `service_definitions` | UPDATE OF workflow_id, organization_id (changed) | `run:` each live attempt |
| `agents` | UPDATE OF solution_id (changed); DELETE | `run:` each live agent run |

No application write site changes for stamping: no guard calls and no router
moves (SC4 accepted).

### 3.3 Why this is correct (finding 1)

Writer W takes subject `s`'s bucket lock exclusively at commit, reads
`v = clock_timestamp()`, and holds the lock until its commit is visible
(Postgres releases transaction locks after marking the transaction
committed). Minter M holds `s`'s bucket lock shared while it reads `T =
clock_timestamp()` and every fact, and sets `iat = floor(T)`. Shared and
exclusive conflict, so the two locked intervals are disjoint:

- **W before M.** W's change is visible to all of M's reads, and `v < T`. M
  waits until `floor(T) ≥ v` (§3.4), so the fresh token is accepted.
- **M before W.** W locks only after M commits, so `v > T ≥ iat` and the token
  is refused (`not_before > iat`) from the moment W's change becomes visible.
  Before then the change is not in effect, so accepting the token is correct.

Only the database primary's clock is used, and only its monotonicity is
assumed (chrony/NTP slews; it does not step backwards). Commit latency, long
transactions and app-host clock skew do not appear in the argument. Every
change of a fact a token carries reaches one of the token's subjects (the
mapping in §3.2 plus `token_subjects`, §4.2, checked in §9.2).

PyJWT's `verify_iat` is turned off in `decode_token` (`core/security.py:146`),
because a database-clock `iat` can be ahead of a validating host's clock.
`exp`, signature, `iss` and `aud` are still verified.

### 3.4 Minter protocol

`issue_access_token(subject)` always runs in **its own short session** (never
the caller's transaction). Its shared locks then last milliseconds, and it
can never sit on a lock that a writer's commit is waiting for:

```text
budget 5 s:
  BEGIN
  S  = subject pre-query (run/attempt/agent-run row; run user and actor ids;
       their home orgs, base roles and role ids; workflow and Solution ids)
  for b in sorted(buckets(S)): pg_try_advisory_xact_lock_shared(CLASS, b)
       any false → ROLLBACK; sleep 20 ms; retry          -- never waits, so no deadlock
  T  = clock_timestamp()                                  -- database clock, under the locks
  F  = full fact load (§4.1)
  if token_subjects(F) ⊄ S → ROLLBACK; S ∪= token_subjects(F); retry
  V  = max(not_before) over S
  if V is not None and floor(T) < V → ROLLBACK; sleep(ceil(V) − T + 0.01); retry
  iat = floor(T); sign; COMMIT
on budget exhausted → AuthorityUnsettled               -- /auth/refresh → 503, Retry-After: 1
```

The condition `floor(T) ≥ V` is checked on a freshly read database clock after
every wait. A stamp at 102.4 therefore makes a minter at 102.45 wait about
0.56 s and mint `iat = 103`, which passes (finding 7). Contention lasts only
as long as a writer's commit holds its locks.

### 3.5 Validator check (PR1b)

In the request's own transaction (no extra transaction):

```sql
SELECT EXISTS (SELECT 1 FROM authz_not_before
                WHERE (subject_kind, subject_id) IN (…)
                  AND not_before > to_timestamp(:iat))
```

True means 401 refresh-required. The plain read sees committed stamps only, so
an uncommitted change does not refuse tokens yet, and a committed one does
immediately. `MCP verify_token` runs the same query in its existing short
session.

---

## 4. The single minter

```python
@dataclass(frozen=True)
class RuntimeSubject: kind: RuntimeKind; credential_id: UUID; service_id: UUID | None = None
@dataclass(frozen=True)
class IssuedAccessToken: token: str; expires_at: datetime; claims: AccessTokenClaims

async def issue_access_token(subject: RuntimeSubject) -> IssuedAccessToken
def issue_run_refresh_token(subject: RuntimeSubject) -> str
def principal_from_claims(claims: AccessTokenClaims) -> UserPrincipal
def token_subjects(claims: AccessTokenClaims) -> frozenset[Subject]
def parse_runtime_claims(payload: dict[str, Any]) -> AccessTokenClaims | None
async def authenticate_runtime_token(db: AsyncSession, claims: AccessTokenClaims) -> RuntimeAuthResult
```

`not_before.py` holds `SubjectKind`, `Subject`, `bucket(subject)`, the
validator query, `authz_nbf_stamp_now` wrapper (`record_authz_event`) and the
`NOT_BEFORE_FACTS` registry.

### 4.1 Facts

1. `load_runtime_authority` (`runtime.py:75`) with today's liveness rules
   (`RuntimeIdentityError` becomes `TokenSubjectUnavailable`). A4 tightens its
   loads to `load_only` (`_load_service_attempt` `:166`, `_load_agent_run`
   `:207`, the service-path `Workflow`, `list_workflow_grants`,
   `workflow_permission_requests`). It adds `RuntimeAuthority` (`:59`) fields
   `solution_active` and `lease_expires_at` (service only; `None` for
   claim-built authorities).
2. Run user's roles, names, permissions and boundaries come from the database,
   never `shared/role_cache` (D5).
3. Run user identity + home org `is_provider`; actor identity, org and roles
   when they differ.

### 4.2 Subjects carried

`token_subjects` = `user:usr.id`, `user:act.id`, `role:usr.base.id`, `role:`
every `usr.roles[].id` and `act.roles[].id`, `organization:usr.org`,
`organization:act.org`, `run:sub`, `workflow:run.wf`, `solution:run.sol`,
`impersonation:imp.sid`; `None` values skipped.

### 4.3 Lifetimes

| Kind | Access token | Run refresh token |
|---|---|---|
| `workflow_execution` | `3600 + secrets.randbelow(1801)` s | no `exp`; valid while the run is live (SC3) |
| `service_attempt` | until the committed `lease_expires_at` (≈ 60 s), re-minted after each heartbeat commit (SC1) | no `exp`; valid while the attempt is live |
| `agent_run` (MCP bridge) | `3600 + secrets.randbelow(1801)` s | none (D6) |

All access lifetimes are ≤ 5400 s (asserted), the prune horizon. The run
refresh token is `{iss, aud, type:"run_refresh", ver:1, iat, jti, sub,
credential_kind, service_id?}`; it carries identity only and minting it reads
no database. `decode_token(expected_type="access")` rejects it.

---

## 5. Run refresh

### 5.1 Endpoint (PR1a)

`POST /auth/refresh` (`routers/auth.py:999`) keeps its `TokenRefresh` (`:296`)
and `Token` (`:252`) models, so the CLI fingerprint is unchanged (D7). It
decodes without `expected_type`. `refresh` goes to today's human branch,
untouched. `run_refresh` requires `ver == 1` and calls `issue_access_token`.
`TokenSubjectUnavailable` → 401; `AuthorityUnsettled` → 503 `Retry-After: 1`;
success returns `Token(access_token=new, refresh_token=<same token>)` with no
cookies. Run refresh skips the per-IP `auth_limiter`. Tokens are reusable and
not rotated (D8).

### 5.2 Workers (PR1b)

In `worker.py:37-52`, `engine_refresh_token` goes to `BIFROST_REFRESH_TOKEN`.
It joins `_CONTEXT_REDIS_EXCLUDED_KEYS` (`process_pool.py:82`) and the output
redaction (`worker.py:297`, `engine.py:324`). Refreshed access tokens are
redacted through `install_access_token` (`bifrost/client.py:917`).

### 5.3 SDK (PR1b)

In `bifrost/client.py`, engine-local requests refresh on 401 through the
network `/auth/refresh` and replay once over the socket (~`:965`, ~`:1007`).
Update the docstrings that say otherwise. `refresh_connection_access_token`
(`:267`) and `save_refreshed_credentials` (`credentials.py:645`) already accept
an unchanged refresh token.

### 5.4 Services (PR1b; finding 4)

- **Launch:** the claim commits first (`service_claim.py:566`). Minting
  happens in `_launch_claimed`'s second session (`:581-590`), before routing.
- **Rotation:** `tick` commits the heartbeat at `:179`. `_beat_owned` stops
  minting and returns the renewed attempts. After that commit each one is
  minted with `issue_access_token` (concurrency limit 4) using the committed
  `lease_expires_at`, and only then written to `service_token_key`. A
  rolled-back heartbeat yields no token.
- **Credentials:** `bifrost/_service_runtime.install_service_credentials(
  access, refresh)` runs at start (`worker.py:419-421`, `engine.py:950-952`).
  `install_rotated_service_access_token(access)` in the supervisor
  (`engine.py:1082`) leaves the refresh slot alone. Nested service tokens are
  scrubbed from the diagnostic Redis context copy (`process_pool.py:1159`).

---

## 6. Repository authority rule (PR1b)

Rule: claims in an access token minted by `issue_access_token` are authority
until it expires or a not-before stamp covers it.

1. `parse_runtime_claims` + `authenticate_runtime_token` are the only
   claims→principal path. They require signature, `type == "access"`,
   `ver == 1`, a strict schema and a passing stamp check.
2. `principal_from_claims` is imported only by `tokens.py` (AST test).
3. `create_access_token` refuses the reserved claims `ver`, `credential_kind`,
   `usr`, `act`, `run`, `imp`. Only `tokens._encode_claims` signs them.
4. `api/src/repositories/README.md`: the "Authority model" paragraph,
   implementation rule 1 and the `RuntimeAuthority` paragraph change to the
   rule; legacy runtime tokens are described as bridged until PR1c.
5. `test_runtime_auth.py::test_runtime_principal_uses_database_authority_not_spoofed_token_claims`
   becomes three tests: the legacy payload uses the bridge until PR1c; `ver:1`
   claims are authority with one stamp query; a tampered token fails.
   Docstrings to update: `runtime_auth.py:1,37`, `runtime.py:1-5`,
   `core/principal.py:22-24,63-82`, `mcp_server/server.py:143-145`.

---

## 7. Application write paths

None change for stamping (§3.2). PR1 changes application code only at mint
and validation sites (§1), the service rotation order (§5.4) and the
impersonation end event (§3.2).

---

## 8. Rollout

### 8.1 What a mixed fleet actually needs

| Hazard | Covered by |
|---|---|
| Older pods change authority without stamping | Triggers live in the database from the first migration, so every pod's writes stamp. Needs no ordering |
| A new worker's refresh lands on an API pod without `run_refresh` | `run_refresh` must exist on every API replica before any minter issues refresh tokens → **PR1a before PR1b** |
| New-format token reaches an older pod | Older pods validate it through `load_runtime_principal` (names kept, D1). Needs no ordering |
| Old-format token reaches a new pod | Bridge (§8.3) |
| Bridge removed while old tokens live | Old-token minters gone for ≥ 25 h → **PR1c precondition** |

### 8.2 Merge units

Main squash-merges each PR, so each unit is one commit on main.

| Unit | Contents | Authorization behaviour |
|---|---|---|
| **PR1a** | table, triggers, functions, protocol columns; heartbeat protocol reporting and certification; claims schema; minter; `run_refresh`; reserved-claim refusal; impersonation end stamp; completeness tests | unchanged: today's minters and validators |
| **PR1b** | migration precondition (protocol 1 certified); minters switch; validators trust claims with the stamp check; bridge; SDK; README; protocol 2 reporting | claims are authority |
| **PR1c** (file the issue when PR1b merges; following release) | precondition (protocol 2 certified ≥ 25 h); delete the bridge | legacy tokens refused |

**Certification** rides the existing heartbeat transactions; it adds no
transaction and no background loop. API replicas report
`scheduler_replicas.authz_protocol` (`scheduler/control.py:93` →
`services/scheduler_diagnostics.py:32`); workers report
`execution_workers.authz_protocol` (`services/worker_pool/claims.py:91,124,324`).
In its heartbeat transaction, each replica runs one extra statement: insert
`authz_protocol_state(p, now())` if no replica or non-retired worker with a
heartbeat in the last 30 s reports less than `p`.

**Migration preconditions.** PR1b's migration raises "Upgrade to the PR1a
release and let it finish rolling out first" unless protocol 1 is certified.
PR1c's raises unless protocol 2 was certified ≥ 25 h ago (the longest old
engine token lives 24 h + 5 min). Both pass on a fresh database (no
`scheduler_replicas` rows). A failed init container leaves the previous
release serving.

### 8.3 Legacy bridge (Jack, 2026-10-29; PR1b → removed in PR1c)

A runtime-shaped payload without `ver` (`runtime_auth.py:29`) is validated
exactly as today — `load_runtime_principal` in the middleware's DB session
(`app_wiring.py:242-246`), including the live-run check — until its own
`exp`. Nothing new is granted: its refresh slot holds the same access token,
which `/auth/refresh` rejects. Each acceptance logs
`authz.legacy_runtime_token_accepted` (kind, exp).

---

## 9. Test plan

Targets for `./test.sh`; none are run while the capacity benchmark holds the
host.

### 9.1 Protocol, minter and services

| File | Contract |
|---|---|
| `tests/unit/authorization/test_not_before_triggers.py` | each §3.2 row (incl. role-delete cascade, generic `update(model).values(**v)`, raw SQL) stamps exactly its subjects at commit; unrelated columns don't; rollback → none; change in a rolled-back savepoint → none; nested savepoint released then outer rollback → none; change outside a rolled-back savepoint → stamped; 1,000-row update → one flush, ≤ 256 locks; two writers committing overlapping subjects in opposite orders → both commit (no deadlock) |
| `tests/unit/authorization/test_stamp_ordering.py` (finding 1) | **slow commit:** writer changes a role and delays COMMIT arbitrarily (event-ordered, plus a 3 s case); a minter mints meanwhile from pre-change facts; after commit the token is refused. **Commit during mint:** minter paused between locking and commit (test hook); writer's commit waits; `v > T`; refused. **Writer at commit:** a second connection holds the bucket lock exclusively; minter retries, then mints after release with `iat ≥ v`. **Skew:** app clocks patched ±5 s → no change in any outcome; a database-clock `iat` ahead of the host clock is accepted |
| `tests/unit/authorization/test_minter_wait.py` (finding 7) | database-clock reader patched: stamp 102.4, `T` 102.45 → waits and mints `iat=103`; first token after a stamp is never refused; continuous contention → `AuthorityUnsettled` after 5 s |
| `tests/unit/authorization/test_token_claims.py` | schema round trip incl. `usr` compression; derivations; production-largest fixture encoded size < 6,144 B; warning above; never refused |
| `tests/unit/authorization/test_issue_access_token.py` | lifetimes (spread 3600–5400; service `exp ==` committed lease); `iat/jti`; non-live → `TokenSubjectUnavailable`; own session (caller's open transaction untouched) |
| `tests/unit/authorization/test_token_parity.py` | each `test_runtime.py` scenario: `principal_from_claims(issue…)` equals `load_runtime_principal` with a cleared role cache; a **poisoned** role cache does not reach the claims |
| `tests/unit/authorization/test_runtime_auth.py` | §6.5 tests; **exactly one SQL statement** for a runtime request (`before_cursor_execute` counter, as `test_runtime.py:286`) |
| `tests/unit/services/test_service_claim_tokens.py` | heartbeat flushes a new lease then raises → no token written; `exp` never exceeds the committed lease; slow mint doesn't delay the heartbeat |
| `tests/unit/services/test_worker_sdk_http.py` (`:1482` inverted) | engine-local 401 → one network refresh → one socket replay |
| auth refresh tests | live → new token, same refresh token, no cookies; ended run / expired lease → 401; 50 concurrent run refreshes from one IP → all 200 while human refresh stays limited; 10 refreshes then run end → 401 |
| migration precondition tests | PR1b/PR1c migrations refuse without certification; pass on a fresh database |

Mutation matrix (one test each): mint → change through the real service
function → old token refused → refresh → new claims reflect it. Changes: role
grant/revoke; boundary add/remove; base role; org move; provider flag (SQL);
role rename; role permission edit; role delete; distinct actor's role or org;
workflow powers; Solution deactivate/reactivate; `Agent.solution_id` capture;
impersonation end; execution, service-attempt (incl. `expire_leases`) and
agent-run end.

### 9.2 Completeness

- **Read side:** under a `do_orm_execute` capture, the minter's `(table,
  column)` reads equal `NOT_BEFORE_FACTS`. Meta-test: an extra read fails it.
- **Registry ↔ triggers:** every registry entry has collect + flush triggers
  in `pg_trigger` covering its columns (from `pg_get_triggerdef` and each
  trigger's `COMMENT` tag), and vice versa. Meta-test: dropping one trigger in
  the test database fails it.
- **Reachability:** for each §3.2 row, a token carrying the changed fact
  includes the stamped subject (base role, distinct actor, both
  organizations). Meta-test: `token_subjects` without the base role fails it.

### 9.3 One minter

- `create_access_token` raises on reserved claims, including when called from
  `_http_bridge._token_from_context`.
- AST over `api/src`, `api/shared`, `api/bifrost`: `jwt.encode` callers ⊆
  {`tokens._encode_claims`, `security.create_access_token`,
  `create_refresh_token`, `create_mfa_token`,
  `solution_dev._mint_solution_dev_token`, `oauth_state_core.encode_state_jwt`};
  `create_access_token` callers ⊆ {`create_embed_access_token`,
  `create_browser_impersonation`} ∪ the PR2-marked human sites. A synthetic
  module with a new `jwt.encode` is flagged.

### 9.4 E2E (`tests/e2e/platform/test_runtime_token_claims.py`)

Covers: revocation mid-run → refresh → new facts; run end (token and refresh
refused); long wait (expired access → refresh → continue); Solution
deactivate → 409 after refresh; legacy bridge (accepted until `exp`, refresh
refused).

Existing suites must pass:
- `tests/unit/authorization/*`
- `tests/e2e/platform/test_agent_runtime_boundary.py`,
  `test_chat_runtime_boundary.py`, `test_engine_token_redaction_live.py`,
  `test_solution_permission_runtime.py`, `test_r3a_identity_authorization.py`,
  `test_sdk_socket_audit_parity.py`
- `tests/e2e/mcp/test_mcp_parity.py`
- service-credential e2e
- `tests/unit/test_contract_version.py`, `test_dto_flags.py`,
  `test_mcp_thin_wrapper.py`

### 9.5 Benchmark

Run `./test.sh bench run --profile baseline --backend fake` on `ad623e250`,
after PR1a and after PR1b. Add a `services` profile (20 supervised services,
10 min) to `scripts/bench/profiles/`.

| Measure | Expectation |
|---|---|
| Transactions per run (`stats.py:112`) | PR1a: unchanged (stamping rides existing commits). PR1b: `wf-sdk` −1 per SDK call (the middleware's runtime-auth session `app_wiring.py:243` is gone; the stamp check uses the request's own transaction); `wf-noop` ≤ +1 (run-start mint) |
| Statements per runtime request | ~10 → 1 |
| API CPU ms per run, `wf-sdk` burst p50/p95 | down after PR1b |
| Idle tx/s (`stats.py:101`) | unchanged |
| `services` profile: heartbeat tick p95, worker and API tx/s | tick p95 unchanged; API tx/s down |

No class may regress beyond the harness's ±9 % noise. If the stamp lookup
shows up, a cache is a later, separately approved change.

---

## 10. Task breakdown

One implementer, in order. Each task is ≤ ~300 lines with its tests and runs
`./test.sh quality api`.

**PR1a**

| # | Task |
|---|---|
| A1 | Migration: table, protocol table/columns, `authz_nbf_collect`, `authz_nbf_flush`, `authz_nbf_stamp_now`; ORM |
| A2 | Migration: the §3.2 triggers with `COMMENT` tags; `test_not_before_triggers.py` |
| A3 | `not_before.py` (subjects, buckets, validator query, `record_authz_event`, registry); heartbeat protocol reporting + certification |
| A4 | `load_only` tightening; `RuntimeAuthority.solution_active` / `.lease_expires_at`; `context.py:120` docstring |
| A5 | Claims schema with compressed `usr`, `principal_from_claims`, `token_subjects`; `test_token_claims.py` |
| A6 | Minter (§3.4), `issue_run_refresh_token`, reserved-claim refusal; `test_issue_access_token.py`, `test_minter_wait.py`, `test_stamp_ordering.py`, `test_token_parity.py` |
| A7 | `/auth/refresh` `run_refresh`; impersonation end stamp; refresh tests |
| A8 | Completeness, reachability and one-minter tests; mutation matrix |

**PR1b**

| # | Task |
|---|---|
| B1 | Migration precondition (protocol 1); protocol 2 reporting |
| B2 | Validation cutover (middleware parse, `authenticate_runtime_token` in the request session, `core/auth.py`, MCP `verify_token`, `verify_iat`); bridge + counter; one-statement test |
| B3 | Workflow mint cutover; worker env; redaction; Redis context scrub; SDK engine-local refresh; remove `mint_engine_token`; `tests/helpers/runtime_tokens.py` |
| B4 | Service mint after commit; credential install split; remove `mint_service_token`; `test_service_claim_tokens.py` |
| B5 | MCP bridge runtime branch |
| B6 | README rule and docstrings |
| B7 | E2E; benchmarks |

**PR1c:** precondition (protocol 2 ≥ 25 h); delete the bridge; invert the
bridge e2e test.

---

## 11. Decisions

- **D1** Claims layout (§2): `sub`/`credential_kind`/`service_id` names kept
  so older pods validate new tokens.
- **D2** `iat = floor(T)`, with `T` read from the database clock under the
  shared locks; refused iff `not_before > iat`; `verify_iat` off.
- **D3** Writers stamp at commit under exclusive bucket locks taken in sorted
  order; minters take shared locks with try-only acquisition (§3.2–§3.4).
- **D4** Postgres only; no Redis copy (Jack, 2026-10-30).
- **D5** Facts come from the database, never `shared/role_cache`;
  `load_runtime_principal` is the in-process path and the parity oracle.
- **D6** Agent runs: no in-process token; the MCP bridge mints per call; no
  refresh token.
- **D7** `POST /auth/refresh` dispatches on `type`; run refresh skips the
  per-IP limiter.
- **D8** Run refresh tokens are reusable, not rotated, and hold no state;
  validity is liveness, with no `exp` (SC3, accepted).
- **D9** Service access tokens expire at the committed lease and are minted
  after each heartbeat commit (SC1, accepted).
- **D10** Old-format tokens are bridged until PR1c (Jack).
- **D11** Refresh required: 401 + `WWW-Authenticate: Bearer
  error="invalid_token", error_description="refresh_required"`.
- **D12** Completeness: triggers + registry ↔ triggers + read capture +
  reachability (§9.2).
- **D13** `sol_active` answers the run's own `?solution=` gate.
- **D14** Impersonation subject and end stamp (SC2, accepted).
- **D15** Facts without a spec subject stamp the affected live runs (agents,
  service definitions); the platform default workflow mode stamps every
  workflow with `permission_mode IS NULL`.
- **D17** Run refresh and refreshed access tokens are redacted; nested service
  tokens are scrubbed from the diagnostic copy.
- **D18** Delivery: PR1a → PR1b → PR1c with migration preconditions (SC6,
  accepted).
- **D19 (PR2 decision, Jack 2026-10-30; Q1 option 1)** Compress the `usr`
  block (zlib + base64url) for all tokens; the size warning measures the
  encoded token. PR1's claims schema uses this encoding from the start, so
  runs and people share one format and PR2 needs no claims-version change.

No open questions remain for PR1.
