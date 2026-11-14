# Cloudflare Containers worker capacity

Operator runbook for deploying Bifrost's external-worker capacity provider on
Cloudflare Containers. The protocol is defined in
[`docs/architecture/external-worker-capacity.md`](../architecture/external-worker-capacity.md).

## What this deploys

A Worker that implements the five capacity endpoints and starts container
incarnations on request. The API remains canonical: it decides demand, admits
workers, and fences enrollment. This Worker never sees job payloads.

## Prerequisites

1. A published worker image, **pinned by digest**, whose embedded build version
   you can read. A floating tag is not acceptable: the API fences reservations
   whose reported build differs from `WORKER_VERSION`.
2. A dedicated bearer token, 32–512 non-space ASCII characters, stored as a
   Worker secret: `npx wrangler secret put CAPACITY_TOKEN`.
3. Network reachability from the container to the API and to PostgreSQL. The
   worker container uses the authenticated TCP transport; see
   [`worker-private-transport.md`](../architecture/worker-private-transport.md).

## Configure

Edit `deploy/cloudflare/worker/wrangler.toml`:

| Var | Meaning |
|---|---|
| `SCOPE` | Opaque provider scope. Must match the API side exactly. |
| `WORKER_VERSION` | Must equal the image's embedded build version. |
| `MEMORY_LIMIT_BYTES` | Fixed worker memory; the API's scaling policy must match. |
| `instance_type` (in `[[containers]]`) | Real memory and disk for each container; must be at least `MEMORY_LIMIT_BYTES` and large enough for the unpacked image. |
| `max_instances` (in `[[containers]]`) | Must be at least `MAX_WORKERS`: a start beyond the application's instance count is refused even when the scope registry would admit it. |
| `MAX_WORKERS` | Ceiling this provider will run. |
| `MAX_LIFETIME_SECONDS` | Upper bound the API may request; cannot exceed six hours, which is the platform's inactivity-timeout maximum. |
| `WORKER_CONTAINER_ENV` | JSON object of the worker container's environment. Do not set `BIFROST_VERSION`: the pinned image carries its own build, and the reported build is what admission fences on. This is plaintext configuration, so put real values in a secret or an uncommitted `.dev.vars`. |
| `CAPACITY_TOKEN` | **Secret**, not a var: `npx wrangler secret put CAPACITY_TOKEN`, 32–512 non-space characters. The provider answers `503` rather than serving unauthenticated when it is missing. |

`WORKER_VERSION` disagreeing with the image is the most common misconfiguration;
it produces enrollments that are accepted and then fenced out. A container that
reaches the API and is closed with `1008 policy violation` during `hello` is this
fence: check that `WORKER_CONTAINER_ENV` does not carry a stale `BIFROST_VERSION`
shadowing the image's own build.

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

`instance_type` is fixed when the application is created; changing it later
requires replacing the application.

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

Exit code `0` means the process ended on its own; a non-zero code is the
container entrypoint failing. Watch for it with `npx wrangler tail` while a
worker is being admitted.

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
