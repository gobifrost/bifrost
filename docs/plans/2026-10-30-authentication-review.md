# PR2 Human Authentication — Independent Security Review Record

Date: 2026-10-30
Status: architecture/source review complete; **final candidate and test approval pending**.

This note preserves the independent architecture and source reviews of PR2 human
authentication. It applies to the contract in
[`2026-10-30-authentication-consolidation.md`](2026-10-30-authentication-consolidation.md)
and the approved claims design in
[`../superpowers/specs/2026-10-27-access-token-claims-design.md`](../superpowers/specs/2026-10-27-access-token-claims-design.md).
It does not authorize deployment. The PR1b runtime compatibility bridge remains
live; PR1c is deferred to a later release.

## Architecture conclusions

- Use PostgreSQL, not Redis, as the canonical human refresh registry:
  `human_auth_sessions(sid, user_id, current_jti_hash, expires_at, revoked_at)`.
  The stable session-family ID (`sid`) is necessary. Without it, a refresh of
  predecessor JTI R0 to R1 followed by logout presenting R0 could leave R1 live.
- Lock the User row for issue, rotation, session logout, revoke-all, password
  change, and MFA reset. Rotation replaces the current hashed JTI under that
  lock. Revocation marks families revoked and bumps the single global authority
  epoch in the same transaction. This closes the refresh-versus-revoke race
  without a Redis/Postgres dual-write gap or a per-user authority epoch.
- Human access tokens use the existing signed `ver: 1` claims/minter, strict
  human schema parsing, and the global epoch. Human and runtime schemas must
  remain disjoint; old embedded role/admin flags never become authority.
- Browser, CLI, and MCP intentionally share one human-session refresh
  credential and human access-token audience. This is distinct from runtime
  refresh credentials, machine enrollment, embeds, MFA/state tokens, preview
  capabilities, and browser impersonation markers.
- For browser cookie overrun, deterministic JWT fragmentation is acceptable
  transport: `access_token` holds the original JWT or a count/digest manifest;
  numbered HttpOnly/Secure/SameSite fragments reconstruct the exact original
  JWT. Missing, mixed, malformed, or tampered fragments fail closed before the
  normal signature, schema, expiry, and epoch checks. It introduces no opaque
  lookup or alternate authority.
- Human WebSockets must revalidate their original handshake credential before
  each sensitive inbound event and outbound delivery. Expiry, epoch mismatch,
  or logout closes with 4001; the client refreshes over HTTP and reconnects.
  No polling is needed.

## Findings and corrections reviewed

### Corrected P1 issues

1. **Refresh/logout family race.** Replaced JTI-only Redis registration with
   the stable Postgres SID family and current JTI hash. A stale predecessor can
   now revoke the family successor.
2. **Redis dual-write authority gap.** Removed Redis as refresh authority;
   durable rotation and revocation share the primary transaction and lock.
3. **Nested connection-pool starvation.** The initial session service held its
   mutation transaction/User lock while `issue_access_token` opened a second
   main-pool `REPEATABLE READ` connection. With the configured small pool,
   concurrent distinct-user refreshes could exhaust all slots waiting for
   inner mint connections. The corrected shape is:
   - verify signed refresh claims;
   - perform a short nonauthoritative Postgres preflight for the exact live
     SID/JTI, then close it;
   - mint a candidate access token through the unchanged one-minter snapshot;
   - open the mutation transaction, lock User, recheck eligibility and the
     exact live session/JTI/expiry, rotate, and commit.

   A revocation that commits after preflight/mint wins at the locked recheck.
   An authority write between candidate mint and commit only makes that
   candidate refresh-required on its first protected use; it never authorizes
   with stale facts. Do not remint while holding the lock.
4. **Password change did not revoke durable sessions.** Password update now
   calls the in-transaction human-session revoker before commit, so the new
   password, family revocation, and epoch bump are atomic. Administrative MFA
   reset follows the same rule.
