# Cloudflare Containers worker capacity

Operator runbook for deploying Bifrost's external-worker capacity provider on
Cloudflare Containers. The protocol is defined in
[`docs/architecture/external-worker-capacity.md`](../architecture/external-worker-capacity.md).

## What this deploys

### API-owned execution

The implementation builds on the provider from #1331.
It derives each launcher's control credential from the dedicated capacity token,
incarnation UUID and resource UID. The installation signing key never leaves
the API. Requirements reach the worker through authenticated control bootstrap.
Do not supply database, Redis, S3 or installation signing credentials in
WORKER_CONTAINER_ENV; this candidate rejects them.

The checked-in template declares `workflow.execute` and `app.compile.v2`.
Only advertise handlers and compute capabilities implemented by the pinned
image. With the matching API-only adapters, an image may also advertise agents
and summaries; privileged platform-job runners remain out of scope. Sharing an
image does not itself make any additional handler API-only.

API and launcher must be upgraded together for the bootstrap exchange; the
external handshake deliberately does not accept the installation signing key.
The provider resource protocol is version 2.

A Worker that implements the five capacity endpoints and starts container
incarnations on request. The API remains canonical: it decides demand, admits
workers, and fences enrollment. This Worker never sees job payloads.

## Connect Through Settings

1. Set the installation's deployment-owned `BIFROST_PUBLIC_URL` to its public
   HTTPS origin. Cloudflare containers need HTTPS and WebSocket access to it.
   The setup API rejects an alternative control origin.
