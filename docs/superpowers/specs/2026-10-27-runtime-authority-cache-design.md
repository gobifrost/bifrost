# Runtime Authority Cache — Design

Status: **Draft for Jack's review.** Follows the per-execution database
findings in `docs/benchmarks/2026-10-27-baseline.md` and the idle-churn plan
(`docs/superpowers/plans/2026-10-28-idle-database-churn.md`). Modelled on
Microsoft Entra ID's access tokens plus Continuous Access Evaluation (CAE), as
Jack asked (2026-10-27). Calls marked *(my call)* are for Jack to check.

## 1. Why

Every SDK request from a running workflow or agent re-proves who the caller is
from the database before the route does any work:

- `load_runtime_principal` (`services/authorization/runtime_auth.py`) loads the
  execution or agent run, its run user (`load_run_user`), the acting user if
  different, the workflow and its powers (`load_powers_for_workflow`), then the
  acting user with their organization, then roles (Redis role cache).
- `get_execution_context` (`core/auth.py:401`) then loads the Solution row for
  `?solution=` and the Application and Solution rows for `X-Bifrost-App`.

That is about five transactions per SDK call in the baseline. A no-op
workflow's run reads its user about 9 times and the authorization context
about 3 times. With the API on one CPU, this repeated work is part of why ten
`wf-sdk` runs at once take about 8× longer than one.

The facts being re-read almost never change during a run. The design question
is how to stop re-reading them without serving a decision that has gone stale.

## 2. The Entra model, mapped

| Entra ID | Bifrost today | Bifrost with this design |
|---|---|---|
| Sign-in issues an access token | Run start issues a typed runtime token (`credential_kind`, `sub` = run ID) | Unchanged |
| Resource validates the token locally (signature, expiry, claims) | Signature checked locally, then **every authority fact re-read from the database** | Signature checked locally; authority facts read from a cache filled once per run per API process |
| Token lifetime bounds how stale claims can be (about 1 hour) | Not applicable: nothing is reused | A cached entry is re-resolved after **60 s** at most *(my call)* |
| CAE critical events (account disabled, sessions revoked, location change) revoke access near-real-time | Not applicable | Revocation events over Redis drop entries immediately on every API replica |
| CAE-aware resources accept long-lived tokens because events cover freshness | Not applicable | Events cover freshness; the 60 s bound only covers a lost event |

What we deliberately don't copy: Entra puts authorization claims (groups,
roles) inside the token. `load_runtime_principal` is documented as resolving
"without trusting its authority claims", and the runtime token sits inside
customer workflow code. **The token stays an identity reference; the cache
lives in the API** *(my call)*. That keeps tokens small, keeps authority
decisions server-side, and needs no token format, CLI or SDK change.

## 3. What we're doing

1. **Cache the resolved runtime principal per API process,** keyed by
   `(credential_kind, credential_id, service_id)`. The value is the
   `RuntimeAuthority` and the `UserPrincipal` built from it, stamped with the
   time it was resolved.
2. **Cache the per-request Solution and Application gate results** from
   `get_execution_context` the same way: whether an install is active, and
   which install an app belongs to.
3. **Revoke by event.** Every change that can alter a cached answer publishes a
   revocation event on one Redis channel. Each API replica drops the matching
   entries.
4. **Bound staleness.** An entry older than 60 s is re-resolved from the
   database on its next use, whatever the events said.
5. **Stop caching when events can't be trusted.** If a replica's subscription
   to the revocation channel drops, it clears its cache and resolves from the
   database on every request until it has resubscribed *(my call)*. That is
   today's behaviour, so it adds no new code path; it only stops serving
   answers the replica can't vouch for.

## 4. What we're not doing

- No change to how tokens are issued, signed or sent, and no change to the CLI,
  SDK, MCP or token contract.
- No change to authorization rules. The same functions produce the same
  principal; only how often they run changes.
- No cache for human browser or API-key requests in this change. Their path
  (`_get_current_user_optional`) already validates signed claims locally and
  uses the Redis role cache.
