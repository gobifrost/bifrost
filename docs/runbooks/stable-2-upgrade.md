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

## Remaining App Compatibility Gate

SDK2 bundles remain supported without a rebuild. The server denies stale
protected operations, routes an internal refresh request to the matching host,
and gives the old same-origin query-token transport a transient retry. The host
uses the existing serialized refresh flow; the unchanged App reads the new
bridge token and resubscribes. Invalid or revoked credentials remain terminal.
See [Application SDK Updates](application-sdk-update.md) for this behavior and
optional automatic source rebuilds.

The full stable release still needs immutable-image qualification of an existing
App through token expiry and an authority change before any rebuild. Retrieving
an unchanged bundle, or rebuilding an App with retained source, alone does not
prove that live-subscription boundary. The focused SDK2 fixture and live socket
checks supplement that release gate; they do not replace the final image run.

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
  --target-source FULL_SOURCE_COMMIT \
  --evidence-dir /absolute/existing/evidence-directory
```

Qualification must cover direct migrations, a real 1.4.1 worker calling the new
API, pending-work recovery exactly once, and populated organizations, users,
roles, encrypted integrations, linked workflow/form/agent records, and an
unchanged deployed App. Verify the old bundle's behavior before any optional SDK
update. Agent tool bindings can be checked without an external AI-provider call.

The existing [populated staged receipt](../releases/evidence/2.0.0-populated-upgrade.json)
used an intermediate image and an App rebuild. It remains evidence for the
specific data-preservation and recovery assertions it records, but does not
qualify a direct compatible upgrade or the final release artifact.
