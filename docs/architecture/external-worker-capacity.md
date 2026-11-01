# External Worker Capacity

External capacity providers supply physical Python workers to the existing
worker pool. PostgreSQL remains authoritative for demand, admission, claims,
progress, cancellation, and recovery. Providers receive resource metadata only;
they never receive job payloads or choose which jobs execute.

## Selection and Bounds

Set `BIFROST_WORKER_CAPACITY_URL` to an HTTPS endpoint and
`BIFROST_WORKER_CAPACITY_TOKEN` to a
dedicated bearer token to select the external worker capacity controller. Both
settings are required together. With neither configured, existing Kubernetes
worker provisioning remains selected. Selecting an external provider excludes
the Kubernetes worker controller; Kubernetes isolated-build infrastructure
remains independently available. Provider failures do not switch providers.

The provider advertises an opaque scope, worker build version, fixed memory
limit, maximum worker count, and maximum lifetime. Configure the existing worker
scaling policy explicitly: standard and largest worker memory must equal the
advertised memory, and the worker count and lifetime must fit its bounds.
Settings -> Workers exposes scaling and concurrency for every provider;
Kubernetes isolation controls require a deployment with Kubernetes Job access.
Policy saves fetch the provider's current capabilities before acquiring the
settings advisory lock, then validate the merged policy under that lock.
An incompatible policy returns HTTP 422 without changing stored settings. If
the provider cannot be verified, the save returns HTTP 503 with a sanitized
message and preserves the policy. No cached profile or provider fallback is
used. The controller independently checks the same bounds at admission, so
capability changes after a save still fail closed at reconciliation.
Kubernetes and external managed workers share the canonical worker ceiling,
warm minimum, demand calculation, and memory and handler occupancy accounting.

## Resource Protocol

All requests authenticate with `Authorization: Bearer <token>`. Resource requests
also carry `X-Bifrost-Capacity-Scope`. JSON schemas are the strict
`WorkerCapacityProfile`, `WorkerCapacityReservation`, and `WorkerCapacityResource`
models in `api/shared/models.py`.

| Request | Contract |
| --- | --- |
| `GET /v1/capabilities` | Return protocol version 1 and immutable provider scope plus current build and bounds. |
| `GET /v1/workers/{uuid}` | Strongly read the resource, or return 404 when absent. |
| `PUT /v1/workers/{uuid}` | Idempotently prepare the immutable reservation without starting a process. Return `prepared` and its stable resource UID. |
| `POST /v1/workers/{uuid}/start` | Start the prepared resource matching the supplied `resource_uid`. Return `starting` or `running`. |
| `DELETE /v1/workers/{uuid}` | Stop and delete only the resource matching the supplied `resource_uid`. Return a `deleted` tombstone. |

The controller first commits the canonical reserved worker UUID and expected
worker build version. A provider version change fences an unprepared older
reservation rather than relabeling its UUID; queued demand can reserve a new one.
The controller then
prepares the provider resource, commits its resource UID and `starting` state,
and only then requests a start. Resource reconciliation HTTP calls hold no database connections
or row locks. Concurrent controllers converge on the same UUID and immutable
resource. After an uncertain mutation response, reconciliation reads that
resource before deciding its next action. A recovered resource must still report
the canonical reservation build before binding or startup; a GET response does
not bypass the build check applied to a fresh prepare response. Cleanup of a
fenced incarnation remains possible by its exact resource UID. The client
follows no redirects and performs no automatic HTTP retries.

Providers must preserve idempotency and tombstones, reject conflicting
reservations or resource UIDs, and never restart a stopped incarnation. Report
out-of-memory only from explicit runtime evidence, not a generic process exit.

## Confirmed Physical Cleanup

Lost and retired external workers remain eligible for resource cleanup until
provider absence or a validated `deleted` response confirms deletion. Provider
outages and controller restarts do not expire this eligibility after one hour.
The controller persists `capacity_resource_deleted_at` only for the unchanged,
still-terminal incarnation, preserving its resource UID and loss/retirement
history. Confirmed rows stop generating provider lookups and DELETE calls.
The nullable migration lets existing terminal rows converge through the same
readback path; it changes no provider protocol or Kubernetes cleanup cutoff.
Do not prune unconfirmed worker rows while their physical resources may exist.

## Launcher Admission and Recovery

The prepared resource launches the normal worker image with
`BIFROST_WORKER_FORM=external`, `BIFROST_WORKER_INCARNATION_ID` set to the reserved
UUID, and `BIFROST_WORKER_RESOURCE_UID` set to the prepared UID. These identities
are covered by the existing signed launcher handshake. Kubernetes job/pod UIDs
are forbidden for this form. External workers cannot register themselves as
static workers.

Admission requires a matching canonical `starting` reservation, resource UID,
and reported worker build version. An unknown or different build cannot enroll,
and a reconnect cannot change its admitted build version.
It atomically binds the first physical launcher UUID. Reconnection preserves
that identity; a replacement process cannot take over the same incarnation.
Normal claim-generation and owner fences continue to protect every work family.

On confirmed physical loss or a qualified heartbeat timeout, the controller
first marks the worker lost and performs canonical run recovery. Physical
cleanup follows that fence. Idle drain, maximum lifetime, build rotation, and
warm-worker policy use the shared worker-pool machinery. An external controller
can adopt a new provider build without restarting. Its still-live controller
identity therefore does not exempt old-build workers from normal rotation once
no live controller advertises that build. Rotation drains workers under the
existing warm minimum and capacity ceiling; it does not terminate active jobs.

## Development Acceptance

Provider protocol tests use real database reservations with an HTTP transport
fixture to exercise concurrent reconciliation, lost start responses, resource
identity rejection, admission, fencing before cleanup, and shared capacity
limits. These tests do not establish Cloudflare deployment or runtime acceptance.
That evidence must identify the deployed provider version, pinned worker image,
canonical worker and claim identities, and observed scale-out and recovery.

### Published Development Images

The `Development Worker Image` workflow publishes on pushes to `dev/**` and
supports manual dispatch. It builds the normal production Dockerfile for
`linux/amd64`, stamps the source-derived development version, exercises the
published image, signs its digest, and attests its build provenance. It does not
deploy a provider or promote a production tag.

Use the successful run for the exact source commit. Its
`dev-worker-image-<source SHA>` artifact, retained for seven days, contains
`identity.json` and `worker-image.tar.gz`. The identity records the source,
published digest, embedded version, local image ID, archive checksum, producer
run, and attempt. Verify the producer identity, provenance and checksums before
transferring the image; an archive or matching tag alone is insufficient. Pin
the verified platform digest in the infrastructure repository before deploying
it through that repository's development procedure.

Keep publication, deployment, launcher admission and job completion as separate
acceptance observations. Record the provider deployment version, canonical
worker UUID, resource UID and independently observed physical image alongside
the job receipt. A newer source or image does not inherit an older build's
runtime acceptance merely because its tests pass.
