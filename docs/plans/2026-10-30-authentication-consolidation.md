# Authentication Consolidation — PR2 Delivery

Status: **PR2 implemented; final subscription-consumer verification and protected merge pending**
(2026-10-30). PR1c remains deferred to a later release.
Owner: this authentication chat, branch `codex/auth-consolidation`, isolated
worktree `/home/jack/.codex/worktrees/auth-consolidation/bifrost`.

PR1a #1146 and runtime PR1b #1147 are merged, including the single global random
64-bit Postgres authority epoch, shared runtime credentials, refresh and live-run
checks with a legacy bridge. Neither completes authentication consolidation.
Merge does not imply production deployment. Do not redo those PRs.

## Coverage Inventory

| Authentication surface | Source and shared path | Disposition |
| --- | --- | --- |
| Password login / human sessions | `routers/auth.py`, `shared/human_sessions.py`, `authorization/tokens.py` | PR2: one minter and durable session family; retain existing login/MFA decisions. |
| SSO callback / account linking | `routers/oauth_sso.py`, `services/oauth_sso.py` | Share completed-login issuance/cookies; provider exchange, state/PKCE and linking remain separate authentication proofs. |
| MFA completion / trusted devices | `routers/auth.py`, `routers/mfa.py`, `services/mfa_service.py` | Share final human session issuance; short-lived purpose-bound MFA challenges, TOTP/recovery and trusted-device proof remain distinct. |
| Passkey login / enrollment / bootstrap | `routers/passkeys.py`, `routers/auth.py`, `services/passkey_service.py` | Existing login helper or shared session service after enrollment commit; retain RP/origin and single-use challenge checks. |
| Invite registration | `services/user_invite_service.py`, `routers/auth.py` | Opaque hashed, expiring single-use invitation stays a registration proof; completed login uses shared human session. |
| CLI / Python SDK / device approval | `shared/device_auth.py`, `bifrost/client.py`, `bifrost/_refreshing.py` | Same human access/refresh family, existing credential storage and challenge-driven transport. Device approval stays non-impersonated and explicit. |
| Local Solution development proxy | `bifrost/solution_dev/proxy.py` | Same human credentials; challenge-only bounded HTTP replay. Supplemental preview capability stays separate. |
| Browser HTTP / V2 SDK | `core/auth.py`, `shared/auth_cookies.py`, `client/src/lib/api-client.ts`, App SDK transport/provider | Exact human claims + epoch; same signed JWT in bearer/cookie transport, bounded challenge replay, cross-tab serialization. |
| Browser WebSocket | `core/auth.py:get_current_user_ws`, `routers/websocket.py` | Validate human epoch/expiry at connect and before every protected incoming message/outgoing delivery; close and reconnect through shared refresh. |
| External MCP OAuth | `services/mcp_server/auth.py` | Callback verifies current human cookie; code exchange and refresh share session service; verifier shares exact claims/epoch and role facts. |
| Internal MCP REST bridge | `mcp_server/tools/_http_bridge.py`, `mcp_server/authorization.py` | Forward the original external bearer; internal human contexts mint from database subject, never copied role/admin flags. Runtime contexts retain durable run subject and shared runtime minter. |
| Workflows / agent runs / services | `authorization/tokens.py`, `runtime_auth.py`, workflow consumer, `service_claim.py` | Already delivered PR1b. Keep claims refresh, exact live-run checks, redaction and compatibility routing. In-process agent authority remains database-backed, not a new JWT class. |
| Remote launcher enrollment / control | `worker/remote/protocol.py`, `host.py`, ownership/fencing | Installation-secret HMAC, nonce/sequence and fenced leases remain machine enrollment/control credentials. Child runtime credential protocol remains PR1b's supported path. |
| Workflow API keys | `routers/workflow_keys.py`, `routers/endpoints.py`, `services/workflow_keys.py` | Keep hashed, workflow-scoped enabled/expiry verification. A key can start only its workflow; resulting run uses shared runtime credentials. Never a human session. |
| App/form embed / public form | `services/embed_auth.py`, `core/embed_middleware.py`, `security.create_embed_access_token` | Keep typed scoped capabilities, HMAC verification and restricted routes. No human grants or refresh family. |
| Browser impersonation | `shared/browser_impersonation.py`, `routers/auth.py` | Keep separate revocable marker and live run-as checks. Bind initiating refresh JTI to canonical human session registry; ending marker changes no global epoch. |
| Solution dev context | `shared/solution_dev.py` | Keep short-lived supplemental subject-bound capability, active install/app ownership and permission revalidation. Not bearer authentication. |
| Inbound webhooks | `routers/hooks.py`, `services/webhooks/*` | Adapter signature/client-state verifies external sender, not user/session. Existing authorization and event/run creation remain intact. |
| Outbound integration/MCP OAuth | `mcp_client/auth_resolution.py`, OAuth connection services/state, encrypted token store | Provider-issued credentials, owner/org selection and refresh remain separate from Bifrost-issued human/session JWTs. No credential-purpose flattening. |
| Password change / administrative MFA reset | `routers/profile.py`, `services/user_mfa_reset.py` | Revoke all human families and bump epoch in the same transaction as the credential reset. |

