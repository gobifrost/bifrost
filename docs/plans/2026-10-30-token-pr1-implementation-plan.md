# Token PR1 — Runs and Shared Pieces: Implementation Plan (revision 2)

Spec: `docs/superpowers/specs/2026-10-27-access-token-claims-design.md` (approved).
Branch `token/pr1-runs`, based on `origin/main` `ad623e250`; line numbers are at
that commit. Revision 2 answers Astra's review
(`astra-token-pr1-plan-1.md`, verdict "revise plan") and Jack's decision on the
old-token question (a short-lived bridge, removed in the following release).

PR1 delivers spec §10 item 1: `issue_access_token`, the claims schema, the
not-before stamps (table, migration, Redis copy), run refresh tokens and the
README rule. Every runtime kind switches to claims; people stay on today's
path until PR2. Decisions are `D…` (§11), spec conflicts `SC…` (§12), open
questions `Q…` (§13).

### What changed from revision 1

| Astra finding | Resolution | Where |
|---|---|---|
| 1. D3 relied on "+2 s" | Stamps get their time **after** the change commits, from the database clock, by whoever first sees the committed change; `iat` comes from the same clock before facts are read. No latency or app-clock assumption. Proof and deterministic slow-commit/skew tests | §3.2, §3.6, §9.2 |
| 2. Warm marker can't certify; resync unfenced; async copies | Redis is trusted only under a short **warm lease renewed by one certifier** after it has checked Postgres; resync is fenced against Redis restarts; per-write **pending guards** make validators consult Postgres from before commit until publication; no fire-and-forget copy is needed for correctness | §3.3–§3.5 |
| 3. Savepoints | No session listeners. The stamp row is written by a database trigger and the guard by an ordinary statement in the same transaction, so savepoints and rollbacks are handled by Postgres; publication only ever reads committed rows | §3.1, §3.3 |
| 4. Service token from an uncommitted lease | Rotation mints only after the heartbeat transaction commits, in a fresh session, with `exp` from the committed lease; rollback test | §5.4 |
| 5. Rolling deploys; T10 before minters | Three merge units (PR1a inert foundation, PR1b switch + bridge, PR1c bridge removal) with **migration-enforced** upgrade preconditions; mixed-fleet matrix | §8 |
| 6. Completeness and one-minter can't prove guarantees | Postgres triggers write every stamp row (cascades, generic updates and raw SQL included); a test-only deferred constraint proves every committed change also carried its guard; reserved runtime claims are refused by `create_access_token`; negative tests | §3.1, §7, §9.3–§9.4 |
| 7. Fractional-second wait | Stamps are whole seconds (ceiling); the minter re-reads the database clock after each wait and requires `iat ≥ stamp` | §3.6 |
| 8. Acceptance gaps | Poisoned-cache parity, mutation matrix, shared-IP refresh burst, repeated refresh then end, service-populated benchmark | §9 |
| 9. Spec conflicts | Listed for Jack, not settled | §12 |
| 10. Wrong references | Corrected throughout (`runtime.py:59/75/150/192/226`, `capture.py:265-275`, `service_claim.py:566`) | — |

---

## 1. Scope boundary

### 1.1 Mint sites

| Site | PR1 | PR2 / unchanged |
|---|---|---|
| `api/src/core/security.py:458` `mint_engine_token` (caller `jobs/consumers/workflow_execution.py:933-937`) | PR1b: removed; consumer mints access + run refresh token via `issue_access_token` / `issue_run_refresh_token` | — |
| `api/src/core/security.py:426` `mint_service_token` (callers `services/service_claim.py:463` `_rotate_token`, `:691` `_launch_claimed`) | PR1b: removed; both mint after commit (§5.4) | — |
| `services/mcp_server/tools/_http_bridge.py:75-86` runtime branch | PR1b: `issue_access_token` (D6) | — |
| `_http_bridge.py:88-104` human branch | — | PR2 |
| `routers/auth.py:684` (`mfa_initial_verify`), `:836` (`_generate_login_tokens`; also `routers/passkeys.py:207`), `:1126` (`refresh_token`), `:1892` (`setup_passkey_verify`); `routers/oauth_sso.py:423` (`oauth_callback`); `routers/mfa.py:254` (`verify_mfa`); `services/mcp_server/auth.py:509,:564` (both in `_token`, def `:412`) | — | PR2 |
| `core/security.py:245` `create_embed_access_token` (spec: `create_embed_token`), used `routers/embed.py:161,196,311` | — | unchanged (spec §7) |
| `shared/browser_impersonation.py:90` marker (`create_browser_impersonation`) | PR1a adds the end-stamp only (SC2) | format unchanged |
| `core/security.py:179` `create_mfa_token`; `shared/solution_dev.py:121` (`_mint_solution_dev_token`); `services/oauth_state_core.py:79` (`encode_state_jwt`) | — | unchanged (not access tokens) |
| `core/security.py:57` `create_access_token` | PR1a: refuses reserved runtime claims (§9.4) | PR2 |

### 1.2 Validation and authority sites

| Site | PR1 change |
|---|---|
| `core/app_wiring.py:221` middleware (runtime branch `:242-246`) | PR1b: `authenticate_runtime_token`; new-format tokens use no DB session; legacy-format tokens take today's path (bridge, §8.3) |
| `core/auth.py:135` `_get_current_user_optional` (runtime branch `:184-192`) | PR1b: same result object; legacy bridge unchanged path |
| `core/auth.py:343` `get_current_user` | PR1b: refresh-required 401 (D11) |
| `core/auth.py:401` `get_execution_context` | PR1b: own-Solution `?solution=` answered from `run.sol_active` (`:448-465`); role read `:430-433` skipped for runtime principals. Browser `X-Bifrost-App` gate `:472-495` → PR2 |
| `services/mcp_server/auth.py:661` `verify_token` (runtime branch `:689-745`) | PR1b: `authenticate_runtime_token`; tool-filter roles from `usr.roles[].name` |
| `routers/auth.py:999` `/auth/refresh` | PR1a: `run_refresh` branch (inert until PR1b issues refresh tokens) |
| `core/auth.py:39` `select_http_auth_token`, `:591` `get_current_user_ws`; `runtime_auth.py:34` `load_runtime_principal` and its in-process callers (`jobs/consumers/agent_run.py:274,376`, `services/execution/autonomous_agent_executor.py:1367`, `services/authorization/callers.py:58`, `services/mcp_server/authorization.py:22`) | unchanged (D6) |
| `services/authorization/enforce.py:118` `load_caller` human branch; `shared/browser_impersonation.py:130` | PR2 |

Not in PR1: policy evaluation's data reads, logout/revoke-all, people
lifetimes/cookies (Q1), MCP OAuth refresh JTIs, the human `roles` claim shape.

