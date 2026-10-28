# Runtime Tokens Carry Their Authority — Design

Status: **Draft for Jack's review.** Replaces the earlier authority-cache draft
(same date). Follows the per-execution database findings in
`docs/benchmarks/2026-10-27-baseline.md`. Modelled on Microsoft Entra ID:
claims in a short-lived access token, a refresh token that re-evaluates, and
Continuous Access Evaluation (CAE) for changes that must apply before expiry.
Calls marked *(Jack)* need his sign-off.

## 1. Why

Human tokens already work the Entra way: a browser login token carries the
user's org, roles and admin flag, and the API reads them from the token.

Runtime tokens, which workflows, services and agent runs use to call the API,
don't. They carry only the run's ID (`mint_engine_token`, `mint_service_token`
in `core/security.py`; agent runs in `jobs/consumers/agent_run.py`). On
**every** call, `load_runtime_principal` (`services/authorization/runtime_auth.py`)
rebuilds the caller from the database:

1. the execution, service attempt or agent run (is it live, who is it for);
2. the run user's identity kind and active flag, then their full authorization
   context (`build_authorization_context`: user row, role boundaries, role
   permissions);
3. the acting user, if different;
4. the workflow and its powers (`load_powers_for_workflow`);
5. the acting user again with their organization, then their role names.

`get_execution_context` (`core/auth.py:401`) then reads the Solution and
Application rows for the request. The baseline measured about five
transactions per SDK call, and with the API on one CPU this is part of why ten
`wf-sdk` runs at once take about 8× longer than one.

`api/src/repositories/README.md` makes this deliberate: "Claims describing a
runtime, an organization, or a role do not become authority by themselves."
The reasons were that a run can outlive a role change, and that a finished
run's token must stop working at once. This design keeps both guarantees by
Entra's means instead of by re-reading the database on every call, and
**replaces that rule** *(Jack)*.

## 2. The model

| Entra ID | Bifrost runtime tokens |
|---|---|
| Access token carries the user's claims; resources validate it locally | The runtime **access token** carries the run's authority facts (section 3). The API checks signature and expiry and builds the principal from the claims, with no database read |
| Access tokens are short-lived (60–90 min) | Access tokens last **15 minutes** *(Jack)*, the lifetime services already use (`SERVICE_TOKEN_LIFETIME_SECONDS`) |
| A refresh token gets a new access token, and Entra re-evaluates the user at that moment | A runtime **refresh token** (identity only, valid for the run's lifetime) is exchanged for a new access token. The exchange runs today's full database resolution, so a refreshed token always reflects current state |
| CAE: after a critical event the resource rejects older tokens with a claims challenge, and the client refreshes | **Not-before stamps** in Redis. A change to any fact a token carries stamps that subject; the API refuses tokens issued before the stamp with a "refresh required" response, and the SDK refreshes and retries once |
| Token lifetime is the backstop when an event is missed | Same: a missed stamp lasts at most one access-token lifetime |

## 3. What the access token carries

Everything `load_runtime_principal` produces today, so the API builds the same
`UserPrincipal` and `RuntimeAuthority` and makes the same decisions with the
same functions (`decide_for`, `check_target`):

- **The run:** kind, credential ID, service ID, workflow ID, Solution ID, root
  execution ID, started-by user, impersonation session and impersonator.
- **The run user:** user ID, identity kind, active flag, and their
  authorization context: home organization, base role, external flag, base
  permissions and role grants with boundaries.
- **The acting user:** ID, email, name, external flag, provider-org flag,
  organization, role IDs and names.
- **The workflow's powers:** permission mode and grants.
- Standard `iat`, `exp` and a new `jti`.

The request-level Solution and Application gates in `get_execution_context`
use the token's Solution ID and the same not-before mechanism (section 4) for
uninstalls, instead of reading the rows each call.

**Size:** a run user with many roles makes a larger token. Before building,
measure the largest authorization context in production (read-only). If any
token would exceed 6 KB, bring the numbers back before choosing an encoding:
Entra handles the same case with a "groups overage" reference, which would be
a second path and is not assumed here.

**Visibility:** the token sits inside customer workflow code, and JWT claims
are readable, not encrypted. Workflow code could read the run user's
permission list. It can't change it (the token is signed) *(Jack)*.

## 4. Revocation: not-before stamps

One rule: **any change to a fact a token carries stamps the affected subject
with the current time.** The API refuses a runtime token whose `iat` is
earlier than any stamp for its subjects.

- **Subjects:** the run, the run user, the acting user, their roles, the
  organization, the workflow, the Solution and the impersonation session.
  Stamps live in Redis as `bifrost:authz:nbf:<subject>:<id>`, each expiring
  after one access-token lifetime, since older tokens have expired anyway.
- **Per call:** one Redis `MGET` for the token's subjects. That replaces about
  five database transactions.
- **Run ended:** the run's stamp is set when it leaves a live status. Refresh
  also fails, because the database says the run isn't live, so the run's
  tokens stop working at once, as today.
- **Role or permission change:** the stamp makes the next call return "refresh
  required"; the SDK refreshes and gets a token with the new facts. Changes
  take effect on the next call, as they do today.
- **Where stamps are written:** in the service or repository write for each
  fact, next to the existing `role_cache.invalidate_user` / `invalidate_role`
  calls for role changes. Not in routers.
- **Completeness:** a unit test enumerates every ORM column that minting reads
  and fails when a column is added without a stamp on its write paths.

**If Redis can't be read,** the API refuses the call with a retryable 503
rather than accepting tokens it can't check *(Jack)*. No second path.

Account deactivation keeps today's behaviour: it doesn't cancel a run already
in progress, so it stamps nothing it doesn't stamp today.

## 5. Refresh

- At run start the parent mints both tokens. The SDK already has an access
  token and a refresh token slot and a refresh-on-401 coordinator
  (`refresh_connection_access_token` in `api/bifrost/client.py`); today both
  slots hold the same runtime token.
- The refresh token carries only the run's identity (as runtime tokens do
  today), lives for the run's current lifetime (`mint_engine_token`'s timeout
  plus 300 s; services keep their lease-based renewal), and is accepted only by
  the refresh endpoint.