5. **Mixed old/new API deployment revocation bypass.** Old API pods accept
   legacy human flags without epoch checks while PR2 rejects legacy humans and
   issues new claims they cannot parse. Ordinary rolling activation is unsafe.
   The release contract now requires a maintenance cutover: drain/block API
   and WebSocket traffic, migrate, verify homogeneous PR2 API replicas behind
   ingress, then restore traffic. All legacy human access and Redis-only
   refresh credentials reauthenticate.
6. **MCP OAuth code and auth boundary.** The reviewed corrections bind codes
   to client ID, consume them atomically, retain only authoritative subject
   data, and map epoch/expiry refusal through Starlette authentication handling
   to the exact refresh-required challenge before a tool can run.

### Corrected P2 issues

- Logout and revoke-all receive request cookies and remove referenced fragment
  cookies; orphan fragments are inert after their manifest is removed.
- The MCP bridge resolves a database subject or forwards the original validated
  bearer, rather than minting from copied context role/admin flags.
- Human eligibility consistently excludes inactive, system, and identity
  accounts; the forward user epoch trigger includes `is_system`.
- The contract now explicitly documents shared browser/CLI/MCP human credential
  semantics, legacy-human reauthentication, and the maintenance-only cutover.
- Additional corrections recorded by the implementing reviewer: MCP callback
  preserves OAuth state when authentication requires refresh; client code tracks
  the bearer per attempt and bounds identity reload; a refresh in flight cannot
  resurrect a token after logout; password enrollment is prohibited for support
  accounts; audit attribution comes from signed human `usr`; and device
  `expires_in` follows the actual JWT expiry.

## Remaining review/verification requirements

The current source review is not final approval. Focused Docker/API and client
verification is queued behind the sibling benchmark host lock; initial focused
unit, pyright, and browser diagnostics are useful but are not final-candidate
results. Before PR/merge review, require evidence for:

- focused unit and E2E coverage of human claims/schema dispatch, all issuers,
  refresh rotation, predecessor logout, revoke-all, password/MFA reset,
  cancellation, and concurrent rotate/revoke;
- the pool-scope invariant: candidate mint occurs with no session mutation
  transaction held, while the locked recheck remains authoritative;
- cookie small/large round trips, Set-Cookie bounds, corrupt/mixed/missing
  fragments, rotation to fewer fragments, and logout/revoke-all cleanup;
- HTTP, WebSocket, MCP callback, MCP middleware/tool execution, embed, and
  CSRF consumption of the shared fragment parser as appropriate;
- exact `refresh_required` challenge before effect execution, including MCP
  epoch/expiry and browser/SDK replay boundaries;
- API lint/type checks and affected client type/lint/unit coverage, plus one
  focused browser happy path; and
- final source diff review after all late corrections, with no claim of broad
  suite success before the queued work completes.

Two lower-severity implementation checks should be retained for final review:

1. Public login/MFA/SSO/passkey issuer boundaries should map an
   `HumanSessionUnavailable` eligibility race to a generic account/authentication
   refusal, rather than a 500. Do not hide epoch/database operational failures.
2. Fragment cleanup should delete only a bounded, syntactically valid numeric
   fragment set referenced by the manifest. Deleting every client-provided
   `access_token.*` name can amplify response headers. Cleaning stale received
   fragments on rotation is desirable for cookie hygiene; stale fragments are
   not authority because only the current manifest is accepted.

## Verification failures and dispositions

- The first expanded backend selection passed 893 tests and exposed five
  failures. The new fixture refresh URL omitted a separator; it now appends
  `/auth/refresh` explicitly. MCP epoch refusal now emits the protocol refresh
  challenge, so its old `None` assertion was replaced with the exact refusal
  expectation. The access-list checks correctly caught obsolete flag sites and
  route metadata; the canonical catalog, exact source registries and generated
  projection were updated without weakening the scanner. The focused correction
  and affected-consumer selection then passed all 174 tests, including DTO and
  CLI contract tripwires. Evidence: `/tmp/bifrost-auth-focused-unit-r2.xml`.
