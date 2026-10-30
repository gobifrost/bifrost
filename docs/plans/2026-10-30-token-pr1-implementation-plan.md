# Token PR1 — Runs and Shared Pieces: Implementation Plan (revision 4)

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
| 4 | this | **Authority version instead of timestamps** (Jack, 2026-10-30, on Fable's design review and Astra review 2): one global authority epoch with random values, run liveness from the run's own row, a single REPEATABLE READ minter snapshot |

Revision 3's machinery is deleted: per-subject timestamps, advisory locks and
buckets, clock reads, waits and fractional rounding, pruning, transaction-local
subject lists, the minter retry loop and its 503 path, heartbeat protocol
columns, and service token rotation.

### Astra review 2 (revision 3) — disposition

| # | Finding | Resolution | Where |
|---|---|---|---|
| 1 | First deferred firing isn't the final write | An immediate trigger clears a transaction-local "bumped" flag on every change; the deferred trigger bumps whenever the flag is clear. Any change after a forced (`SET CONSTRAINTS ALL IMMEDIATE`) or earlier firing clears the flag and queues its own later firing, so the last change is always followed by a bump. One global row, so no lock ordering is involved | §3.2–§3.3 |
| 2 | Fan-out stamping races new consumers | No fan-out: every carried fact bumps the one global epoch | §3.1 |
| 3 | PR1a breaks retained minters; decode compatibility | Reserved-claim refusal ships in PR1b with the minter removals; `verify_iat` is off from PR1a | §8.2, §6 |
| 4 | Heartbeat certification ≠ fleet readiness | Mixed fleets are shown to be **secure** in every combination (database triggers; older pods validate from the database); readiness only affects availability. Durable evidence: the first start of each protocol, recorded by the code itself; old processes need no fencing because removing support is gated on an enforced cutoff | §8 |
| 5 | No enforced legacy-token maximum | The bridge refuses legacy tokens after a recorded cutoff (first PR1b start + 24 h 5 min), whatever lifetime they were minted with; PR1c removes code that is already inert | §8.3 |
| 6 | Clock, failover and retention assumptions | No clocks or retention. Equality-only random epochs: a lossy failover or restore can never collide with a superseded value. Validator and minter read the primary | §3.4 |
| 7 | TRUNCATE and maintenance | Statement-level `AFTER TRUNCATE` triggers bump; trigger-disabled maintenance must end with `authz_epoch_bump()` (runbook rule plus a migration scan test); OLD/NEW handling is moot with one epoch | §3.2, §9.2 |
| 8 | Retry budget | Gone: no locks, waits or retries | — |
| 9 | Rotation delays heartbeats | Rotation removed: liveness reads enforce the lease per request, so service tokens refresh like other runs | §5.4 |
| 10 | Cost claims; quadratic collection | Claims narrowed (§9.5; spec amended); collection is a constant-cost flag | §3.2, §9.5 |

---

## 1. Scope boundary

### 1.1 Mint sites

| Site | PR1 | PR2 / unchanged |
|---|---|---|
| `core/security.py:458` `mint_engine_token` (caller `jobs/consumers/workflow_execution.py:933-937`) | PR1b: removed; consumer mints access + run refresh token | — |
| `core/security.py:426` `mint_service_token` (callers `services/service_claim.py:463` `_rotate_token`, `:691` `_launch_claimed`) | PR1b: removed; launch mints access + run refresh token; rotation deleted (§5.4) | — |
| `services/mcp_server/tools/_http_bridge.py:75-86` runtime branch | PR1b: `issue_access_token` (D6) | — |
| `_http_bridge.py:88-104` human branch; `routers/auth.py:684,836,1126,1892`; `routers/passkeys.py:207`; `routers/oauth_sso.py:423`; `routers/mfa.py:254`; `services/mcp_server/auth.py:509,564` | — | PR2 |
| `core/security.py:245` `create_embed_access_token`; `shared/browser_impersonation.py:90` marker | — | unchanged |
| `core/security.py:179` `create_mfa_token`; `shared/solution_dev.py:121`; `services/oauth_state_core.py:79` | — | unchanged (not access tokens) |
| `core/security.py:57` `create_access_token` | PR1b: refuses reserved runtime claims (staged with the removals above) | PR2 |
| `core/security.py:146` `decode_token` | PR1a: `options={"verify_iat": False}` | — |

### 1.2 Validation and authority sites

| Site | PR1 change |
|---|---|
| `core/app_wiring.py:221` middleware (runtime branch `:242-246`) | PR1b: parses `ver:1` claims without a DB session (attribution only); legacy tokens keep today's path (bridge, §8.3) |
| `core/auth.py:135` `_get_current_user_optional` (`:184-192`) | PR1b: `authenticate_runtime_token(db, claims)` runs the validator query (§3.5) in the **request's own DB session**, then builds the principal |
| `core/auth.py:343` `get_current_user` | PR1b: refresh-required 401 (D11) |
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
| `create_embed_token` name | allowlisted by real name |
| Agent runs mint no JWT | D6 |
| Run refresh endpoint; worker env (`worker.py:51-52`) | §5 |
| Service lifetime | D9: standard lifetime; lease enforced by the liveness read |
| `iat`/`jti`; 1 s ambiguity | moot: revocation uses the epoch, not `iat` |
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
  non-empty `bnd`, matching `build_authorization_context`.
- `RunUser(usr.id, ctx, usr.kind, usr.active)`.
- The actor (`act` or `usr`) supplies email, name, `is_external`,
  `is_provider_org`, role ids/names, and `is_superuser = PLATFORM_ADMIN_ROLE_ID
  ∈ actor role ids`.
- `organization_id = run.org`.
- `RuntimeAuthority` from `credential_kind/sub/service_id/run/imp`, plus
  `solution_active` (D13).

`iat` is set from the minting host's clock for JWT hygiene only; nothing
compares it.

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
| default workflow mode / grants | 0 |

That is about 20 a day, bursty around user provisioning, for 1,713 users,
60 roles and 96 orgs. At that rate **one global epoch** is simplest: any change
to any carried fact invalidates every run token, and each active run refreshes
once on its next call.

**Re-measure trigger:** each validator refusal logs `authz.refresh_required`.
Move to per-subject versions (rows keyed by subject in the same table; nothing
is built for it now) if **either** holds:
- bumps exceed **30 per hour** in any hour on three days of a week; or
- refresh-required responses exceed **5 %** of authenticated runtime requests
  over a day.

### 3.2 Table, bump function and triggers (PR1a migration `20261030_authz_versions.py`)

```text
authz_versions(subject_kind varchar(32), subject_id uuid, version bigint NOT NULL,
               PRIMARY KEY (subject_kind, subject_id))
seed: ('global', '00000000-0000-0000-0000-000000000000', <random>)
authz_protocol_state(protocol int PRIMARY KEY, first_started_at timestamptz NOT NULL)
```

- `authz_epoch_bump()` sets the global row's `version` to a fresh random
  signed 64-bit value from `gen_random_uuid()` (core since PG 13; the stack
  runs `pgvector/pgvector:pg16`, `docker-compose.yml:31`). It is **not** `+1`:
  values are compared for equality only, and random values can't coincide
  across diverging histories (§3.4).
- A `BEFORE DELETE` row trigger and a `BEFORE TRUNCATE` trigger on
  `authz_versions` raise, so the global row can't be removed. Validators and
  minters refuse if it is missing anyway.
- On every carried-fact table:
  1. `authz_epoch_a_collect` — `AFTER INSERT/UPDATE/DELETE … FOR EACH ROW`,
     restricted by `WHEN` to the relevant changes below. It runs
     `set_config('bifrost.authz_epoch_bumped', '0', true)`: a transaction-local
     setting, constant cost per row, no lists.
  2. `authz_epoch_b_flush` — `CONSTRAINT TRIGGER … DEFERRABLE INITIALLY
     DEFERRED FOR EACH ROW` with the same `WHEN`. If the setting is not `'1'`,
     it calls `authz_epoch_bump()` and sets it to `'1'`.
  3. `authz_epoch_truncate` — `AFTER TRUNCATE FOR EACH STATEMENT`, calling
     `authz_epoch_bump()` directly.

The names are ordered on purpose: for one row event Postgres fires AFTER ROW
triggers in name order, so in immediate mode the collect always precedes its
own flush.

| Table | Relevant change |
|---|---|
| `users` | UPDATE OF email, name, organization_id, base_role_id, is_external, identity_kind, is_active (changed); DELETE |
| `organizations` | UPDATE OF is_provider (changed); DELETE |
| `user_roles`, `user_role_boundaries`, `role_permissions` | INSERT, UPDATE, DELETE (incl. FK cascades and `COPY FROM`) |
| `roles` | UPDATE OF name (changed); DELETE |
| `workflows` | UPDATE OF solution_id, permission_mode (changed); DELETE |
| `workflow_permission_grants`, `solution_workflow_permission_requests` | INSERT, UPDATE, DELETE |
| `system_configs` | any row where OLD or NEW has category `workflow_permissions`, key `default_mode`, org NULL |
| `solutions` | UPDATE OF status (changed); DELETE |
| `agents` | UPDATE OF solution_id (changed); DELETE |
| `service_definitions` | UPDATE OF workflow_id, organization_id (changed) |
| `executions`, `agent_runs`, `service_attempts` | UPDATE of a carried column that is set once at creation (run user, actor, workflow, org, lineage, impersonation, `service_id`) — never expected; covered for completeness. Status, state and lease are **not** here: they are read live (§3.5) |

Every table also has the TRUNCATE trigger. No application write site changes.

**Maintenance rule:** anything that disables triggers
(`session_replication_role = replica`, `ALTER TABLE … DISABLE TRIGGER`, bulk
restores) must finish with `SELECT authz_epoch_bump();`. The rule goes in
`docs/runbooks/authz-epoch.md`. A test scans `api/alembic/versions/` for those
statements and requires the bump in the same migration (§9.2).

### 3.3 Correctness of the bump (finding 1)

1. **A bump follows the last change.** Every relevant row change C runs its
   collect (flag := `'0'`) and queues its own flush event, which fires after
   C's collect: at COMMIT in deferred mode, or at the end of C's statement after
   the name-ordered collect in immediate mode (`SET CONSTRAINTS ALL
   IMMEDIATE`). That flush bumps unless the flag is already `'1'`, and the
   flag can be `'1'` only if a bump ran after C's collect. So after C there is
   always a bump.
2. **Later events.** A flush fired early (forced) doesn't stop later changes
   from bumping, because each later change clears the flag again. The same
   holds for changes made by other deferred triggers during COMMIT: Postgres
   keeps firing newly queued deferred events until none remain.
3. **Savepoints.** An aborted savepoint undoes its row changes, its bump
   `UPDATE` and its `set_config` together. Deferred events queued in it are
   discarded. Postgres re-arms outer events that the aborted savepoint had
   fired (AfterTriggerEndSubXact), so they fire again at commit; a dedicated
   test pins this (§9.1).
4. **Atomicity.** The bump is an `UPDATE` inside the changing transaction, so
   the change and the new epoch become visible together, or not at all.
5. **TRUNCATE** bumps in its own statement; anything after it clears and bumps
   again under rule 1.

### 3.4 Minter and validator correctness

**Minter.** `issue_access_token(subject)` runs in its own short transaction,
`BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`. It reads every fact and the
epoch from one snapshot σ, and embeds the epoch as `av`. Read-only
REPEATABLE READ transactions take no locks, never wait on writers and get no
serialization failures, so there are no retries.

**Proof.** For any committed change C to a carried fact:
- If C committed before σ, both C's facts and C's epoch (or a later one) are
  in σ, so the token is fresh.
- If C committed after σ, neither is in σ. C's commit replaces the epoch with
  a new random value, so the validator, which reads the committed row on the
  primary in READ COMMITTED, sees `av ≠ epoch` from C's commit on and refuses.

The probability that a random value repeats is 2⁻⁶⁴ per bump. No clock, lock
or ordering is used.

**Failover and restore.** Equality only means a token is accepted exactly
when its epoch value is the database's current one:
- A lossy failover or a backup restore that lands on the epoch a token was
  minted from also lands on the facts it was minted from. Accepting it matches
  the database.
- A superseded value can't come back by counting: a `+1` counter could, when
  two diverging histories reach the same number with different changes.

**Primary only.** Validators and the minter must read the primary. Today
there is one database URL (`docker-compose.yml:90`, PgBouncer → primary). Any
future replica routing must exclude these queries; a code comment and the
`authz` module docstring state it.

### 3.5 Validator (PR1b)

One statement, one round trip, in the request's own transaction. Example for
`workflow_execution`:

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
  validating host's clock, as `runtime.py:181` compares today.

The result maps to:

| Result | Response |
|---|---|
| No row (epoch missing) | 401 and an error log; never a default |
| `epoch ≠ av` | 401 refresh required (D11) |
| `live IS NOT TRUE` | 401 "run is no longer live" (refresh will also fail) |
| otherwise | accept, principal from claims |

Run liveness is read from the run's own row on every request, exactly the
facts `load_runtime_authority` checks today (`runtime.py:115,180-182,211`). Run
end and lease expiry therefore apply on the very next request without any
stamp.

### 3.6 Impersonation end (D14)

No version change. Impersonation session liveness is not a fact any run token
carries: runtime authority has never depended on it (`runtime.py:150-163`
copies only the immutable ids from the run row). Browser markers keep their
per-request Redis session check (`shared/browser_impersonation.py:130`), which
already ends them immediately. Bumping the epoch here would force every run in
the system to refresh with no change in any outcome. This replaces SC2's
revision-2/3 "stamp at impersonation end".

### 3.7 Bursts

During a provisioning burst of K bumps, each active run refreshes at most once
per call. A refresh costs one REPEATABLE READ mint transaction (~12–15
statements), comparable to today's per-request runtime authentication (~10
statements, `docs/benchmarks/2026-10-26-runtime-auth-efficiency.md`). So even
when bumps outpace a run's calls, its per-call cost is at most about today's
plus one extra HTTP round trip. That is acceptable.

