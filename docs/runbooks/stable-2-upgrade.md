# Upgrading From 1.4.1 to 2.0

The compatibility target is a normal image update directly from stable 1.4.1.
There is no intermediate development image or protocol-start prerequisite.
People may need to sign in again in browsers, CLI, and MCP clients. Passwords,
MFA enrollments, passkeys, and encrypted integration credentials are retained.

## Image Update

Use the normal deployment update for init, API, client, and executors, preserving
PostgreSQL, object storage, the platform secret, and existing broker data. Run
the selected image's forward migrations through the normal init service. Do not
create protocol rows manually: an upgraded API records its own protocol start.

Legacy workflow credentials identify their durable execution; the new API
resolves its current user, scope, and workflow powers from database records.
Legacy signed administrator, Solution, and repository flags confer no authority.
A forward migration fills missing lineage for old Running/Cancelling executions;
the credential adapter also handles an old worker claiming a waiting execution
after that migration. Existing lineage and historical actor records are retained.
Ended executions and ambiguous or missing authority fail closed.

The existing RabbitMQ recovery runs automatically in the new API. Keep its broker
URL and data available while recovery completes. Recovered messages become
canonical durable dispatches before acknowledgment. A lost Redis context fails
its run visibly; a broker-drain record alone does not prove every run succeeded.

Retiring the scheduler or RabbitMQ and adding workers are separate, optional
operations. Account for every broker queue and unacknowledged message before
retirement; do not delete or replay poison messages as part of the image update.

Use the CLI supplied by the upgraded API for the current command/response
contract. Reauthentication does not upgrade a separately installed CLI binary.

## App Compatibility

SDK2 bundles remain supported without a rebuild. The server denies stale
protected operations, routes an internal refresh request to the matching host,
and gives the old same-origin query-token transport a transient retry. The host
uses the existing serialized refresh flow; the unchanged App reads the new
bridge token and resubscribes. Invalid or revoked credentials remain terminal.
See [Application SDK Updates](application-sdk-update.md) for this behavior and
optional automatic source rebuilds.

The [direct-image qualification receipt](../releases/evidence/2.0.0-direct-upgrade.json)
records the real 1.4.1 upgrade to immutable API/client images from source
`e7c95328aa87bc3a9313ca05c694b5e8c02f0a4a`. The original SDK2 App recovered
after real access expiry and an authority-epoch change without losing local
component state; its revoked session received no later table update. Its
deployment pointer, retained source and served bundle remained unchanged.
The same rehearsal preserved the seeded entities and encrypted credentials,
finished a held source-worker SDK call and recovered one queued workflow exactly
once. This covers a single API replacement, not a mixed-validator API fleet.

## Recommended App SDK Updates

After the image update, review App SDK status in Apps and update eligible Apps
to adopt hosted SDK delivery. This is recommended maintenance; existing supported
SDK2 Apps do not need a rebuild to keep working.

Automatic App SDK Upgrades is opt-in and defaults to off. It requires eligible
retained source and a reproducible lockfile. You can instead use the manual
Update SDK action. Follow progress in Notifications and confirm the update
finishes successfully; a queued or failed build has not updated the App. See
[Application SDK Updates](application-sdk-update.md) for eligibility and commands.

Check available memory before scheduling builds. The unified executor can run
App SDK jobs on either an existing scheduler or worker. If an old scheduler's
limit prevents builds, consider consolidating that capacity into resized
workers. The [Sizing Guide](kubernetes-worker-deployment.md#resource-sizing)
recommends 2 GB per worker as a starting point; larger App builds may need more.
Establish healthy replacement capacity and let the retiring executor finish its
active work before removing the scheduler. Broker
retirement remains a separate operation and requires accounting for every queue
and unacknowledged message.

A Solution source redeploy builds its Apps locally before uploading their build
output, which can relieve server build pressure. It also reconciles the full
Solution, so review the source and proposed changes first. Independent App
redeploys currently upload source and build on the server; redeploying one from
your workstation does not offload that build. A failed SDK rebuild leaves the
previous active deployment in place.

## Human Authentication

The new session registry intentionally does not accept old human access JWTs or
Redis-only refresh credentials. Signing in again establishes the current durable
session family. Logout and revocation remain enforced. Do not keep an older API
validator reachable after the update: it could accept credentials revoked by the
new session system. The serving API fleet must finish replacing the old build.

Rolling back below the human-authentication cutover is unsupported; use a forward
repair build. See [Human Authentication Cutover](human-auth-cutover.md) for the
session semantics and [Authority Epoch](authz-epoch.md) for trigger-disabling
maintenance and backup-restore rules.

## Disposable Image Rehearsal

The release coordinator uses an isolated Compose project, synthetic data, and no
host ports. Evidence must be outside the checkout:

```bash
python3 scripts/release/stable_2_upgrade_rehearsal.py \
  --run-id stable2-qualification \
  --target-api-image ghcr.io/gobifrost/bifrost-api@sha256:TARGET_DIGEST \
  --target-client-image ghcr.io/gobifrost/bifrost-client@sha256:CLIENT_DIGEST \
  --browser-runner-image sha256:LOCAL_PLAYWRIGHT_IMAGE_ID \
  --target-source FULL_SOURCE_COMMIT \
  --evidence-dir /absolute/existing/evidence-directory
```

Build the browser runner from `client/Dockerfile.playwright` and use its local
immutable image ID. The browser shares the production client's private network
namespace and reaches its real NGINX at `http://localhost`; no host port or
browser authentication bypass is used. The App is built once by 1.4.1 with its
original SDK2, then its deployment pointer, source and served bundle hashes must
remain unchanged throughout the upgrade.

The browser fixture shortens one newly signed human access token to 90 seconds
while preserving its durable session and current claims. It waits for real
expiry, changes an unassigned role's permissions to advance the authority epoch,
and revokes that browser family. A separate control family writes the table
updates; it never rotates the browser's refresh cookie. Live values and local
component state prove recovery without remounting, and a subsequent update must
not reach the revoked subscriber. Production token lifetimes are unchanged.

Qualification must cover direct migrations, a real 1.4.1 worker calling the new
API, pending-work recovery exactly once, and populated organizations, users,
roles, encrypted integrations, linked workflow/form/agent records, and an
unchanged deployed App. Verify the old bundle's behavior before any optional SDK
update. Agent tool bindings can be checked without an external AI-provider call.

The existing [populated staged receipt](../releases/evidence/2.0.0-populated-upgrade.json)
used an intermediate image and an App rebuild. It remains evidence for the
specific data-preservation and recovery assertions it records, but does not
qualify a direct compatible upgrade or the final release artifact.