## PR2 Implementation Contract (Revised After Independent Astra Review)

1. `issue_access_token(HumanSubject)` extends the existing minter. Exact human
   and runtime schemas share envelope/compressed `usr`, signature and random
   60–90 minute expiry. Human `sub` equals `usr.id`; human claims cannot carry
   runtime, embed or impersonation fields. Missing/inactive/system/nonhuman
   accounts cannot mint. Platform Admin comes from canonical wildcard/boundary
   context, never the old `is_superuser` identity flag. System eligibility is
   covered by a forward extension of the users epoch trigger.
2. HTTP/WS/MCP validate the primary global epoch, then use the trusted reconstructed
   AuthorizationContext. Missing epoch fails closed. Versioned schema failures
   never fall through to old flags. Existing resource policy and target
   App/Solution membership/status reads remain live; request headers do not
   become authority. In-process and impersonation callers keep fresh resolution.
3. Postgres `human_auth_sessions` is canonical: stable `sid`, user FK, current
   SHA-256 JTI hash, expiry and revoked time. All human refresh credentials are
   signed `type=refresh` with `sub`, `sid`, `jti`. Rotation is single-use and
   extends the existing configured seven-day flow. Redis no longer holds human
   refresh authority. No per-user authority epoch/cache is added.
4. Issue/refresh mint a candidate using the existing independent REPEATABLE READ
   minter before opening a session mutation transaction. Refresh has a short,
   nonauthoritative registry preflight to avoid expensive minting for already
   invalid credentials. After mint, lock the User row, recheck fresh eligibility
   and the exact live sid/current-JTI/expiry, rotate and commit before publishing.
   This avoids holding a pool connection while mint waits for another from the
   same pool. Never remint inside that lock. A candidate superseded by an
   authority write is simply challenged on first use.
5. Session-scoped logout revokes stable sid even if its presented predecessor JTI
   has rotated. Revoke-all and credential resets revoke every family. Registry
   revocation and global epoch bump commit atomically under the same user lock.
   Revoke racing after preflight/mint wins at locked recheck. Missing sid cannot
   accidentally become revoke-all. Callers finish identity/enrollment transactions
   before the session service; credential-reset consumers use its explicit
   in-transaction revoker.
6. Browser, CLI and MCP **intentionally share one human-session refresh credential**
   and the same access JWT audience/authorization decisions, matching the approved
   one-token-for-people design. Either HTTP refresh adapter can redeem it. MCP's
   `mcp:access` is protocol/tool gating, not narrower user authority. Runtime
   refresh, machine enrollment, embeds, MFA/state and preview capabilities remain
   purpose-distinct and cannot redeem as human sessions.
7. Cookies transport the same access JWT, not an opaque authority lookup. Short
   JWTs use one cookie; larger JWTs use a count/digest manifest and ordered
   HttpOnly/Secure/SameSite chunks of at most 3600 bytes, each complete Set-Cookie
   below 4096 bytes. Missing/mixed/corrupt chunks fail closed. Cookie max-age uses
   actual JWT expiry. Logout/revoke-all delete received fragments. Old unreferenced
   chunks are inert and expire with their original JWT. Total HTTP header limits
   still apply; the shared encoded-token warning above 6144 bytes remains.
8. Browser and SDK transports replay only the authentication boundary's exact
   `refresh_required` challenge, budget three per logical call; no replay of an
   effect-producing generic 401. Web Locks serialize browser refresh; an ended
   session while waiting never counts as successful refresh. Ordinary human
   WebSockets use HttpOnly cookies and revalidate before protected events; after
   refusal the client refreshes/reconnects. No new polling loop is added.
9. **Legacy humans reauthenticate at cutover.** Both old human access JWTs and
   Redis-only refresh JTIs are refused. Old MCP refresh was never registered and
   cannot be accepted through a compatibility bypass. Runtime PR1b's live legacy
   bridge is unchanged and still required until PR1c.

### Deployment Gate: Maintenance Cutover, Not a Rolling Human-Auth Activation

No production deployment is authorized by this chat. A future PR2 release must:

1. Block/drain all API traffic, including existing WebSockets, before mixed
   validators can serve. Do not treat an ordinary rolling deployment as safe.
