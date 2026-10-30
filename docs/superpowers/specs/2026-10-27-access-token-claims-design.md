# One Token System for People and Runs — Design

Status: **Approved; amended 2026-10-30** (see "Amendments" at the end; the
PR1 plan is `docs/plans/2026-10-30-token-pr1-implementation-plan.md`). Replaces the earlier authority-cache and
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
| CAE: after a critical event, resources reject older tokens with a claims challenge; the client refreshes | **Authority version** in Postgres (section 5; amended 2026-10-30). A change to any fact a token carries changes the version; the API refuses tokens minted under an older version with "refresh required"; the web client, CLI and SDK refresh and retry once |
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
- `iat`, `exp`, `jti`, and `av`, the authority version at mint (amended
  2026-10-30).

The API builds the same `UserPrincipal`, `AuthorizationContext` and
`RuntimeAuthority` it builds today and decides with the same functions
(`decide`, `decide_for`, `check_target`). Only where the facts come from
changes.

**Size:** measured in production (read-only, 2026-10-28) across 1,703 active
users. The largest user holds 12 roles with 14 boundaries and 29 permissions;
their authorization claims are about 1.8 KB as JSON (median 0.3 KB, p99
0.7 KB). No workflow has permission grants yet. With identity and run claims
and base64 encoding, the largest token is about 4 KB, under the 6 KB limit, so
the claims go in as plain JSON (*amended 2026-10-30: the `usr` block is
zlib-compressed and base64url-encoded in every token, because a browser cookie
holds only about 4 KB; the largest people token drops from about 4.0 to
2.4 KB*). Minting logs a warning above 6 KB of encoded token, so growth
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
the token. (*Amended 2026-10-30: it runs in its own short `REPEATABLE READ`
transaction, so facts and version come from one snapshot; the signature is
`issue_access_token(subject)`.*) Browser impersonation markers and embed tokens keep their own
formats (section 7).

## 5. Revocation: authority version and run liveness

*Rewritten 2026-10-30 (Jack, on Fable's design review): versions replace the
earlier not-before timestamps, and Postgres replaces the Redis copy.*

One rule: **any change to a fact a token carries changes the authority
version.** Every access token carries the version it was minted under (`av`).
The API refuses a token whose version is not the current one, with a 401 whose
error code means "refresh required".

- **One global authority epoch.** A single row in `authz_versions` holds a
  random 64-bit value. Any committed change to a carried fact replaces it with
  a new random value. Facts are people, roles, boundaries, role permissions,
  organizations' provider flag, workflow powers and ownership, Solution status,
  Agent Solution binding, service definitions and the platform default
  workflow mode. Values are compared for equality only, never ordered, so a
  lossy failover or backup restore can never bring back a superseded value by
  counting. Production writes about 20 such changes a day (about 600–700 in
  30 days, measured 2026-10-30), so one epoch suffices. The table is keyed by
  subject, so per-subject versions can be added later if the measured bump
  rate calls for it; none are built now.
- **Written by Postgres triggers** in the changing transaction, once per
  transaction at commit (a deferred constraint trigger plus a per-transaction
  flag that every change clears), and by statement-level `TRUNCATE` triggers.
  This replaces "in the service or repository write … next to
  `role_cache.invalidate_*` … Not in routers": triggers cover every code path,
  cascade, raw SQL statement and older pod version. Maintenance that disables
  triggers must finish by bumping the epoch (runbook rule; migrations are
  checked by a test).
- **Run liveness is read from the run's own row** (execution status, agent
  run status, service attempt state and lease), exactly as today's per-request
  check does. A run that ends, or a service whose lease lapses, is refused on
  its next request with no version change.
- **Per request:** one statement on the primary, in the request's own
  transaction, reads the epoch row and the run's row by primary key. A missing
  epoch row refuses; it is never treated as a default.
- **Minting** reads every fact and the epoch in one `REPEATABLE READ`
  snapshot, so a token's facts and version always come from the same committed
  state. There are no locks, waits or clocks.
- **Why Postgres only:** the database is the source of truth, so the check
  can't be stale, and it removes the cache machinery where review found most
  defects. A Redis cache may be added later only if the benchmark shows the
  lookup matters.
- **Completeness:** a unit test enumerates every ORM column the minting
  function reads and fails unless each is covered by an epoch trigger or is a
  live-read run column.
- **Clients:** the web client (`client/src/lib/api-client.ts`), the CLI and the
  SDK (`refresh_connection_access_token`) already refresh on 401 and retry. The
  SDK allows up to three refresh-and-retry cycles for `refresh_required`, so a
  burst of changes can't fail a call.

What this does to each change:

| Change | Effect |
|---|---|
| Role granted, revoked or edited | The epoch changes; every token's next request refreshes and gets current grants (today: immediate, from the database) |
| Logout or revoke-all (PR2) | The epoch changes; access tokens stop working now (**today: up to 30 minutes later**) |
| Person deactivated | The epoch changes; their refresh fails, so they're out now (today: up to 30 minutes later) |
| Run ends | Refused on the next request by the liveness read; refresh fails (today: immediate) |
| Run user deactivated mid-run | The run refreshes and continues, as today: runtime refresh doesn't check the active flag (`runtime_auth.py`: "does not retroactively cancel it") |
| Workflow powers, org provider flag, Solution uninstall | The epoch changes; next request refreshes or is refused |
| Impersonation end | No version change: no run token depends on session liveness, and browser markers keep their live-session check |

