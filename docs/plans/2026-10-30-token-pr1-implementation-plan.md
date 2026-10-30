# Token PR1 — Runs and Shared Pieces: Implementation Plan

Spec: `docs/superpowers/specs/2026-10-27-access-token-claims-design.md` (approved).
Branch: `token/pr1-runs`, based on `origin/main` `ad623e250`. Line numbers below
are at that commit. All paths are relative to the repository root.

PR1 delivers, per spec §10: `issue_access_token`, the claims schema, the
not-before stamps (table, migration, Redis copy), run refresh tokens, and the
README rule. Every runtime kind (workflow execution, service attempt, agent run)
switches to claims. People stay on today's path until PR2.

Decisions are numbered `D1`… and collected in §10. Open questions are `Q1`….

---

## 1. Scope boundary

### 1.1 Mint sites

| Site | Today | PR1 | PR2 / unchanged |
|---|---|---|---|
| `api/src/core/security.py:458` `mint_engine_token` | `{sub, credential_kind}`, lifetime timeout+300 s | **Removed.** Replaced by `issue_access_token` + `issue_run_refresh_token` | — |
| `api/src/jobs/consumers/workflow_execution.py:933-937` (caller) | mints engine token | **PR1**: mint access + run refresh token (§4) | — |
| `api/src/core/security.py:426` `mint_service_token` | `{sub, credential_kind, service_id}`, 900 s | **Removed** | — |
| `api/src/services/service_claim.py:463` `_rotate_token` | every heartbeat | **PR1**: `issue_access_token` every heartbeat (D9) | — |
| `api/src/services/service_claim.py:691` `_launch_claimed` | at claim | **PR1**: access + run refresh token | — |
| `api/src/services/mcp_server/tools/_http_bridge.py:75-86` runtime branch | hand-built `{sub, credential_kind, service_id}` | **PR1**: `issue_access_token` (D6) | — |
| `_http_bridge.py:88-104` human branch | hand-built human claims | — | PR2 |
| `api/src/routers/auth.py:684` (`mfa_initial_verify`, def 580), `:836` (`_generate_login_tokens`, def 799; used by login, MFA, device, invite, `routers/passkeys.py:207`), `:1126` (`/auth/refresh`), `:1892` (`setup_passkey_verify`) | hand-built human claims | — | PR2 |
| `api/src/routers/oauth_sso.py:423`, `api/src/routers/mfa.py:254` | human | — | PR2 |
| `api/src/services/mcp_server/auth.py:509, :564` (MCP OAuth; refresh JTI discarded at :510, :565) | human | — | PR2 |
| `api/src/core/security.py:245` `create_embed_access_token` (spec calls it `create_embed_token`), used `routers/embed.py:161,196,311` | embed | — | unchanged (spec §7) |
| `api/shared/browser_impersonation.py:90` marker | 30 min | — | unchanged format; end-stamp in PR2 (D14) |
| `api/src/core/security.py:179` `create_mfa_token` (type `mfa_*`) | not an access token | — | unchanged |
| `api/shared/solution_dev.py:121` (type `DEV_CONTEXT_TYPE`), `api/src/services/oauth_state_core.py:79` (OAuth state) | not access tokens | — | unchanged |
| `api/src/core/security.py:57` `create_access_token`, `:98` `create_refresh_token` | primitives | unchanged; the minter passes `iat`/`jti` in `data` and an `expires_delta` computed from `iat` | PR2 |

### 1.2 Validation and authority sites

| Site | PR1 change | Left for PR2 / unchanged |
|---|---|---|
| `api/src/core/app_wiring.py:225-246` request middleware | Runtime branch calls `authenticate_runtime_token` (no DB session). After `call_next`, awaits in-flight stamp copies (§2.5) | human payload attribution unchanged |
| `api/src/core/auth.py:135` `_get_current_user_optional` (runtime branch 184-192) | Uses the middleware's `RuntimeAuthResult`, or calls `authenticate_runtime_token`. Legacy runtime payloads (no `ver`) return `None` | human branch 194-280 → PR2 |
| `api/src/core/auth.py:343` `get_current_user` | Raises 401 with `WWW-Authenticate: Bearer error="invalid_token", error_description="refresh_required"` when the result is `REFRESH_REQUIRED` (D11) | — |
| `api/src/core/auth.py:401` `get_execution_context` | `?solution=<uuid>` equal to the runtime principal's own Solution uses claim `run.sol_active` instead of `db.get(Solution)` (448-465). The role read at 430-433 is skipped when `user.runtime_authority is not None` (claims already carry the actor's roles; today an actor with zero roles re-reads them every request) | `X-Bifrost-App` gate (472-495), dev context, non-runtime `?solution=` → PR2 |
| `api/src/core/auth.py:39` `select_http_auth_token`, `:591` `get_current_user_ws` | unchanged (new runtime tokens keep `credential_kind`, so precedence and WS refusal still hold) | — |
| `api/src/services/mcp_server/auth.py:661` `verify_token` (runtime branch 689-745) | `authenticate_runtime_token`; tool-filter roles from claim `usr.roles[].name` instead of `_get_user_roles` DB read | human branch → PR2 |
| `api/src/routers/auth.py:999` `/auth/refresh` | Adds the `run_refresh` branch (§4) | human branch → PR2 |
| `api/src/services/authorization/runtime_auth.py:34` `load_runtime_principal` | Unchanged signature; stays the in-process DB path and the parity oracle (D5) | — |
| In-process runtime principals: `jobs/consumers/agent_run.py:274,376`, `services/execution/autonomous_agent_executor.py:1367`, `services/authorization/callers.py:58` `refresh_caller`, `services/mcp_server/authorization.py:22` `load_mcp_caller` | unchanged (no token crosses a process boundary; D6) | — |
| `api/src/services/authorization/enforce.py:118` `load_caller` | unchanged for runtime (already reads `runtime_authority.run_user.ctx`) | human branch's `build_authorization_context` → PR2 |
| `api/shared/browser_impersonation.py:130` `validate_browser_impersonation` | — | PR2 |

Not in PR1: policy evaluation's own data reads, logout/revoke-all stamps,
people lifetimes and cookies, MCP OAuth refresh-token JTI handling, the human
`roles` claim shape (`["authenticated", *names]` today).

### 1.3 Spec-vs-code gaps (from `token-spec-gaps-20261029.md`, re-verified at `ad623e250`)