- The refresh endpoint runs today's `load_runtime_principal` and mints a new
  access token from the result. A run that is no longer live gets 401.
- Services already receive new tokens through `/api/sdk/runtime/service-control`;
  those become the new access-token format, with no other change.
- Agent runs mint their tokens the same way when their child starts.

## 6. What we're not doing

- No change to human browser, API-key or embed tokens.
- No change to authorization rules or decision functions. Only where the facts
  come from changes.
- No change to the CLI contract: the CLI never sees runtime tokens.
- No cache. Nothing needs invalidating except the not-before stamps.

## 7. Acceptance

| Check | Pass condition |
|---|---|
| Parity | For every runtime kind, the principal built from claims equals today's database resolution, across role grant and revoke, org move, provider flag, workflow powers, Solution uninstall, impersonation end and run end |
| Revocation | After each change in that matrix, the next call is refused with "refresh required", the SDK refreshes, and the retry sees the new facts |
| Run end | A token for an ended run is refused on the next call, and refresh fails |
| Long waits | A workflow that sleeps longer than an access-token lifetime refreshes on its next call and continues |
| Completeness | The column-enumeration test passes, and fails when a new read is added without a stamp |
| Redis down | Runtime calls return 503; nothing is accepted unchecked |
| Size | Production's largest authorization context fits the limit in section 3 |
| Benchmark | Per SDK call, about five fewer transactions; `wf-sdk` burst p50 and API CPU ms per run improve; no class regresses |
| Existing suites | Authorization, RBAC, Solution, runtime-identity and service-credential tests pass; tests that assert the README rule are updated with it |

## 8. Decisions for Jack

1. Replace the README rule: runtime-token claims minted from the durable run
   are authority until expiry or a not-before stamp.
2. Access-token lifetime: 15 minutes.
3. Workflow code may read its run user's permission list from the token.
4. Redis unreadable: refuse with 503.

## 9. Order

After idle-churn steps 1–2. Independent of agent runs moving to workers: agent
children call the API with `agent_run` tokens and get the same benefit. It also
fits the later Cloudflare direction: a service without database access can
authorize a runtime call from the token and one revocation lookup.
