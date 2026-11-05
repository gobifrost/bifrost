# Default remote runner: validation and measurements

The thin physical runner is the default worker and scheduler entrypoint in
[PR #1066](https://github.com/gobifrost/bifrost/pull/1066). The image retains the
existing workflow, service, agent, package, and PlatformJob capabilities. This
report records local verification, not a production deployment or a hosted
provider benchmark. Machine-readable evidence is in the
[adjacent JSON](2026-10-25-default-remote-runner.json).

## Memory saved

The savings come from avoiding a separate application/control import graph in
every idle executor. The API owns durable claims, leases, completion handling,
SDK authorization, agents, and summaries. Physical launchers keep OS supervision
and a small workflow template warm. PlatformJob handlers load their full
dependencies in fresh subprocesses and release them when those processes exit.
The worker image still contains those dependencies.

| Whole-container idle working set | Worker | Scheduler | API |
|---|---:|---:|---:|
| After the broad job workload | 87.5 MiB | 84.1 MiB | 558.8 MiB |
| After a later SDK-only run | 74.6 MiB | 75.4 MiB | 373.9 MiB |
| After the final combined workload, including large-result recovery | 84.8 MiB | 84.5 MiB | 562.0 MiB |

Each probe sampled for 30 seconds at 100 ms intervals; the table uses medians
from its final ten seconds. Working set is `memory.current - inactive_file`,
including all processes and container kernel charges. These are usage figures,
not Kubernetes requests or limits. The final combined run exposed the test-reset
Git leak described below; the earlier broad workload successfully exercised all
23 job types. The API remains a substantial shared warm process and grows with
its workload. Active builds, package installation, and workflow children need
additional memory.

These current samples have no fresh matched old-runner control. The earlier
[matched experiment](2026-10-24-thin-runtime-live-jobs.md) measured worker idle
memory falling from 328.6 to 87.7 MiB, with API memory increasing from 473.1 to
508.8 MiB: a net 205.3 MiB reduction for that API/worker pair. Those historical
conditions should not be replaced with the current measurements to invent a new
percentage saving.

## Speed and startup

A quiet local live run measured completed synchronous workflow calls through
real dispatch and execution:

| Workflow | First execution | Median of ten warm executions |
|---|---:|---:|
| Simple return value | 113 ms | 121 ms |
| One scoped SDK config read | 137 ms | 142 ms |

The first execution starts from an already-ready container with normally warmed
source cache. A separate scoped SDK config test completed 160 reads at
concurrency eight and checked set/get/list/delete behavior. Its raw timings are
retained in the evidence; they are diagnostic, not a production throughput
promise. SDK AI completion also ran against a deterministic local provider.

One cached-image Docker worker startup took **7.25 seconds**, measured from the
`docker start` call to the worker readiness marker. The API, Postgres, Redis,
and object storage were already ready, with no user requirements to install.
Other isolated verification was running on the host. This excludes image pull,
Kubernetes scheduling, API cold start, and dependency restoration. The earlier matched spike's 6.44/4.93-second trials are historical,
not current startup guarantees. No production p95/p99 or cross-region latency
claim follows from these local samples.

## Architecture and preserved behavior

- Existing worker and scheduler commands now start physical launchers. Shipped
  Compose/Kubernetes wiring supplies the API URL; enrollment happens
  automatically using the existing installation secret and canonical
  Job/Pod identities. Existing scheduler-only controller Role placement remains
  supported.
- Workflow and service SDK requests use the existing authorized API over HTTP.
  The physical worker no longer hosts the private SDK Unix socket. The control
  WebSocket carries commands, acknowledgements, telemetry, and results; workflow
  code still runs in worker processes.
- PostgreSQL remains authoritative for dispatch, worker identity, and ownership.
  Database-time owner leases and generations fence stale API owners. Reconnect
  inventory adopts the existing children and replays unacknowledged outcomes.
  It does not blindly re-execute started workflows.
- Memory admission reserves capacity before a claim commits. API-agent capacity
  and physical workflow capacity are advertised separately. Large results use
  ordered fragmentation with bounded in-memory assembly rather than a new
  logical result-size restriction.
- Package install/uninstall still recycle both physical workflow templates.
  Services retain ready/log/emit/restart/stop behavior. API and physical worker
  shutdown retain their distinct drain behavior.

## Live coverage

All 23 registered PlatformJob types ran their real handlers: video generation
through chat and SDK, embedding reindex, model catalog refresh, audit archive
and query, run and artifact retention, file move, workspace reimport and bundle
import, app deploy/publish/SDK update, Solution deploy/export/Git sync, workspace
Git, summary backfill, OAuth refresh, webhook renewal, Solution update check,
and file-index reconciliation. Deterministic upstream fixtures stand in for
paid AI/video/OAuth/webhook providers; this verifies integration behavior, not
provider quality or every provider option.

Additional live checks exercised workflows and SDK calls, a real agent run and
automatic summary, service lifecycle, local wheel installation and removal on
both physical executors, cancellation after reconnect, and a 33 MiB result
after reconnect with a single persisted side effect.

An isolated local Kind cluster verified automatic Job/Pod enrollment. A burst
of **53 workflows across three on-demand worker Jobs survived replacement of
both API pods**. Every workflow completed with exactly one canonical claim,
and original Job/Pod bindings were retained. A separate production-image check
ran two real App deploys in isolated **2 GiB** pods while replacing an API
controller; both deploys and a subsequent SDK update succeeded, and maintenance
work completed alongside the builds. The fixture deliberately held Vite for
30 seconds to create overlap; its elapsed time is not a build-speed benchmark.

The Kind restart drill used the initial promoted production image. The isolated
build check used the subsequent image containing shutdown fixes. Later review
changes made TLS policy explicit, simplified cleanup, injected pool identity
through its constructor, and made reservation commit explicit; focused tests
cover those final changes. Image identities and source hashes are in the JSON.

## Verification and failure dispositions

The focused runtime suite passed **425 unit tests**. Python lint/types passed.
After CI found two additional inventory/assertion gaps, the full access-list
and Kubernetes-manifest tripwires passed **81 tests** locally.
The final build/Git, reconnect/cancellation, workflow timing, and SDK throughput
recheck passed all five tests after the reset fix. The preceding combined run
passed 21 tests and exposed the now-corrected stale-Git reset failure. Exact commands, JUnit
summaries, observations, and Kind reports are retained in the JSON. Full local
backend/client/browser suites were not run; the merge queue is the complete
suite gate.

The merge-queue follow-up passed **42 focused remote-owner tests**, then
**637 live tests** in the failing shard's complete prefix through reconnect
and package refresh. A separate **97-test live run** passed all fourteen SDK
test files updated for HTTP transport, run retention, MCP tool authorization,
and the execution identity matrix. These runs reproduce the affected queue
conditions; they do not replace the complete merge-queue gate.
The legacy scheduler-role wrapper and deployment contracts passed another ten
tests, including a fresh-process import guard.
The later recycle/crash-recovery fix passed **56 focused unit tests** and a
**19-test live sequence** covering package replacement followed by SDK calls,
engine-token identity authorization, and child credential isolation. The
package lifecycle accounts for 27.5 seconds of that 47.3-second live run; no
additional background jobs were added for this follow-up.

Failures found during validation received concrete fixes:

- The hybrid fixture initially omitted its Git daemon. Its canonical server
  lifecycle now starts HTTP, TLS, and Git together.
- New reconnect fixtures needed explicit table access policy. The tests now
  seed the existing authorized policy, preserving product authorization.
- Package removal verification read the wrong error field, and the throughput
  cleanup used the wrong HTTP client method for a DELETE body. Both assertions
  now follow the existing API contracts.
- A shutdown test used scheduler timing instead of a causal signal; it now
  waits for the drain-start event. Review also found and fixed physical drain
  after disconnect, termination after completed drain, and reconciliation-task
  cleanup after session completion.
- The Kubernetes drill read database bindings before listing Jobs, allowing
  a concurrent scale-up to appear orphaned. It now observes Jobs before their
  already-committed reservation rows; a regression reproduces that interleave.
- Test reset deleted database/cache/object-store generations while preserving
  application container writable files. A stale `/tmp/git/.git` then became an
  invalid checkout after storage synchronization. Reset now recreates only
  application containers, so their ephemeral files and installed packages
  reset with durable test state. No Git repair fallback was added.
- CI exposed a missing access-inventory entry for the control WebSocket and an
  obsolete Redis readiness assertion. The inventory now records its custom HMAC
  machine authentication, its JSON projection is regenerated, and the manifest
  test requires the existing worker readiness module. Runtime access gates were
  unchanged.
- The merge queue exposed a real ownership-renewal bug under ordinary API
  activity. A telemetry sample older than the three-second admission window
  skipped the first ten-second renewal tick; the next tick reached the
  twenty-second database lease expiry and retired a healthy runner. A local
  shard-prefix reproduction confirmed both physical executors retired during
  repeated MFA enrollment requests. Renewal now uses the full existing owner
  window, while admission still pauses at three seconds. Deterministic tests
  cover delayed telemetry, disconnected transport, and expired telemetry.
- Existing SDK live tests still asserted the previous private-socket transport.
  Their workflow, persistence, and authorization checks remain, and their
  transport assertions now require scoped shared API HTTP with no worker UDS.
- A later full queue run exposed a package-recycle race: the physical monitor
  observed the template's deliberate shutdown outside the replacement lock,
  exited, and left a reconnecting launcher without telemetry. A deterministic
  regression reproduced that interleave. Liveness checks now share the recycle
  lock. Genuine template crashes retain the existing pool's automatic template
  replacement without discarding live child handles; a failed replacement
  clears readiness and exits nonzero for normal container recovery.
- The same queue run found one remaining identity-authorization test asserting
  the removed worker socket. It now requires scoped HTTP while retaining its
  successful engine-token user-list check. The rest of that shard passed
  844 tests, with 19 existing skips.
- Review found that the legacy token-mounted controller wrapper imported
  controller dependencies into the physical launcher as well as its helper.
  Those imports now execute only inside the helper. Fresh Docker-process import
  probes measured peak RSS of 107,704 KiB before and 23,904 KiB after; these are
  import probes, not whole-container idle measurements. Base manifests keep the
  controller role on the API. Legacy scheduler-role installations retain their
  controller helper's additional memory, without duplicating its imports in
  the physical launcher.
- CodeQL findings prompted an explicit TLS 1.2 minimum in the local fixture and
  cleanup of redundant assignments, protocol bodies, exception handling, and
  test assertions. No skips, retry policy, or timeout inflation was introduced.

## Hosted deployment remains separate

This split provides a smaller physical compute unit for a future hosted
deployment. It does not shrink the dependency image, remove Redis/Postgres,
make the API scale to zero, or validate Cloudflare/Neon connectivity, lifecycle,
regional latency, or billing. Trusted PlatformJob children still need their
existing database, cache, and object-storage connections. The hosted deployment
roadmap remains future work; no production or cloud infrastructure was changed
for this validation.
