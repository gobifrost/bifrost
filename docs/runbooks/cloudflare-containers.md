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
| `WORKER_IMAGE` | Must equal the digest in the `[[containers]]` block. |
| `MEMORY_LIMIT_BYTES` | Fixed worker memory; the API's scaling policy must match. |
| `MAX_WORKERS` | Ceiling this provider will run. |
| `MAX_LIFETIME_SECONDS` | Upper bound the API may request. |
| `API_ORIGIN` | Canonical API origin, HTTPS only. |
| `WORKER_CONTAINER_ENV` | JSON object of the worker container's environment. |

`WORKER_VERSION` and `WORKER_IMAGE` disagreeing with the image is the most
common misconfiguration; it produces enrollments that are accepted and then
fenced out.

## Verify before deploying

```bash
node --test capacity.test.mjs
```

The contract tests cover prepare idempotency, conflicting reservations, UID
fencing, duplicate starts, lost start responses, terminal fences, tombstones,
and the capacity ceiling. They run without a deployment because the engine is
pure.

Then run the shared black-box conformance suite against the deployed endpoint:

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
- One registry Durable Object per scope accounts capacity atomically, so two
  concurrent prepares cannot exceed `MAX_WORKERS`.
- Records are durable. A Worker restart, eviction, or redeploy does not lose
  reservations or tombstones, and a restart must not re-launch a container: the
  intent marker forces an observation before any retry.
- A stopped incarnation is terminal. It is never restarted, and its record is
  retained.

## Rotation

**Token.** Deploy the new token alongside the old, update the API controllers,
confirm workers still reconcile, then revoke the old token. Rotating by
replacing the secret in place will fail enrollment until the API matches.

**Scope.** A scope change is a provider replacement, not an edit. Drain and
confirm deletion of every worker on the old scope, then deploy under the new
scope and point the API at it. Changing `SCOPE` in place strands prepared
workers that the new scope cannot address.

## Limits

- Container cold start is not guaranteed to fit the API's start timeout for a
  large image. Measure it; see the runbook's acceptance section.
- This provider does not implement Fast Path isolation or isolated builds.
- Cost tracks container lifetime; use `MAX_LIFETIME_SECONDS` and the API's idle
  drain to bound it.
