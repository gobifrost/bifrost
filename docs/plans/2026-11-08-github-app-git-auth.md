# GitHub App Authentication for Git Sync

Status: scoped design, 2026-11-08. Thomas reports maintainer approval for
replacing the PAT used by Bifrost's own GitHub integration. Manifest registration
and OAuth-assisted connection are required in the first deliverable.

This replaces the Git-auth direction in the
[earlier integration proposal](https://github.com/gobifrost/bifrost/blob/25a1908a59edd31471b1e33afaeedd1c4e5439db/docs/plans/2026-11-06-github-app-integration.md). Generic
integration duties remain separate from this native platform feature.

## Outcome and Scope

Implementation is tracked in [#1292](https://github.com/gobifrost/bifrost/issues/1292).

An administrator can create an operator-owned GitHub App from Bifrost, approve
its installation in GitHub, select an existing repository and branch, and use
the current connect, preview, pull and push experience. Normal setup requires no
PAT, copied PEM, client secret or installation ID. GitHub still owns the approval
screens and any organization-owner approval requirements.

Use one App registration per deployment and the existing workspace Git
connection scope. Preserve current authorization and scope resolution; do not
introduce a general installation-to-tenant registry. A GitHub account or
organization selected by the operator owns the App. Default to a private App
installed on its owner's account. Cross-account installation requires an
explicit choice of public visibility, disclosed before registration.

This delivery has no general event subscriptions, webhook ingress, native
integration SDK, issue/PR/check automation, repository creation, App transfer,
GitHub Enterprise support, or fleet/broker changes. It does not depend on the
integration package or webhook-deduplication PR #1285.

## Setup Journey

1. **Create GitHub App.** In GitHub settings, the administrator chooses the
   GitHub owner, App name, visibility and whether syncing GitHub Actions workflow
   files is required. Bifrost builds a manifest with the deployment's configured
   external origin and fixed callback paths. Use Contents read/write, implicit
   Metadata read, and Workflows write only when explicitly selected. Disable
   webhook delivery and request no events. Never derive callback origins from
   untrusted request headers or accept arbitrary return URLs.
2. **Register in GitHub.** Submit the manifest to GitHub's personal-account or
   organization registration page. Use a short-lived, single-use state bound to
   the initiating Bifrost user, deployment, effective scope and setup attempt.
   GitHub approval creates the App in the chosen account; Bifrost does not
   register a centrally hosted App on behalf of every deployment.
3. **Capture Credentials.** Validate the registration callback, then exchange
   its temporary code at `/app-manifests/{code}/conversions` within GitHub's
   one-hour window. Save the returned App ID, client ID, App identity, encrypted
   private key and encrypted OAuth client secret on the server before continuing.
   Discard the unused webhook secret. Return only credential-presence flags and
   non-secret setup status to the browser.
4. **Authorize and Install.** Start an explicit GitHub App OAuth flow with fresh
   state for this attempt. Use `request_oauth_on_install: false` so authorization
   starts under Bifrost's state contract. Guide the user to install the App and
   select repository access when necessary, then resume verification after the
   installation setup URL returns. User authorization and installation are
   separate steps; neither is inferred from the other. Pending organization
   approval is a resumable state, not a connected account.
5. **Verify and Select.** Use the temporary user token to enumerate accessible
   installations and repositories. Verify the selected installation belongs to
   this App and the selected repository is accessible to both the installation
   and the authorizing user. Treat any callback `installation_id` as a hint.
   Display the verified owner and repository before confirmation. Resolve and
   retain immutable installation/repository IDs alongside display names.
6. **Connect.** Run the existing connection preview and confirmation flow for
   the selected repository and branch. Activate the new auth configuration only
   after verification and the normal connection operation succeed. Discard the
   temporary user token when setup completes or expires. Later Git operations
   authenticate with installation tokens and do not depend on that user's login.

Keep the previously proposed manual configuration path as an explicit
**Use Existing App** option. It uses the same verification and Git credentials
service. Manifest setup is the primary path, not a deferred enhancement.

## Credentials and Runtime Contract

- Extend the existing encrypted GitHub configuration with an explicit auth kind,
  App credential reference, installation ID, repository ID and credential
  generation. Keep App secrets outside portable manifests, exports, normal
  integration resolution, API responses, logs and PlatformJob payloads/results.
  Pending setup credentials use the same encryption and admin access boundary.
- A shared credential resolver signs an App JWT and requests an installation
  token scoped to the selected repository and required permissions. Both REST
  GitHub calls and Git subprocesses resolve current credentials before network
  work. Queued/recovered jobs carry connection identity, not a saved token.
  Pin their repository identity and reject a replaced binding rather than
  silently retargeting an old job to the newly configured repository.
- Cache tokens only until shortly before their returned expiry; installation
  tokens currently expire after one hour. Coalesce concurrent refreshes across
  replicas. Fence cached tokens and in-flight refresh results by credential
  generation so replacement or disconnect cannot restore old authority.
- Use `/installation/repositories` for App repository inventory rather than
  `/user/repos`. During setup, also intersect with the user's accessible
  repositories. Validate selected repository access before activation and on
  subsequent credential acquisition. Surface revoked installations, removed
  repositories and missing permissions as actionable connection errors.
- Preserve fixed GitHub.com endpoints, timeouts, bounded pagination and existing
  rate-limit handling. Tokens for one installation share its quota; minting
  additional tokens does not increase it. Preserve per-command Git authentication
  without putting credentials into remote URLs, `.git/config` or checkpoints.
- Preserve previews, conflicts, branch protections, commit-author settings and
  existing PlatformJob execution/progress. Short setup exchanges stay request
  scoped; any operation needing durable work extends PlatformJob. No new worker,
  scheduler, job-status endpoint or browser polling loop.
- Existing PAT connections continue until explicitly migrated. A successful
  switch removes the replaced PAT credential. An App error never falls back to
  personal credentials. Disconnection clears local credentials and caches; it
  does not silently uninstall or delete the GitHub App.

## Setup State and Recovery

Use a setup attempt with a bounded lifetime and server-side state, separate from
the active connection. Reuse `oauth_state_core.py` with a Git-specific audience
and consume-once nonces. Bind callbacks to the initiating identity and scope;
recheck current Bifrost authorization before saving credentials or connecting.
Use distinct state for registration and OAuth. An installation setup URL alone
cannot change credentials or bind a repository. Strip codes/state from the
browser's final URL and redact them in request/access logs. Temporary user
tokens are encrypted if retained across setup requests and expire with the
attempt; do not add a permanent user-token refresh service.

Serialize registration conversion per attempt. Record that conversion started
before calling GitHub. Repeated callbacks reuse a completed local result and
must not perform a second conversion. If GitHub may have created the App or
consumed the code but credentials were not durably saved, show **Registration
Needs Review** and the relevant GitHub settings link. Do not automatically
re-exchange an uncertain code, create another App, or claim remote rollback.
The operator can inspect GitHub and explicitly resume using an existing App.

Cancellation, expired state, browser refresh and process restart preserve the
last working connection. After credentials are saved, a new setup session can
resume installation/authorization without registering another App. Returning
with the wrong GitHub account, forged installation IDs or cross-scope state
cannot activate a connection. Manifest or OAuth permission errors explain the
required operator action without silently broadening App permissions.

## Implementation Seams and Evidence

Inspected upstream source: `origin/main` at
`dfcbe4161dac965e9167c2b4b81a23f7737162a3` on 2026-11-08.

- `api/src/services/github_config.py`: encrypted PAT configuration and current
  scope behavior. Extend credential handling without exposing App keys.
- `api/src/services/github_api.py`: accepts a fixed token; repository discovery
  uses `/user/repos`. Add refreshable credentials and App-aware discovery.
- `api/src/services/github_sync.py`: API and Git operations, per-command auth and
  redaction. Preserve the sync engine while replacing credential acquisition.
- `api/src/jobs/platform/git_operation.py`: resolves configuration in the worker.
  Resolve the current auth generation again when queued/recovered work executes.
- `api/src/routers/github.py` and `client/src/pages/settings/GitHub.tsx`: existing
  authorized settings, repository selection and connection-preview surfaces.
- `api/src/services/oauth_state_core.py`: signed, audience-bound state and
  consume-once nonce helpers. Reuse primitives, not the generic OAuth token store
  or its unrelated integration configuration lifecycle.

Implement against current main in a focused Git-auth PR. Salvage only reviewed
key-validation, JWT-signing and token-exchange primitives from preserved work;
do not carry forward the broad native integration architecture from #1274.

## Acceptance and Verification

- One browser happy path proves manifest submission, mocked GitHub conversion,
  OAuth authorization, installation/repository selection and the existing
  connect preview/confirmation. It requires no manually copied credentials.
- Focused endpoint/database tests prove encrypted credential persistence,
  registration-callback replay/concurrency, uncertain conversion, restart/resume,
  denied/pending installation, expired/mismatched state, authorization changes,
  forged installation/repository IDs and preservation of a working PAT connection
  until a successful explicit switch.
- Unit tests prove JWT signing, repository/permission scoping, expiry and
  concurrent refresh, invalidation fencing, installation-wide quota/backoff,
  and redaction of keys, client secrets, OAuth codes and both token types.
- Existing Git tests plus targeted credential-consumer coverage prove connect,
  fetch/pull and commit/push use the selected installation across API and Git
  paths; queued/recovered operations refresh credentials. Revocation, repository
  removal and workflow-file permission failures fail without PAT fallback.
- Run focused tests through `./test.sh`, API quality and affected client
  tests/types/lint. Generate OpenAPI client types and run DTO/CLI/MCP/export
  tripwires for changed contracts. Normal review and merge-queue gates apply.
  Classify the eventual user-visible PR under Product Updates.
- A real private-repository registration/install/connect/pull/push exercise is
  separate live qualification with an explicitly authorized disposable App and
  repository. Fixtures prove source contracts, not live GitHub acceptance.

Delivery order: encrypted configuration/credential resolver; manifest registration
and safe resume; OAuth/installation verification; existing Git consumers and UI;
focused qualification and PR review. All are required for the first release.
`workspace.git` payload version 3 fences older executors that do not understand
App credentials. Upgrade API and workers before enabling setup. Versions 1 and 2
retain their exact payload schema; an unbound legacy job must be requeued after
an App migration rather than using a PAT or targeting the replacement connection.
This spec update performs no App registration, installation, credential change,
permission change, ownership transfer or production deployment.

## Provider References

- [Manifest registration](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest)
- [GitHub App user authorization](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app)
- [Installation setup URL and verification](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url)
- [Installation authentication](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation)
- [App permissions](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app)
