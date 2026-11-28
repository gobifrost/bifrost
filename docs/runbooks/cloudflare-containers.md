# Cloudflare Containers worker capacity

Delivery scope for #1400 is Cloudflare Containers only. AWS, Azure and GCP
provider delivery is deferred to a separate follow-up. Shared runtime contracts
and existing local/Kubernetes compatibility remain prerequisites; reference
adapters are not acceptance evidence or supported deliveries for those clouds.

Operator runbook for deploying Bifrost's external-worker capacity provider on
Cloudflare Containers. The protocol is defined in
[`docs/architecture/external-worker-capacity.md`](../architecture/external-worker-capacity.md).

## What this deploys

### API-owned execution

The native Cloudflare provider runs the API-only compute adapters. Earlier
#1331 material is historical evidence, not the current deployment reference.
It derives each launcher's control credential from the dedicated capacity token,
incarnation UUID and resource UID. The installation signing key never leaves
the API. Requirements reach the worker through authenticated control bootstrap.
Do not supply database, Redis, S3 or installation signing credentials in
WORKER_CONTAINER_ENV; this candidate rejects them.

The template does not declare job types. The matching image registers its
capabilities through authenticated worker hello; the provider reports only
hardware and build. Settings rules choose which work may trigger overflow.
Workflows, agents, summaries and the current platform-job catalog use shared
API-owned orchestration and API-only compute stages. Maintenance and control
handlers that need no physical compute stay in the API; they need no worker.
Workers receive only the inputs for registered compute recipes. Authorization,
credentials and durable effects remain API-owned.

This candidate remains unqualified on Cloudflare. Provider readiness and local
correctness do not establish real-container workloads, fault/scaling behavior,
latency or cost; historical deployment receipts are not current-image acceptance.

API and launcher must be upgraded together for the bootstrap exchange; the
external handshake deliberately does not accept the installation signing key.
The provider resource protocol is version 1.

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

Onboarding requests a fixed 4 GiB profile per worker. Its provider maximum and the
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
   Worker secret. Do not place it in `wrangler.toml`, shell history, a command
   argument, or `WORKER_CONTAINER_ENV`.
3. A trusted public HTTPS API origin that the Cloudflare container can reach for
   its WebSocket control connection. `localhost`, private addresses, a
   development-only certificate, and a URL requiring an interactive browser
   login are not valid container inputs. This candidate does not provision
   database or Redis tunnels.

## Pinned deployment order

Run these commands from `deploy/cloudflare/worker`. This runbook pins
Wrangler `4.162.0`; do not substitute a floating Wrangler release.

1. Set `wrangler.toml` values first: a digest-pinned image, the image's exact
   embedded `WORKER_VERSION`, an opaque `SCOPE`, and bounds that the API will
   also enforce. The image registers its own capabilities after it connects;
   do not configure job types in the provider. `MEMORY_LIMIT_BYTES` must equal
   the API policy's standard and
   largest worker memory; `MAX_WORKERS` and `MAX_LIFETIME_SECONDS` must cover
   the API policy's worker-count, warm-minimum, and lifetime bounds; and
   `ScopeRegistry` enforces `MAX_WORKERS` across reservations, including
   uncertain starts. The `durable_object` policy has no `max_instances`. Set
   `WORKER_CONTAINER_ENV` to a trusted public `BIFROST_API_URL` and required
   runtime paths only. Do not set `BIFROST_VERSION` there: the pinned image's
   reported build is the admission identity.
2. Provision the dedicated provider token as a Worker secret before selecting
   the provider in the API. The command reads the value interactively; never
   pass it as an argument:

   ```bash
   npx --yes wrangler@4.162.0 secret put CAPACITY_TOKEN
   ```

3. Deploy and record the pinned Wrangler version and image digest with the
   deployment receipt:

   ```bash
   npx --yes wrangler@4.162.0 --version
   npx --yes wrangler@4.162.0 deploy
   ```

4. Only after the Worker secret and deployment are in place, configure the API
   process with the existing external-capacity variables:

   ```text
   BIFROST_WORKER_CAPACITY_URL=https://<worker>.<account>.workers.dev
   BIFROST_WORKER_CAPACITY_TOKEN=<same dedicated token>
   ```

   The URL and token are an all-or-nothing pair: setting one without the other
   fails closed. The API host, not the Cloudflare Worker, owns these values. Do
   not put them in `WORKER_CONTAINER_ENV`; that object is for the launched
   Bifrost worker and must contain a trusted public `BIFROST_API_URL`, not
   provider credentials.