2. Apply the forward session/eligibility migration with its bounded locks.
3. Start and verify every serving API replica uses the exact PR2 build, with no
   old API or separately hosted old human issuer remaining behind ingress.
4. Rebuild deployed V2 Apps with the release SDK through `application.sdk_update`
   (or source redeploy if retained source is absent). Old deployed bundles stop
   on a 4001 socket close and cannot recover the newly enforced human epoch
   invalidation. Verify completed jobs and active SDK provenance before reopening
   those Apps. Keep SDK build workers on the selected release as well.
5. Restore traffic only after homogeneous validation and App SDK updates are
   proven; all people sign in again. Runtime PR1b protocol/launcher compatibility
   remains supported.
6. After activation, rollback below PR2 is unsupported: an older validator can
   resurrect legacy credentials it still trusts. Blocking ingress only during
   rollback or asking users to sign in does not revoke those credentials. Keep
   PR2 validation in any repair build; a lower-version recovery requires a
   separately reviewed credential-revocation and secret-encryption recovery plan.

Old APIs accept legacy flags without epoch checks and reject new human JWTs;
leaving one serving would permit revocation bypass and break new sessions.

## Delivery and Verification Work List

- [x] Record consolidation as unfinished in an isolated worktree.
- [x] Inspect complete chronology, approved amended spec, runtime plan and gap notes.
- [x] Build the cross-surface coverage inventory above.
- [x] Independent Astra architecture review; replace Redis dual-write/JTI-only proposal
      with durable session families, explicit maintenance cutover and WebSocket checks.
- [x] Finish shared human claims/session implementation and all consumer adjustments.
- [x] Focused Docker API type/lint, auth/runtime/authorization/consumer/contract tests.
- [x] Live human refresh/revocation, concurrency and credential-reset verification.
- [x] Focused browser/V2 SDK auth and one Playwright happy path.
- [x] Independent exact-candidate security review and dispositions.
- [x] Product Updates classification/prose, current-main clean candidate and PR #1149.
- [ ] Protected CI/merge lifecycle with existing authorization; no production deploy.
- [ ] Refresh verified Product Updates inventory after the source merge, prove the
  required notice is in the prepared dev-image bundle, and merge the content PR.
- [x] Benchmark comparison coordinated with the sibling, without editing its harness
      or claiming unavailable measurements. Performance benefit remains unmeasured.

Final focused commands, results, timings and limits are in
[Authentication Verification](2026-10-30-authentication-verification.md). All
known diagnostic failures have concrete dispositions in the independent review
record. No full-suite or performance success is claimed.

Initial diagnostic: **58 focused unit tests passed**, no failures/errors/skips;
JUnit `/tmp/bifrost-auth-initial-unit.xml`. API pyright: zero errors/warnings.
Ruff found one unused import, now removed; final quality is pending. A first
browser diagnostic passed **124 focused Vitest tests**, tsc passed, lint had zero
errors and one pre-existing `seed-review-pack.ts` console warning; subsequent
Web Locks corrections require focused rerun. These are not final-candidate claims.

Session retention: active users' expired rows are pruned on new issue. Expired
rows for users who never sign in again still need a bounded operational retention
policy; do not invent a feature-specific background job.

## PR1c: Explicitly Deferred to a Later Release

Do not remove the live runtime bridge in PR2. A later release must have shipped
PR1b and confirmed rollout. Its fail-closed migration requires no live legacy
workflow/service attempt and no connected legacy launcher. Upgrade launchers and
restart listed legacy services through supported operations first. PR1c must refuse
protocol-1 launchers at hello and routing, remove the bridge/minters/rotation and
legacy ServiceControl fields together, refresh the CLI contract, and preserve
operator evidence. No deployment here establishes this prerequisite.

## Evidence and Host Coordination

- Chronology: primary `.worktrees/pr-sweep-20261029/docs/plans/2026-10-29-pr-sweep-idle-churn-tokens-handoff.md`.
- Runtime plan: primary `.worktrees/token-pr1b/docs/plans/2026-10-30-token-pr1-implementation-plan.md`.
- Gap notes: `/home/jack/bifrost-bench-evidence/notes/token-spec-gaps-20261029.md`.
- Approved design: `docs/superpowers/specs/2026-10-27-access-token-claims-design.md`;
  this document records the PR2 implementation decisions and review corrections.
- Sibling owns capacity/memory work, benchmark scripts and measurements. Its old
  confirmation-running claim was stale; this chat never assumed or restarted it.
- Acquire `/tmp/bifrost-capacity-host.lock` with flock before every expensive
  build/test/debug workload, retain until owned stacks stop, inspect older activity,
  and record requests/state in `/tmp/bifrost-capacity-coordination.md`. Never
  SIGSTOP benchmark children or wait for another owner while holding the lock.
