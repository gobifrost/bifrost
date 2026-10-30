# Token PR1 — Runs and Shared Pieces: Implementation Plan (revision 6)

Spec: `docs/superpowers/specs/2026-10-27-access-token-claims-design.md`
(amended 2026-10-30 to match). Branch `token/pr1-runs`, based on `origin/main`
`ad623e250`; line numbers are at that commit.

PR1 delivers spec §10 item 1: `issue_access_token`, the claims schema, the
authority version, run refresh tokens and the README rule. Every runtime kind
switches to claims; people stay on today's path until PR2. Decisions are
`D…` (§11).

### History

| Rev | Commit | Change |
|---|---|---|
| 1 | `9ed3edfc9` | first plan |
| 2 | `f2e0a8222` | Astra review 1 |
| 3 | `5a3f0ead7` | Postgres-only not-before timestamps |
| 4 | `1e5517111` | one global random authority epoch; run liveness from the run row; REPEATABLE READ minter (Jack, on Fable's review and Astra review 2) |
| 5 | `ea0c15d60` | Astra review 3, which confirmed the epoch proof, the liveness predicates and D14 |
| 6 | this | Astra review 4 (1 P1, 3 P2; earlier findings resolved for forward operation) |

### Astra review 4 — disposition

| # | Finding | Resolution | Where |
|---|---|---|---|
| 1 | After a PR1b→PR1a rollback, a PR1b child can't refresh on expiry | The expiry challenge and its side-effect-free dependency test ship in **PR1a**; only the epoch challenge waits for PR1b. A contract test runs a PR1b client against PR1a's expiry response | §5.3, §8.2, §9.1 |
| 2 | The combined trigger with `OLD`/`NEW` in `WHEN` can't be created | Event-specific deferred constraint triggers (`_ins`, `_upd`, `_del`) sharing `authz_epoch_bump_once()`; per-table events listed; run tables are UPDATE-only for carried columns; §9.2 checks the exact trigger set | §3.2, §9.2 |
| 3 | Compose migration probe can disturb dependencies; `latest` isn't evidence | Pinned release tag or digest; dependency health checked first; `run --rm --no-deps init`; app-only `up --no-deps`; running image IDs verified against the release digest; claim corrected | §8.4 |
| 4 | PR1c needs an ongoing launcher admission check | The negotiated credential protocol is persisted with the ownership generation; PR1c refuses protocol-1 launchers at hello, before any assignment, and routing excludes them; reconnect test | §5.4, §8.2, §9.1 |
| — | Safe cuts | Dropped the 3 s snapshot case (event-ordered barriers cover it); full `./test.sh quality api` at five checkpoints instead of after every task | §9.1, §10 |

### Astra review 3 — disposition

| # | Finding | Resolution | Where |
|---|---|---|---|
| 1 | "Zero users" isn't a fresh install | `alembic/env.py` records the revision the upgrade run **started** from; `None` means an empty schema migrated in this same run. A test upgrades a truly empty database to head | §8.2, §9.1 |
| 2 | Refresh misses `engine_stream` and the MCP bridge | One challenge-driven refresh layer in the HTTPX transport covers every runtime transport, streams included; streams refresh only before any bytes are exposed. The MCP bridge gets the same layer, re-minting in-process. Every transport is listed in §5.3 | §5.3 |
| 3 | D9 interrupts services; "restarts once" unsupported | The legacy service rotation and delivery path is kept until PR1c. Remote launchers report a credential protocol; new-format credentials go only to compatible launchers, legacy credentials to the rest. No restart is relied on | §5.4 |
| 4 | Rollout and drain evidence; cutoff anchor | Legacy tokens only work while their run is live, so the run's liveness — not a clock — bounds them. Run rows record their credential format. PR1c's fail-closed precondition requires no live legacy-format run and no connected legacy launcher, plus a documented operator step. Rollback, restore and a concrete Compose procedure are specified; the trade-off is stated | §8 |
| 5 | D20 budget and replay contract | One budget of 3 per public SDK call, carried across the outer transient-retry loops. Replay happens **only** on the validator's challenge, which an auth dependency raises before any handler effect. Tests prove one global budget, zero effects on challenged attempts and exactly one effect after a non-idempotent replay. Cost claim and spec corrected | §3.7, §5.3 |
| 6 | Collect/re-arm layer is unnecessary | Deferred flush with a once-per-transaction flag only; unrestricted `UPDATE` triggers with value-change `WHEN`; TRUNCATE kept. Tests for a writer committing between the minter's fact and epoch reads, and for REPEATABLE READ being set before the first statement | §3.2–§3.4, §9.1 |

---

## 1. Scope boundary

### 1.1 Mint sites

| Site | PR1 | PR2 / unchanged |
|---|---|---|
| `core/security.py:458` `mint_engine_token` (caller `jobs/consumers/workflow_execution.py:933-937`) | PR1b: new-format access + run refresh token for compatible routes; the legacy minter, renamed `tokens._legacy_runtime_token`, only for legacy launchers (§5.4); deleted in PR1c | — |
| `core/security.py:426` `mint_service_token` (callers `services/service_claim.py:463` `_rotate_token`, `:691` `_launch_claimed`) | PR1b: new-format at launch on compatible routes; legacy launch and rotation kept for legacy-format attempts until PR1c | — |
| `services/mcp_server/tools/_http_bridge.py:75-86` runtime branch | PR1b: `issue_access_token` plus the refresh transport (D6) | — |
| `_http_bridge.py:88-104` human branch; `routers/auth.py:684,836,1126,1892`; `routers/passkeys.py:207`; `routers/oauth_sso.py:423`; `routers/mfa.py:254`; `services/mcp_server/auth.py:509,564` | — | PR2 |
| `core/security.py:245` `create_embed_access_token`; `shared/browser_impersonation.py:90` marker | — | unchanged |
| `core/security.py:179` `create_mfa_token`; `shared/solution_dev.py:121`; `services/oauth_state_core.py:79` | — | unchanged (not access tokens) |
| `core/security.py:57` `create_access_token` | PR1b: refuses reserved runtime claims; the legacy minter signs through `tokens._encode_legacy` until PR1c | PR2 |
| `core/security.py:146` `decode_token` | PR1a: `options={"verify_iat": False}` | — |

### 1.2 Validation and authority sites

| Site | PR1 change |
|---|---|
| `core/app_wiring.py:221` middleware (runtime branch `:242-246`) | PR1b: parses `ver:1` claims without a DB session (attribution only); legacy tokens keep today's path (bridge, §8.3) |
| `core/auth.py:135` `_get_current_user_optional` (`:184-192`) | PR1b: `authenticate_runtime_token(db, claims)` runs the validator (§3.5) in the request's own DB session |
| `core/auth.py:343` `get_current_user` | PR1a: the challenge (D11) for an expired, signature-valid access token (runtime or human). PR1b: also for epoch mismatch |
| `core/auth.py:401` `get_execution_context` | PR1b: own-Solution `?solution=` from `run.sol_active` (`:448-465`); role read `:430-433` skipped for runtime principals |
| `services/mcp_server/auth.py:661` `verify_token` (`:689-745`) | PR1b: same validator in its existing short session; tool-filter roles from `usr` |
| `routers/auth.py:999` `/auth/refresh` | PR1a: `run_refresh` branch |
| `core/auth.py:39`, `:591`; `runtime_auth.py:34` `load_runtime_principal` and in-process callers (`jobs/consumers/agent_run.py:274,376`, `services/execution/autonomous_agent_executor.py:1367`, `services/authorization/callers.py:58`, `services/mcp_server/authorization.py:22`) | unchanged (D6) |
| `services/authorization/enforce.py:118` human branch; `shared/browser_impersonation.py:130` | PR2 |

Not in PR1: policy evaluation's data reads, logout/revoke-all, people
lifetimes and cookies, MCP OAuth refresh JTIs, the human `roles` claim shape.

### 1.3 Gaps from `token-spec-gaps-20261029.md`

| Gap | Disposition |
|---|---|
| `create_embed_token` name | allowlisted by its real name |
| Agent runs mint no JWT | D6 |
| Run refresh endpoint; worker env (`worker.py:51-52`) | §5 |
| Service lifetime | D9 |
| `iat`/`jti`; 1 s ambiguity | moot: revocation uses the epoch |
| Warm marker | moot |
| Cookie limit, cookie max_age (`auth.py:126`), MCP `expires_in` (`mcp_server/auth.py:518,571`), MCP refresh JTI (`:510,:565`) | PR2; D19 |
| Browser App/Solution gates (`core/auth.py:459,490,493`) | runtime own-Solution gate PR1b; rest PR2 |
| `build_authorization_context` docstring (`context.py:120`) | fixed in A4 |
| `roles` claim shape | runtime claims carry ids + names; human shape PR2 |
| Completeness mechanism | §9.2 |

---

## 2. Claims schema (`ver: 1`)

Pydantic models in `api/src/services/authorization/tokens.py`
(`frozen=True, extra="forbid"`; internal, not an HTTP contract).

```text
iss, aud, type="access", ver=1, iat:int, exp:int, jti:str
av: str                       authority epoch at mint (decimal string of a signed 64-bit value)
sub: str                      runtime: credential id (name kept, D1)
credential_kind               "workflow_execution" | "service_attempt" | "agent_run"
service_id: str | absent      service_attempt only
usr: str                      base64url(zlib(JSON)) of the run user (D19):
     { id, email, name, kind, org, ext, prov, active,
       base: {id, perm:[str]}, roles: [ {id, name, perm:[str], bnd:[[kind, org|null]]} ] }
act: { id, email, name, org, ext, prov, roles:[{id, name}] } | absent   acting user ≠ run user
run: { org, wf, sol, sol_active, root, by, powers: {mode, grants:[str]} | null }
imp: { by, sid } | absent
```

`principal_from_claims` reproduces `runtime_auth._principal_from_authority`
(`runtime_auth.py:66-133`):
- `AuthorizationContext` from `usr`; role grants are the roles with a
  non-empty `bnd`.
- `RunUser(usr.id, ctx, usr.kind, usr.active)`.
- The actor (`act` or `usr`) supplies identity and role ids/names, and
  `is_superuser = PLATFORM_ADMIN_ROLE_ID ∈ actor role ids`.
- `organization_id = run.org`.
- `RuntimeAuthority` from the run claims, plus `solution_active` (D13).

`iat` is set from the minting host's clock for JWT hygiene; nothing compares
it.

**Size** (synthetic payloads shaped like production): with plain JSON,
production's largest user is 4,444 B; adding a distinct 12-role actor,
impersonation and 10 grants gives 6,308 B. Compressing `usr` cuts the largest
people token from 4,001 to 2,397 B (measured), so run tokens shrink by about
1.6 KB as well. Minting warns when the **encoded** token exceeds 6,144 B and
never refuses.

---

## 3. Authority version

### 3.1 Basis: production write rate

Jack measured production read-only over 30 days. Authority-relevant writes had
an upper bound of about **600–700**:

| Write | Count |
|---|---:|
| `role.user_assigned` | 211 |
| `user.create` | 163 |
| `workflow.update` (a subset is relevant) | 135 |
| `user.delete` | 83 |
| `solution_workflow_permission_requests` | 37 |
| agents updated | 31 |
| solutions | 12 |
| roles | 7 |
| orgs | 5 |
| default mode / grants | 0 |

That is about 20 a day, bursty around user provisioning, for 1,713 users,
60 roles and 96 orgs. **One global epoch** suits that rate.

**Re-measure trigger:** move to per-subject versions (rows keyed by subject in
the same table; nothing is built for it now) if **either** holds:
- bumps exceed **30 per hour** in any hour on three days of a week; or
- `authz.refresh_required` responses exceed **5 %** of authenticated runtime
  requests over a day.

### 3.2 Table, bump function and triggers (PR1a migration `20261030_authz_versions.py`)

```text
authz_versions(subject_kind varchar(32), subject_id uuid, version bigint NOT NULL,
               PRIMARY KEY (subject_kind, subject_id))
seed: ('global', '00000000-0000-0000-0000-000000000000', <random>)
authz_protocol_state(protocol int PRIMARY KEY, first_started_at timestamptz NOT NULL)
executions.credential_version smallint NULL; service_attempts.credential_version smallint NULL
```

- **`authz_epoch_bump_once()`**: if the transaction-local setting
  `bifrost.authz_epoch_bumped` is not `'1'`, set the global row's `version`
  to a fresh random signed 64-bit value from `gen_random_uuid()`, then set the
  setting to `'1'` (`set_config(…, true)`).
  - Random rather than `+1`: values are compared for equality only (§3.4).
  - Postgres 16 is in use (`docker-compose.yml:31`), so `gen_random_uuid()` is
    core.
- **Protection**: `BEFORE DELETE` and `BEFORE TRUNCATE` triggers on
  `authz_versions` raise.
- **Per carried-fact table**, event-specific triggers, all calling
  `authz_epoch_bump_once()`. PostgreSQL forbids `OLD` in a `WHEN` when INSERT
  is one of the events, and `NEW` when DELETE is, so each event gets its own
  trigger:
  - `authz_epoch_ins` — `CONSTRAINT TRIGGER AFTER INSERT … DEFERRABLE
    INITIALLY DEFERRED FOR EACH ROW` (`WHEN` may reference `NEW` only).
  - `authz_epoch_upd` — same, `AFTER UPDATE`, unrestricted (no `UPDATE OF`,
    which can miss changes made by `BEFORE` triggers), with
    `WHEN (OLD.<col> IS DISTINCT FROM NEW.<col> OR …)`.
  - `authz_epoch_del` — same, `AFTER DELETE` (`WHEN` may reference `OLD`
    only).
  - `authz_epoch_truncate` — `AFTER TRUNCATE FOR EACH STATEMENT`; it bumps
    immediately, because constraint triggers can't be statement-level.
- **Events per table.** INSERT fires only where a new row can change what an
  existing token means. A new user, role, workflow, Solution, agent or run
  carries no existing token, so none of those fire on INSERT. That keeps the
  bump rate at the measured ~20/day, where blanket run INSERT triggers would
  invalidate every token on each run start.

| Table | INSERT | UPDATE (`WHEN` value changed) | DELETE | Measured rate (30 d) |
|---|---|---|---|---|
| `users` | — | email, name, organization_id, base_role_id, is_external, identity_kind, is_active | yes | `user.delete` 83; updates within `workflow`/role counts |
| `organizations` | — | is_provider | yes | orgs 5 |
| `user_roles`, `user_role_boundaries` | yes | any column | yes (incl. FK cascades) | `role.user_assigned` 211 (assignments on `user.create` 163 included) |
| `role_permissions` | yes | any column | yes | roles 7 |
| `roles` | — | name | yes | roles 7 |
| `workflows` | — | solution_id, permission_mode | yes | subset of `workflow.update` 135 (deletes included) |
| `workflow_permission_grants` | yes | any column | yes | 0 |
| `solution_workflow_permission_requests` | yes | any column | yes | 37 |
| `system_configs` | `WHEN` NEW is the default-mode row | `WHEN` OLD or NEW is the default-mode row and the row changed | `WHEN` OLD is the default-mode row | 0 |
| `solutions` | — | status | yes | solutions 12 |
| `agents` | — | solution_id | yes | agents updated 31 |
| `service_definitions` | — | workflow_id, organization_id | — (deleting a definition removes its attempts; liveness refuses) | low |
| `executions`, `agent_runs`, `service_attempts` | — | only the carried creation-time columns (run user, actor, workflow, org, lineage, impersonation, `service_id`) — never expected | — (a deleted live run fails the liveness read) | 0 |

Every table also has the TRUNCATE trigger. Status, state and lease are read
live (§3.5), never versioned. Workflow DELETE frequency during file sync isn't
broken out in the measurement; the re-measure trigger (§3.1) watches it.

No application write site changes.

**Maintenance rule** (`docs/runbooks/authz-epoch.md`): anything that disables
triggers (`session_replication_role = replica`, `ALTER TABLE … DISABLE
TRIGGER`, bulk restores) must finish with `SELECT authz_epoch_bump_once();` in
a new transaction. A test scans `api/alembic/versions/` for those statements
and requires the bump in the same migration.

### 3.3 Correctness of the bump (finding 6)

The proof needs only this: **every committed transaction that changes a
carried fact also commits a new epoch value.** With one global row, a bump
anywhere in the transaction is enough, because every change in that
transaction, earlier or later, commits atomically with it.

- **Normal path.** Any relevant row change queues a deferred event that fires
  before COMMIT completes, so a transaction with a change always runs
  `authz_epoch_bump_once()`. The flag makes the first call bump and later
  calls return, so there is one bump per transaction.
- **`SET CONSTRAINTS ALL IMMEDIATE`, or firing during COMMIT.** The bump may
  happen early. Later changes in the same transaction see the flag and don't
  bump again, which is correct: they commit with the epoch the earlier bump
  set.
- **TRUNCATE.** The statement trigger bumps immediately through the same flag.
- **Savepoints.** A savepoint that rolls back undoes its bump `UPDATE` and its
  `set_config` together.
  - If outer changes still need a bump, Postgres re-arms the outer deferred
    events that the aborted savepoint fired (Astra review 3 confirmed this),
    and they bump again at commit.
  - If the aborted savepoint held the only changes, it also discards their
    events, and no bump is needed.
- **Bump count.** At most one per transaction, plus one per savepoint whose
  bump was rolled back.

### 3.4 Minter and validator correctness

**Minter.** `issue_access_token(subject)` opens its own session and sets
`isolation_level="REPEATABLE READ"` and read-only on the connection **before
the first statement** (asyncpg starts the transaction with that level; this
works through PgBouncer transaction pooling). It reads every fact and the
epoch from that snapshot σ and embeds the epoch as `av`. Read-only
REPEATABLE READ transactions take no locks, never wait on writers and get no
serialization failures, so there are no retries.

**Proof.** For any committed change C:
- If C committed before σ, C's facts and C's epoch (or a later one) are in σ,
  so the token is fresh.
- If C committed after σ, neither is in σ. C's commit installs a new random
  epoch, so the validator — reading the committed row on the primary in READ
  COMMITTED — sees `av ≠ epoch` from C's commit on and refuses.
- Order inside σ doesn't matter. A writer committing between the minter's
  fact reads and its epoch read is invisible to both.

**Failover and restore.** Equality only means a lossy failover or backup
restore that lands on a token's epoch also lands on that token's facts, so
accepting it matches the database. A superseded value can't come back by
counting.

**Primary only.** Validators and the minter read the primary: today there is
one URL (`docker-compose.yml:90`). Any future replica routing must exclude
these queries; the module docstring states it.

### 3.5 Validator (PR1b)

One statement in the request's own transaction. Example for workflow
executions:

```sql
SELECT v.version AS epoch,
       e.status IN ('Pending', 'Running', 'Cancelling') AS live
  FROM authz_versions v
  LEFT JOIN executions e ON e.id = :credential_id
 WHERE v.subject_kind = 'global'
   AND v.subject_id = '00000000-0000-0000-0000-000000000000'
```

The other kinds join a different row:
- `agent_run`: `agent_runs.status IN ('running','cancelling')`.
- `service_attempt`: `service_attempts.state IN ('starting','running','stopping')
  AND lease_expires_at > :now AND service_id = :service_id`, where `:now` is the
  validating host's clock, as `runtime.py:181` compares today (Astra review 3
  confirmed the predicates).