### 1.3 Gaps from `token-spec-gaps-20261029.md`

| Gap | Disposition |
|---|---|
| `create_embed_token` vs `create_embed_access_token` | allowlisted by real name (§9.4) |
| Agent runs mint no JWT; MCP bridge mints | D6 |
| Run refresh endpoint; worker env slots (`worker.py:51-52`) | §5 |
| Service lifetime | SC1 |
| No `iat`/`jti`; 1 s ambiguity | §3.6, D2 |
| Warm marker key | §3.4 |
| Cookie limit, cookie max_age (`auth.py:126`), MCP `expires_in` (`mcp_server/auth.py:518,571`), MCP refresh JTI (`:510,:565`) | PR2; Q1 |
| Browser App/Solution gates (`core/auth.py:459,490,493`) | runtime own-Solution gate PR1b; rest PR2 |
| `build_authorization_context` docstring (`context.py:120`) | fixed in A5 |
| `roles` claim shape | runtime claims carry ids + names; human shape PR2 |
| Completeness mechanism | §7, §9.3 |

---

## 2. Claims schema (`ver: 1`)

Pydantic models in `api/src/services/authorization/tokens.py`
(`frozen=True, extra="forbid"`; internal, not an HTTP contract).

```text
iss, aud, type="access", ver=1, iat:int, exp:int, jti:str
sub: str                      runtime: credential id (name kept, D1)
credential_kind               "workflow_execution" | "service_attempt" | "agent_run"
service_id: str | absent      service_attempt only
usr: { id, email, name, kind (identity_kind|null), org (home|null), ext, prov, active,
       base: {id, perm:[str]},
       roles: [ {id, name, perm:[str], bnd:[[kind, org|null]]} ] }      run user
act: { id, email, name, org, ext, prov, roles:[{id, name}] } | absent  acting user ≠ run user
run: { org, wf, sol, sol_active, root, by, powers: {mode, grants:[str]} | null }
imp: { by, sid } | absent
```

`principal_from_claims` reproduces `runtime_auth._principal_from_authority`
(`runtime_auth.py:66-133`): `AuthorizationContext` from `usr` (role grants =
roles with a non-empty `bnd`, which is what `build_authorization_context`
yields); `RunUser(usr.id, ctx, usr.kind, usr.active)`; actor = `act` or `usr`
supplies `email/name/is_external/is_provider_org/role_ids/role_names/roles`
and `is_superuser = PLATFORM_ADMIN_ROLE_ID ∈ actor role ids`;
`organization_id = run.org`; `RuntimeAuthority` from `credential_kind/sub/
service_id/run/imp` plus new `solution_active` (D13). Grants use
`WorkflowGrant.model_construct` (signed claims are not re-validated).

