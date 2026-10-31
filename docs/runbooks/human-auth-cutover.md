# Human Authentication Cutover

This runbook describes a future PR2 release. It does not authorize deployment.
Runtime-token PR1a/PR1b and their later PR1c prerequisites are unchanged; see
[Authority Epoch](authz-epoch.md). The implementation/coverage ledger is
[Authentication Consolidation](../plans/2026-10-30-authentication-consolidation.md).

## Required Maintenance Boundary

**Do not activate PR2 with an ordinary rolling API deployment.** An old API accepts
legacy human flags without checking the epoch. During mixed serving, it can
accept a credential the new API has revoked; it also cannot accept the new human
claim format. A new Postgres migration alone does not prevent that bypass.

1. Select the immutable PR2 release image and record the currently running image
   identities. Confirm dependencies are healthy and a recovery plan is available.
   Inventory deployed V2 Apps and verify retained source is available for SDK
   updates, or obtain their original source for redeployment before maintenance.
2. Block API ingress and drain all API requests and WebSocket connections. Include
   direct API endpoints and separately exposed MCP endpoints, not just the web
   frontend. Stop old serving APIs before allowing new ones to issue credentials.
3. Apply the forward migrations. Session-table creation and the users authority
   trigger extension have one-second lock/statement caps. A timeout rolls back
   the upgrade; diagnose the holder and retry while traffic remains blocked.
4. Start the selected new API build. Verify every serving replica and every
   exposed MCP mount uses the same selected PR2 validation code; no old human
   issuer or validator may remain reachable behind ingress. Use deployment image
   digests, not a moving tag or a successful migration, as build evidence.
5. Rebuild existing V2 Apps with the selected release's SDK before reopening
   those Apps. Their deployed bundles retain an older SDK after a platform
   upgrade; that client stops on a 4001 socket close instead of refreshing and
   reconnecting after an authority change. Use the supported
   `bifrost apps sdk update APP` operation and verify its completed PlatformJob
   and active SDK provenance. If source is unavailable, redeploy from the
   original source. See [Application SDK Updates](application-sdk-update.md).
   Run these operations through controlled operator access to the new serving
   build while ordinary ingress remains blocked; the SDK build worker must also
   use the selected release. Do not reconcile unrelated Solution entities.
   This is SDK contract **3**: older recorded contracts become `update_required`,
   rather than merely a newer fingerprint being available.
6. Restore ingress only after the homogeneous serving fleet and App SDK updates
   are verified. Every
   person signs in again in browsers, CLI and MCP clients. Passwords, enrolled
   MFA and passkeys are retained; legacy access JWTs and Redis-only refresh
   credentials are not migrated or accepted.
7. Verify login/MFA, a normal request, refresh rotation, logout/revoke-all, MCP
   reconnect and WebSocket reconnect against the selected build. A used or
   revoked refresh must fail. Password changes and administrative MFA resets
   sign out every human session.

Once activated, rolling back below PR2 is unsupported. Old code can resurrect
legacy credentials it still trusts, even after the new session registry revoked
all families. Keep the new validation in a repair build. A lower-version disaster
recovery needs a separately reviewed revocation plan; do not casually rotate the
platform secret because it also protects stored encrypted credentials.

## Credential Semantics

Browsers, CLI and MCP intentionally share one human access/refresh credential
class and JWT audience. A refresh family has a stable session ID, a single
current JTI hash, expiry and revocation state in Postgres. Either human refresh
adapter can redeem it. Human eligibility and current grants are reevaluated at
mint; per-request authorization checks the primary global epoch. No Redis copy
of cheap Postgres facts is used.

Session mutation happens after the candidate minter has released its own snapshot
connection. The locked user/session recheck decides whether rotation can commit;
revocation and epoch bump commit together. An authority write between candidate
mint and commit merely makes that candidate refresh-required on its first use.

Runtime refresh is a different, live-run-bound credential. Workflow API keys,
launcher enrollment HMAC, embed/MFA/state/impersonation and Solution preview
capabilities keep their scoped verification boundaries. None redeems as a human
session.

## Browser Transport

The same signed JWT travels in Authorization headers and HttpOnly cookies.
Compressed human claims normally fit one cookie. Larger credentials use an
integrity-bound manifest and ordered cookie fragments of at most 3600 bytes;
this changes transport, not authority. Signature, exact schema, expiry and epoch
validation still apply after reassembly. Missing or mismatched fragments fail
closed. Cookie expiry follows JWT expiry; unreferenced fragments are inert.

Total proxy header limits still apply. Monitor the shared encoded-token size
warning above 6144 bytes; do not claim that fragmenting a cookie removes proxy
header limits. Configure proxy limits consistently with supported workloads.

Browser refresh requires the Web Locks API in a supported secure context. If
unavailable, the client refuses rotation instead of performing an unsafe unlocked
single-use refresh. Normal browser WebSockets use HttpOnly cookies. A socket
checks its original credential before each protected input or delivery; epoch
change or expiry closes it so the client refreshes and reconnects. No protected
message is delivered using an ended session.

## Retention

New human session issuance prunes the same user's expired families. Expired rows
for users who never sign in again remain a tracked operational retention gap.
Use the shared platform maintenance/job infrastructure if a global bounded
retention task is added; do not create a parallel feature-specific worker.
