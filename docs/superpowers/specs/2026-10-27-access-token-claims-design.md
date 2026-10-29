# One Token System for People and Runs — Design

Status: **Draft for Jack's review.** Replaces the earlier authority-cache and
runtime-only drafts (same date). Follows the per-request database findings in
`docs/benchmarks/2026-10-27-baseline.md`. Built the way Microsoft Entra ID
works: claims in a signed access token, a refresh token that re-evaluates, and
Continuous Access Evaluation (CAE) for changes that must apply before expiry
(Jack, 2026-10-27: "the same exact way").

## 1. Why

Today people and runs use two different systems, and both read the database
on every request to decide what the caller may do.

| | People (browser, CLI, MCP) | Runs (workflows, services, agent runs) |
|---|---|---|
| Token carries | Identity, org, role names, flags | Only the run's ID |
| Per request | Identity from the token, then the person's grants from the database (`load_caller` → `build_authorization_context`, 2–3 queries) | Everything from the database (`load_runtime_principal`, about 5 queries), plus Solution and Application rows in `get_execution_context` |
| Lifetime | 30-minute access token, 7-day rotating refresh token | The run's timeout plus 300 s (up to about 24 hours); services re-mint every 15 minutes |
| Revoking early | Logout and revoke-all kill only the refresh token; the access token lives out its 30 minutes | The run ending takes effect on the next call, because every call reads the database |
| Minted in | About seven places (`routers/auth.py` ×4, `oauth_sso.py`, `mfa.py`, `mcp_server/auth.py` ×2), each building claims by hand; MCP tokens omit `roles` | `mint_engine_token`, `mint_service_token`, the agent-run paths |

The baseline measured about five transactions per SDK call; with the API on
one CPU that is part of why ten `wf-sdk` runs at once take about 8× longer
than one.

## 2. The model

| Entra ID | Bifrost, for people and runs alike |
|---|---|
| Access token carries the caller's claims; resources validate it locally | The access token carries identity **and** authorization facts (section 3). The API checks signature and expiry and decides from the claims, with no database read |
| Access tokens live 60–90 minutes, picked at random per token | Same: **60–90 minutes, random per token.** The randomness spreads renewals, so tokens issued together (a morning of sign-ins, a burst of runs) don't all expire and renew in the same minute |
| A refresh token gets a new access token, and Entra re-evaluates the caller at that moment | Same. A person's refresh token is today's rotating 7-day token. A run's refresh token identifies the run and lasts for the run's lifetime. Either way, refreshing re-reads current state from the database |
| CAE: after a critical event, resources reject older tokens with a claims challenge; the client refreshes | **Not-before stamps** in Redis (section 5). A change to any fact a token carries stamps that subject; the API refuses older tokens with "refresh required"; the web client, CLI and SDK refresh and retry once |
| Token lifetime is the backstop for a missed event | Same |

## 3. What every access token carries

One claims schema, built by one function from the database:

- **Identity:** user ID, email, name, identity kind, home organization,
  external flag, provider-org flag.
- **Authorization:** the authorization context `build_authorization_context`
  produces today: base role, base permissions, and role grants with their
  boundaries. Role IDs and names.
- **Run (runtime tokens only):** kind, credential ID, service ID, workflow ID,
  Solution ID, root execution ID, started-by user, the acting user's identity
  facts when different from the run user, and the workflow's powers
  (permission mode and grants).
- **Impersonation:** session ID and impersonator, when present.
- `iat`, `exp`, `jti`.

The API builds the same `UserPrincipal`, `AuthorizationContext` and
`RuntimeAuthority` it builds today and decides with the same functions
(`decide`, `decide_for`, `check_target`). Only where the facts come from
changes.

**Size:** measured in production (read-only, 2026-10-28) across 1,703 active
users. The largest user holds 12 roles with 14 boundaries and 29 permissions;
their authorization claims are about 1.8 KB as JSON (median 0.3 KB, p99
0.7 KB). No workflow has permission grants yet. With identity and run claims
and base64 encoding, the largest token is about 4 KB, under the 6 KB limit, so
the claims go in as plain JSON. Minting logs a warning above 6 KB, so growth
is visible well before the usual 8 KB proxy header limit; it never refuses a
token, which would lock the person out. Entra's answer for the oversized
case ("groups overage") is a second path and is not built.

**Visibility:** JWT claims are readable, not encrypted. A person can read
their own grants from their token, and workflow code can read its run user's
grants. Neither can change them, because the token is signed. Entra tokens are
equally readable.

## 4. One minting function

`issue_access_token(db, subject)` replaces every hand-built claims dict: the
login, SSO, MFA, refresh and MCP OAuth paths for people, and the engine,
service and agent-run paths for runs. It reads the database once and returns
the token. Browser impersonation markers and embed tokens keep their own
formats (section 7).

## 5. Revocation: not-before stamps

One rule: **any change to a fact a token carries stamps the affected subject
with the current time.** The API refuses an access token whose `iat` is
earlier than any stamp for its subjects, with a 401 whose error code means
"refresh required".

- **Subjects:** user, role, organization, run, workflow, Solution,
  impersonation session.