**Size** (synthetic payloads shaped like the spec's production numbers):
typical run 1,819 B; production's largest user 4,444 B; largest user + distinct
12-role actor + impersonation + 10 grants 6,308 B (warns); pathological
(20 roles, 30 boundaries) 10,115 B. Production max ≈ 4.4 KB. Minting warns
above 6,144 B and never refuses (spec §3).

---

## 3. Not-before stamps

### 3.1 Table and triggers (PR1a migration `20261030_authz_not_before.py`)

```text
authz_not_before(
  subject_kind   varchar(32)  CHECK IN ('user','role','organization','run','workflow','solution','impersonation'),
  subject_id     uuid,
  not_before     timestamptz NULL     -- finalized stamp, whole seconds
  pending        boolean NOT NULL DEFAULT false   -- a committed change awaits finalization
  pending_xid    xid8 NULL            -- transaction that marked it pending
  guarded_xid    xid8 NULL            -- transaction that registered the Redis guard
  unpublished    boolean NOT NULL DEFAULT false   -- finalized but not yet in Redis
  PRIMARY KEY (subject_kind, subject_id))
INDEX ix_authz_nbf_open ON (subject_kind, subject_id) WHERE pending OR unpublished
INDEX ix_authz_nbf_not_before ON (not_before)
authz_protocol_state(protocol int PRIMARY KEY, certified_at timestamptz NOT NULL)
scheduler_replicas.authz_protocol int NULL;  execution_workers.authz_protocol int NULL
```

ORM models in `api/src/models/orm/authz_not_before.py`, registered in
`models/orm/__init__.py`; the two `authz_protocol` columns on
`models/orm/scheduler_diagnostics.py:13` and `models/orm/execution_workers.py:27`.

A PL/pgSQL function `authz_nbf_mark(kind text, id uuid)` does `INSERT … ON
CONFLICT DO UPDATE SET pending = true, pending_xid = pg_current_xact_id()`.
Row triggers call it **in the transaction that makes the change** (spec §5);
the migration contains literal SQL (migrations stay immutable; §9.3 keeps code
and triggers in step). Live statuses are the stored enum values (`'Pending'`,
`'Running'`, `'Cancelling'`; agent runs `'running'`, `'cancelling'`; attempts
`'starting'`, `'running'`, `'stopping'`).

| Table | Trigger condition | Subject(s) |
|---|---|---|
| `users` | UPDATE OF email, name, organization_id, base_role_id, is_external, identity_kind, is_active (value changed); DELETE | `user:id` |
| `organizations` | UPDATE OF is_provider (changed); DELETE | `organization:id` |
| `user_roles`, `user_role_boundaries` | INSERT / UPDATE / DELETE (incl. FK cascades) | `user:user_id` (old and new) |
| `role_permissions` | INSERT / UPDATE / DELETE | `role:role_id` |
| `roles` | UPDATE OF name (changed); DELETE | `role:id` |
| `workflows` | UPDATE OF solution_id, permission_mode (changed); DELETE | `workflow:id` |
| `workflow_permission_grants`, `solution_workflow_permission_requests` | INSERT / UPDATE / DELETE | `workflow:workflow_id` (old and new) |
| `system_configs` | any row with category `workflow_permissions`, key `default_mode`, org NULL | `workflow:` every workflow with `permission_mode IS NULL` |
| `solutions` | UPDATE OF status (changed); DELETE | `solution:id` |
| `executions` | status leaves a live value; DELETE of a live row; UPDATE OF run_user_id, executed_by, workflow_id, organization_id, started_by_user_id, root_execution_id, impersonated_by_user_id, impersonation_session_id (changed) | `run:id` |
| `agent_runs` | status leaves running/cancelling; DELETE live; UPDATE OF run_user_id, acting_user_id, agent_id, org_id, impersonation columns (changed) | `run:id` |
| `service_attempts` | state leaves a live value; DELETE live; lease_expires_at decreased; UPDATE OF run_user_id, service_id (changed) | `run:id` |
| `service_definitions` | UPDATE OF workflow_id, organization_id (changed) | `run:` each live attempt |
| `agents` | UPDATE OF solution_id (changed); DELETE | `run:` each live agent run |

Impersonation end (SC2) has no table change: `record_authz_event(session,
Subject.impersonation(sid))` calls `authz_nbf_mark` and sets the guard in one
short transaction **before** the Redis session record is deleted
(`shared/browser_impersonation.py:186,225`).

### 3.2 Correctness argument (finding 1)

Let change C commit at database time `Tc`. Its row becomes `pending` in C's
own transaction, so it is visible exactly from `Tc`.

- A stamp value is assigned only by `finalize_stamps` (§3.3), in a statement
  that reads the committed pending row, as `ceil(clock_timestamp())`. That
  statement starts after `Tc`, so `F ≥ Tc` on the database clock, however long
  C's commit took.
- A minter takes `T0 = clock_timestamp()` in a statement **before** it reads
  any fact and sets `iat = floor(T0)`. If `T0 < Tc`, the token may hold
  pre-change facts, and `iat ≤ T0 < Tc ≤ F`, so `iat < F` and it is refused.
  If `T0 ≥ Tc`, every fact read after `T0` reflects C.
- Between `Tc` and finalization, the row is `pending`; validators that read
  Postgres refuse any token for a pending subject (§3.5), and Redis is not
  consulted for a guarded subject (§3.3). A fresh token can't exist while the
  row is pending, because the minter finalizes pending rows of its subjects
  before minting (§3.6).

Only the database primary's clock is used for `F` and `iat`, so app-host skew
is irrelevant. The one assumption is that the primary's clock does not step
backwards (it slews under chrony/NTP). PyJWT's `verify_iat` is turned off in
`decode_token` (`core/security.py:146`) because a database-clock `iat` can be
ahead of a validating host's clock; `exp`, signature, `iss`, `aud` are still
verified.

### 3.3 Write path: trigger + guard; finalization

`api/src/services/authorization/not_before.py`:

```python
class SubjectKind(StrEnum): USER, ROLE, ORGANIZATION, RUN, WORKFLOW, SOLUTION, IMPERSONATION
@dataclass(frozen=True, order=True)
class Subject: kind: SubjectKind; id: UUID

async def guard_authz_change(session: AsyncSession, *subjects: Subject) -> None
async def record_authz_event(session: AsyncSession, *subjects: Subject) -> None   # no table change (impersonation end)
async def finalize_stamps(subjects: Iterable[Subject] | None = None) -> list[Subject]  # own session; None = all pending
```

**Guard** (called in the service/repository function that makes the change,
before commit, next to existing `role_cache`/`invalidate_*` calls — spec §5):
1. `x = SELECT pg_current_xact_id()` (top-level id, also inside a savepoint);
2. upsert each subject row with `guarded_xid = x` (does not touch `pending`);
3. Redis: `ZADD bifrost:authz:nbf:<kind>:<id>:pending <now+5400> <x>` and
   `SADD bifrost:authz:nbf:_guarded <kind>:<id>`, `EXPIRE` 5400 s.

While the zset holds a live member, validators consult Postgres for that
subject (§3.5), so the change is enforced from the instant it commits, and
before that the old committed row still applies. If the transaction rolls back
(whole or the savepoint containing the change), Postgres discards the trigger
row change; the leftover Redis member only costs Postgres reads until the
certifier clears it. No session events, so nested commits never publish
anything (finding 3).

**Finalization** (`finalize_stamps`, own short transaction):
```sql
UPDATE authz_not_before
   SET not_before = greatest(coalesce(not_before,'-infinity'),
                             to_timestamp(ceil(extract(epoch FROM clock_timestamp())))),
       pending = false, pending_xid = NULL, unpublished = true
 WHERE ctid IN (SELECT ctid FROM authz_not_before
                 WHERE pending [AND (subject_kind, subject_id) IN (...)]
                 FOR UPDATE SKIP LOCKED)
RETURNING subject_kind, subject_id, not_before;
```
then the Redis max-merge (§3.4) for every returned row, then `unpublished =
false` for those rows, then COMMIT. If Redis fails, it commits with
`unpublished = true` (the certifier publishes later). A crash after the Redis
write and before COMMIT leaves the row pending; the next finalization writes a
value ≥ the published one. Rows locked by an uncommitted writer are skipped —
that writer's change is not committed yet.

Callers of `finalize_stamps`: the certifier (all), the minter (its subjects).
Validators never write.

### 3.4 Redis keys

| Key | Value / type | Lifetime |
|---|---|---|
| `bifrost:authz:nbf:<kind>:<uuid>` | `F` (integer epoch seconds) | `EXAT F + 5400` |
| `bifrost:authz:nbf:<kind>:<uuid>:pending` | zset: member = guarding xid, score = expiry epoch | `EXPIRE 5400` on each `ZADD` |
| `bifrost:authz:nbf:_guarded` | set of `<kind>:<uuid>` with pending members | — |
| `bifrost:authz:nbf:_warm` | certifier lease id | `EX 15`, renewed by the certifier only |
| `bifrost:authz:nbf:_fence:<uuid>` | resync fence | `EX 60` |

Max-merge script (never lowers a stamp):
`if not GET(k) or tonumber(GET(k)) < tonumber(F) then SET k F EXAT F+5400`.

### 3.5 Read path (validators, PR1b)

`check_not_before(subjects, iat) -> ACCEPT | REFRESH_REQUIRED`:

1. One `EVALSHA` (keys: `_warm`, then value and pending keys per subject;
   args: `iat`, now). Returns `cold` if `_warm` is missing; `refresh` if any
   value `> iat`; `pg:<subjects>` for subjects with a live pending member;
   otherwise `ok`.
2. `pg` / `cold` / Redis error (after one retry) → one indexed query
   `SELECT subject_kind, subject_id, not_before, pending FROM authz_not_before
   WHERE (subject_kind, subject_id) IN (…)`, in a short session from
   `get_session_factory()`: refuse if any row is `pending` or `iat <
   not_before`.
3. A process that saw a Redis error deletes `_warm` on its next successful
   Redis call, before trusting any answer.

Steady-state cost: one Redis round trip, zero SQL.

### 3.6 Minter's stamp protocol (fixes findings 1 and 7)

```text
for attempt in 1..5:
    T0 = SELECT clock_timestamp()                -- database clock, before any fact
    facts = load(subject)                        -- §4.1
    subjects = token_subjects(facts)
    rows = stamp rows for subjects (Postgres)
    if any rows pending: finalize_stamps(those); continue
    M = max(not_before) as integer seconds (None if no rows)
    if M is None or floor(T0) >= M: iat = floor(T0); mint; return
    sleep(M - T0 + 0.05)                         -- measured on the DB clock; next loop re-reads it
raise AuthorityUnsettled                         -- /auth/refresh → 503 + Retry-After: 1
```

`M` is a whole number, so `floor(T0) ≥ M ⇔ T0 ≥ M`: after waking, the
fresh `T0` passes deterministically once the database clock reaches `M`
(finding 7: `S = 102.4` is stored as `103`; a minter at `102.45` sleeps 0.6 s
and mints `iat = 103`). A change that lands during the loop forces another
pass; five passes cover a stamp storm of ~4 s before the refresh is refused
with a retryable 503. The minter never rolls back its caller's transaction;
facts use `populate_existing=True`, and read-committed statements see newly
committed rows.

### 3.7 Certifier (PR1a)

An asyncio task started in the trigger-leader services
(`scheduler/control.py:291` `_start_leader_services`, stopped at `:322`) — not
a scheduled-task catalog entry, so it writes no task-state rows. Every 5 s:

1. `finalize_stamps(None)` (committed pending rows).
2. Publish `unpublished` rows (max-merge), then clear the flag.
3. For each `_guarded` subject: drop pending members whose
   `pg_xact_status(xid)` is not `in progress` once the subject's row is
   neither pending nor unpublished; `SREM` when empty.
4. Prune rows with `not pending AND not unpublished AND not_before < now() -
   5400 s` (`SKIP LOCKED`, `LIMIT 500`).
5. Warm lease: if `_warm` exists and steps 1–2 left nothing open, renew it
   (`SET _warm <id> XX EX 15`). If missing, run a fenced resync: `SET
   _fence:<u> 1 EX 60`; max-merge every row with `not_before > now() - 5400 s`;
   then a script sets `_warm` only if `_fence:<u>` still exists (a Redis
   restart mid-resync drops the fence, so warm is not set); `DEL` the fence.
6. Protocol certification (§8.2).

Idle cost: one short transaction per 5 s on the leader replica (≈ 0.2 tx/s).

### 3.8 Guarantees (findings 1–3)

| Situation | Result |
|---|---|
| Guarded change, Redis healthy | enforced from commit (Postgres via the guard), then via Redis |
| Writer crashes after commit | row stays pending; guard keeps validators on Postgres → enforced; certifier finalizes ≤ 5 s |
| Redis down for validators | Postgres per request (spec) |
| Redis restarted empty, or restarted during a resync | `_warm` and fence lost → validators use Postgres until a fenced resync completes |
| A process saw a Redis failure | deletes `_warm` on reconnect → everyone uses Postgres until resync |
| Change with **no** guard (a missed call — caught by tests, §9.3), or a guard/publication the writer could not deliver because Redis was unreachable **from the writer only** | Postgres row is still written by the trigger; Redis may lack it until the certifier publishes or stops renewing `_warm`: **≤ 5 s** (SC5) |
| Certifier down | `_warm` expires in 15 s → Postgres per request until a leader returns |

---

## 4. The single minter

```python
@dataclass(frozen=True)
class RuntimeSubject: kind: RuntimeKind; credential_id: UUID; service_id: UUID | None = None
@dataclass(frozen=True)
class IssuedAccessToken: token: str; expires_at: datetime; claims: AccessTokenClaims

async def issue_access_token(db: AsyncSession, subject: RuntimeSubject) -> IssuedAccessToken
def issue_run_refresh_token(subject: RuntimeSubject) -> str
def principal_from_claims(claims: AccessTokenClaims) -> UserPrincipal
def token_subjects(claims: AccessTokenClaims) -> frozenset[Subject]
async def authenticate_runtime_token(payload: dict[str, Any]) -> RuntimeAuthResult | None
```

### 4.1 Facts

1. `load_runtime_authority` (`runtime.py:75`), unchanged liveness rules
   (`RuntimeIdentityError` → `TokenSubjectUnavailable`). A5 tightens its loads to
   `load_only` the used columns (`_load_service_attempt` `:166`,
   `_load_agent_run` `:207`, the service-path `Workflow`, `list_workflow_grants`,
   `workflow_permission_requests`), and adds to `RuntimeAuthority` (`:59`)
   `solution_active` (one `select(Solution.status)`) and `lease_expires_at`
   (service attempts only; `None` otherwise and for claim-built authorities).
2. Run user's roles with names, permissions and boundaries from the database,
   never `shared/role_cache` (D5).
3. Run user identity + home org `is_provider`; acting user identity, org and
   roles when `acting_user_id != run_user.user_id`.

### 4.2 Subjects carried (complete mapping, finding 6)

`token_subjects(claims)` = `user:usr.id`, `user:act.id`, `role:usr.base.id`,
`role:` every `usr.roles[].id` and `act.roles[].id`, `organization:usr.org`,
`organization:act.org`, `run:sub`, `workflow:run.wf`, `solution:run.sol`,
`impersonation:imp.sid` (SC2); `None` values skipped. A unit test asserts that
every subject kind written by a trigger row in §3.1 reaches this set for the
tokens that carry the changed fact (table in §9.3).

### 4.3 Lifetimes

| Kind | Access token | Run refresh token |
|---|---|---|
| `workflow_execution` | `3600 + secrets.randbelow(1801)` s | no `exp`; valid while the run is live (SC3) |
| `service_attempt` | until the committed `lease_expires_at` (≈ 60 s), re-minted after each heartbeat commit (SC1) | no `exp`; valid while the attempt is live |
| `agent_run` (MCP bridge) | `3600 + secrets.randbelow(1801)` s | none (D6) |

All access lifetimes ≤ 5400 s (asserted). Size > 6,144 B logs a warning with
kind, id and size only. `exp = iat + lifetime`, passed via `expires_delta`.

### 4.4 Run refresh token

`{iss, aud, type:"run_refresh", ver:1, iat, jti, sub, credential_kind,
service_id?}`; identity only, no DB read. `decode_token(expected_type=
"access")` rejects it; the human refresh branch rejects it by type.

---

## 5. Run refresh

### 5.1 Endpoint (PR1a)

Existing `POST /auth/refresh` (`routers/auth.py:999`); `TokenRefresh`
(`:296`) and `Token` (`:252`) unchanged, so no CLI fingerprint change (D7).
Decode without `expected_type`; `type == "refresh"` → today's human branch,
untouched. `type == "run_refresh"` → require `ver == 1`; `issue_access_token`;
`TokenSubjectUnavailable` → 401 "Run is no longer live"; `AuthorityUnsettled`
→ 503 `Retry-After: 1`; success → `Token(access_token=new,
refresh_token=<same run refresh token>)`, no cookies. Run refresh skips the
per-IP `auth_limiter` (every child of a worker shares its pod IP; the token is
server-signed and liveness-checked). Not single-use, not rotated (D8).

### 5.2 Workers (PR1b)

`services/execution/worker.py:37-52`: context key `engine_refresh_token` →
`BIFROST_REFRESH_TOKEN`. Add it to `_CONTEXT_REDIS_EXCLUDED_KEYS`
(`process_pool.py:82`) and to the output redaction next to `engine_token`
(`worker.py:297`, `engine.py:324`); refreshed access tokens are added to the
same redaction set through `install_access_token` (`bifrost/client.py:917`).

### 5.3 SDK (PR1b)

`bifrost/client.py`: engine-local requests refresh on 401 through the network
`/auth/refresh` and replay once over the socket (both request paths ~`:965`
and ~`:1007`; rebuild the engine client after `install_access_token`); update
the docstrings that say engine-local never refreshes. `refresh_connection_
access_token` (`:267`) and `save_refreshed_credentials` (`credentials.py:645`)
already accept an unchanged refresh token in the process backend.

### 5.4 Services (PR1b; finding 4)

- Launch: the claim is already committed before launch
  (`service_claim.py:566`); `_launch_claimed` runs in a second session
  (`:581-590`). Mint access + refresh tokens there, before routing.
- Rotation: `tick` commits the heartbeat at `:179` after `_beat_owned`.
  `_beat_owned` stops minting; it returns the attempts that renewed. **After**
  that commit, each is minted in its own short session (concurrency limit 4),
  reading the committed `lease_expires_at` for `exp`, and only then written to
  `service_token_key`. A rolled-back heartbeat therefore never yields a token.
  Stamp waits in one mint don't delay the heartbeat or other attempts.
- `bifrost/_service_runtime.install_service_credentials(access, refresh)` at
  start (`worker.py:419-421`, `engine.py:950-952`);
  `install_rotated_service_access_token(access)` in the supervisor
  (`engine.py:1082`) leaves the refresh slot alone. Nested
  `service.token`/`service.refresh_token` are scrubbed from the diagnostic
  Redis context copy (`process_pool.py:1159`).

---

## 6. Repository authority rule (PR1b)

Rule (spec §8): claims in an access token minted by `issue_access_token` are
authority until it expires or a not-before stamp covers it.

1. `authenticate_runtime_token` is the only claims→principal path: signature,
   `type == "access"`, `ver == 1`, strict schema, `check_not_before` ACCEPT.
2. `principal_from_claims` is imported only by `tokens.py` (AST test).
3. `create_access_token` refuses reserved claims (`ver`, `credential_kind`,
   `usr`, `act`, `run`, `imp`); only `tokens.py`'s `_encode_claims` signs them
   (§9.4).
4. `api/src/repositories/README.md`: "Authority model" paragraph and
   implementation rule 1 change to the rule above; "`RuntimeAuthority` is
   loaded from the current database state" becomes "built from claims that
   `issue_access_token` loaded from current state at mint or refresh; a
   not-before stamp forces a re-mint". Legacy-format runtime tokens are
   described as bridged until PR1c.
5. Tests pinning the old rule: `tests/unit/authorization/test_runtime_auth.py::
   test_runtime_principal_uses_database_authority_not_spoofed_token_claims`
   becomes: legacy payload uses the DB path (bridge) until PR1c; `ver:1` claims
   are authority with zero SQL; tampered token fails signature. Docstrings:
   `runtime_auth.py:1,37`, `runtime.py:1-5`, `core/principal.py:22-24,63-82`,
   `mcp_server/server.py:143-145`.

---

## 7. Guards at write sites (spec §5 location)

The triggers make the Postgres stamps complete; guards make enforcement exact
from commit. Each listed function calls `guard_authz_change` before commit.
Router writes move into services first: `routers/executions.py:853,863`,
`routers/agent_runs.py:645,1132`, `routers/solutions.py:2242`,
`routers/profile.py:93`. The test oracle (§9.3) is the completeness check;
this list is the starting inventory.

| Fact | Functions |
|---|---|
| Role permissions / name / delete | `services/role_permissions.py:57`; `shared/sdk_roles.py` update (~440-470) and delete (~520-545) — delete also guards holders |
| Assignments, boundaries | `services/user_role_assignments.py:140,524` (deletes `:649,:656`); `shared/sdk_roles.py` assign (~780-803), remove (`:822`, `:863`); `shared/sdk_users.py:152` |
| User fields, delete | `shared/sdk_users.py:92,648,653,662,673,677,936,1001`, user delete; profile name (moved) |
| Workflow powers / ownership / delete | `services/workflow_permissions.py:214` (`:247,:252`, request rows); `services/solutions/deploy.py:1056` and its workflow upserts; `services/solutions/capture.py:265-275` (`_capture_model`, generic `update(model).values(**values)` — guards per model); workflow delete paths |
| Solution status / delete | `routers/solutions.py:2242` (moved), `services/solutions/zip_install.py:535`, hard delete |
| Agent `solution_id` | capture/deploy agent writes (guard live runs of the agent) |
| Run end — executions | `repositories/executions.py:191` (non-live status), moved router writes, `jobs/schedulers/execution_cleanup.py:186`, `services/worker_pool/recovery.py:94,188` (add `RETURNING id`), `services/legacy_broker_drain.py:177`, `services/execution/workflow_dispatch.py:258`, `shared/run_cancellation.py:27`, `shared/sdk_workflow_execution.py:776`, `jobs/schedulers/deferred_execution_promoter.py:193` |
| Run end — agent runs | `jobs/consumers/agent_run.py` (13 writes), moved router writes, `services/execution/agent_run_dispatch.py:73`, `services/worker_pool/recovery.py:119`, `services/legacy_broker_drain.py:246`, `services/chat_runs.py:543`, `services/agent_executor.py`, `services/execution/autonomous_agent_executor.py` |
| Run end — service attempts | `services/service_lifecycle.py:812`, `:922` |
| Impersonation end | `shared/browser_impersonation.py:186,225` via `record_authz_event` |

---

## 8. Rollout (finding 5, Jack's bridge decision)

### 8.1 Merge units

Main squash-merges each PR (one commit per PR; e.g. `ad623e250`, `16d2e45a5`),
so every merged commit is one of these units. Inside a unit, tasks keep the
suite green but are not deployed separately.

| Unit | Contents | Authorization behaviour |
|---|---|---|
| **PR1a — foundation** | table, triggers, protocol table/columns; guards; finalize; certifier; Redis publication; claims schema; minter; `run_refresh` branch; `create_access_token` reserved-claim refusal; completeness and oracle tests; impersonation end-stamp | **None changes.** Minters still issue today's tokens; validators unchanged; nothing reads stamps |
| **PR1b — switch** | migration precondition (protocol 1 certified); dispatch/service/bridge minters; worker env; SDK engine-local refresh; validators trust claims; refresh-required; legacy bridge; README; protocol 2 reporting | claims are authority |
| **PR1c — bridge removal** (follow-up, following release; file the issue when PR1b merges) | migration precondition (protocol 2 certified ≥ 25 h); delete legacy acceptance in middleware, `core/auth.py`, MCP `verify_token`; remove bridge counter | legacy runtime tokens refused |

### 8.2 Upgrade preconditions (mechanical)

- API replicas report `scheduler_replicas.authz_protocol` in the existing
  heartbeat (`scheduler/control.py:93` → `services/scheduler_diagnostics.py:32`):
  PR1a = 1, PR1b = 2. Workers report `execution_workers.authz_protocol` in
  their heartbeat (`services/worker_pool/claims.py:91,124,324`).
- The certifier inserts `authz_protocol_state(p, now())` once every replica
  and non-retired worker with a heartbeat in the last 30 s reports ≥ p.
- PR1b's migration raises "Upgrade to <PR1a release> and let it finish rolling
  out first" unless protocol 1 is certified. PR1c's raises unless protocol 2
  was certified ≥ 25 h ago (the longest old engine token is 24 h + 5 min).
  Both pass on a fresh database (no `scheduler_replicas` rows ever written;
  the earlier unit's migration then records its own certification). A failed
  init container leaves the previous release serving; nothing breaks.

### 8.3 Legacy bridge (PR1b → removed in PR1c)

A runtime-shaped payload without `ver` (`is_runtime_credential_payload`,
`runtime_auth.py:29`) is validated exactly as today: `load_runtime_principal`
in the middleware's DB session (`app_wiring.py:242-246`), including the live-run
check, until the JWT's own `exp`. Nothing new is granted: its refresh slot
holds the same access token, which `/auth/refresh` rejects, so it can't be
exchanged for a new-format token. Each acceptance logs
`authz.legacy_runtime_token_accepted` (kind, exp) to confirm the drain before
PR1c.

### 8.4 Mixed-fleet matrix

| Mix | Hazard | Why it is safe |
|---|---|---|
| pre-PR1a + PR1a | old pods change authority | triggers (installed by the migration before any PR1a pod starts) stamp every write from any pod; nothing trusts claims yet |
| PR1a + PR1b | new-format token on a PR1a pod | PR1a validates it through `load_runtime_principal` (names kept, D1) |
| PR1a + PR1b | refresh lands on a PR1a pod | `run_refresh` exists from PR1a |
| PR1a + PR1b | PR1a pod writes authority | triggers stamp; PR1a guards are present |
| PR1a + PR1b | old-format token on a PR1b pod | bridge |
| pre-PR1a + PR1b | skip-upgrade | refused by PR1b's migration |
| PR1b + PR1c | legacy tokens | none remain: protocol 2 certified ≥ 25 h before PR1c migrates |

---

## 9. Test plan

`./test.sh` targets (not run during the capacity benchmark).

### 9.1 Unit — protocol and minter

| File | Contract |
|---|---|
| `tests/unit/authorization/test_not_before_triggers.py` | for every row of §3.1, a SQL change (incl. FK cascade on role delete, generic `update(model).values(**v)`, raw SQL) marks the expected subjects pending; unrelated column changes don't; rolled-back transaction or savepoint leaves no mark; nested savepoint commit + outer rollback leaves no mark |
| `tests/unit/authorization/test_finalize.py` | `F ≥` commit time on the DB clock; whole seconds; max-merge; Redis failure → committed with `unpublished`; crash between Redis write and commit (simulated) → re-finalize ≥ published |
| `tests/unit/authorization/test_certifier.py` | finalizes pending; publishes unpublished; clears resolved guards (committed, aborted, in-progress kept); renews `_warm` only when clean; fenced resync with a Redis `FLUSHALL` injected between pipeline and fence check → warm not set; concurrent writer during resync → its row published; protocol certification |
| `tests/unit/authorization/test_not_before_check.py` | `ok`/`refresh`/`pg`/`cold`; boundary `iat == F` accepted, `iat == F-1` refused; pending row refused; Redis error → retry → Postgres; observer deletes `_warm` after an error; **separate observers**: process A's publish fails, process B reads before anyone deletes warm → B consults Postgres because the subject is guarded; **process death** (writer killed after commit, before finalize) → refused via guard until the certifier finalizes |
| `tests/unit/authorization/test_stamp_ordering.py` (finding 1) | **slow commit**: writer changes a role and holds COMMIT; minter mints from pre-change facts; writer commits after an arbitrary delay (event-ordered, plus a 3 s delay case); the minted token is refused via Redis and via Postgres. **Skew**: validator and minter app clocks patched ±5 s — no effect on refusal or acceptance; database-clock `iat` ahead of host clock accepted (verify_iat off) |
| `tests/unit/authorization/test_minter_wait.py` (finding 7) | fake DB clock: stamp 102.4 → stored 103; minter at 102.45 sleeps and mints `iat=103`; first token after a stamp is never refused; stamp storm → `AuthorityUnsettled` after 5 passes |
| `tests/unit/authorization/test_token_claims.py` | schema round trip; derivations; production-largest fixture < 6,144 B; warning above it; never refused |
| `tests/unit/authorization/test_issue_access_token.py` | lifetimes (spread 3600–5400; service `exp == committed lease`); `iat/jti`; non-live → `TokenSubjectUnavailable`; run refresh token shape |
| `tests/unit/authorization/test_token_parity.py` (finding 8) | for each scenario of `test_runtime.py`, `principal_from_claims(issue…)` equals `load_runtime_principal` with a cleared role cache; separately, a **poisoned** role cache (stale role list for the actor) does not reach the claims |
| `tests/unit/authorization/test_runtime_auth.py` | §6.5 three tests; zero SQL for a warm runtime request (`before_cursor_execute` counter, as `test_runtime.py:286`) |
| `tests/unit/services/test_service_claim_tokens.py` (finding 4) | heartbeat flushes a new lease then raises before commit → no token written; tokens' `exp` never exceeds the committed lease; slow mint in one attempt doesn't delay the heartbeat |
| `tests/unit/services/test_worker_sdk_http.py::…:1482` (inverted) | engine-local 401 → one network refresh → one socket replay |
| auth refresh tests | live → new token, same refresh token, no cookies; ended run / expired lease → 401; **50 concurrent run refreshes from one IP → all 200** while human refresh stays limited; **repeated refresh (10×) then run end → 401** |
| migration precondition tests | PR1b/PR1c migrations refuse without certification; pass on a fresh database |

### 9.2 Mutation matrix (finding 8)

One unit test per change, each: mint → change through the real service
function → old token refused (Redis and Postgres paths) → refresh → new claims
reflect the change: role grant/revoke, boundary add/remove, base-role change,
org move, provider flag (SQL, no app writer), role rename, role permission
edit, role delete (cascade), distinct actor's role change, distinct actor's org
move, workflow powers, Solution deactivate/reactivate, `Agent.solution_id`
capture, impersonation end, execution end, service attempt end (incl.
`expire_leases`), agent run end.

### 9.3 Completeness (finding 6)

- **Read side** (`test_not_before_completeness.py`): the minter runs for each
  kind under a `do_orm_execute` capture; the set of `(table, column)` read must
  equal `NOT_BEFORE_FACTS` (code registry). Meta-test: a patched loader that
  reads an extra column fails it.
- **Registry ↔ triggers**: every registry entry has a trigger in `pg_trigger`
  whose definition covers its columns (parsed from `pg_get_triggerdef` and the
  trigger's `COMMENT` tag), and vice versa.
- **Guard oracle** (test stack only, installed by `tests/conftest.py` and
  `tests/e2e/conftest.py` from `tests/fixtures/authz_guard_oracle.sql`): a
  `DEFERRABLE INITIALLY DEFERRED` constraint trigger on `authz_not_before`
  re-reads the row at commit and raises `unguarded authz change <kind>:<id>`
  when `pending_xid` is set and differs from `guarded_xid`. Every unit and e2e
  test that commits a fact change through a code path without the right guard
  fails at commit, naming the subject — including generic updates, `setattr`
  writes and cascades.
- **Negative tests** (`test_guard_oracle.py`): change without guard → commit
  fails; guard on the wrong subject (role instead of user) → fails; guard in a
  different transaction → fails; guard inside a rolled-back savepoint with the
  change outside → fails; role delete guarding the role but not its holders →
  fails.
- **Subject reachability**: for each §3.1 row, a token whose claims carry the
  changed fact includes that subject in `token_subjects` (base role, distinct
  actor and both organizations included).

### 9.4 One minter

- `create_access_token` raises on reserved claims; unit test.
- AST over `api/src`, `api/shared`, `api/bifrost`: `jwt.encode` callers ⊆
  {`tokens._encode_claims`, `security.create_access_token`,
  `security.create_refresh_token`, `security.create_mfa_token`,
  `solution_dev._mint_solution_dev_token`, `oauth_state_core.encode_state_jwt`};
  `create_access_token` callers ⊆ {`security.create_embed_access_token`,
  `browser_impersonation.create_browser_impersonation`} ∪ PR2-marked human
  sites (§1.1, incl. `_http_bridge._token_from_context` human branch).
- Negatives: a runtime-claims call to `create_access_token` inside
  `_token_from_context` raises (the allowlist can't hide it); a synthetic
  module with a new `jwt.encode` is flagged by the AST check.

### 9.5 E2E (`tests/e2e/platform/test_runtime_token_claims.py`)

Revocation (role grant then revoke mid-run → refresh → new facts); run end
(token and refresh refused); long wait (expired access token → refresh →
continue); Solution deactivate → 409 after refresh; legacy bridge (old-format
token accepted until `exp`, refresh refused). Existing suites that must pass:
`tests/unit/authorization/*`, `tests/e2e/platform/test_agent_runtime_boundary.py`,
`test_chat_runtime_boundary.py`, `test_engine_token_redaction_live.py`,
`test_solution_permission_runtime.py`, `test_r3a_identity_authorization.py`,
`test_sdk_socket_audit_parity.py`, `tests/e2e/mcp/test_mcp_parity.py`,
service-credential e2e, `tests/unit/test_contract_version.py`,
`tests/unit/test_dto_flags.py`, `tests/unit/test_mcp_thin_wrapper.py`.

### 9.6 Benchmark

`./test.sh bench run --profile baseline --backend fake` on `ad623e250`, after
PR1a and after PR1b; report in `docs/benchmarks/`. Add a `services` profile
(20 supervised services, 10 min) to `scripts/bench/profiles/`.

| Measure | Expectation |
|---|---|
| Transactions per run per class (`stats.py:112`) | PR1a: +0 to +1 per authority write (guard), +0 per run (trigger rides the completion transaction). PR1b: `wf-sdk` drops ≥ 1 per SDK call (`app_wiring.py:243` session gone); `wf-noop` ≤ +1 (run-start mint) |
| API CPU ms per run (`stats.py:65`), `wf-sdk` burst p50/p95 | down after PR1b |
| Idle tx/s (`stats.py:101`) | +≈ 0.2 (certifier) |
| `services` profile: heartbeat tick p95, worker tx/s, API tx/s | tick p95 unchanged (mints are after commit, concurrent); API tx/s down (service-control polls no longer read the DB) |
| Statements per runtime request | 0 warm; 1 query in Postgres mode |

No class may regress beyond the harness's ±9 % noise.

---

## 10. Task breakdown

One implementer, in order; each ≤ ~300 lines with its tests; `./test.sh
quality api` per task.

**PR1a**
| # | Task |
|---|---|
| A1 | Migration part 1 + ORM: `authz_not_before`, `authz_protocol_state`, protocol columns, `authz_nbf_mark` |
| A2 | Migration part 2: triggers of §3.1 with `COMMENT` tags; `test_not_before_triggers.py` |
| A3 | `not_before.py`: `Subject`, `guard_authz_change`, `record_authz_event`, `finalize_stamps`, Redis scripts, keys in `core/cache/keys.py`; `test_finalize.py` |
| A4 | Certifier in `scheduler/control.py` leader services; protocol reporting in replica/worker heartbeats; certification; `test_certifier.py` |
| A5 | `load_only` tightening; `RuntimeAuthority.solution_active` / `.lease_expires_at`; `context.py:120` docstring |
| A6 | Claims schema, `principal_from_claims`, `token_subjects`, size; `test_token_claims.py` |
| A7 | Minter (§3.6), `issue_run_refresh_token`, reserved-claim refusal; `test_issue_access_token.py`, `test_minter_wait.py`, `test_token_parity.py` |
| A8 | `/auth/refresh` `run_refresh` branch; refresh tests |
| A9 | Guards: roles, assignments, users (+ profile move) |
| A10 | Guards: workflows, Solutions, agents, impersonation end (+ solutions router move) |
| A11 | Guards: run ends (+ executions/agent-runs router moves, `RETURNING`) |
| A12 | Oracle fixture, registry ↔ triggers, read-side capture, negative tests, one-minter AST; mutation matrix (§9.2) |

**PR1b**
| # | Task |
|---|---|
| B1 | Migration precondition (protocol 1); protocol 2 reporting |
| B2 | `check_not_before` (§3.5); `test_not_before_check.py`, `test_stamp_ordering.py` |
| B3 | Validation cutover: `authenticate_runtime_token`, middleware, `core/auth.py`, MCP `verify_token`, `verify_iat`; legacy bridge + counter; zero-SQL test |
| B4 | Workflow mint cutover; worker env; redaction; Redis scrub; SDK engine-local refresh; remove `mint_engine_token`; `tests/helpers/runtime_tokens.py` and fixture migration |
| B5 | Service mint cutover after commit; credential install split; remove `mint_service_token`; `test_service_claim_tokens.py` |
| B6 | MCP bridge runtime branch (async mint via `get_db_context()`) |
| B7 | README rule and docstrings (§6) |
| B8 | E2E (§9.5); benchmarks (§9.6) |

**PR1c** (follow-up issue, following release): C1 migration precondition
(protocol 2 certified ≥ 25 h) and bridge removal; bridge e2e test inverted.

---

## 11. Decisions

- **D1 Claims layout** (§2): `sub`/`credential_kind`/`service_id` keep their
  names so PR1a pods validate new tokens; `usr` + optional `act`; role ids and
  names once per role.
- **D2 `iat`**: `floor(T0)` of the database clock before facts are read;
  refused iff `iat < stamp`; no `nbf`; `verify_iat` off.
- **D3 Stamp time**: assigned after commit by the first finalizer that sees
  the committed change, from the database clock, rounded up to a whole second
  (§3.2). Replaces revision 1's "+2 s".
- **D4 Redis trust**: certifier-renewed warm lease, fenced resync, per-write
  pending guards, observer warm deletion (§3.3–§3.8).
- **D5 Fact source**: roles from the database, never `shared/role_cache`
  (it is invalidated before commit today). `load_runtime_principal` stays the
  in-process path and parity oracle.
- **D6 Agent runs**: no token in-process; the MCP bridge mints per REST call
  with `issue_access_token`; no refresh token.
- **D7 Refresh route**: `POST /auth/refresh`, dispatch on `type`; run refresh
  skips the per-IP limiter.
- **D8 Run refresh semantics**: reusable, not rotated, no Redis state; ends
  with the run (liveness check). Expiry: SC3.
- **D10 Old-format tokens**: bridged (Jack) per §8.3 until PR1c.
- **D11 Refresh-required signal**: 401 + `WWW-Authenticate: Bearer
  error="invalid_token", error_description="refresh_required"`; body unchanged.
- **D12 Completeness**: database triggers write every stamp row; a test-only
  deferred constraint proves every committed change was guarded; read-side
  capture and registry ↔ trigger checks; negative tests (§9.3).
- **D13 `sol_active`**: answers the run's own `?solution=` gate from claims.
- **D15 Facts without a spec subject**: spec subjects only; `Agent.solution_id`
  and `ServiceDefinition` changes stamp the affected live runs; the platform
  default workflow mode stamps every workflow with `permission_mode IS NULL`.
- **D16 Router writes** of carried facts move into services (six sites).
- **D17 Redaction** of run refresh and refreshed access tokens; nested
  service tokens scrubbed from the diagnostic context copy.
- **D18 Delivery**: three merge units with migration-enforced preconditions
  (§8), so every merged commit is safe on any mixed fleet.

---

## 12. Spec conflicts for Jack

- **SC1 — Service token lifetime (D9).** Spec §2: "Access tokens live 60–90
  minutes, picked at random per token"; §6: services "keep receiving new tokens
  through `/api/sdk/runtime/service-control`". Conflict: a 60–90 min token lets
  a fenced service child (lease lost) keep API access well past its lease;
  today the API cuts it at lease expiry. Recommended: service access tokens
  expire at the committed lease expiry (≈ 60 s) and are re-minted after every
  heartbeat commit. Impact: preserves today's fencing; costs one mint per
  service per 10 s in the worker parent, replacing the API's per-second
  service-control authentication reads.
- **SC2 — Impersonation end (D14).** Spec §3/§5: tokens carry "session ID and
  impersonator", subjects include "impersonation session", and §7 "ending a
  session now also stamps it". Revision 1 omitted it for runs. Recommended:
  follow the spec in PR1 — run tokens include the impersonation subject;
  impersonation end records a stamp (§3.1) before its Redis record is deleted.
  Impact: runs started under an ended impersonation refresh once and continue
  (runtime authority never depended on impersonation liveness), so users see
  no change; it also prepares PR2's browser markers.
- **SC3 — Workflow refresh expiry (D8).** Spec §6: the run refresh token is
  "valid for the run's lifetime". Revision 1 capped it at timeout + 300 s
  (24 h + 5 min for no-timeout workflows), which ends refresh for longer live
  runs. Recommended: no `exp` on run refresh tokens; validity is run liveness.
  Impact: long no-timeout runs can refresh for as long as they live (today
  their engine token dies at 24 h + 5 min); a stolen refresh token works while
  the run is live — the same exposure as the run's own process today.
- **SC4 — Where stamps are written.** Spec §5: "in the service or repository
  write for each fact, next to the existing `role_cache.invalidate_user` /
  `invalidate_role` calls … Not in routers." Recommended: Postgres triggers
  write the stamp rows; the service/repository functions write the
  guards (Redis pending markers) at the spec's location. Impact: stamps become
  complete for every code path, cascade, raw SQL and older pod version
  (finding 6 and the mixed-fleet hazard); no user-visible change.
- **SC5 — Partial Redis partition.** Spec §5: "A Redis outage costs one cheap
  query per request, never an unchecked token." Residual: if Redis is
  unreachable from a writer (or a finalizer) but reachable from validators, or
  a guard call is missing (a test-caught bug), a validator can accept a stale
  token for up to the certifier interval (5 s). Recommended: accept the 5 s
  bound. Impact: affects only that split-brain case; closing it would need a
  Postgres read per request.
- **SC6 — Delivery.** Spec §10: "One design, two PRs". Recommended: PR1 lands
  as two merge units plus the bridge removal (§8.1), with migration-enforced
  order. Impact: one extra deploy cycle; required for safe rolling and
  version-skipping upgrades and for Jack's bridge.

---

## 13. Open questions

- **Q1 (for PR2, raised now) — browser cookie limit.** A people token for
  production's largest user is ≈ 4,001 B; its `Set-Cookie` ≈ 4,068 B, at the
  ~4,096 B per-cookie limit browsers enforce, well below the spec's 6 KB
  warning. A slightly larger role set would be silently dropped (sign-in loop).
  Options:
  1. **Compress the `usr` block** (zlib + base64url in one claim): measured
     largest token 4,001 → 2,397 B. Departs from spec §3 "plain JSON"; keeps a
     single standard cookie.
  2. **Split the access token across two cookies** and rejoin server-side:
     keeps plain JSON; more cookie plumbing; the `Cookie` header stays under
     8 KB until tokens reach ~6 KB.
  3. **Keep browser access tokens identity-only** and read grants per browser
     request: no size risk, but browsers lose PR2's database savings.
  Recommendation: option 1, applied to all tokens so there is one encoding,
  with the size warning measured on the encoded token.

Q2 (old-format tokens) is resolved by Jack's bridge decision (§8.3).
