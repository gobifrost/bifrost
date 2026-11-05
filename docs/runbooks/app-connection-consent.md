# App connection consent

Bifrost CLI and inbound MCP clients share explicit personal approval. Signing in
or opening a connection link never approves an MCP client.

## Who approves

An active human can approve access to their own account. This does not register
an application as trusted for other people, grant roles, or widen organization
reach. Browser impersonation, embedded sessions, and workflow/service credentials
cannot approve new personal connections. Administrators retain the existing
external-MCP master switch; every MCP tool still checks the caller's current
access. There is no new administrator allowlist in this repair.

This follows the distinction between personal OAuth consent and organization-wide
app restrictions documented by
[GitHub](https://docs.github.com/en/organizations/managing-oauth-access-to-your-organizations-data/about-oauth-app-access-restrictions).
[Auth0](https://auth0.com/docs/get-started/applications/third-party-applications/user-consent-and-third-party-applications)
also requires consent for third-party applications. A registered app name is
self-reported metadata, not verified publisher identity.

## Connection behavior

- The screen shows the signed-in account, app name, registered return address,
  and the fact that the app receives the account's existing permissions and can
  remain connected. The CLI also shows its device code.
- Missing, null, or blank app names display **Unnamed App**. They do not prevent
  an otherwise valid connection. No remote logo or publisher document is fetched.
- A user-bound approval proof and signed pending request are required before
  issuing a code. The first approval or denial consumes the request atomically.
  Refresh/authentication challenges leave the pending request available. Approval
  also requires the matching live HttpOnly browser refresh cookie; an access
  token alone cannot create another durable session.
- A denied MCP request returns `access_denied` to the validated client callback;
  it never creates an authorization code.
- The CLI retains its device-code endpoints and response shapes. Existing human
  sessions and token refresh contracts are unchanged. Access-only bearer calls
  to the browser approval endpoints are intentionally refused; scripts must not
  automate consent using only an access token.

## Callbacks and expiration

Client registration metadata lives in PostgreSQL without an arbitrary expiration.
Existing Redis registrations are imported into durable storage on their first
connection, retaining their client IDs and callback lists. This one-way import
also covers old replicas registering clients during a rolling deployment; the
schema migration itself requires only PostgreSQL. Expired Redis registrations cannot be recovered; an affected
client must register again. Registration is public metadata, not consent or
account access, and the registration endpoint is rate limited.

Callback matching is exact for HTTPS and custom native schemes. HTTP loopback
callbacks may vary only their port, as required by
[RFC 8252 §7.3](https://www.rfc-editor.org/rfc/rfc8252#section-7.3).
The hostname, path, and query must still match; localhost and numeric loopback
addresses are not interchangeable. Userinfo, fragments, unsafe schemes, and
non-loopback HTTP callbacks are rejected. Existing callback query parameters
are preserved when adding encoded OAuth response parameters.

Pending consent links and authorization codes each expire after five minutes.
These are independent from token/session expiration. Pending links opened before
the upgrade may need to be restarted because they have no signed consent binding.
Normal client registration recovery is described in the
[MCP SDK migration guide](https://ts.sdk.modelcontextprotocol.io/v2/migration/upgrade-to-v2).

## Catalog review

The repair addresses the registered-callback and explicit-consent concerns in
[catalog PR 643](https://github.com/hashgraph-online/awesome-codex-plugins/pull/643#issuecomment-6611306789).
Acceptance by the external reviewer still requires re-review; local validation
must not be described as their approval.

The latest catalog-owned scan separately scores 73/100. Reproducing its pinned
scanner (`3.0.123`, action commit `caba2e96aa8ad2feb6cf6fca52442b52e22e779f`)
finds that it rejects `1.4.2-dev.1` as non-semver because it expects `X.Y.Z`.
Prerelease identifiers are supported by [SemVer](https://semver.org/#spec-item-9).
A controlled scratch-copy check changing only those version fields to `1.4.2`
scores 77/100. That is diagnostic evidence, not the candidate score. The actual
candidate retains its true prerelease version and scores 73; no suppression is
introduced.