| Gap | Disposition |
|---|---|
| Spec name `create_embed_token` vs real `create_embed_access_token` (`security.py:245`) | Allowlisted under its real name in the one-minter test |
| Agent runs mint no JWT (synthetic payload + `load_runtime_principal`); MCP bridge mints | D6 |
| Run refresh endpoint unnamed; `/auth/refresh` needs `type=refresh` + Redis JTI; worker puts the engine token in both env slots (`worker.py:51-52`) | D7, D8 |
| Service lifetime (900 s) vs 60–90 min | D9 |
| No `iat`/`jti` today; 1 s `iat` vs stamp ambiguity | D2, D3 |
| Warm marker key | D4 (`bifrost:authz:nbf:_warm`) |
| Cookie 4096 B limit; cookie max_age 30 min (`auth.py:126`); MCP `expires_in` 1800 (`mcp_server/auth.py:518,571`) | PR2; Q1 |
| MCP refresh JTI discarded (`mcp_server/auth.py:510,565`) | PR2 |
| Other mint sites to allowlist | §8.4 allowlist |
| Browser App/Solution gates read rows (`core/auth.py:459,490,493`) | Runtime own-Solution gate → PR1 (claim); rest → PR2 |
| `build_authorization_context` docstring says 2 queries, runs 3 (`context.py:120`) | Docstring fix in T5 |
| `roles` claim `["authenticated", *names]` vs "role IDs and names" | Runtime claims carry ids + names (§2.1). Human shape → PR2 |
| Completeness-test mechanism unspecified | D12 |

---

## 2. Data model

### 2.1 Claims schema (`ver: 1`)

Pydantic models in `api/src/services/authorization/tokens.py` (internal; not an
HTTP contract, so not under `models/contracts/`). `model_config =
ConfigDict(frozen=True, extra="forbid")`. Keys are short to save bytes.

```text
iss, aud                      str (settings.jwt_issuer / jwt_audience)
type                          "access"
ver                           1                     # claims schema version (new-format marker)
iat                           int (epoch s, floor)  # see D2
exp                           int
jti                           str (uuid4)
sub                           str                   # runtime: credential id (unchanged meaning)
credential_kind               "workflow_execution" | "service_attempt" | "agent_run"
service_id                    str | absent          # service_attempt only (name kept, D10)
usr: {                        # the run user (people tokens in PR2 reuse this block)
  id: UUID, email: str, name: str, kind: str | null (identity_kind),
  org: UUID | null (home org), ext: bool, prov: bool (home org is_provider),
  active: bool,
  base: { id: UUID, perm: [str] },                       # base role + its permissions
  roles: [ { id: UUID, name: str, perm: [str],
             bnd: [ [kind: "organization"|"managed_organizations"|"platform", org: UUID|null] ] } ]
}
act: { id, email, name, org, ext, prov, roles: [ {id, name} ] } | absent   # acting user ≠ run user
run: {
  org: UUID | null,              # RuntimeAuthority.organization_id
  wf: UUID | null, sol: UUID | null, sol_active: bool | null,
  root: UUID | null, by: UUID | null,
  powers: { mode: "full"|"restricted", grants: [str] } | null
}
imp: { by: UUID, sid: UUID } | absent
```

Derivations in `principal_from_claims` (exactly today's
`runtime_auth._principal_from_authority`, `runtime_auth.py:66-133`):

- `AuthorizationContext(user_id=usr.id, home_organization_id=usr.org,
  base_role_id=usr.base.id, is_external=usr.ext,
  base_permissions=frozenset(usr.base.perm), role_grants=` roles with a
  non-empty `bnd`, sorted by role id`)`. `build_authorization_context`
  (`context.py:117`) only yields roles that have boundary rows; roles with no
  boundary stay in `usr.roles` for ids/names only.
- `RunUser(usr.id, ctx, usr.kind, usr.active)`.
- Actor = `act` if present else `usr`. `UserPrincipal.role_ids/role_names/roles`
  = actor's roles; `is_superuser = PLATFORM_ADMIN_ROLE_ID in actor role ids`;
  `is_external`, `is_provider_org`, `email`, `name` from the actor;
  `organization_id = run.org`; `is_active=True`, `is_verified=True`.
- `RuntimeAuthority` from `credential_kind`, `sub`, `run.*`, `service_id`,
  `imp.*`, plus the new field `solution_active` (D13).
- `Powers(mode, tuple(WorkflowGrant.model_construct(permission=p)))` — signed
  claims are not re-validated per request.