2. Open **Settings → Workers → Scaling & Isolation → Cloudflare Workers**.
   Enter a Cloudflare API token with account access to deploy Worker scripts,
   manage Containers, and obtain registry push/pull credentials. See
   [Cloudflare's token permission reference](https://developers.cloudflare.com/fundamentals/api/reference/permissions/).
3. Choose a small maximum worker count and select **Connect Cloudflare**. If the
   token spans several accounts, choose the intended account when prompted.
4. Follow the shared job progress. The API stores the token encrypted, copies
   its matching published image into the account's registry, and reconciles the
   Worker, Durable Objects, container application, and workers.dev route through
   Cloudflare's APIs. Docker and Wrangler are not required inside the API.
5. **Provider Ready** confirms the provider configuration; submit a supported
   Normal job to verify the first physical worker. Use the capacity overview
   and Diagnostics to distinguish Starting, Ready, Lost, and cleanup pending.
6. Set the Cloudflare start categories. Ready local capacity is reused first;
   selected work can start Cloudflare before adding Kubernetes workers unless
   **Let Kubernetes Scale First** is enabled. With no local or Kubernetes capacity,
   Cloudflare handles all supported Normal work. Fast Path stays local.

The qualified profile is fixed at 4 GiB per worker. Its provider maximum and the
global managed-worker limit both apply; Kubernetes memory settings do not resize
Cloudflare. Start categories control provisioning, not affinity: an existing
Cloudflare worker may execute any supported Normal work.

Unpublished builds need a deployment-owned `BIFROST_WORKER_CAPACITY_IMAGE`
reference pinned by digest. Its embedded version must exactly match the API.
There is no alternate-image fallback. Legacy environment-based provider
configuration and the stored connection cannot be active together.

Running App deployment cancellation remains unavailable by its existing job
policy. API process loss stops untracked compute and settles the fenced claim;
it does not restore agent or App orchestration into a replacement API process.

## Manual Provider Deployment

## Prerequisites

1. A published worker image, **pinned by digest**, whose embedded build version
   you can read. A floating tag is not acceptable: the API fences reservations
   whose reported build differs from `WORKER_VERSION`.
2. A dedicated bearer token, 32–512 non-space ASCII characters, stored as a
   Worker secret: `npx wrangler secret put CAPACITY_TOKEN`.
3. HTTPS/WebSocket reachability from the container to the API. This spike does
   not provision database or Redis tunnels.

## Configure

Edit `deploy/cloudflare/worker/wrangler.toml`:

| Var | Meaning |
|---|---|
| `SCOPE` | Opaque provider scope. Must match the API side exactly. |
| `WORKER_VERSION` | Must equal the image's embedded build version. |
| `MEMORY_LIMIT_BYTES` | Fixed worker memory matching the container profile; independent of Kubernetes sizing. |
| `instance_type` (in `[[containers]]`) | Real memory and disk for each container; must be at least `MEMORY_LIMIT_BYTES` and large enough for the unpacked image. |
| `max_instances` (in `[[containers]]`) | Must be at least `MAX_WORKERS`: a start beyond the application's instance count is refused even when the scope registry would admit it. |
| `MAX_WORKERS` | Ceiling this provider will run. |
| `MAX_LIFETIME_SECONDS` | Upper bound the API may request; cannot exceed six hours, which is the platform's inactivity-timeout maximum. |
| `EXECUTION_HANDLERS` | Required JSON list of execution handlers implemented by the pinned image. |
| `COMPUTE_CAPABILITIES` | Required JSON list of semantic compute capabilities; declare `app.compile.v2` only once that image supports App compilation. |
| `WORKER_CONTAINER_ENV` | JSON object containing the API URL and runtime paths. Platform credentials are refused. Do not set `BIFROST_VERSION`: the pinned image carries its own build, and the reported build is what admission fences on. |
| `CAPACITY_TOKEN` | **Secret**, not a var: `npx wrangler secret put CAPACITY_TOKEN`, 32–512 non-space characters. The provider answers `503` rather than serving unauthenticated when it is missing. |

`WORKER_VERSION` disagreeing with the image is the most common misconfiguration;
it produces enrollments that are accepted and then fenced out. A container that
reaches the API and is closed with `1008 policy violation` during `hello` is this
fence: check that `WORKER_CONTAINER_ENV` does not carry a stale `BIFROST_VERSION`
shadowing the image's own build.

The capability lists form the protocol-v2 envelope. They are not inferred from
`WORKER_VERSION`; a v1 provider is rejected. Upgrade and deploy the Worker and
its pinned image together before declaring a new handler or compute capability.

The second most common is a container smaller than the workload. The Containers
application's size comes from `instance_type` in the `[[containers]]` block, not
from `MEMORY_LIMIT_BYTES`: omit it and Cloudflare creates the application as
`lite` (256 MiB memory, 2 GB disk). A worker image is larger than that disk
unpacked, so every instance fails immediately with `ImagePullError` and never
reaches a location. Instances that never schedule, with `LOCATION` blank in
`wrangler containers instances`, are the symptom. Check the application record
before reading instance logs:

```bash
curl -s -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/containers/applications/$APP_ID" \
  | jq '.result.configuration, .result.health'
```

Cloudflare documents image and `instance_type` changes on the `default`
scheduling policy as application rollouts, and the scheduling policy itself is
immutable per application. With Wrangler 4.162.0 a redeploy did not apply either
change to an existing application — the record kept the original size and image —
while deleting and recreating the application did. So treat this as
version-dependent: check `wrangler containers applications get` after a deploy,
and if the configuration did not move, recreate the application.

## Diagnosing a container that never enrolls

Container stdout and stderr are collected only when observability is enabled.
The `[observability]` block in the shipping `wrangler.toml` enables it for the
Worker and, by default, for its containers. Without it a container that dies
during startup leaves no trace: the API reservation goes `lost` and nothing
records why.

When a container process exits, the provider logs one line:

```
capacity container exit {"code":1,"message":"..."}
```

Exit code `0` means the main process ended without an error — including an
intentional stop, so it is not proof that the process exited on its own. A
non-zero code is the container entrypoint failing. Watch for it with
`npx wrangler tail` while a worker is being admitted.

One failure is specific to this platform. Docker and Kubernetes export
`HOSTNAME`; Container runtimes generally do not, and Cloudflare does not. The
worker's launcher identity requires a hostname and refuses to start without
one:

```
src.worker.remote.identity.IdentityError: remote hostname is required
```

The provider supplies it for the incarnations it starts, so a deployment that
sees this line is running an older provider build.

## Verify before deploying

```bash
node --test capacity.test.mjs
```

The contract tests cover prepare idempotency, conflicting reservations, UID
fencing, duplicate starts, lost start responses, terminal fences, tombstones,
and the capacity ceiling. They also cover the container-facing seams: the
launch environment (including the hostname the launcher identity requires) and
the inactivity timeout. They run without a deployment.

Then run the shared black-box conformance suite from the contract change that
ships it (`deploy/capacity/conformance/conformance.py` in that change; it is not
part of this provider) against the deployed endpoint:

```bash
python deploy/capacity/conformance/conformance.py \
  --base-url https://<worker>.<account>.workers.dev \
  --token-env CAPACITY_TOKEN \
  --scope <SCOPE>
```

Without a hook the physical-launch cases report `SKIP`, which is not a pass. To
claim the physical cases, supply a hook that can observe cumulative container
launch identities.

## State and restart behaviour

- One Durable Object per incarnation holds that worker's record: reservation,
  resource UID, lifecycle, intent marker, and tombstone.
- A worker incarnation outlives the request that starts it. The container
  Durable Object sets an inactivity timeout covering the worker's lifetime,
  because Cloudflare otherwise stops a container as soon as the Durable Object
  that started it goes idle, which is immediately after `start()` returns.
- One registry Durable Object per scope accounts capacity atomically, so two
  concurrent prepares cannot exceed `MAX_WORKERS`.
- Records are durable. A Worker restart, eviction, or redeploy does not lose
  reservations or tombstones, and a restart must not re-launch a container: the
  intent marker forces an observation before any retry.
- A lost start reply is replayed only when the durable container record proves
  `never_started`; an ambiguous or accepted physical launch remains fenced for
  cleanup rather than starting a second process.
- A stopped incarnation is terminal. It is never restarted, and its record is
  retained.

## Rotation

**Token.** The provider accepts exactly one token, so rotation is a coordinated
replacement and there is a short window where one side rejects the other. Set the
new secret on the provider and update `BIFROST_WORKER_CAPACITY_TOKEN` on the API
controllers in the same change, then confirm workers reconcile. Replacing only
one side fails enrollment or returns `401` until both match.

**Scope.** A scope change is a provider replacement, not an edit. Drain and
confirm deletion of every worker on the old scope, then deploy under the new
scope and point the API at it. Changing `SCOPE` in place strands prepared
workers that the new scope cannot address.

## Limits

- Container cold start is not guaranteed to fit the API's start timeout for a
  large image. Measure it against the API's start timeout before relying on the
  default.
- This provider does not implement Fast Path isolation or isolated builds.
- Cost tracks container lifetime; use `MAX_LIFETIME_SECONDS` and the API's idle
  drain to bound it.
