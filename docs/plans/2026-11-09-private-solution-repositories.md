# Private Solution repositories through a GitHub App

## Outcome and scope

Extend the platform Git authentication introduced in #1292 / #1295 so an
operator can preview, install and update Solutions from explicitly authorized
private GitHub repositories. Preserve the workspace Git connection and existing
public-repository and ZIP install paths. This is a dependent follow-up based on
114d0fcdc990fbf2f9c6d234631750ec087d4970.

The user authorized implementation, local qualification, publication and normal
PR stewardship. Live App registration/installation/transfer, credential/access
changes, production deployment and human approval controls remain outside this
delivery. Use synthetic provider credentials and isolated Docker fixtures.

## Current seams

- Solution preview, first install, Git reconnection, connected updates and
  update checks share `solutions/git_sync.py:clone_repo_to_dir`. It currently
  performs an unauthenticated shallow clone.
- Existing Solution authorization remains authoritative: repository access does
  not grant `solutions.deploy`, change tenant scope or admit a new writer.
- The App setup in `shared/github_git_app.py` binds callbacks to the initiating
  user/scope/setup, encrypts pending secrets and verifies installation/repository
  ownership. Its active configuration currently belongs to workspace Git.
- `GitAppCredentials` already coalesces encrypted token refreshes and enforces
  shared per-installation quota cooldown. Preserve that path, adding an explicit
  read permission and a separate cache identity for Solution fetches.

## Connection contract

Use reusable, scoped Solution-source connections independent of workspace Git.
One connection identifies one GitHub App installation and an explicit allowlist
of repository IDs and canonical names. Several Solutions can use it, including
different repositories. Several installations can have separate connections.

Store the connection in the existing `SystemConfig` encrypted-byte facility,
with a UUID identity, exact organization/global scope, generation and enabled
state. App keys/client secrets and authoritative repository bindings are sealed
with the existing encryption key; normal API responses expose only identity and
repository metadata. No integration SDK/secret resolver or portable export may
return credentials or instance-specific connection IDs.

Add a nullable `git_source_id` reference on Solution installs through an additive
migration. It selects a connection; the existing canonical repository URL selects
one explicitly allowed repository. This environment-local reference is not part
of a portable Solution definition. Existing rows remain public/anonymous unless
an operator explicitly selects a connection. Disconnecting a source must leave
bound Solutions failing closed, never switch them to anonymous or personal auth.

Source scope must match the Solution install/preview scope exactly. Global
sources are not an implicit fallback for organization installs. A source URL must
be canonical GitHub HTTPS, without credentials, query or fragment, and must match
the selected allowlisted repository before any token is acquired or sent.

## Setup and operator flow

Extend the existing App setup with a server-persisted `solution` purpose. Keep
manifest registration, manual Existing App setup, callback validation and recovery
in the existing machinery. New Solution-only manifests request Contents read;
existing Apps may have more permission, but every Solution token is narrowed to
Contents read and exactly one repository. Setup purpose cannot change midway.

Allow explicitly reusing the workspace App registration or a saved Solution
connection without changing workspace configuration. Fresh GitHub authorization
verifies the installation and repository selection. Save a separate encrypted
Solution connection; adding another verified repository to the same App,
installation and scope extends its allowlist. Pending OAuth user tokens expire
and are deleted after setup; unattended updates use App installation auth.

Expose source management in GitHub settings and a source/repository selector in
Solution install/reconnect controls. An operator can also configure Solution
access without first connecting a workspace repository. Keep source management
behind existing GitHub administration authority and source use behind existing
Solution permissions. CLI/MCP accept the non-secret source ID wherever their
existing Solution repo install/preview/reconnect operations accept repo coordinates.

## Fetch and lifecycle

Wire source resolution into repo preview/import, first install, reconnect,
connected sync and scheduled version checks. Reuse existing Solution deploy,
PlatformJob, resource locks and notifications. Do not create an execution engine,
job table or browser polling loop. Preserve validated staged bytes for first
install so its worker does not silently fetch a different commit.

Reuse the existing safe per-command Git authentication environment: credentials
never enter remote URLs, persisted Git config, job payloads, output or exceptions.
Reject authentication-related redirects and credential-bearing caller URLs.
Recheck source generation/availability around authenticated fetches; changed or
disabled sources cannot authorize deployment of a newly fetched snapshot. Keep
revoked/expired installations, repository removal, token-refresh races and rate
limits explicit. No fallback to another identity or broader connection.

The generation check runs after the clone and before returning its snapshot to
the caller. Once an authorized first-install request has accepted staged bytes,
its existing durable deployment semantics remain intact: disconnecting Git
prevents later fetches and does not retroactively cancel accepted deployments.

Deleting/disabling a connection invalidates future credential acquisition and
retains a resolvable disabled reference for installed Solutions. Reconnect is
explicit. Connection identity/app/installation changes require repository
verification; cached tokens include generation, repository and permission.

## Verification

- Unit tests: exact-scope/source/URL binding; readonly token permissions;
  multiple repos; token cache separation/coalescing; disable/generation races;
  revoked installations/repo removal; quota cooldown; error/log redaction.
- Endpoint tests: authorization and forged/foreign source refusal; encrypted
  storage and safe DTOs; public/PAT workspace behavior unchanged; setup purpose
  and OAuth replay boundaries; portable exports omit local source authority.
- One API lifecycle uses a private fixture repo for a real first-install job
  and subsequent real Git update job. The second job proves fresh credential
  resolution and the version transition. Lower-level tests own denial cases.
- One browser happy path selects a private source, previews and installs a tiny
  Solution through existing progress. Component/service tests cover input and
  scope changes. No live GitHub registration or deployment is implied.
- Run focused Solution/Git/App/authorization and DTO/CLI/manifest tripwires,
  generated OpenAPI types, API/client quality, migration qualification and the
  relevant browser path through canonical `./test.sh` commands. Full hosted CI
  and normal maintainer security review remain merge requirements.

No generic event integration, repository writes, GitHub Enterprise, publisher
licensing, Marketplace or production rollout is included.

## Publication

Tracked in #1297. The user requested a draft follow-up and official stacking on
#1295. GitHub's native stacks require all branches in the same repository;
#1295's head is in the contributor fork, so the existing PR cannot participate.
Preserve #1295 and its review history. Publish the child as an explicitly
dependent draft and retain its parent commit boundary. Do not claim a native
GitHub stack or replace the parent without a separate decision. Once the parent
lands, retarget/rebase the child on main and requalify its diff and gates.

Reference: https://docs.github.com/en/pull-requests/reference/stacked-pull-requests

## Parent connection-safety follow-up

Incorporate #1295's maintainer fixes through `a04da06a3`: workspace repository
changes are rejected before readiness, credential issuance and activation.
Request-time setup expiry remains enforced; an already admitted Git PlatformJob
may complete after that expiry while retaining the connection-generation fence.

These checks apply to workspace connections. A Solution setup independently
verifies its selected repository and obtains a Contents-read token; an existing
or changed workspace connection does not constrain its repository. Solution
setups remain ineligible for workspace connection admission, including execution
of an already admitted job. Focused regressions cover both setup purposes and
retain the parent's transition and expiry tests.

## Rolling upgrades

`solution.git_sync` and `solution.update_check` enqueue payload version 2 so old
runners cannot claim new authenticated fetches. Current runners retain the exact
version 1 payload schemas and apply the same source validation to existing jobs.
Upgrade all APIs, schedulers and executors before configuring private sources;
drain or stop old executors first. An old scheduler can still enqueue its own
version 1 sweep, so mixed-version operation is not qualified for private sources.