**Size** (measured with synthetic payloads shaped like the spec's production
numbers; script reproduced in T4's test fixture):

| Shape | JWT bytes |
|---|---:|
| Typical run (2 roles, 2 boundaries, same actor) | 1,819 |
| Production's largest user (12 roles, 14 boundaries, 29 permissions), same actor | 4,444 |
| Largest user + distinct 12-role actor + impersonation + 10 workflow grants | 6,308 (warns) |
| Pathological: 20 roles, 30 boundaries, distinct actor, 29 grants | 10,115 (warns) |

Max for production data today ≈ 4.4 KB. Minting logs a warning above
6,144 bytes and never refuses (spec §3). Runtime tokens travel only in
`Authorization` headers (worker Unix socket has no proxy limit; network calls
pass the ingress 8 KB header limit up to the pathological case).

### 2.2 Not-before table and migration

ORM `api/src/models/orm/authz_not_before.py`, registered in
`api/src/models/orm/__init__.py`:

```python
class AuthzNotBefore(Base):
    __tablename__ = "authz_not_before"
    subject_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("subject_kind IN ('user','role','organization','run','workflow','solution','impersonation')",
                        name="ck_authz_not_before_subject_kind"),
        Index("ix_authz_not_before_not_before", "not_before"),
    )
```

Migration `api/alembic/versions/20261030_authz_not_before.py`: `create_table` +
index; `downgrade` drops both. All seven spec subject kinds are allowed now so
PR2 needs no migration. One row per subject (upsert keeps the newest time).

### 2.3 Redis keys

Helpers in `api/src/core/cache/keys.py` (repo convention):

| Key | Value | TTL |
|---|---|---|
| `bifrost:authz:nbf:<kind>:<uuid>` | stamp as epoch float string | `EXAT ceil(stamp) + 5400` |
| `bifrost:authz:nbf:_warm` | epoch of last full re-copy | none (lost only on Redis restart/flush) |
| `bifrost:authz:nbf:_resync` | lock owner | `NX EX 30` |

Max-merge copy (Lua, via `register_script`) so out-of-order copies never lower
a stamp:

```lua
local cur = redis.call('GET', KEYS[1])
if (not cur) or (tonumber(cur) < tonumber(ARGV[1])) then
  redis.call('SET', KEYS[1], ARGV[1], 'EXAT', ARGV[2])
end
return 1
```

### 2.4 Writing a stamp (Postgres, transactional)

`api/src/services/authorization/not_before.py`:

```python
class SubjectKind(StrEnum): USER, ROLE, ORGANIZATION, RUN, WORKFLOW, SOLUTION, IMPERSONATION
@dataclass(frozen=True, order=True)
class Subject: kind: SubjectKind; id: UUID

COMMIT_MARGIN_SECONDS = 2.0      # D3
STAMP_WINDOW_SECONDS = 5400      # longest access-token lifetime

def stamp_not_before(session: AsyncSession, *subjects: Subject) -> None
def install_not_before_listeners() -> None    # idempotent, like install_solution_write_guard
```

- `stamp_not_before` only records subjects in
  `session.sync_session.info["authz_nbf_pending"]` (a set). It does no I/O.
- A session-wide `before_commit` listener (installed next to
  `install_solution_write_guard` in `api/src/core/database.py:163-166`) runs,
  when the set is non-empty:
  1. `S = time.time() + COMMIT_MARGIN_SECONDS`;
  2. one `INSERT … VALUES (sorted subjects) ON CONFLICT (subject_kind,
     subject_id) DO UPDATE SET not_before = GREATEST(authz_not_before.not_before,
     EXCLUDED.not_before) RETURNING subject_kind, subject_id, not_before`
     (sorted to avoid lock-order deadlocks);
  3. prune: `DELETE FROM authz_not_before WHERE ctid IN (SELECT ctid FROM
     authz_not_before WHERE not_before < now() - interval '5400 seconds' FOR
     UPDATE SKIP LOCKED LIMIT 500)` (never waits on another writer);
  4. moves the returned rows to `info["authz_nbf_committing"]`.
  Writing at `before_commit` keeps stamp-to-commit latency to the commit round
  trip, even for long transactions (Solution deploy/uninstall). The session
  factory sets `autoflush=False`; pending ORM objects still flush after the
  hook inside the same `commit()`.
- `after_commit`: hands `info["authz_nbf_committing"]` to
  `_schedule_copy(rows)`; `after_rollback` / `after_soft_rollback` clear both
  keys. A stamp registered inside a rolled-back savepoint is still written at
  the outer commit (conservative: one extra refresh).
- The hook runs only for `AsyncSession`/ORM commits. Core-only
  `engine.begin()` writes cannot stamp; the write-path tripwire (§8.3) flags
  them.

### 2.5 Copying to Redis, and Redis failure

- `_schedule_copy` creates an `asyncio` task (strong reference in a module
  set `_inflight_copies`) that pipelines the Lua script per row.
- `async def wait_for_stamp_copies(timeout=1.0)`: the request middleware awaits
  it after `call_next` and before returning, so a client never sees the
  response to a change before Redis holds its stamp. Non-HTTP writers
  (consumers, schedulers, claim loop) don't wait.
- **Any** Redis failure observed by a process — a failed copy, or a failed
  stamp read after its one retry — sets `_warm_reset_pending` and starts (once
  per process) `_reset_warm_marker()`, which retries `DEL
  bifrost:authz:nbf:_warm` every 2 s until it succeeds or 5400 s pass. Deleting
  the marker sends every process to the cold path (Postgres + re-copy), so a
  stamp lost during an outage is enforced from the next request after Redis
  returns. This is the spec's "re-copy" made reachable when Redis was down at
  the time (D4).

### 2.6 Checking a token (per request)

```python
async def check_not_before(subjects: frozenset[Subject], iat: int) -> Verdict  # ACCEPT | REFRESH_REQUIRED
```

1. `MGET _warm, k1..kn` (one round trip; n ≈ 5–30). On error, retry once.
2. Warm marker present → refuse iff `iat < float(v)` for any subject value
   (D2); missing subject keys mean "no stamp".
3. Marker missing, or both attempts failed → one indexed Postgres query in a
   short session from `get_session_factory()`:
   `SELECT max(not_before) FROM authz_not_before WHERE (subject_kind,
   subject_id) IN (…)`; refuse iff `iat < max`.
4. Marker missing and Redis reachable → `SET _resync NX EX 30`; the winner
   spawns a background re-copy: select rows with `not_before > now() - 5400 s`,
   pipeline the Lua script, then `SET _warm <now>`.

Consistency summary: a committed change always has its Postgres row (same
transaction). Redis converges by the post-commit copy; a lost copy forces the
cold path everywhere (warm-marker reset). A Redis outage costs one indexed
query per runtime request, never an unchecked token (spec §5).

---

## 3. The single minter

`api/src/services/authorization/tokens.py`:

```python
@dataclass(frozen=True)
class RuntimeSubject:
    kind: RuntimeKind            # from services/authorization/runtime.py
    credential_id: UUID
    service_id: UUID | None = None

@dataclass(frozen=True)
class IssuedAccessToken:
    token: str
    expires_at: datetime
    claims: AccessTokenClaims

class TokenSubjectUnavailable(RuntimeIdentityError): ...

async def issue_access_token(db: AsyncSession, subject: RuntimeSubject) -> IssuedAccessToken
def issue_run_refresh_token(subject: RuntimeSubject, *, timeout_seconds: int | None) -> str
def principal_from_claims(claims: AccessTokenClaims) -> UserPrincipal
def token_subjects(claims: AccessTokenClaims) -> frozenset[Subject]
async def authenticate_runtime_token(payload: dict[str, Any]) -> RuntimeAuthResult | None
```

PR2 widens `subject` to a union with `PersonSubject`; PR1 code paths take only
`RuntimeSubject`.

### 3.1 Facts loaded (one read pass)

1. `load_runtime_authority(db, kind, credential_id, service_id=…)`
   (`runtime.py:208`) — the same liveness rules as today; raises
   `RuntimeIdentityError` (wrapped as `TokenSubjectUnavailable`). T5 tightens
   its full-entity loads to `load_only` of the used columns
   (`ServiceAttempt`, `ServiceDefinition`, service-path `Workflow`, `AgentRun`,
   `Agent`, `list_workflow_grants`, `workflow_permission_requests`) so the read
   set equals the carried facts (needed by D12), and adds two fields to
   `RuntimeAuthority` (`runtime.py:191`): `solution_active` (one
   `select(Solution.status)` when `solution_id` is set; D13) and
   `lease_expires_at` (service attempts only, for D9; `None` otherwise and for
   claim-built authorities, and excluded from the parity comparison).
2. Run user's role list with names/permissions/boundaries: one query joining
   `UserRole`→`Role` plus the boundary/permission rows already loaded by
   `build_authorization_context`; **not** `shared/role_cache.get_user_roles`
   (D5).
3. Run user identity (`email`, `name`, `organization_id`, `is_external`,
   `identity_kind`, `is_active`) and home org `is_provider`; actor identity +
   org + roles when `acting_user_id != run_user.user_id`.

### 3.2 `iat`, `jti`, `nbf` (D2, D3)

```text
for attempt in 1..3:
    t0 = time.time()
    facts = load (3.1)
    subjects = token_subjects(facts)
    newest = SELECT max(not_before) … for subjects        # Postgres, same session
    if newest is None or floor(t0) >= newest: break
    await asyncio.sleep(newest - t0 + 0.05)               # ≤ ~3 s, only right after a stamp
iat = floor(t0); jti = uuid4(); no `nbf` claim
```

The loop never rolls back the caller's transaction (the service claim mints
inside its uncommitted claim); facts use `populate_existing=True` so a re-read
sees newly committed rows. After three attempts it mints with `iat=floor(t0)`;
that token is refused on use and the client refreshes (safe direction).

