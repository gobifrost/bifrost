# Cloudflare Containers execution capacity (burstable Bifrost workers)

Run Bifrost execution workers as Cloudflare Containers that burst on demand
and enroll into an instance through the canonical external capacity protocol.
PostgreSQL remains authoritative for demand, admission, claims, progress,
cancellation, and recovery. The provider only prepares, starts, and deletes
container incarnations by exact resource UID.

Companion to the edge-frontend track (`docs/runbooks/cloudflare-frontend.md`):
Workers serve the UI at the edge; Containers supply burstable execution
behind the API. The two tracks share nothing but the account.

## Prerequisites

- A digest-pinned worker image built from the exact source the API runs
  (`image = "...@sha256:..."` in `deploy/cloudflare/worker/wrangler.toml`).
  Never deploy `:latest` or a bare tag: the API fences enrollment to the
  exact reported worker build version, and a rollout never relabels an
  already-reserved incarnation.
- A reachable canonical API origin over HTTPS (existing infrastructure).
  Workers connect back to it; it is never reachable from the public internet
  through this recipe.
- Wrangler authenticated for the target account. Workers Paid required:
  Containers only run on Paid plans.
- On the API side, `BIFROST_WORKER_CAPACITY_URL` (this worker's HTTPS
  endpoint) plus `BIFROST_WORKER_CAPACITY_TOKEN` (the `CAPACITY_TOKEN`
  secret), with worker scaling policy sized to the advertised memory,
  count, and lifetime bounds.

## Deploy

```bash
cd deploy/cloudflare/worker
npm test  # protocol contract: node --test capacity.test.mjs
# Pin SCOPE, WORKER_VERSION, WORKER_IMAGE (@sha256), API_ORIGIN in wrangler.toml
npx wrangler secret put CAPACITY_TOKEN
npx wrangler deploy
```

`CAPACITY_TOKEN` must be a dedicated random bearer (32-512 printable ASCII),
never reused from another control. Selectors in `wrangler.toml` contain no
database URL or password; Hyperdrive is deliberately absent here — canonical
coordination stays on direct PostgreSQL (see
`docs/architecture/external-worker-capacity.md`, "Direct PostgreSQL
Coordination; Hyperdrive Excluded").

## How burst and enroll work

1. The API's canonical pool computes demand and reserves a worker UUID plus
   build version, then `PUT /v1/workers/{uuid}` (prepare, idempotent).
2. The provider launches one container with `BIFROST_WORKER_FORM=external`,
   `BIFROST_WORKER_INCARNATION_ID` (reserved UUID), and
   `BIFROST_WORKER_RESOURCE_UID` (prepared UID), commits the UID, then
   `POST /v1/workers/{uuid}/start`.
3. The launcher enrolls over the signed control handshake. Admission requires
   a matching canonical `starting` reservation, the same resource UID, and
   the same reported build; the first physical launcher UUID binds atomically
   and reconnections preserve it. Claim generation and owner fences continue
   per work family.
4. On loss or retirement the controller fences first, then `DELETE
   /v1/workers/{uuid}` by exact UID; the provider returns a `deleted`
   tombstone and never restarts a stopped incarnation.

Full protocol: `docs/architecture/external-worker-capacity.md`. Reference
implementation: `deploy/cloudflare/worker/capacity.mjs` (pure protocol logic
with injected container runtime; 10 contract tests).

## Verify

```bash
npm test  # idempotent prepare/start/delete, UID fencing, tombstones
```

Live checklist (disposable dev Worker + dev API, bounded window, cleanup
verified before counting anything):

1. `GET /v1/capabilities` reports protocol 1 and the pinned build/bounds.
2. Unauthenticated and wrong-scope calls are rejected; no state changes.
3. Stage one workflow run with only the container provider ready: it waits,
   then claims and completes on the Cloudflare worker after start.
4. Retire the worker: canonical state goes terminal, provider deletion is
   recorded, counts return to zero, no orphan compute remains.

## Limits

- **Canonical coordination stays on direct PostgreSQL.** Hyperdrive does not
  support advisory locks or `LISTEN`/`NOTIFY`, so it can never carry admission,
  fencing, or leases; never point canonical pools at it (see
  `docs/architecture/external-worker-capacity.md`, "Direct PostgreSQL
  Coordination; Hyperdrive Excluded"). Containers reach private origins through
  the authenticated TCP transport with original DB hostnames preserved for TLS
  verification (`docs/architecture/worker-private-transport.md`).
- **No filesystem.** Object storage stays S3-compatible; Workers/Containers
  have no shared disk.
- **Reference store is in-memory.** The recipe's record map is a reference
  simplification: a production provider MUST persist records (e.g. a Durable
  Object) so idempotency and tombstones survive restarts.
- **Build rotation and memory admission are fail-closed.** Unknown builds
  cannot enroll; OOM is reported only from explicit runtime evidence.
- **Cost.** Containers bill for provisioned active time plus Workers
  requests/CPU; keep a small `instances` floor and let demand burst the
  rest. Bounded canaries have stayed well under $1; billing reports are not
  a spending control — keep the timebox.

## What this does not change

No API, auth, scheduling, storage, dispatch, or execution semantics. The API
works identically with Kubernetes, static, or Cloudflare capacity behind the
same canonical pool — providers receive resource metadata only and never
choose jobs.