## 6. Refresh

- **People:** unchanged flow. `/auth/refresh` rotates the single-use refresh
  token, re-checks the user, and now mints through `issue_access_token`.
- **Runs:** at run start the parent mints an access token and a run refresh
  token (identity only, valid for the run's lifetime, accepted only by the
  refresh endpoint; *amended 2026-10-30: it has no fixed expiry and is
  valid while the run is live*). The SDK's existing access and refresh slots, which today
  both hold the same runtime token, hold the two. Refresh runs today's
  `load_runtime_principal`; a run that isn't live gets 401. Services keep
  receiving new tokens through `/api/sdk/runtime/service-control`, now in the
  new format. *Amended 2026-10-30: services get an access token and a run
  refresh token at launch and refresh like other runs. Their lease is enforced
  on every request by the liveness read, so parent token rotation is removed.*

## 7. What we're not doing

- **Embed tokens** (anonymous form and app sessions, `create_embed_token`)
  keep their HMAC-scoped format. They carry no user grants.
- **Browser impersonation markers** keep their format; ending a session now
  ends them through their own live-session check (*amended 2026-10-30: no
  version change, see section 5*).
- No change to authorization rules or decision functions.
- No authorization cache, and no Redis copy of the authority version
  (amended 2026-10-30).

## 8. Rule change

`api/src/repositories/README.md` says: "Claims describing a runtime, an
organization, or a role do not become authority by themselves." It becomes:
**claims in an access token minted by `issue_access_token` are authority until
the token expires, while its authority version is current and its run is
live** (amended 2026-10-30). Tests that pin the old
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
| Completeness | The column-enumeration test passes, and fails when a read is added without an epoch trigger or live read |
| Snapshot (amended 2026-10-30) | A token minted while a change is uncommitted, or before a slow commit lands, is refused once the change commits; clock skew changes no outcome; `SET CONSTRAINTS ALL IMMEDIATE`, savepoints and `TRUNCATE` still bump (deterministic tests) |
| Size | Minting warns above 6 KB of encoded token (tested); production's largest is about 2.4 KB with the compressed `usr` block |
| Benchmark | *Amended 2026-10-30:* authenticating a runtime request takes no extra transaction and one statement (today one extra transaction with about ten statements), so `wf-sdk` makes one fewer transaction per SDK call; each run adds one at start and one per refresh; `wf-sdk` burst p50 and API CPU ms per run improve; no class regresses |
| Existing suites | Authorization, RBAC, Solution, runtime-identity, MCP auth, service-credential, web client auth and CLI login tests pass |

## 10. Delivery

One design, two PRs:

1. **Runs and the shared pieces:** `issue_access_token`, the claims schema,
   the authority version (table, triggers and migration), run refresh
   tokens, and the README rule. *Amended 2026-10-30: delivered as merge units
   PR1a (version, minter and refresh endpoint, no behaviour change), PR1b
   (switch, plus a bridge that accepts old-format run tokens until they
   expire, with an enforced cutoff 24 h 5 min after PR1b first starts), and
   PR1c (removal of the already inert bridge in the following release), with
   the order enforced by migrations.*
2. **People:** login, SSO, MFA, refresh and MCP OAuth switch to
   `issue_access_token`, and per-request grant reads go away.

Each PR is measured with the benchmark before and after (database
transactions per run and idle, API CPU per run, API memory, burst latency).
Both come after idle-churn steps 1–2. They are independent of agent runs
moving to workers. They also fit the later Cloudflare direction: a service
with no database access can authorize any request from the token and one
revocation lookup.

## Amendments (2026-10-30)

Jack's decisions after the independent reviews of the PR1 plan (Astra twice,
Fable once):

- **Authority version, not timestamps** (§5). One global random epoch,
  bumped by Postgres triggers once per changing transaction; equality only;
  minting in one `REPEATABLE READ` snapshot; validation in one statement on the
  primary. Re-measure the bump rate and move to per-subject versions if bumps
  exceed 30 per hour in any hour on three days of a week, or refresh-required
  responses exceed 5 % of runtime requests over a day.
- **Postgres only** (§5): no Redis copy.
- **Run liveness from the run's own row** (§5): replaces run stamps and makes
  the earlier lease-length service tokens unnecessary. Services use standard
  lifetimes and refresh; parent rotation is removed (§6).
- **Impersonation end changes no version** (§5, §7): no run token depends on
  session liveness; this supersedes the earlier "stamp at impersonation end".
- **Run refresh tokens** have no fixed expiry; they are valid while the run
  is live (§6).
- **Compressed `usr` block** (zlib + base64url) in every token, decided for
  PR2's browser cookies and used from PR1 so there is one format (§3).
- **PR1 merge units** PR1a, PR1b and PR1c, with migration-enforced order and
  an enforced cutoff for old-format run tokens (§10).
- **Benchmark expectation** corrected from "about five fewer transactions per
  SDK call" to the measured-scope expectation in §9.