Why this is correct: a change that commits after `t0` has `S ≥ commit ≥ t0 ≥
iat` (given stamp→commit latency + clock skew < 2 s), so the token is refused.
A change committed before `t0` is in `facts`; if its stamp `S > floor(t0)` the
loop waits until `floor(t0) ≥ S`, so a fresh token is never refused by a stamp
on a fact it already reflects. The SDK retries once after refresh, so this
wait is what prevents a refresh→401→fail loop.

`decode_token` (`security.py:146`) passes `options={"verify_iat": False}`:
`iat` is compared to stamps by our code, and PyJWT would otherwise reject a
token whose `iat` is ≥1 s ahead of the validating host's clock. `exp`, `iss`,
`aud`, signature are still verified. Tokens without `iat` are unaffected.

### 3.3 Lifetimes

| Kind | Access token | Run refresh token |
|---|---|---|
| `workflow_execution` | `3600 + secrets.randbelow(1801)` s | `exp = effective_timeout + 300` (today's `mint_engine_token` bound; `NO_TIMEOUT_TOKEN_SECONDS` for timeout 0) |
| `service_attempt` | until the attempt's `lease_expires_at` at mint (≈ `lease_ttl_seconds` = 60 s), re-minted every heartbeat (D9) | no `exp`; valid while the attempt is live (D8) |
| `agent_run` (MCP bridge) | `3600 + secrets.randbelow(1801)` s | none (D6) |

`issue_access_token` asserts lifetime ≤ `STAMP_WINDOW_SECONDS`. Size > 6,144
bytes → `logger.warning("access token %s bytes for %s %s", …)` without claim
contents.

### 3.4 Run refresh token

`{iss, aud, type: "run_refresh", ver: 1, iat, jti, sub, credential_kind,
service_id?, exp?}` — identity only, no facts, no DB read. Accepted only by
`/auth/refresh`; `decode_token(expected_type="access")` rejects it, and the
human refresh branch rejects it by type.

---

## 4. Run refresh

- **Route:** existing `POST /auth/refresh` (`routers/auth.py:998`), D7. Request
  `TokenRefresh` and response `Token` are unchanged (both in
  `routers/auth.py:252,296`), so no new contract model and no CLI fingerprint
  change.
- **Dispatch:** decode without `expected_type`; `type == "refresh"` → today's
  human branch, unchanged (rate limit, JTI rotation, cookies). `type ==
  "run_refresh"` → require `ver == 1`; build `RuntimeSubject`; `issued = await
  issue_access_token(db, subject)`; `TokenSubjectUnavailable` → 401 `"Run is
  no longer live"`; return `Token(access_token=issued.token,
  refresh_token=<the same run refresh token>)`. No cookies. No per-IP
  `auth_limiter` (every child of a worker shares its pod IP; the token is
  server-signed and liveness-checked). The impersonation-cookie check stays
  first and is irrelevant to the SDK.
- **Semantics:** not single-use, not rotated (D8). Revoked by run end (the
  minter's liveness check) — the same moment today's engine token stops
  working.
- **Worker env** (`services/execution/worker.py:37-52`): context key
  `engine_refresh_token` → `BIFROST_REFRESH_TOKEN`; `engine_token` →
  `BIFROST_ACCESS_TOKEN`. Add `engine_refresh_token` to
  `_CONTEXT_REDIS_EXCLUDED_KEYS` (`process_pool.py:82`) and register it with
  the engine's output redaction next to `engine_token` (`worker.py:297`,
  `engine.py:324`). Refreshed access tokens are registered with the same
  redaction set by the SDK's `install_access_token` hook (T8).
- **Services:** `context["service"]` gains `refresh_token`. Split
  `bifrost/_service_runtime.install_service_credentials` into
  `install_service_credentials(access_token, refresh_token)` (start,
  `worker.py:419-421`, `engine.py:950-952`) and
  `install_rotated_service_access_token(access_token)` (supervisor,
  `engine.py:1082`) which leaves `BIFROST_REFRESH_TOKEN` alone. The nested
  `service.token`/`service.refresh_token` are scrubbed from the diagnostic
  Redis context copy (`process_pool.py:1159`).
- **SDK** (`api/bifrost/client.py`): engine-local requests now refresh on 401
  over the network `/auth/refresh` and replay once over the socket (both
  `_request_with_refresh_sync` ~965 and `_request_with_refresh` ~1007; rebuild
  the engine client after `install_access_token`). Update the two docstrings
  that say engine-local never refreshes. `refresh_connection_access_token`
  (`client.py:267`) and `save_refreshed_credentials` (`credentials.py:645`)
  already handle an unchanged refresh token in the process backend.
- **Services keep** receiving tokens via `/api/sdk/runtime/service-control`
  (`shared/runtime_io.py:46`), now in the new format; its 401→stop behaviour
  (`engine.py:1069`) is unchanged, but the SDK client refreshes a
  stamp-refused token before that 401 surfaces.

---

## 5. Repository authority rule

**Rule (spec §8):** claims in an access token minted by `issue_access_token`
are authority until the token expires or a not-before stamp covers it.

Enforcement points:

1. `authenticate_runtime_token` is the only function that turns claims into a
   principal. It requires a valid signature (`decode_token`), `type ==
   "access"`, `ver == 1`, strict `AccessTokenClaims` validation, and an
   `ACCEPT` from `check_not_before`. A runtime-shaped payload without `ver`
   (`is_runtime_credential_payload`, `runtime_auth.py:29`) is refused; there
   is no DB fallback (D10).
2. `principal_from_claims` is imported only by `tokens.py` and tests; an AST
   test (`test_one_minter.py`) asserts it.
3. Nothing else becomes authority: `MCPContext` flags (`load_mcp_caller`),
   request headers/bodies, role cache entries, and human tokens (until PR2)
   keep today's rules.
4. `api/src/repositories/README.md` "Authority model" paragraph and rule 1 of
   "Implementation rules" change to the rule above ("…from request data; the
   only serialized authority is a token minted by `issue_access_token`").
   The "`RuntimeAuthority` is loaded from the current database state"
   paragraph becomes: built from claims that `issue_access_token` loaded from
   current database state at mint/refresh; not-before stamps force a re-mint.
   `CLAUDE.md`/`AGENTS.md` org-scoping sections need no change.
5. Tests pinning the old rule change with it:
   `tests/unit/authorization/test_runtime_auth.py::test_runtime_principal_uses_database_authority_not_spoofed_token_claims`
   becomes three tests (legacy runtime payload refused; `ver:1` claims are the
   authority with zero SQL; a tampered token fails signature). Docstrings that
   restate the old rule: `runtime_auth.py:1,37`, `runtime.py:1-5`,
   `core/principal.py:22-24,63-82`, `services/mcp_server/server.py:143-145`.

---

## 6. Revocation paths in PR1

Every write registers `stamp_not_before(session, …)` in the transaction that
makes the change (§2.4), in the service/repository function, next to the
existing `role_cache`/`invalidate_*` calls where they exist. Router writes that
change a carried fact move into a service function first (spec: "Not in
routers"): `routers/executions.py:853,863`, `routers/agent_runs.py:645,1132`,
`routers/solutions.py:2242`, `routers/profile.py:93`.

| Change | Subject(s) | Write functions (initial list; §8.3 tripwire is authoritative) |
|---|---|---|
| Role permissions | `ROLE` | `services/role_permissions.py:57` `set_role_permissions` |
| Role rename / delete | `ROLE` (+ `USER` per holder on delete) | `shared/sdk_roles.py` update (~440-470), delete (~520-545) |
| Assignments / boundaries | `USER` | `services/user_role_assignments.py:140` `insert_assignment`, `:524` `replace_role_assignments` (incl. `:649,:656` deletes), `shared/sdk_roles.py` assign (~780-803) / remove (`:822`, delete at `:863`), `shared/sdk_users.py:152` |
| Base role, org move, external flag, active flag, email, name, delete | `USER` | `shared/sdk_users.py:92,648,653,662,673,677,936,1001` and the user delete path; `routers/profile.py:93` name (moved to a service) |
| Org provider flag | `ORGANIZATION` | no writer today (tripwire keeps it that way or forces a stamp) |
| Workflow powers / ownership / delete | `WORKFLOW` | `services/workflow_permissions.py:214` `sync_solution_permission_requests` (incl. `:247`, `:252`, request rows), `services/solutions/deploy.py:1056` (`permission_mode`), Solution capture/deploy writes of `Workflow.solution_id` (`capture.py:220,265`; deploy upserts), workflow row deletes |
| Platform default workflow mode | `WORKFLOW` for every workflow with `permission_mode IS NULL` | no writer today (`SystemConfig` category `workflow_permissions` is read-only in app code); tripwire forces stamping if one is added |
| Solution deactivate / reactivate / hard delete | `SOLUTION` | `routers/solutions.py:2242` (moved to a service), `services/solutions/zip_install.py:535`, hard-delete service |
| `Agent.solution_id` change | `RUN` for each live agent run of that agent (D15) | Solution capture/deploy agent writes |
| **Run end** — execution leaves `PENDING/RUNNING/CANCELLING` | `RUN` | `repositories/executions.py:191` `update_execution` (when the new status is not live), `routers/executions.py:853,863` (moved), `jobs/schedulers/execution_cleanup.py:186`, `services/worker_pool/recovery.py:94,188` (add `.returning(Execution.id)`), `services/legacy_broker_drain.py:177` (returning), `services/execution/workflow_dispatch.py:258`, `shared/run_cancellation.py:27`, `shared/sdk_workflow_execution.py:776`, `jobs/schedulers/deferred_execution_promoter.py:193` (only if its source status can be live) |
| **Run end** — agent run leaves `running/cancelling` | `RUN` | `jobs/consumers/agent_run.py` (13 status writes), `routers/agent_runs.py:645,1132` (moved), `services/execution/agent_run_dispatch.py:73`, `services/worker_pool/recovery.py:119`, `services/legacy_broker_drain.py:246`, `services/chat_runs.py:543`, `services/agent_executor.py`, `services/execution/autonomous_agent_executor.py` (sub-run completion) |
| **Run end** — service attempt leaves `starting/running/stopping` | `RUN` | `services/service_lifecycle.py:812` `complete_attempt`, `:922` `expire_leases` |

Not stamped in PR1 (no run behaviour depends on them; parity): logout,
revoke-all, impersonation end (PR2, D14). Lease renewals
(`ServiceAttempt.lease_expires_at` extended) are liveness-only and not carried.
Creation of a run row in a terminal state needs no stamp (no token exists yet).

---

## 7. Compatibility and rollout

- **New tokens on old API pods** (rolling deploy): new runtime tokens keep
  `sub`, `credential_kind`, `service_id`, so old pods resolve them from the DB
  as today. Old pods reject `run_refresh` at `/auth/refresh`; refresh is only
  needed after a stamp refusal or a 60–90 min expiry, neither of which an old
  pod produces during a rollout window.
- **Old tokens on new pods** (D10): refused. Impact, once, at the PR1 deploy:
  a run started by an old-build worker that makes a *network-path* SDK call to
  an upgraded API pod fails with 401 (it cannot refresh: its refresh slot
  holds the old access token). Worker-socket calls are served by the run's own
  (old) worker parent and are unaffected; services use the socket for
  `service-control`. Runs on remote launchers (`sdk_socket_path=None`,
  `worker/remote/launcher.py:379`) use the network for every call, so their
  in-flight runs at deploy time fail on their next call. See Q2.
- **Migration** runs in the init container before the API starts; an empty
  table is valid. No backfill.
- **CLI/SDK contract:** `Token`, `TokenRefresh`, `ServiceControlRequest/
  Response` unchanged → `EXPECTED_CONTRACT_FINGERPRINT` unchanged, no
  `MIN_CLI_VERSION` bump (`shared/version.py:14`). The SDK behaviour change
  (engine-local refresh) only runs inside worker children, which ship in the
  same image. Run `./test.sh tests/unit/test_contract_version.py` and
  `tests/unit/test_dto_flags.py` to confirm.
- **MCP thin-wrapper rule:** no tool handler changes. `_http_bridge.py` is
  infrastructure outside `PARITY_HANDLERS` (`tests/unit/test_mcp_thin_wrapper.py`)
  and may import `tokens.py`; confirm the test still passes.
- **Product updates:** internal plumbing; propose `omit` in
  `product-updates/dispositions/pr-<n>.json` at PR time.

---

## 8. Test plan

Backend tests run with `./test.sh` (not run while the capacity benchmark
holds the host). Existing helpers: `db_session` (`tests/conftest.py:128`).

### 8.1 Unit

| File | Contract |
|---|---|
| `tests/unit/authorization/test_not_before.py` (new) | stamp row exists only after commit; rollback leaves none; upsert keeps max; prune skips locked rows; `S = clock + 2 s`; after-commit copy max-merges and sets `EXAT`; `wait_for_stamp_copies` waits; copy failure resets the warm marker (retry loop) |
| `tests/unit/authorization/test_not_before_check.py` (new) | warm hit accept/refuse at the `iat < S` boundary; Redis error → one retry → Postgres; **Redis down**: change during outage enforced via Postgres; **Redis restarted empty** (FLUSHDB): marker missing → Postgres decision + single re-copy under lock + marker set; nothing stamped in the last 90 min lost |
| `tests/unit/authorization/test_token_claims.py` (new) | schema round trip; `extra="forbid"`; `principal_from_claims` derivations; size: production-largest fixture < 6,144 B, warning logged above it (caplog), never refused |
| `tests/unit/authorization/test_issue_access_token.py` (new) | lifetimes per kind (spread 3600–5400 over N samples; service `exp == lease_expires_at`); `iat/jti` present; non-live run → `TokenSubjectUnavailable`; recent stamp → minter waits and `iat ≥ S`; run refresh token shape/`exp` per kind |
| `tests/unit/authorization/test_token_parity.py` (new) | **Parity:** for each kind and scenario of `test_runtime.py` (same actor, distinct actor, full/restricted/inline powers, impersonation provenance, service, agent run), `principal_from_claims(issue…​.claims)` equals `load_runtime_principal(db, …)` field by field (role grants compared as sets) |
| `tests/unit/authorization/test_runtime_auth.py` (changed) | §5.5 three tests; **zero SQL** for a warm-Redis runtime request (`before_cursor_execute` counter, pattern at `test_runtime.py:286`) |
| `tests/unit/authorization/test_not_before_completeness.py` (new) | D12 read side: runs the minter for each kind under a `do_orm_execute` capture; the set of `(table, column)` read equals `NOT_BEFORE_FACTS` keys (fails on a new read or a stale entry) |
| `tests/unit/authorization/test_not_before_write_paths.py` (new) | D12 write side (§8.3) |
| `tests/unit/authorization/test_one_minter.py` (new) | §8.4 |
| `tests/unit/core/test_request_context_middleware_access_checks.py`, `tests/unit/core/test_workflow_operation_usage_middleware.py` (changed) | middleware uses claims, opens no DB session, awaits stamp copies |
| `tests/unit/services/test_worker_sdk_http.py::test_engine_local_401_does_not_refresh_or_replay` (`:1482`, inverted) | engine-local 401 → one network refresh → one replay over the socket |
| `tests/unit/test_service_runtime_sdk.py` (changed) | rotation keeps the refresh slot |
| `tests/unit/test_engine_token_lifetime.py`, `tests/unit/test_service_token.py`, `tests/unit/core/test_security.py` (mint parts) | replaced by `test_issue_access_token.py` |
| ~20 files using `mint_engine_token`/`mint_service_token` (listed by `rg -l 'mint_(engine|service)_token' api/tests`) | switch to `tests/helpers/runtime_tokens.py`: `signed_runtime_token(claims)` (signs prepared claims; tests may sign directly — the one-minter test scans product code only) and a `warm_not_before` fixture that sets the marker |
| auth router unit tests for `/auth/refresh` | `run_refresh` branch: live → new token, same refresh token, no cookies, no IP limit; ended run → 401; `run_refresh` refused as access token and by the human branch |

### 8.2 E2E (`tests/e2e/platform/test_runtime_token_claims.py`, new)

One observable contract per test, real API + worker:

1. **Revocation:** grant a role to a running workflow's run user (live
   `wf-wait`-style workflow polling an SDK read) → the next SDK call is refused
   with `refresh_required`, refreshes, and sees the grant; then revoke → the
   next call refreshes and loses it.
2. **Run end:** take a live execution's minted token (minted in-test via
   `issue_access_token` against the run's row), cancel through the real
   endpoint → the token gets 401 on its next use and `/auth/refresh` with its
   refresh token gets 401.
3. **Long wait:** an access token past `exp` for a live run → 401 → `/auth/refresh`
   with the run refresh token → 200 → call succeeds (cross-process contract;
   lifetime itself is unit-tested).
4. **Workflow powers / Solution uninstall:** deactivate the run's Solution →
   next `?solution=` call refreshes and gets 409, same as today.
5. Existing suites that must pass: `tests/unit/authorization/*`,
   `tests/e2e/platform/test_agent_runtime_boundary.py`,
   `test_chat_runtime_boundary.py`, `test_engine_token_redaction_live.py`,
   `test_solution_permission_runtime.py`, `test_r3a_identity_authorization.py`,
   `tests/e2e/mcp/test_mcp_parity.py`, `tests/e2e/platform/test_sdk_socket_audit_parity.py`,
   service credential e2e tests, `tests/unit/test_contract_version.py`,
   `tests/unit/test_mcp_thin_wrapper.py`.

### 8.3 Write-path tripwire (D12)

AST over `api/src/**` and `api/shared/**` (no alembic, no tests). For each
registered model/column in `NOT_BEFORE_FACTS` it finds:

1. Core DML: `update(M)`, `delete(M)`, `insert(M)` (import aliases resolved:
   `UserORM`, `sql_update`, `pg_insert`), with `.values()` keys (kwargs and
   dict literals) for `update`.
2. ORM constructors of registered association models (`UserRole`,
   `UserRoleBoundary`, `RolePermission`, `WorkflowPermissionGrant`,
   `SolutionWorkflowPermissionRequest`).
3. Attribute assignments `x.<col> = …` for registered mutable columns, in
   functions that reference `M` (name or alias).
4. `session.delete(x)` / `db.delete(x)` in functions that reference a
   registered model.

The enclosing function must call `stamp_not_before` directly, or appear in
`EXEMPT_WRITE_SITES` (`qualified name → reason`, e.g. "inserts a new run row";
"sets a live status"). Stale exemptions fail. Columns registered as
`Immutable(reason)` must have zero update sites.

### 8.4 One-minter test

AST over `api/src`, `api/shared`, `api/bifrost`: every `Call` to
`create_access_token` or `jwt.encode` (aliases resolved) maps to
`path::qualname`. The set must equal:

- `src/services/authorization/tokens.py::issue_access_token`,
  `::issue_run_refresh_token`
- `src/core/security.py::create_access_token` (the `jwt.encode` inside),
  `::create_refresh_token`, `::create_mfa_token`, `::create_embed_access_token`
- `shared/browser_impersonation.py::create_browser_impersonation` (marker)
- `shared/solution_dev.py::_mint_solution_dev_token`,
  `src/services/oauth_state_core.py::encode_state_jwt` (not access tokens)
- **PR1-only, marked `# PR2`:** `routers/auth.py` (`mfa_initial_verify`,
  `_generate_login_tokens`, `refresh_token`, `setup_passkey_verify`),
  `routers/oauth_sso.py::oauth_callback`, `routers/mfa.py::verify_mfa`,
  `services/mcp_server/auth.py::…._token` (both mints, `:509`, `:564`),
  `services/mcp_server/tools/_http_bridge.py::_token_from_context` (human
  branch; the runtime branch moves to `issue_access_token` in T14). PR2
  deletes these entries.

It also asserts `principal_from_claims` is imported only by `tokens.py`.

### 8.5 Benchmark hooks

Capacity harness (`./test.sh bench run --profile baseline --backend fake`,
`scripts/bench/`), on `ad623e250` (before) and the PR head (after), recorded in
`docs/benchmarks/2026-1x-xx-token-pr1.md`:

| Measure | Source | Expectation |
|---|---|---|
| Transactions per run, per class | `stats.py:112` (`xact`) | `wf-sdk` drops ≥ 1 per SDK call (the middleware's runtime-auth transaction, `app_wiring.py:243`); spec target ≈ 5 fewer per call — measure, don't assume. `wf-noop` may rise by ≤ 1 (run-start mint; T8 merges it into the existing org-lookup session) |
| API CPU ms per run | `stats.py:65` | `wf-sdk` down |
| `wf-sdk` 10-at-once p50/p95 | report | down |
| Idle transactions/s | `stats.py:101` | unchanged (services, when present, lose 1 tx/s each from `service-control` polling) |
| SQL statements per runtime request | unit counter (§8.1) | 0 (Redis warm); 1 query (Redis down) |
| API memory | `smaps.py` | no regression |

No class may regress beyond the harness's ±9 % sequential noise.

---

## 9. Task breakdown

One implementer, in order. Each task keeps the suite green, adds its tests,
and stays ≤ ~300 lines of diff. Verification per task: the listed tests plus
`./test.sh quality api`.

| # | Task | Files | Tests |
|---|---|---|---|
| T1 | Table, ORM model, migration | `alembic/versions/20261030_authz_not_before.py`, `models/orm/authz_not_before.py`, `models/orm/__init__.py` | migration up/down in `test_not_before.py` setup; `test_datetime_consistency.py` |
| T2 | Stamp write path: `Subject`, `stamp_not_before`, listeners, prune, Redis copy + in-flight set, warm-marker reset; install in `core/database.py`; keys in `core/cache/keys.py` | `services/authorization/not_before.py`, `core/database.py`, `core/cache/keys.py` | `test_not_before.py` |
| T3 | Read path: `check_not_before`, cold path, resync lock | `not_before.py` | `test_not_before_check.py` |
| T4 | Claims schema, `principal_from_claims`, `token_subjects`, size fixture | `services/authorization/tokens.py` | `test_token_claims.py` |
| T5a | `load_only` tightening in `load_runtime_authority` and the powers loaders; `RuntimeAuthority.solution_active` and `.lease_expires_at`; `context.py:120` docstring | `runtime.py`, `explain.py`, `workflow_permissions.py`, `context.py` | existing `test_runtime.py`, `test_runtime_auth.py`, `test_explain_loading.py` |
| T5b | Minter: `issue_access_token`, `issue_run_refresh_token`, lifetimes, `iat` loop, size warning | `tokens.py` | `test_issue_access_token.py`, `test_token_parity.py` |
| T6 | Stamp writers: roles, assignments, users (+ move `profile.py:93` into a service) | `shared/sdk_roles.py`, `services/role_permissions.py`, `services/user_role_assignments.py`, `shared/sdk_users.py`, `routers/profile.py` + new service fn | per-writer "stamp row written in the same transaction" tests |
| T7 | Stamp writers: workflows, Solutions, `Agent.solution_id` (+ move `solutions.py:2242`) | `services/workflow_permissions.py`, `services/solutions/{deploy,capture,zip_install}.py`, hard-delete service, `routers/solutions.py` | same |
| T8a | Run end: executions + service attempts (+ move `executions.py:853,863`; `RETURNING` on bulk updates) | §6 execution/service rows | same |
| T8b | Run end: agent runs (+ move `agent_runs.py:645,1132`) | §6 agent-run rows | same |
| T9 | Completeness: `NOT_BEFORE_FACTS` registry, read-side capture, write-path tripwire, exemptions | `not_before.py` (registry), tests | `test_not_before_completeness.py`, `test_not_before_write_paths.py` |
| T10 | Validation cutover: `authenticate_runtime_token`; middleware; `core/auth.py` runtime branch, refresh-required header, own-Solution gate; MCP `verify_token` runtime branch; `decode_token` `verify_iat`; legacy runtime payloads refused | `tokens.py`, `core/app_wiring.py`, `core/auth.py`, `core/security.py`, `services/mcp_server/auth.py` | rewritten `test_runtime_auth.py`, middleware tests, zero-SQL test |
| T11 | `/auth/refresh` `run_refresh` branch | `routers/auth.py` | refresh unit tests |
| T12 | Workflow mint cutover: consumer mints access + refresh in the org-lookup session; worker env; redaction; Redis scrub; SDK engine-local refresh; remove `mint_engine_token`; test helper migration (part 1) | `jobs/consumers/workflow_execution.py`, `services/execution/{worker,process_pool,engine}.py`, `bifrost/client.py`, `core/security.py`, `tests/helpers/runtime_tokens.py` | inverted `test_worker_sdk_http.py:1482`, worker credential tests, fork tests |
| T13 | Service mint cutover: `_launch_claimed`, `_rotate_token` (exp = lease), `install_service_credentials` split, nested scrub; remove `mint_service_token`; helper migration (part 2) | `services/service_claim.py`, `bifrost/_service_runtime.py`, `services/execution/{engine,worker,process_pool}.py`, `core/security.py` | `test_service_runtime_sdk.py`, `test_service_principal.py`, service e2e |
| T14 | MCP bridge runtime branch: async mint via `get_db_context()` | `services/mcp_server/tools/_http_bridge.py` | bridge unit test; `test_mcp_thin_wrapper.py`; `tests/e2e/mcp/test_mcp_parity.py` |
| T15 | One-minter test; README rule; docstrings (§5.5) | `tests/unit/authorization/test_one_minter.py`, `api/src/repositories/README.md`, docstrings | `test_one_minter.py` |
| T16 | E2E acceptance (§8.2) | `tests/e2e/platform/test_runtime_token_claims.py` | itself |
| T17 | Benchmark before/after and report | `docs/benchmarks/…-token-pr1.md` | — |

Rationale for order: stamps and their writers (T1–T9) land before validation
switches to claims (T10), so no commit accepts claims that a write could have
made stale without a stamp.

---

## 10. Decisions and open questions

### Decisions

- **D1 Claims layout.** §2.1. `sub`/`credential_kind`/`service_id` keep their
  names (old pods keep working mid-rollout). Run user in `usr`, acting user in
  `act` only when different (spec §3 "acting user's identity facts when
  different"). Role ids and names carried once per role.
- **D2 `iat` and comparison.** Integer `iat = floor(t0)` taken *before* the
  facts are read; refuse iff `iat < S`. No `nbf` claim. PyJWT `verify_iat` off;
  our comparison owns `iat`.
- **D3 Stamp time and placement.** `S = app clock + 2 s`, written in a
  `before_commit` listener so the stamp is the last write before COMMIT. The
  margin covers stamp→commit latency and inter-host skew; the minter waits out
  any stamp newer than `floor(t0)` (≤ ~3 s, rare) so refresh never loops.
- **D4 Redis.** Keys per §2.3; warm marker `bifrost:authz:nbf:_warm`; a process
  that sees any Redis failure deletes the warm marker once Redis answers, which
  forces the spec's Postgres read + 90-minute re-copy everywhere. Copies max-merge
  with `EXAT = ceil(S) + 5400`.
- **D5 Fact source.** The minter reads roles from the database, never from
  `shared/role_cache` (that cache is invalidated before commit at today's
  write sites, so it can hold pre-change data under a fresh `iat`).
  `load_runtime_principal` stays as the in-process path and parity oracle.
- **D6 Agent runs.** No JWT for the in-process agent runtime (unchanged
  `load_runtime_principal`); the only agent-run access token is the MCP
  bridge's, minted per REST call by `issue_access_token`, standard lifetime,
  no refresh token (nothing would hold it). The bridge's human branch is PR2.
- **D7 Refresh route.** Reuse `POST /auth/refresh` and its DTOs; dispatch on
  `type`. The SDK's refresh path is hard-wired to that route
  (`client.py:328`), so no SDK routing change and no contract change. Run
  refresh skips the per-IP limiter.
- **D8 Run refresh semantics.** Multi-use, not rotated, no Redis state;
  revoked by run end through the minter's liveness check. `exp`: workflow =
  today's engine-token bound; service = none (attempts are unbounded;
  liveness + lease decide). Agent runs: none issued.
- **D9 Service lifetime.** Service access tokens expire at the attempt's lease
  expiry at mint (≈ 60 s) and are re-minted on every heartbeat, as today. This
  keeps today's guarantee that a fenced child loses API access when its lease
  lapses, which a 60–90 min token would widen. Cost: one minter pass per
  service per 10 s in the worker parent, replacing the API's per-second
  `service-control` authentication reads.
- **D10 Old-format runtime tokens.** Refused after deploy; no compatibility
  path (CLAUDE.md: no unrequested fallbacks). User impact in §7 and Q2.
- **D11 Refresh-required signal.** 401 with `WWW-Authenticate: Bearer
  error="invalid_token", error_description="refresh_required"` and detail
  "Access token refresh required"; no body schema change. Clients already
  refresh on any 401.
- **D12 Completeness test.** Read side: captured minter reads must equal the
  `NOT_BEFORE_FACTS` registry. Write side: AST tripwire over Core DML,
  association constructors, attribute assignments and deletes on registered
  columns; each site's function calls `stamp_not_before` or carries a reasoned
  exemption. Requires the `load_only` tightening in T5.
- **D13 Solution active claim.** `run.sol_active` (and
  `RuntimeAuthority.solution_active`) lets the runtime's own `?solution=` gate
  answer without a row read; uninstall/reactivate stamps `SOLUTION`.
- **D14 Impersonation subject.** Not checked for run tokens in PR1 and no
  end-stamp writer yet: runtime authority never depended on impersonation
  liveness (`runtime.py:283-296`), so parity is unchanged. The `imp` claims are
  carried. Spec's impersonation-end stamp lands in PR2 with browser markers.
- **D15 Facts with no spec subject.** Spec subjects only; no new kinds.
  `Agent.solution_id` changes stamp `RUN` for the agent's live runs; a platform
  default workflow-mode writer (none exists) would stamp every `WORKFLOW` whose
  `permission_mode IS NULL`; `ServiceDefinition.workflow_id/organization_id`
  are registered immutable (no writer; tripwire enforces).
- **D16 Router writes.** Six router-level writes of carried facts move into
  service functions so stamps never live in routers (spec §5).
- **D17 Token redaction and diagnostics.** The run refresh token and refreshed
  access tokens join the engine's output redaction; the nested service tokens
  are scrubbed from the diagnostic Redis context copy (today only top-level
  `engine_token` is).

### Open questions (behaviour or security)

- **Q1 (PR2, raised now).** Browser cookie limit. A people token for
  production's largest user is ≈ 4,001 bytes; its `Set-Cookie` header is
  ≈ 4,068 bytes, at the ~4,096-byte per-cookie limit browsers enforce. A
  slightly larger role set would be silently dropped by the browser (a sign-in
  loop), well below the spec's 6 KB warning. PR2 needs a decision on cookie-
  bound tokens before switching people over.
- **Q2.** Old-format runtime tokens are refused from the PR1 deploy (D10).
  Runs in flight at that deploy that make network-path SDK calls — all calls
  on remote launchers, a minority on local workers — fail once. Is a one-time
  failure acceptable (optionally deploying in a quiet window), or should Jack
  request a compatibility path for the transition?