- **Postgres holds the stamps; Redis caches them** (Jack, 2026-10-28). A stamp
  is a row in a small `authz_not_before` table (subject kind, subject ID,
  time), written **in the same transaction as the change it records**, so a
  committed change always has its stamp. After commit it is copied to Redis as
  `bifrost:authz:nbf:<subject>:<id>`. Rows and keys older than 90 minutes are
  useless (every older token has expired) and are deleted when new stamps are
  written. This needs one migration.
- **Per request:** one Redis `MGET` for the token's subjects plus a warm marker
  key, replacing the database reads.
- **When Redis can't answer:** retry once; if it still fails, or the warm
  marker is missing because Redis restarted empty, read the stamps for those
  subjects from Postgres (one indexed query) and re-copy the last 90 minutes of
  stamps to Redis. A Redis outage costs one cheap query per request, never an
  unchecked token.
- **Where stamps are written:** in the service or repository write for each
  fact, next to the existing `role_cache.invalidate_user` / `invalidate_role`
  calls for role changes. Not in routers.
- **Completeness:** a unit test enumerates every ORM column the minting
  function reads and fails when a column is added without a stamp on its write
  paths.
- **Clients:** the web client (`client/src/lib/api-client.ts`), the CLI and the
  SDK (`refresh_connection_access_token`) already refresh on 401 and retry.
  The new error code needs no new client flow.

What this does to each change:

| Change | Effect |
|---|---|
| Role granted, revoked or edited | Next request refreshes and gets the new grants (today: immediate, from the database) |
| Logout or revoke-all | Stamps the user; access tokens stop working now (**today: up to 30 minutes later**) |
| Person deactivated | Stamps the user; refresh fails, so they're out now (today: up to 30 minutes later) |
| Run ends | Stamps the run; refresh fails because the run isn't live (today: immediate) |
| Run user deactivated mid-run | The run refreshes and continues, as today: runtime refresh doesn't check the active flag (`runtime_auth.py`: "does not retroactively cancel it") |
| Workflow powers, org provider flag, Solution uninstall, impersonation end | Stamps that subject; next request refreshes or is refused |

## 6. Refresh

- **People:** unchanged flow. `/auth/refresh` rotates the single-use refresh
  token, re-checks the user, and now mints through `issue_access_token`.
- **Runs:** at run start the parent mints an access token and a run refresh
  token (identity only, valid for the run's lifetime, accepted only by the
  refresh endpoint). The SDK's existing access and refresh slots, which today
  both hold the same runtime token, hold the two. Refresh runs today's
  `load_runtime_principal`; a run that isn't live gets 401. Services keep
  receiving new tokens through `/api/sdk/runtime/service-control`, now in the
  new format.

## 7. What we're not doing

- **Embed tokens** (anonymous form and app sessions, `create_embed_token`)
  keep their HMAC-scoped format. They carry no user grants.
- **Browser impersonation markers** keep their format; ending a session now
  also stamps it.
- No change to authorization rules or decision functions.
- No authorization cache. Redis holds only copies of the not-before stamps.

## 8. Rule change

`api/src/repositories/README.md` says: "Claims describing a runtime, an
organization, or a role do not become authority by themselves." It becomes:
**claims in an access token minted by `issue_access_token` are authority until
the token expires or a not-before stamp covers it.** Tests that pin the old
rule change with it.

## 9. Acceptance

| Check | Pass condition |
|---|---|
| Parity | For people and every runtime kind, the principal and authorization context built from claims equal today's database resolution, across role grant and revoke, org move, provider flag, workflow powers, Solution uninstall, impersonation end and run end |
| Revocation | After each change in that matrix, the next request gets "refresh required", the client refreshes, and the retry sees the new facts |
| Logout | After logout or revoke-all, the old access token is refused on its next use |
| Run end | An ended run's token is refused on the next call, and refresh fails |
| Long waits | A workflow that sleeps past its access token's lifetime refreshes on its next call and continues |
| Lifetime | Issued lifetimes are spread across 60–90 minutes |
| One minter | No access token is minted outside `issue_access_token` except embed tokens and impersonation markers (a test enforces it) |
| Completeness | The column-enumeration test passes, and fails when a read is added without a stamp |
| Redis down | Requests keep working, checked against the Postgres stamps; a change made during the outage is enforced on the next request |
| Redis restarted empty | The warm marker is missing, stamps are read from Postgres and re-copied; nothing stamped in the last 90 minutes is lost |
| Size | Minting warns above 6 KB (tested); production's largest is about 4 KB |
| Benchmark | Per SDK call about five fewer transactions; `wf-sdk` burst p50 and API CPU ms per run improve; no class regresses |
| Existing suites | Authorization, RBAC, Solution, runtime-identity, MCP auth, service-credential, web client auth and CLI login tests pass |

## 10. Delivery

One design, two PRs:

1. **Runs and the shared pieces:** `issue_access_token`, the claims schema,
   the not-before stamps (table, migration and Redis copy), run refresh
   tokens, and the README rule.
2. **People:** login, SSO, MFA, refresh and MCP OAuth switch to
   `issue_access_token`, and per-request grant reads go away.

Each PR is measured with the benchmark before and after (database
transactions per run and idle, API CPU per run, API memory, burst latency).
Both come after idle-churn steps 1–2. They are independent of agent runs
moving to workers. They also fit the later Cloudflare direction: a service
with no database access can authorize any request from the token and one
revocation lookup.