5. Verify the Worker returns only its expected health response before enabling
   policy demand. Do not treat this reachability check as a worker-launch or
   conformance result:

   ```bash
   python3 - <<'PY'
   import json
   import os
   from urllib.request import Request, urlopen

   worker_url = os.environ["BIFROST_WORKER_CAPACITY_URL"].rstrip("/")
   token = os.environ["BIFROST_WORKER_CAPACITY_TOKEN"]
   request = Request(f"{worker_url}/healthz", headers={"Authorization": f"Bearer {token}"})
   with urlopen(request, timeout=10) as response:
       print(json.dumps({"status": response.status, "health": json.load(response)}, sort_keys=True))
   PY
   ```

## Configure

Edit `deploy/cloudflare/worker/wrangler.toml`:

| Var | Meaning |
|---|---|
| `SCOPE` | Opaque provider scope. Must match the API side exactly. |
| `WORKER_VERSION` | Must equal the image's embedded build version. |
| `MEMORY_LIMIT_BYTES` | Fixed worker memory matching the container profile; independent of Kubernetes sizing. |
| `containers.images.worker.image` | Digest-pinned image in the Cloudflare managed registry; must be prepared before Worker publication. |
| `MAX_WORKERS` | ScopeRegistry's serialized admission cap; uncertain starts retain their slot until deletion is proven. |
| `MAX_WORKERS` | Ceiling this provider will run. |
| `MAX_LIFETIME_SECONDS` | Upper bound the API may request; cannot exceed six hours, which is the platform's inactivity-timeout maximum. |
| `WORKER_CONTAINER_ENV` | JSON object containing the API URL and runtime paths. Platform credentials are refused. Do not set `BIFROST_VERSION`: the pinned image carries its own build, and the reported build is what admission fences on. |
| `CAPACITY_TOKEN` | **Secret**, not a var: `npx wrangler secret put CAPACITY_TOKEN`, 32–512 non-space characters. The provider answers `503` rather than serving unauthenticated when it is missing. |

`WORKER_VERSION` disagreeing with the image is the most common misconfiguration;
it produces enrollments that are accepted and then fenced out. A container that
reaches the API and is closed with `1008 policy violation` during `hello` is this
fence: check that `WORKER_CONTAINER_ENV` does not carry a stale `BIFROST_VERSION`
shadowing the image's own build.

The provider profile has no job-type list. `WORKER_VERSION` remains the
version-matched image identity for admission; after launch the worker hello
automatically registers the image's actual capabilities. Upgrade and deploy
the Worker and its pinned image together. UI overflow rules decide which jobs
may use this capacity; they are not provider configuration.

Fresh deployments use Cloudflare's `durable_object` scheduling policy. The
Worker selects the prepared `ctx.container.images.worker` handle and
`instance: "standard-1"` at start, preserving the qualified 4 GiB memory and
8 GB disk tier. `MEMORY_LIMIT_BYTES` advertises memory; it does not choose
hardware. ScopeRegistry retains the worker-count bound because this policy
has no application-level `max_instances`.

API setup initiates native image preparation and observes only its documented
`pending` state until `ready`, bounded to fifteen minutes as in pinned Wrangler.
An error, unknown state, account refusal or transport failure stops setup; no
worker starts or upload retries hide a failure. The shared PlatformJob records
preparation progress before publishing Worker code or activating capacity.
Manual Wrangler deployment performs this preparation itself.

Scheduling policy is immutable. An existing `default` application cannot be
updated in place, and its namespace cannot attach to a replacement application.
API setup refuses it before Worker upload. Drain the existing installation and
provision a fresh dedicated provider scope/script; do not delete active
containers or reuse their namespace. This migration does not transfer container
filesystems or Durable Object state.

See Cloudflare's [scheduling policies](https://developers.cloudflare.com/containers/configuration/scheduling-policy/)
and [migration guide](https://developers.cloudflare.com/containers/guides/migrate-to-durable-object-scheduling-policy/).
Native image readiness is a deployment prerequisite; worker enrollment and job
execution still require separate live qualification.

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
new secret on the provider, update `BIFROST_WORKER_CAPACITY_TOKEN` on the API
host, then confirm workers reconcile. Replacing only one side fails enrollment
or returns `401` until both match.

**Scope.** A scope change is a provider replacement, not an edit. Drain and
confirm deletion of every worker on the old scope, then deploy under the new
scope and point the API at it. Changing `SCOPE` in place strands prepared
workers that the new scope cannot address.

## Limits

- Container cold start is not guaranteed to fit the API's start timeout for a
  large image. Measure it against the API's start timeout before relying on the
  default.
- Cloudflare cold-start and physical-launch conformance remain unqualified by
  this runbook. A healthy provider or a completed settings deploy is not proof
  of a `ContainerStarted` event or a completed workflow.
- This provider does not implement Fast Path isolation or isolated builds.
- Cost tracks container lifetime; use `MAX_LIFETIME_SECONDS` and the API's idle
  drain to bound it.
