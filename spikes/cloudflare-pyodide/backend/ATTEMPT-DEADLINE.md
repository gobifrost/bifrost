# Per-attempt deadline with a healthy relay

The companion now owns an original-deadline task for an accepted invocation.
When result collection fails or remains pending, that task delivers the upstream
timeout envelope through the real WorkflowExecutionConsumer under the current
remote owner fence. It never cancels the retained collection, releases the opaque
handle or changes the worker-wide heartbeat. Public admission remains disabled.

This is a native companion adapter, not a new upstream API or business queue.
The small envelope mirrors ProcessPoolManager._report_timeout in the checked
source; persistence, authorization, credentials, logs, stale-result handling and
terminal status remain in the upstream consumer. Normal return means consumed
(possibly a stale replay), not physical stop or proof this outcome won a race.

## Observed real engine/SDK case

The actual opaque pool dispatches the pinned integration/module/table fixture
through local cf CLI/workerd. After an independently read SDK write 70, the owned
provider container is stopped before a terminal envelope returns. The live relay
keeps genuine owner renewal and heartbeats.

- An early deadline report is rejected before the result callback.
- The owned task waits for the immutable native dispatch cutoff. It uses the
  existing consumer to persist Timeout and revoke run access/refresh credentials.
- The first delivery receipt is deliberately lost after the real canonical
  commit. That exception propagates. Replay is consumed without a second launch,
  duplicate log or renewed deadline; access and refresh each return 401.
- The relay stays Ready. No worker-wide lost-executor recovery is invoked. Only
  the selected execution is delivered to the canonical result consumer.
- The actual native opaque handle, original timing, Busy count and failed
  unacknowledged collection remain held. No killed_at or physical-stop result is
  invented. The already committed SDK effect remains 70.

The local provider process exits 143/PID 0/no OOM. This is controlled process-loss
injection, not deployed isolate cancellation proof. Owned test containers stop;
volumes and the separate dev stack remain. No cloud invocation or runtime/
admission/security change occurred.

## Failures kept and corrected

V1 proves explicit logical timeout/replay with a live relay. V2 adds the owned
task and post-commit receipt loss, but its runtime run fails: AttemptResult is a
shielded awaitable, not an asyncio Future accepted by asyncio.wait. V3 passes
the retained transport Task, and the helper explicitly refuses non-Future inputs
before starting work. No alternate asyncio implementation is introduced.

A separate fail-first unit case reproduces a timer race: a 20 ms real scheduler
stall delays the timer task's start after its duration was computed. The old
code incorrectly treated a completed collection as timely. The corrected path
also checks the original UTC cutoff. No clock is patched or deadline extended.
That case fails before the fix and passes among the final 10 focused tests.

Those tests also cover early/stale-owner rejection, callback failure, owner
replacement after callback, normal collection, failed collection, and supervisor
cancellation without canceling retained work. Unit cases test adapter boundaries;
the real engine/SDK/canonical fixture establishes integration compatibility.

## Source, bounds and commands

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/attempt-deadline-v3/ct128-canonical-failure.py --attempt-deadline --revision 3
node spikes/cloudflare-pyodide/backend/verify-attempt-deadline.mjs
```

Assigned CT128 remains 2 CPU/8 GiB/no swap. Existing bounded project, provider
1 CPU/2 GiB/128 PIDs, cached dependency image and qualified runtime bundle are
unchanged. V3's four runtime phases exit 0; supervisor 93.280 s includes setup,
waiting for the original 30-second cutoff and cleanup. It is not startup/CPU
latency. No native shared-source changes in this increment.

[V3 raw receipt](../results/attempt-deadline/v3.json) records every command,
packet/workspace/API overlay digest and provider readback. V1, failed V2, original
helper snapshots, V2 error chain, the exposing stall test and before-fix result
are preserved beside it. Final 10 unit cases, Ruff and Pyright pass in the cached
network-disabled guest image; checker bounds remain 512 MiB/1 GiB respectively.

## Remaining frontier

This does not complete provider settlement, physical CPU cancellation or public
runtime startup/admission. The fixture starts supervision after observed collection
failure; the helper also accepts retained pending Tasks, covered at the unit
boundary. It is not yet wired as a production dispatcher backend.

Next local qualification: allow a finite invocation to return after logical
timeout and prove that the real result path settles/releases the held handle
without overwriting canonical Timeout. Deployed enforcement/accounting and
untrusted isolation remain separate acceptance gates. No silent Linux fallback
or workflow redispatch is introduced.