The SDK retries a call once after refreshing (`bifrost/client.py:991,1028`).
If a second bump lands between the refresh and the retry (a window of
tens of milliseconds), the retry is refused again and the call would fail. To
prevent that, D20: when a 401 carries `error_description="refresh_required"`,
the SDK refreshes and retries up to **3** times; any other 401 still gets one
refresh. With bumps 1 s apart the chance of three consecutive misses is about
10⁻⁴ per call. A tight provisioning loop (> 10 bumps/s sustained) would also
trip the re-measure trigger (§3.1).

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
   loads to `load_only` (`_load_service_attempt` `:166`, `_load_agent_run`
   `:207`, the service-path `Workflow`, `list_workflow_grants`,
   `workflow_permission_requests`) and adds `RuntimeAuthority.solution_active`
   (`:59`).
2. The run user's roles, names, permissions and boundaries come from the
   database, never `shared/role_cache` (D5).
3. The run user's identity and home org `is_provider`; the actor's identity,
   org and roles when they differ.
4. The global epoch row (missing → `AuthorityStateMissing`, a 500 with an
   error log).

### 4.2 Lifetimes

| Kind | Access token | Run refresh token |
|---|---|---|
| `workflow_execution`, `service_attempt` | `3600 + secrets.randbelow(1801)` s | no `exp`; valid while the run is live (SC3) |
| `agent_run` (MCP bridge) | `3600 + secrets.randbelow(1801)` s | none (D6) |