| Result | Response |
|---|---|
| No row (epoch missing) | 401 and an error log; never a default |
| `epoch ≠ av` | 401 with the challenge (D11) |
| `live IS NOT TRUE` | 401 "run is no longer live", no challenge (refresh would fail too) |
| otherwise | accept |

### 3.6 Impersonation end (D14, confirmed by Astra review 3)

No version change. No run token depends on impersonation session liveness
(`runtime.py:150-163` copies only immutable ids). Browser markers keep their
per-request session check (`shared/browser_impersonation.py:130`).

### 3.7 Bursts (finding 5)

During a burst of K bumps, a run's call is refused at most once per bump that
lands during it. Each refusal costs one refresh: a REPEATABLE READ mint of
~12–15 statements, comparable to today's ~10-statement per-request runtime
authentication (`docs/benchmarks/2026-10-26-runtime-auth-efficiency.md`).

Each public SDK call has a **budget of 3 challenge-driven refreshes** (D20).
Worst case: a call costs today's authentication plus up to 3 mints and
3 extra round trips. If more than 3 bumps land within one call's attempts, the
call **fails** with the 401. With bumps 1 s apart, the chance of exhausting
the budget is about 10⁻⁴ per call. Sustained bursts faster than that also trip
the re-measure trigger (§3.1).

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
def parse_runtime_claims(payload: dict[str, Any]) -> AccessTokenClaims | None
async def authenticate_runtime_token(db: AsyncSession, claims: AccessTokenClaims) -> RuntimeAuthResult
```

### 4.1 Facts (one REPEATABLE READ snapshot)

1. `load_runtime_authority` (`runtime.py:75`) with today's liveness rules
   (`RuntimeIdentityError` becomes `TokenSubjectUnavailable`). A4 tightens its
   loads to `load_only` (`:166`, `:207`, the service-path `Workflow`,
   `list_workflow_grants`, `workflow_permission_requests`) and adds
   `RuntimeAuthority.solution_active` (`:59`).
2. Roles, names, permissions and boundaries come from the database, never
   `shared/role_cache` (D5).
3. Run user and actor identity, home orgs and `is_provider`.
4. The global epoch row (missing → `AuthorityStateMissing`, a 500 with an
   error log).

### 4.2 Lifetimes

| Kind | Access token | Run refresh token |
|---|---|---|
| `workflow_execution`, `service_attempt` (new format) | `3600 + secrets.randbelow(1801)` s | no `exp`; valid while the run is live (SC3) |
| `agent_run` (MCP bridge) | `3600 + secrets.randbelow(1801)` s | none (D6) |

The run refresh token is `{iss, aud, type:"run_refresh", ver:1, iat, jti,
sub, credential_kind, service_id?}`. It carries identity only, and minting it
reads no database.

---

## 5. Run refresh

### 5.1 Endpoint (PR1a)

`POST /auth/refresh` (`routers/auth.py:999`) keeps `TokenRefresh` (`:296`) and
`Token` (`:252`), so the CLI fingerprint is unchanged. It dispatches on `type`:
- `refresh` → today's human branch, untouched.
- `run_refresh` → requires `ver == 1` and calls `issue_access_token`.
  - `TokenSubjectUnavailable` → 401.
  - Success → `Token(access_token=new, refresh_token=<same>)`, no cookies.
  - No per-IP limiter.
  - Reusable, not rotated (D8).

### 5.2 Workers (PR1b)

- `worker.py:37-52` puts `engine_refresh_token` into `BIFROST_REFRESH_TOKEN`.
- `engine_refresh_token` joins `_CONTEXT_REDIS_EXCLUDED_KEYS`
  (`process_pool.py:82`) and the output redaction (`worker.py:297`,
  `engine.py:324`).
- Refreshed access tokens are redacted through `install_access_token`
  (`bifrost/client.py:917`).

### 5.3 Challenge-driven refresh on every runtime transport (findings 2 and 5)

**Challenge (D11).** `get_current_user` returns 401 with `WWW-Authenticate:
Bearer error="invalid_token", error_description="refresh_required"` only
when a refresh fixes the problem. The auth dependency raises it before the
route handler runs.
- **From PR1a:** a signature-valid access token whose `exp` has passed,
  runtime or human (`decode_token` distinguishes `ExpiredSignatureError`).
  Shipping this in PR1a means a PR1b child still refreshes on expiry after the
  supported PR1b→PR1a rollback, because PR1a already redeems `run_refresh`
  (Astra review 4 #1).
- **From PR1b:** also an epoch mismatch.

**Side-effect-free precondition (PR1a).** A test walks the FastAPI dependency
graph (`route.dependant`) of every route that accepts runtime tokens. It
asserts every dependency resolved before the user dependency is in an
allowlist of side-effect-free dependencies (`get_db`, settings, path/body
parsing). So a challenged attempt has had **no handler or dependency effect**.

**SDK refresh layer** (`bifrost/client.py`). A `RefreshingTransport` wraps
every HTTPX transport the SDK creates: network async and sync, and the engine
Unix socket async and sync (`:626, :670, :707, :733`).
- On a 401 carrying the challenge it closes that response unread, calls
  `refresh_connection_access_token` (sync variant for sync clients), rewrites
  the request's `Authorization` header and re-sends.
- The budget is an object in `request.extensions["bifrost_refresh_budget"]`
  (3 per call). The public methods (`get/post/put/patch/delete`, `request`,
  `engine_request(_sync)`, `stream`, `engine_stream`, `get_sync`, …) create it
  once and pass the same `extensions` through `_send_with_retry` and
  `_send_sync_with_retry` (`:118, :154`). Transient retries therefore share it.
- **No other 401 replays**, including AI-provider authentication errors mapped
  to 401 after the provider ran (`shared/sdk_ai.py:218`).
- The old "any 401 → refresh once" code in `_request_with_refresh(_sync)` and
  `request()` (`:991, :1028, :1104`) is removed.
- Callers using the CLI's persistent credentials still refresh proactively
  before expiry (`client.py:806`).

**Streams.** `stream()` and `engine_stream()` (`:1112, :1133`) open the
request through the same transport. The challenge check happens on the status
line, **before the response is returned to the caller**, so no stream bytes
have been exposed. Once a response is yielded, it is never replayed.

| Runtime transport | Covered by |
|---|---|
| SDK unary async/sync, network and engine socket (`engine_request`, `get_sync`, …, `request()`) | `RefreshingTransport` with a per-call budget |
| SDK streams (`stream`, `engine_stream`, e.g. `ai.stream()`) | the same transport, before bytes are exposed |
| Service supervisor `service-control` (`engine.py:1066`) | `engine_request` |
| Runtime log/IO helpers (`bifrost/_runtime_io.py`) | `engine_request_sync` |
| Cold module fetch (`core/module_cache_sync.py:112,246`, its own `httpx.Client`) | wraps its client in `RefreshingTransport` |
| MCP HTTP bridge, runtime contexts (`_http_bridge.py:131-158`) | `BridgeRefreshingTransport` around the in-process ASGI or override transport: on the challenge it re-mints with `issue_access_token` (in-process) and re-sends, budget 3 per request. This covers a bridge context whose own earlier request changed authority |
| In-process agent runtime, worker-socket server side | no token |

### 5.4 Services and launcher compatibility (finding 3)

- **Credential format on the run row.** Every minting path records
  `credential_version = 2` on the execution or service attempt row when it
  issues new-format credentials. NULL means legacy: old code doesn't know the
  column. Agent runs need none, because their tokens never leave the process
  (D6).
- **Launcher compatibility.** Remote launchers add
  `"credential_protocol": 2` to their hello (`worker/remote/launcher.py:277-299`).
  A missing field means 1.
  - The negotiated value is **persisted with the ownership generation**: a
    new `execution_workers.remote_credential_protocol` column is written in
    the same `UPDATE` that increments `remote_session_generation`
    (`worker/remote/ownership.py:70`).
  - Routing reads the value under the same generation fence
    (`worker/remote/fencing.py:75`), so a reconnect with a different protocol
    can't be confused with the session that was routed.
  - Routing picks the route **before** minting: the pool exposes the chosen
    route's credential protocol to the minting callback, in
    `workflow_execution.py:933` and `service_claim.py:691`.
  - Local workers are always protocol 2 in PR1b images.
  - Protocol 2 routes get new-format access + run refresh tokens.
  - Protocol 1 routes get **legacy** tokens, exactly today's format, from
    `tokens._legacy_runtime_token`.
- **Legacy service rotation kept.** For attempts with `credential_version IS
  NULL`, `_rotate_token` (`service_claim.py:453`) and the `service-control`
  token delivery (`shared/runtime_io.py:46-70`) continue exactly as today
  until PR1c. New-format attempts are not rotated; their children refresh like
  any run, and their lease is enforced on every request by the liveness read.
- **Nothing relies on a restart.** In-flight legacy work continues on legacy
  credentials through the bridge window; new work never lands on an
  incompatible launcher with new-format credentials.
- **Install split.**
  `bifrost/_service_runtime.install_service_credentials(access, refresh)`
  covers new-format starts (`worker.py:419-421`, `engine.py:950-952`). The
  legacy single-token install stays for legacy children.
- **Diagnostics.** Nested service tokens are scrubbed from the diagnostic
  Redis context copy (`process_pool.py:1159`).

---

## 6. Repository authority rule (PR1b)

Rule: claims in an access token minted by `issue_access_token` are authority
while the token is unexpired, its authority version is current and its run is
live.

1. `parse_runtime_claims` + `authenticate_runtime_token` are the only
   claims→principal path (signature, `type`, `ver == 1`, strict schema,
   validator).
2. `principal_from_claims` is imported only by `tokens.py` (AST test).
3. `create_access_token` refuses the reserved claims `ver`, `av`,
   `credential_kind`, `usr`, `act`, `run`, `imp` (PR1b). The legacy minter
   signs through `tokens._encode_legacy`, deleted in PR1c.
4. `api/src/repositories/README.md`: the "Authority model" paragraph, rule 1
   and the `RuntimeAuthority` paragraph change to the rule; legacy tokens are
   bridged until PR1c.
5. `test_runtime_auth.py::test_runtime_principal_uses_database_authority_not_spoofed_token_claims`
   becomes three tests: legacy payload via the bridge; `ver:1` claims are
   authority with one validator statement; tampered token fails. Docstrings:
   `runtime_auth.py:1,37`, `runtime.py:1-5`, `core/principal.py:22-24,63-82`,
   `mcp_server/server.py:143-145`.

---

## 7. Application write paths

None change for authority (§3.2). PR1 touches application code only at the
mint and validation sites (§1), the SDK and bridge transports (§5.3), launcher
compatibility and service credentials (§5.4), and the `credential_version`
writes.

---

## 8. Rollout (finding 4)

### 8.1 Security holds in every mix; readiness is about availability

| Combination | Why it is secure |
|---|---|
| Any pod version writes authority | Triggers in the database from PR1a's migration bump the epoch |
| New-format token on a PR1a pod | Validated through `load_runtime_principal` (today's database path; names kept, D1); `verify_iat` off |
| Legacy token on a PR1b pod | Bridge: today's database path, including the live-run check |
| Older code starting late (rollback, restore, scale-from-zero) | Validates from the database; writes still bump |

Availability is affected in two cases:
- a run refresh reaching an API without `run_refresh` (pre-PR1a) fails closed;
- after PR1c, a legacy-format run that is still live is refused.

### 8.2 Merge units, evidence and preconditions

Main squash-merges each PR, so each unit is one commit.

| Unit | Contents | Behaviour |
|---|---|---|
| **PR1a** | migration (versions table, seed, protection, `authz_epoch_bump_once`, event triggers, `authz_protocol_state`, `credential_version` and `remote_credential_protocol` columns); `env.py` starting-revision capture; claims schema; minter; `run_refresh`; `verify_iat` off; **expiry challenge and dependency-graph test**; protocol-1 start record; Compose image-tag variable; runbook; completeness tests | unchanged, apart from the expiry 401 gaining its challenge header |
| **PR1b** | precondition; minters switch per route protocol, plus the legacy minter for protocol-1 routes and legacy rotation; reserved-claim refusal; validators and the epoch challenge; bridge; SDK and MCP refresh transports; launcher hello field and persisted protocol; README | claims are authority |
| **PR1c** (file the issue when PR1b merges; next release) | fail-closed precondition; **launcher admission check**; delete the bridge, the legacy minter and rotation, the legacy credential install and the three `ServiceControl` token fields (`shared/models.py:48-57`); refresh the contract fingerprint | legacy tokens refused; protocol-1 launchers refused |

**Fresh-install detection.** In `alembic/env.py`'s `do_run_migrations`
(`:93`), before `context.run_migrations()`, read
`MigrationContext.configure(connection).get_current_revision()` and store it as
`config.attributes["bifrost_starting_revision"]`. `None` means this run began
on an empty schema: no tokens, runs or pods can exist. This works for both the
init container's subprocess `alembic upgrade head`
(`scripts/init_container.py:73`) and test harnesses that call
`command.upgrade`.

**Evidence.**
- PR1a and later API processes run `INSERT INTO authz_protocol_state VALUES
  (<protocol>, now()) ON CONFLICT DO NOTHING` at startup. The row is durable.
- On a fresh install the migration inserts its own protocol row.

**PR1b precondition (fail-closed).** PR1b's migration raises "Upgrade to the
PR1a release and let it finish rolling out first" unless the start was fresh
or a protocol-1 row exists. This blocks skip-upgrades and restores of
pre-PR1a backups.

The operator step (§8.4) confirms PR1a's rollout finished. If it is skipped,
the only effect is that run refreshes reaching a straggling pre-PR1a API fail
closed. **Trade-off:** fully automatic rollout proof would need fleet-wide
version fencing that older code can't take part in. A durable check plus an
operator step is proportionate, given that violations cost availability, not
security.

**PR1c precondition (fail-closed).** PR1c's migration raises unless the start
was fresh **or** all of these hold:
- no live execution or service attempt has `credential_version IS NULL`; and
- no `execution_workers` row with a live remote owner lease has
  `remote_credential_protocol` NULL or < 2.

**PR1c launcher admission (ongoing).** Offline protocol-1 launchers may
reconnect after the migration. So PR1c's hello handling refuses a launcher
reporting protocol < 2 (or none) **before** ownership is granted or any work
is assigned, closing the socket with "upgrade this launcher". Routing also
excludes rows whose persisted protocol is < 2. This is a local admission
check, not fleet fencing.

The operator step confirms the PR1b rollout finished. **Trade-off:** a
straggling pre-PR1b dispatcher that starts a legacy run after the check makes
that run fail closed.

**The anchor is liveness, not a clock.** A legacy token authorizes only while
its run is live: the bridge is today's path, which checks liveness on every
request. So the precondition "no live legacy-format run" *is* the
legacy-minting-ended fact. Once it holds, every unexpired legacy token belongs
to a finished run and is already refused, whatever lifetime it was minted with
(including the unbounded `access_token_expire_minutes`, `config.py:254`). A
time cutoff isn't needed: legacy workflow runs end at their timeout; legacy
services end when stopped, restarted or moved; and the check reports any that
remain.

**Rollback.**
- The init container skips migrations when the database is ahead
  (`scripts/init_container.py:35-56`).
- PR1b → PR1a: PR1a validates every token from the database and redeems
  `run_refresh`; fine.
- Rollback below PR1a: refreshes fail closed. Unsupported; the runbook says so.
- After PR1c, a rollback mints legacy runs again. Rolling forward makes those
  runs fail closed (the migration has already run, so the precondition can't
  re-check). The runbook tells operators to let such runs finish first.

**Backup restore.**
- Restoring a pre-PR1a backup under PR1b+ code: the init container upgrades
  through PR1a, and PR1b's precondition fails with no protocol-1 row, so
  PR1a must run first.
- Restoring after PR1c with live legacy runs: those runs fail closed. They
  are stale in-flight records anyway.

### 8.3 Legacy bridge (Jack, 2026-10-29; PR1b → removed in PR1c)

A runtime-shaped payload without `ver` (`runtime_auth.py:29`) is validated
exactly as today: `load_runtime_principal` in the middleware's DB session
(`app_wiring.py:242-246`), including the live-run check, until its own `exp`.
- Nothing new is granted: its refresh slot holds the same access token, which
  `/auth/refresh` rejects.
- Legacy service attempts keep receiving rotated legacy tokens (§5.4).
- Each acceptance logs `authz.legacy_runtime_token_accepted` (kind, exp).
- Older APIs accept legacy tokens by the same rule, so there is no
  inconsistency between versions.

### 8.4 Operator procedure (runbook `docs/runbooks/authz-epoch.md`)

**Compose (existing installation):**

1. **Pin the release.** PR1a changes `docker-compose.yml`'s Bifrost images
   (`:83, :139, :203, :224`) to `ghcr.io/gobifrost/bifrost-api:${BIFROST_VERSION:-latest}`
   (and the client likewise). The operator sets `BIFROST_VERSION=<release
   tag>` in `.env`, or pins `@sha256:<digest>` from the release notes.
2. `docker compose pull init api worker client` — application images only;
   PostgreSQL, PgBouncer, Redis and SeaweedFS are not pulled or changed.
3. Check dependencies are already healthy: `docker compose ps postgres
   pgbouncer redis seaweedfs` shows each `running (healthy)`. If not, stop
   and fix them first.
4. `docker compose run --rm --no-deps init` — migrations alone, without
   reconciling or recreating any dependency. A precondition error stops here;
   the application containers still run the previous release, and only the
   database transaction of the failed migration was attempted (rolled back).
5. `docker compose up -d --no-deps api worker client`.
6. **Verify image identity:** `docker inspect --format '{{.Image}}'
   $(docker compose ps -q api worker)` must equal `docker image inspect
   --format '{{.Id}}' ghcr.io/gobifrost/bifrost-api:$BIFROST_VERSION`, and
   that image's `RepoDigests` must match the release's published digest.
   This, plus healthy status, is the rollout-finished confirmation. `ps`
   showing a tag is not enough.
7. Move to the next unit only in a later upgrade.

**Kubernetes (Flux):** the init Job or initContainer runs migrations first; a
failure blocks the rollout and existing pods keep serving. Confirm with
`kubectl rollout status deploy/<api>` and the worker controller showing only
current Jobs.

**Before PR1c:**
- Run `bifrost admin authz legacy-status`, a read-only CLI command over the
  same query as the precondition. It lists live legacy runs and legacy
  launchers.
- Upgrade remote launchers.
- Restart any listed legacy service attempts through the normal service
  restart; they relaunch on compatible routes with new-format credentials.

---

## 9. Test plan

Targets for `./test.sh`; none are run while the capacity benchmark holds the
host.

### 9.1 Version mechanism, transports, rollout

| File | Contract |
|---|---|
| `tests/unit/authorization/test_authz_epoch_triggers.py` | each §3.2 row bumps and irrelevant columns don't; a change made by a `BEFORE` trigger is seen (unrestricted UPDATE); FK cascade, `COPY FROM`, generic `update(model).values(**v)`, raw SQL; `TRUNCATE`; config rows moving into and out of the default-mode predicate; 10,000-row update → one bump; `SET CONSTRAINTS ALL IMMEDIATE` then later changes → one bump, and the epoch at commit differs from the start; forced firing inside a savepoint → `ROLLBACK TO SAVEPOINT` → commit → bumped; rollback → none; global row can't be deleted or truncated |
| `tests/unit/authorization/test_authz_snapshot.py` | **writer commits between the minter's fact reads and its epoch read** (minter paused by a test hook) → token refused after the commit; slow commit (event-ordered barriers); minter's first statement runs under REPEATABLE READ (`SHOW transaction_isolation` captured before any fact read); clock patched ±1 h → no outcome change; failover: epoch set back to the token's value → accepted, any other value → refused |
| `tests/unit/authorization/test_validator.py` | one statement; missing row → 401 + log; mismatch and expired → challenge header; ended runs and expired leases → 401 without challenge |
| `tests/unit/authorization/test_challenge_precedes_effects.py` | dependency-graph walk (§5.3); challenged mutation requests leave **zero** handler effects |
| `tests/unit/sdk/test_refreshing_transport.py` | one budget of 3 across `_send_with_retry` transient retries (502 then challenges); same for sync and `request()`; a non-idempotent POST replayed after a challenge produces **exactly one** effect; a non-challenge 401 (AI provider auth error) is not replayed; `stream`/`engine_stream`: challenge on the status line → refresh → reopen, and a stream already yielded is never replayed; engine-socket and network transports both covered; the cold module fetch is covered |
| `tests/unit/services/mcp_server/test_bridge_refresh.py` | mint → concurrent authority commit → request → challenge → re-mint → success; a bridge context whose first request changes authority makes its second request succeed after one re-mint; budget exhausted → 401 surfaces |
| `tests/unit/authorization/test_token_claims.py`, `test_issue_access_token.py`, `test_token_parity.py` | as revision 4 (schema and compression, lifetimes, poisoned role cache) |
| `tests/unit/services/test_service_credentials.py` | protocol-2 route → new-format access + refresh, `credential_version = 2`, no rotation; protocol-1 route → legacy token, NULL, rotation and `service-control` delivery unchanged; the hello without the field counts as protocol 1; the protocol is persisted with the ownership generation |
| `tests/unit/worker/remote/test_launcher_admission.py` (PR1c) | a protocol-1 (or field-less) launcher reconnecting after PR1c is refused at hello — no ownership row change, no assignment; routing skips a row with a persisted protocol < 2 |
| `tests/unit/sdk/test_expiry_challenge_cross_version.py` (PR1a server contract, PR1b client) | PR1a: an expired new-format token gets the exact challenge header, and `run_refresh` succeeds. PR1b: the SDK transport, given that recorded PR1a response, refreshes and replays — the PR1b client against PR1a after token expiry |
| `tests/e2e/platform/test_authz_migrations.py` (pattern `test_builder_residue_migration_rehearsal.py`) | **empty database → `alembic upgrade head` succeeds**; a database at the revision before PR1a → head fails at PR1b's precondition; with protocol-1 evidence it passes; PR1c fails while a live NULL-format run or protocol-1 launcher exists, and passes when none |
| auth refresh tests | live → token, same refresh token, no cookies; ended run / expired lease → 401; 50 concurrent run refreshes from one IP → all 200 |

**Mutation matrix** (one test each): mint → change through the real service
function → old token challenged → refresh → new claims reflect it, for every
§3.2 fact. Run, attempt and agent-run ends are refused by liveness.

### 9.2 Completeness

- **Read side:** the minter's `(table, column)` reads equal `AUTHZ_FACTS`
  (trigger columns plus the live-read run columns). Meta-test included.
- **Registry ↔ triggers:** `AUTHZ_FACTS` declares, per table, which of
  INSERT / UPDATE (with its columns) / DELETE fire (§3.2 table). The test
  asserts `pg_trigger` holds **exactly** that set of `authz_epoch_ins` /
  `_upd` / `_del` constraint triggers plus one `authz_epoch_truncate` per
  table, nothing else and nowhere else. Each `_upd` `WHEN` lists exactly the
  registry columns (from `pg_get_triggerdef`). Run tables must have no
  `_ins`. Meta-tests: dropping a trigger, or adding an INSERT trigger to
  `executions`, fails it.
- **Maintenance scan:** any later migration with `session_replication_role`
  or `DISABLE TRIGGER` must call `authz_epoch_bump_once()`.

### 9.3 One minter

- `create_access_token` raises on reserved claims (PR1b).
- AST over `api/src`, `api/shared`, `api/bifrost`:
  - `jwt.encode` callers ⊆ {`tokens._encode_claims`, `tokens._encode_legacy`
    (until PR1c), `security.create_access_token`, `create_refresh_token`,
    `create_mfa_token`, `solution_dev._mint_solution_dev_token`,
    `oauth_state_core.encode_state_jwt`};
  - `create_access_token` callers ⊆ {`create_embed_access_token`,
    `create_browser_impersonation`} ∪ PR2-marked human sites.
- A synthetic new `jwt.encode` is flagged.

### 9.4 E2E (`tests/e2e/platform/test_runtime_token_claims.py`)

Covers:
- role revocation mid-run → challenge → refresh → new facts;
- `ai.stream()` after an epoch change succeeds;
- run end: token and refresh refused;
- long wait: expiry → challenge → refresh → continue;
- Solution deactivate → 409 after refresh;
- legacy bridge.

Existing suites as revision 4: authorization units, runtime/chat boundary,
redaction, Solution permission, r3a, socket audit parity, MCP parity,
service e2e, contract version, DTO flags, MCP thin wrapper.

### 9.5 Benchmark (scoped)

Run `./test.sh bench run --profile baseline --backend fake` on `ad623e250`,
after PR1a and after PR1b; add a `services` profile (20 services, 10 min).

| Measure | Expectation |
|---|---|
| Authentication of a runtime request | today one extra transaction (`app_wiring.py:243`) with ~10 statements → no extra transaction, one statement; the route's own work unchanged |
| Transactions per run (`stats.py:112`) | PR1a unchanged; PR1b: `wf-sdk` −1 per SDK call; +1 per run start (mint); +1 per challenge-driven refresh |
| MCP bridge calls | mint (one transaction, ~12–15 statements) + one validator statement, replacing today's ~10-statement middleware transaction: roughly neutral |
| Authority writes | one bump per transaction; measure a 10,000-row bulk update |
| API CPU ms per run, `wf-sdk` burst p50/p95 | down after PR1b |
| Idle tx/s | unchanged |
| `services` profile | new-format attempts no longer mint every 10 s; heartbeat tick p95 down |

No class may regress beyond ±9 % noise.

---

## 10. Task breakdown

One implementer, in order. Each task is ≤ ~300 lines and runs its own
targeted tests. The full `./test.sh quality api` runs at five coherent
checkpoints: after A2, after A8, after B3, after B8, and at PR1c.

**PR1a**

| # | Task |
|---|---|
| A1 | Migration: `authz_versions` + seed + protection, `authz_epoch_bump_once`, `authz_protocol_state`, `credential_version` columns; ORM; `env.py` starting-revision capture |
| A2 | Migration: flush and truncate triggers (§3.2); `test_authz_epoch_triggers.py` |
| A3 | `authz` module (epoch read, validator queries, `AUTHZ_FACTS`); protocol-1 start record; `verify_iat` off; expiry challenge + dependency-graph test; Compose image-tag variable; runbook |
| A4 | `load_only` tightening; `RuntimeAuthority.solution_active`; `context.py:120` docstring |
| A5 | Claims schema with compressed `usr`; `principal_from_claims`; `test_token_claims.py` |
| A6 | Minter (REPEATABLE READ before the first statement), `issue_run_refresh_token`; `test_issue_access_token.py`, `test_authz_snapshot.py`, `test_token_parity.py` |
| A7 | `/auth/refresh` `run_refresh`; refresh tests |
| A8 | Completeness, maintenance scan, one-minter AST; mutation matrix; migration rehearsal (empty database) |

**PR1b**

| # | Task |
|---|---|
| B1 | Precondition migration; protocol-2 start record |
| B2 | Validator cutover, epoch challenge, bridge + counter |
| B3 | `RefreshingTransport` for all SDK transports incl. streams and the cold module fetch; per-call budget; remove the old any-401 refresh; tests |
| B4 | Launcher hello field and persisted protocol; route-first minting; workflow mint cutover; worker env; redaction; context scrub; legacy minter; `credential_version` writes |
| B5 | Service credentials per route; legacy rotation retained for NULL attempts; credential install split |
| B6 | MCP bridge runtime branch + `BridgeRefreshingTransport`; reserved-claim refusal |
| B7 | README and docstrings; `bifrost admin authz legacy-status` |
| B8 | E2E; benchmarks |

**PR1c:** fail-closed precondition; launcher admission check with its
reconnect test; delete the bridge, the legacy minter and rotation, the legacy
install and the `ServiceControl` token fields; refresh the fingerprint.

---

## 11. Decisions

- **D1** Claims layout; names kept so PR1a pods validate new tokens.
- **D4** One global random epoch, equality only, bumped once per changing
  transaction by database triggers; rows keyed by subject; no per-subject
  versions or hooks.
- **D5** Facts from the database, never `role_cache`; `load_runtime_principal`
  is the parity oracle.
- **D6** Agent runs: no in-process token; the MCP bridge mints per call and
  re-mints on the challenge.
- **D7** `POST /auth/refresh` dispatches on `type`; no per-IP limiter for
  runs.
- **D8** Run refresh tokens are reusable, unrotated, stateless, valid while
  the run is live (SC3).
- **D9** New-format service attempts use standard lifetimes and refresh, with
  the lease enforced per request. Legacy-format attempts keep today's rotation
  until PR1c. New-format credentials go only to compatible launchers.
- **D10** Legacy tokens are bridged; their life is bounded by run liveness;
  PR1c removes the bridge behind a fail-closed precondition.
- **D11** The challenge (`error="invalid_token"`,
  `error_description="refresh_required"`) is emitted only by the auth
  dependency, before any handler effect: for expired access tokens from PR1a,
  and also for epoch mismatch from PR1b.
- **D12** Completeness: triggers, registry ↔ triggers, read capture,
  maintenance scan.
- **D13** `sol_active` answers the run's own `?solution=` gate.
- **D14** Impersonation end changes no version (confirmed).
- **D17** Run refresh and refreshed tokens redacted; nested service tokens
  scrubbed.
- **D18** PR1a → PR1b → PR1c; durable evidence; fresh install = empty starting
  revision; pinned-release Compose procedure in §8.4; PR1c refuses
  protocol-1 launchers at hello.
- **D19 (PR2 decision; used from PR1)** Compressed `usr`; the warning measures
  the encoded token.
- **D20** One budget of 3 challenge-driven refreshes per SDK call, shared
  across transient retries; replay only on the challenge.

### User-visible changes and operator steps

**Behaviour:**
- Runtime SDK calls no longer replay on non-challenge 401s. An AI-provider
  authentication error, for example, now surfaces instead of being retried
  once after a pointless refresh.
- During a burst of more than three authority changes within one call, the
  call can fail with 401 (§3.7).
- After PR1c, a still-live legacy-format run is refused. The precondition and
  the `legacy-status` command make that visible first.

**Operator steps:**
- Compose upgrades pin `BIFROST_VERSION`, pull only app images, check dependency health, run `docker compose run --rm --no-deps init`, then `up -d --no-deps` the app services and verify running image IDs against the release digest (§8.4).
- After PR1c, upgrade any remote launcher still on protocol 1: it is refused at connect.
- Confirm each unit's rollout before the next.
- Before PR1c: upgrade remote launchers and restart the legacy services that
  `legacy-status` lists.
- Never roll back below PR1a.
- After disabling triggers for maintenance, run
  `SELECT authz_epoch_bump_once();`.