- No change to account deactivation semantics. Today a deactivated account
  does not cancel a run already in progress (`runtime_auth.py`: "does not
  retroactively cancel it"); the cache keeps that.

## 5. Revocation events

One Redis channel, `bifrost:authz:revoke`. Each message names what changed;
every API replica drops matching entries.

| What changed | Event | Entries dropped | Published from |
|---|---|---|---|
| Execution leaves a live status | `run_ended(kind, id)` | That run | Execution status writes |
| Agent run leaves `running`/`cancelling` | `run_ended(kind, id)` | That run | Agent run status writes |
| Service attempt ends or loses its lease | `run_ended(kind, id)` | That attempt | Service attempt writes |
| User's roles change | `user(id)` | Runs whose run user or acting user is that user | The existing `role_cache.invalidate_user` / `invalidate_role` call sites |
| Role renamed or deleted | `role(id)` | Runs whose principal holds that role | Same call sites |
| User's organization, `is_external`, identity kind or active flag changes | `user(id)` | As above | User update paths |
| Organization's `is_provider` changes | `org(id)` | Runs in that organization | Organization update path |
| Workflow permission mode or powers change | `workflow(id)` | Runs of that workflow | Workflow update and manifest import paths |
| Solution installed, uninstalled or deactivated | `solution(id)` | Gate results for that install and runs of its workflows | Solution lifecycle paths |
| Impersonation session ended | `impersonation(id)` | Runs carrying that session | Impersonation end path |

The cache keeps small reverse indexes (user, role, org, workflow, solution and
impersonation session → runs) so an event drops only what it affects.

**Completeness is the risk.** A mutation path that forgets to publish leaves an
entry stale for up to 60 s. Two guards:

- Publishing sits next to the existing role-cache invalidation, which already
  covers every role mutation, and in the service or repository write for each
  other row, not in routers.
- A unit test enumerates every ORM column the resolver reads (`Execution`,
  `AgentRun`, `ServiceAttempt`, `User`, `Organization`, `Workflow`, powers
  rows, `Solution`, `Application`) and fails when a column is added to those
  reads without a matching revocation event.

## 6. Size and memory

One entry is a principal plus its authority, a few KB. Entries exist only for
runs that made an SDK call on that replica in the last 60 s, and the cache is
capped with least-recently-used eviction at 10,000 entries *(my call)*. At a
few hundred live runs that is well under 1 MiB per API process.

## 7. Expected effect

- An SDK call from a run already cached does no authority reads: about five
  fewer transactions per call, and the matching API CPU.
- The first call of each run, and any call after 60 s or after an event, costs
  what it does today.
- Measured with the benchmark: transactions per run for `wf-noop` and `wf-sdk`,
  API CPU ms per run, and `wf-sdk` burst p50, before and after.

## 8. Acceptance

| Check | Pass condition |
|---|---|
| Parity | For every runtime kind, the cached principal equals a fresh resolution across a matrix of mutations: role grant and revoke, org move, provider flag, workflow powers, solution uninstall, impersonation end, run end |
| Revocation | After each mutation in the matrix, the next request on every API replica sees the new answer without waiting for the 60 s bound |
| Run end | A request with an ended run's token is refused immediately after the status change, as today |
| Lost subscription | Dropping a replica's Redis subscription clears its cache; requests keep working from the database, and caching resumes after resubscribe |
| Completeness test | The column-enumeration test passes, and fails when a new read is added without an event |
| Benchmark | `wf-sdk` transactions per run fall by about 5 per SDK call; `wf-sdk` burst p50 and API CPU ms per run improve; no class regresses |
| Existing suites | Authorization, RBAC, Solution and runtime-identity tests pass unchanged |

## 9. Order

Lands after idle-churn steps 1–2, which build the Redis subscription helper
this reuses. It is independent of agent runs moving to workers: agent children
still call the API's REST surface with `agent_run` tokens and get the same
benefit.