The run refresh token is `{iss, aud, type:"run_refresh", ver:1, iat, jti,
sub, credential_kind, service_id?}`. It carries identity only, and minting it
reads no database. `decode_token(expected_type="access")` rejects it.

---

## 5. Run refresh

### 5.1 Endpoint (PR1a)

`POST /auth/refresh` (`routers/auth.py:999`) keeps `TokenRefresh` (`:296`) and
`Token` (`:252`), so the CLI fingerprint is unchanged. It decodes without
`expected_type` and dispatches on `type`:
- `refresh` → today's human branch, untouched.
- `run_refresh` → requires `ver == 1` and calls `issue_access_token`.
  - `TokenSubjectUnavailable` → 401.
  - Success → `Token(access_token=new, refresh_token=<same token>)`, no
    cookies.
  - Skips the per-IP `auth_limiter` (every child shares its worker pod's IP).
  - Reusable, not rotated (D8).

### 5.2 Workers (PR1b)

- `worker.py:37-52` puts `engine_refresh_token` into `BIFROST_REFRESH_TOKEN`.
- `engine_refresh_token` joins `_CONTEXT_REDIS_EXCLUDED_KEYS`
  (`process_pool.py:82`) and the output redaction (`worker.py:297`,
  `engine.py:324`).
- Refreshed access tokens are redacted through `install_access_token`
  (`bifrost/client.py:917`).

### 5.3 SDK (PR1b)

- `bifrost/client.py`: engine-local requests refresh on 401 through the
  network `/auth/refresh` and replay over the socket (~`:965`, ~`:1007`).
  Update the docstrings that say otherwise.
- D20: up to 3 refresh-and-retry cycles for `refresh_required`; one for any
  other 401.
- `refresh_connection_access_token` (`:267`) and `save_refreshed_credentials`
  (`credentials.py:645`) already accept an unchanged refresh token.

### 5.4 Services (PR1b; finding 9)

- **Launch.** The claim commits first (`service_claim.py:566`).
  `_launch_claimed`'s second session (`:581-590`) mints an access token and a
  run refresh token with standard lifetimes, and
  `bifrost/_service_runtime.install_service_credentials(access, refresh)` sets
  both slots (`worker.py:419-421`, `engine.py:950-952`).
- **Fencing.** Every request reads the attempt's state and `lease_expires_at`
  (§3.5). A fenced child is refused as soon as its lease lapses, which is what
  SC1's short lifetime was protecting, now without a lifetime rule. The child
  refreshes like any run.
- **Rotation deleted.** Delete `_rotate_token` (`:453`) and its call (`:227`),
  the `service_token_key` writes (`:471,750,868`) and the supervisor's token
  install (`engine.py:1080-1083`). The heartbeat no longer mints, so the
  cadence finding disappears.
- **Wire compatibility.** `ServiceControlRequest.token_expires_at` becomes
  optional and ignored; `ServiceControlResponse.access_token` /
  `token_expires_at` are always `None` (`shared/models.py:48-57`). This lets
  older remote-launcher children keep talking to `service-control` during the
  bridge window. PR1c deletes the three fields and refreshes the CLI contract
  fingerprint.
- **Diagnostics.** Nested service tokens are scrubbed from the diagnostic
  Redis context copy (`process_pool.py:1159`).

---

## 6. Repository authority rule (PR1b)

Rule: claims in an access token minted by `issue_access_token` are authority
while the token is unexpired, its authority version is current and its run is
live.

1. `parse_runtime_claims` + `authenticate_runtime_token` are the only
   claims→principal path. They require signature, `type == "access"`,
   `ver == 1`, a strict schema and a passing validator (§3.5).
2. `principal_from_claims` is imported only by `tokens.py` (AST test).
3. `create_access_token` refuses the reserved claims `ver`, `av`,
   `credential_kind`, `usr`, `act`, `run`, `imp`. It ships in PR1b, in the
   same unit that removes `mint_engine_token`, `mint_service_token` and the
   bridge's runtime branch (Astra 2 #3). Only `tokens._encode_claims` signs
   them.
4. `api/src/repositories/README.md`: the "Authority model" paragraph,
   implementation rule 1 and the `RuntimeAuthority` paragraph change to the
   rule. Legacy runtime tokens are described as bridged until the cutoff
   (§8.3).
5. `test_runtime_auth.py::test_runtime_principal_uses_database_authority_not_spoofed_token_claims`
   becomes three tests:
   - a legacy payload uses the bridge until the cutoff;
   - `ver:1` claims are authority with one validator statement;
   - a tampered token fails.

   Docstrings to update: `runtime_auth.py:1,37`, `runtime.py:1-5`,
   `core/principal.py:22-24,63-82`, `mcp_server/server.py:143-145`.

---

## 7. Application write paths

None change for authority (§3.2). PR1 touches application code only at the
mint and validation sites (§1), service launch and rotation removal (§5.4) and
the SDK (§5.3).

---

## 8. Rollout (Astra 2 #4, #5)

### 8.1 Mixed fleets are secure in every combination

| Combination | Why it is safe |
|---|---|
| Any pod version writes authority | The triggers live in the database from PR1a's migration on, so every write bumps the epoch |
| New-format token on a pre-PR1b pod | That pod validates through `load_runtime_principal` (today's database path; names kept, D1). PR1a also turns `verify_iat` off |
| Legacy token on a PR1b pod | Bridge: today's database path until the cutoff (§8.3) |
| Old process starts late (scale-from-zero, rollback) | Same rows: old code validates from the database and writes are bumped. Only availability can suffer |

Readiness therefore matters **only for availability**: a run refresh routed to
an API pod without `run_refresh` fails closed. That is the one reason PR1a
must be live before PR1b.

### 8.2 Merge units

Main squash-merges each PR, so each unit is one commit.

| Unit | Contents | Behaviour |
|---|---|---|
| **PR1a** | migration (versions table, seed, protection, bump function, triggers, `authz_protocol_state`); claims schema; minter; `run_refresh`; `verify_iat` off; protocol-1 start record; runbook; completeness tests | unchanged |
| **PR1b** | migration precondition; minters switch with reserved-claim refusal; validators; bridge with cutoff; SDK; service launch and rotation removal; README; protocol-2 start record | claims are authority |
| **PR1c** (file the issue when PR1b merges; next release) | precondition (cutoff passed); delete the bridge, the legacy validation branches and the three `ServiceControl` token fields; refresh the contract fingerprint | none: the bridge is already inert |

**Durable evidence.** Each API process, at startup, runs `INSERT INTO
authz_protocol_state VALUES (<its protocol>, now()) ON CONFLICT DO NOTHING`
(PR1a: 1; PR1b: 2). The first start of each protocol is recorded once and never
deleted, unlike heartbeat rows (`scheduler_diagnostics.py:56`). On a fresh
database — zero `users` rows when the migration runs, so no token or run can
exist — the migration inserts its own protocol row with `-infinity`.

**Preconditions.**
- **PR1b's migration** raises "Upgrade to the PR1a release and let it run for
  an hour first" unless protocol 1's `first_started_at ≤ now() − 1 h`. Rollouts
  finish within Kubernetes' progress deadline (10 min) or at once under
  Compose. If a stalled rollout leaves pre-PR1a API pods serving beyond that,
  the only effect is that run refreshes reaching them fail closed (§8.1).
- **PR1c's migration** raises unless the bridge cutoff (§8.3) has passed.
  Removing code that refuses everything changes nothing, so no fleet evidence
  is needed.

A failed migration leaves the previous release serving.

### 8.3 Legacy bridge with an enforced cutoff (Jack's bridge; Astra 2 #5)

A runtime-shaped payload without `ver` (`runtime_auth.py:29`) is validated
exactly as today — `load_runtime_principal` in the middleware's DB session
(`app_wiring.py:242-246`), including the live-run check — while both hold:
- the token's own `exp` hasn't passed; and
- `now < cutoff`, where `cutoff = protocol 2 first_started_at + 24 h 5 min`
  (read once per process from `authz_protocol_state`).

`24 h 5 min` is the longest legacy engine token (`NO_TIMEOUT_TOKEN_SECONDS` +
300). It bounds legacy tokens minted under any configuration, including the
legacy MCP bridge's `access_token_expire_minutes` (`config.py:254`, which has
no upper bound) and tokens minted by old workers still draining after PR1b
started.

Nothing new is granted: the legacy token's refresh slot holds the same access
token, which `/auth/refresh` rejects. Each acceptance logs
`authz.legacy_runtime_token_accepted` (kind, exp).

---

## 9. Test plan

Targets for `./test.sh`; none are run while the capacity benchmark holds the
host.

### 9.1 Version mechanism

| File | Contract |
|---|---|
| `tests/unit/authorization/test_authz_epoch_triggers.py` | each §3.2 row bumps, irrelevant columns don't; FK cascade (role delete), `COPY FROM`, generic `update(model).values(**v)` and raw SQL bump; `TRUNCATE` bumps; system-config rows moving into and out of the default-mode predicate bump; 10,000-row update → exactly one bump at commit; rollback → no bump; the global row can't be deleted or truncated |
| same file — finding 1 | `SET CONSTRAINTS ALL IMMEDIATE` after change A, then change B → the final epoch differs from the one after A; change → forced firing inside a savepoint → `ROLLBACK TO SAVEPOINT` → commit → bumped (pins Postgres re-arming the events); a deferred app trigger that changes a fact during COMMIT → bumped after it |
| `tests/unit/authorization/test_authz_snapshot.py` | **slow commit:** writer changes a role and holds COMMIT arbitrarily (event-ordered, plus a 3 s case); a minter mints meanwhile; after commit its token is refused, and a token minted after commit is accepted. **Skew:** host clocks patched ±1 h → no outcome changes (only `exp` uses a clock). **Failover:** restore the epoch to a token's value → accepted; to any other value → refused |
| `tests/unit/authorization/test_validator.py` | one statement per request (`before_cursor_execute`, as `test_runtime.py:286`); missing epoch row → 401 + error log; mismatch → refresh-required header; execution / agent run ended → 401; service lease expired, state terminal or service id mismatch → 401 |
| `tests/unit/authorization/test_token_claims.py` | schema round trip incl. `usr` compression; derivations; production-largest fixture encoded < 6,144 B; warning above; never refused |
| `tests/unit/authorization/test_issue_access_token.py` | single REPEATABLE READ read-only transaction (caller's transaction untouched); lifetimes spread 3600–5400; non-live → `TokenSubjectUnavailable` |
| `tests/unit/authorization/test_token_parity.py` | each `test_runtime.py` scenario: `principal_from_claims(issue…)` equals `load_runtime_principal` with a cleared role cache; a poisoned role cache doesn't reach the claims |
| `tests/unit/services/test_service_launch_tokens.py` | launch installs access + refresh; no heartbeat mints; a fenced child is refused once its lease lapses; `service-control` accepts the old request shape and returns no token |
| `tests/unit/services/test_worker_sdk_http.py` (`:1482` inverted) | engine-local 401 → network refresh → socket replay |
| `tests/unit/sdk/test_refresh_required_cycles.py` | `refresh_required` 401 three times → three refreshes, then the error surfaces; other 401 → one refresh |
| auth refresh tests | live → new token, same refresh token, no cookies; ended run / expired lease → 401; 50 concurrent run refreshes from one IP → all 200 while human refresh stays limited; 10 refreshes then run end → 401 |
| bridge and precondition tests | legacy token accepted before the cutoff and refused after it, regardless of its `exp`; PR1b and PR1c migrations refuse without their evidence and pass on a fresh database |

**Mutation matrix** (one test each): mint → change through the real service
function → old token refused → refresh → new claims reflect it. Changes:
- role grant and revoke; boundary add and remove; base role; org move;
  provider flag (SQL); role rename; role permission edit; role delete;
- the distinct actor's role or org;
- workflow powers; Solution deactivate and reactivate; `Agent.solution_id`
  capture;
- execution, service-attempt (including `expire_leases`) and agent-run end —
  these are refused via liveness, with no epoch change.

### 9.2 Completeness

- **Read side:** under a `do_orm_execute` capture, the minter's `(table,
  column)` reads equal `AUTHZ_FACTS`. Each entry is either a §3.2 trigger
  column or one of the live-read run columns (status, state,
  `lease_expires_at`). Meta-test: an extra read fails it.
- **Registry ↔ triggers:** every registry table has the collect, flush and
  truncate triggers in `pg_trigger` with matching `WHEN` columns (from
  `pg_get_triggerdef`), and vice versa. Meta-test: dropping one trigger fails
  it.
- **Maintenance scan:** any migration after PR1a containing
  `session_replication_role` or `DISABLE TRIGGER` must also contain
  `authz_epoch_bump()`.

### 9.3 One minter

- `create_access_token` raises on reserved claims (PR1b).
- AST over `api/src`, `api/shared`, `api/bifrost`:
  - `jwt.encode` callers ⊆ {`tokens._encode_claims`,
    `security.create_access_token`, `create_refresh_token`,
    `create_mfa_token`, `solution_dev._mint_solution_dev_token`,
    `oauth_state_core.encode_state_jwt`};
  - `create_access_token` callers ⊆ {`create_embed_access_token`,
    `create_browser_impersonation`} ∪ the PR2-marked human sites.

  A synthetic module adding a `jwt.encode` is flagged. A reserved-claim call
  from `_http_bridge._token_from_context` raises.

### 9.4 E2E (`tests/e2e/platform/test_runtime_token_claims.py`)

Covers:
- role revocation mid-run → refresh → new facts;
- run end: token and refresh both refused;
- long wait: expired access token → refresh → continue;
- Solution deactivate → 409 after refresh;
- legacy bridge before the cutoff.

Existing suites must pass:
- `tests/unit/authorization/*`
- `tests/e2e/platform/test_agent_runtime_boundary.py`,
  `test_chat_runtime_boundary.py`, `test_engine_token_redaction_live.py`,
  `test_solution_permission_runtime.py`, `test_r3a_identity_authorization.py`,
  `test_sdk_socket_audit_parity.py`
- `tests/e2e/mcp/test_mcp_parity.py`
- service e2e
- `tests/unit/test_contract_version.py`, `test_dto_flags.py`,
  `test_mcp_thin_wrapper.py`

### 9.5 Benchmark (Astra 2 #10)

Run `./test.sh bench run --profile baseline --backend fake` on `ad623e250`,
after PR1a and after PR1b. Add a `services` profile (20 supervised services,
10 min) to `scripts/bench/profiles/`.

| Measure | Expectation (scoped) |
|---|---|
| Authentication of a runtime request | today: one extra transaction (`app_wiring.py:243`) with ~10 statements → **no extra transaction, one statement** in the request's own transaction. The route's own work is unchanged |
| Transactions per run (`stats.py:112`) | PR1a: unchanged (the bump rides existing commits; only authority writes bump). PR1b: `wf-sdk` −1 per SDK call; every run +1 at start (the mint); +1 per refresh |
| MCP bridge calls (agent tools) | per call: mint (one transaction, ~12–15 statements) + one validator statement, replacing today's ~10-statement middleware transaction — roughly neutral |
| Authority writes | +2 trigger calls per changed row, one bump per transaction; measure a 10,000-row bulk update |
| API CPU ms per run, `wf-sdk` burst p50/p95 | down after PR1b |
| Idle tx/s (`stats.py:101`) | unchanged; services no longer mint every 10 s |
| `services` profile: heartbeat tick p95; worker and API tx/s | tick p95 down (no mint) |

No class may regress beyond the harness's ±9 % noise. The spec's earlier
"about five fewer transactions per SDK call" is corrected to these
expectations.

---

## 10. Task breakdown

One implementer, in order. Each task is ≤ ~300 lines with its tests and runs
`./test.sh quality api`.

**PR1a**

| # | Task |
|---|---|
| A1 | Migration: `authz_versions` + seed + protection, `authz_epoch_bump`, `authz_protocol_state` with fresh-database rule; ORM |
| A2 | Migration: collect/flush/truncate triggers for §3.2; `test_authz_epoch_triggers.py` |
| A3 | `authz` module (epoch read, validator query, `AUTHZ_FACTS`); protocol-1 start record; `decode_token` `verify_iat` off; runbook `docs/runbooks/authz-epoch.md` |
| A4 | `load_only` tightening; `RuntimeAuthority.solution_active`; `context.py:120` docstring |
| A5 | Claims schema with compressed `usr`, `principal_from_claims`; `test_token_claims.py` |
| A6 | Minter (REPEATABLE READ) and `issue_run_refresh_token`; `test_issue_access_token.py`, `test_authz_snapshot.py`, `test_token_parity.py` |
| A7 | `/auth/refresh` `run_refresh`; refresh tests |
| A8 | Completeness, maintenance-scan and one-minter AST tests; mutation matrix |

**PR1b**

| # | Task |
|---|---|
| B1 | Migration precondition; protocol-2 start record; bridge cutoff |
| B2 | Validation cutover (middleware parse, `authenticate_runtime_token` in the request session, `core/auth.py`, MCP `verify_token`); bridge + counter; `test_validator.py` |
| B3 | Workflow mint cutover; worker env; redaction; context scrub; SDK engine-local refresh and D20; remove `mint_engine_token`; `tests/helpers/runtime_tokens.py` |
| B4 | Service launch tokens; rotation removal; `ServiceControl` compatibility; remove `mint_service_token` |
| B5 | MCP bridge runtime branch; reserved-claim refusal |
| B6 | README rule and docstrings |
| B7 | E2E; benchmarks |

**PR1c:** precondition (cutoff passed); delete the bridge, the legacy
branches and the `ServiceControl` token fields; refresh the contract
fingerprint.

---

## 11. Decisions

- **D1** Claims layout (§2); `sub`/`credential_kind`/`service_id` names kept
  so pre-PR1b pods validate new tokens.
- **D4** One global authority epoch, random 64-bit, equality only, bumped
  once per changing transaction at commit by database triggers (Jack,
  2026-10-30). Rows are keyed by subject, but no per-subject versions or
  hooks are built.
- **D5** Facts from the database, never `shared/role_cache`;
  `load_runtime_principal` stays the in-process path and the parity oracle.
- **D6** Agent runs: no in-process token; the MCP bridge mints per call; no
  refresh token.
- **D7** `POST /auth/refresh` dispatches on `type`; run refresh skips the
  per-IP limiter.
- **D8** Run refresh tokens are reusable, unrotated and stateless; validity is
  liveness, with no `exp` (SC3).
- **D9** Service tokens use standard lifetimes; the lease is enforced by the
  per-request liveness read; parent rotation is deleted. This supersedes SC1's
  lease-length tokens with stronger, immediate fencing.
- **D10** Legacy tokens are bridged with an enforced cutoff (§8.3).
- **D11** Refresh required: 401 + `WWW-Authenticate: Bearer
  error="invalid_token", error_description="refresh_required"`.
- **D12** Completeness: database triggers, registry ↔ triggers, read
  capture, maintenance scan (§9.2).
- **D13** `sol_active` answers the run's own `?solution=` gate.
- **D14** Impersonation end changes no version (§3.6). This supersedes SC2.
- **D15** No per-subject fan-out; every carried fact bumps the one epoch.
- **D17** Run refresh and refreshed access tokens are redacted; nested service
  tokens are scrubbed from the diagnostic copy.
- **D18** Delivery PR1a → PR1b → PR1c; evidence is the durable first start of
  each protocol (§8.2).
- **D19 (PR2 decision; used from PR1)** The `usr` block is zlib +
  base64url in every token, and the size warning measures the encoded token.
- **D20** The SDK allows up to 3 refresh-and-retry cycles for
  `refresh_required`; one for other 401s.

### Decisions that change user-visible behaviour

- **D20:** a workflow SDK call may refresh up to three times during an
  authority-change burst instead of failing after one.
- **D9 / §5.4:** a service child on an **older remote launcher** at the PR1b
  upgrade stops receiving rotated tokens. Its 15-minute legacy token expires,
  `service-control` returns 401, and the service restarts once under its
  restart policy on a current launcher. Local-worker services are served by
  their own parent and are unaffected.
- **D10:** legacy run tokens stop working 24 h 5 min after PR1b first starts,
  even if a nonstandard configuration gave them a longer `exp`.
- **D14:** impersonation end doesn't make runs refresh (revision 3 did; there
  is no outcome change either way).