- The initial session-concurrency harness counted every task's open sessions.
  A rotation could therefore fail the nested-connection assertion merely because
  a different task held a transaction. Task-local ContextVar depth now tests the
  intended invariant while retaining real PostgreSQL rotation/revocation races.
- Live auth tests that revoke every session must explicitly sign the shared test
  person in afterward. Raw WebSocket fixture credentials likewise need the same
  real HTTP challenge refresh as supported clients when earlier writes advance
  the global epoch. These are fixture lifecycle changes, not validator bypasses.
- Browser test failures exposed invalid TSX generic syntax, an unrelated missing
  provider mock, and a synchronous assertion made before the asynchronous Web
  Lock callback. The assertion could strand its gate and leak the module's
  single-flight promise into later tests. Each cause was corrected directly;
  no timeout increase, retry, skip or unlocked refresh path was introduced.

Later focused results and exact-candidate approval must be recorded separately;
these dispositions do not claim that still-pending live/browser checks passed.

## V2 App compatibility decision

Independent Astra review classified human WebSocket 4001 recovery as an
intentional breaking realtime contract. A platform upgrade retains each deployed
V2 App's old bundle; old SDKs stop permanently on 4001, while the new server emits
it when a human token expires or its epoch changes. SDK contract 3 therefore
records the required HTTP refresh/reconnect/resubscribe behavior in the explicit
wire inventory and snapshot. Existing App provenance becomes `update_required`.
The maintenance runbook requires supported SDK rebuilds, or source redeployment
when retained source is unavailable, before reopening those Apps. This follows
the existing contract-2 precedent for incompatible realtime behavior; it does not
raise the CLI contract or remove the runtime legacy bridge.

## Exact candidate source review

Independent Astra reviewed `2267af32b` against `origin/main`. It found one P1:
App SDK status compared fingerprints but never contract versions, so a known
contract-2 deployment was incorrectly labeled `update_available`. The shared
status function now returns `update_required` for a contract mismatch before
fingerprint equality, with older/newer and matching/different-fingerprint cases.
Astra reviewed that correction and found no additional P1/P2 issues in the human
session, cookie, HTTP, WebSocket, MCP or browser boundaries. A suspected issuer
exception-mapping gap was retracted after inspecting the central exception handler
and its passing regression. This is source review; the final delta checks remain
required before delivery approval.

The late consumer scan found legacy-token assumptions in worker-local HTTP
permission tests, workspace requester binding, and external-actor attribution.
They now use exact human credentials/current epochs, real fixture sessions, or
parsed `usr` facts. The workflow-list test explicitly models a missing named
permission rather than relying on a nonexistent legacy-token user.

The first production-client login run received `/auth/me` HTTP 200 but failed its
new assertion: Playwright's `toBeOK` matches APIResponse, not the browser Response.
The assertion now checks its HTTP status directly. A separate instrumented
AuthContext reproduction showed its remaining ordering failure was a local-storage
spy on the wrong object; the corrected spy passed all 12 AuthContext tests. The
speculative bootstrap edit was reverted. Neither failure was masked or retried
without a concrete correction.

A final browser caller audit found that normal logout and impersonation entry
still used raw fetch. They now use the same exact-challenge transport as normal
protected requests. Support-session logout retains its direct revoke request:
expired support markers cannot refresh the operator cookie and still need an
explicit revoke attempt. Impersonation exit retains its original-operator path.
The focused regression challenges normal logout before effects, rotates once,
then completes logout once and clears browser state.

The late worker/SDK selection passed 100 tests and exposed one incorrect migrated
expectation: workflow listing filters unauthorized rows and returns HTTP 200 with
an empty list, rather than requiring a route-wide `workflows.read` grant. The old
legacy-token fixture had refused an unknown user before reaching this contract.
Independent review confirmed `shared/sdk_execution_reads.py` filters each row;
out-of-reach requested scope is the separate 403 contract. The corrected test
must exercise the supported list behavior without weakening authorization.
Failure evidence: `/tmp/bifrost-auth-review-correction-r5-failure.xml`. The script
stopped and removed its isolated stack; remaining checks are still required.
