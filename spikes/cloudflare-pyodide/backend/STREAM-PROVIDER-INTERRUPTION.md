# Provider loss before a terminal event reaches the launcher

The fixed local interruption case now runs through the actual upstream
ThinRemoteProcessPool with an opaque invocation handle and an early accepted
stream frame. The real upstream engine/SDK reads integration configuration,
decrypts a synthetic integration secret, loads the pinned workspace modules and
writes document 70. An independent authenticated API read observes that write
before the supervisor stops the one owned cf CLI/workerd provider container.
The workflow is still awaiting its fixed async delay; no terminal result reaches
the native launcher/collector.
The opaque profile is this contribution's experimental extension; the public
control host still rejects it.

Observed on the refined native pool from fe27a8096:

- Dispatch ACK precedes result collection. The interrupted stream raises
  RemoteProtocolError, without a result envelope or an acknowledged receipt.
- PostgreSQL stays Running until the fixture explicitly invokes upstream
  end_runs_of_lost_worker and announce_lost_runs for that exact fixture worker.
  Canonical state becomes Failed/ExecutorLost without a fabricated Success.
- The already committed SDK effect remains 70; no WorkDispatch is re-created.
  A duplicate reuses the original failed receipt. Launch count remains one.
- Access and refresh credentials return 401 after canonical recovery. The existing
  terminal-log drainer persists integration secret=[REDACTED] once; a repeated
  drain flushes zero. Fixture worker/claim cleanup passes.
- The real native pool still holds the same non-PID invocation, Busy load count,
  original start/timeout/deadline, and no killed_at. Canonical failure/revocation
  does not fabricate provider settlement or release that obligation.

The Linux container's independent stop readback is exited/143/PID 0/no OOM.
That is deliberate local provider-process failure injection, **not** a supported
Dynamic Worker per-isolate cancellation primitive or deployed CPU-stop proof.
The disposable fixture then retires and all owned test containers stop; volumes
and the separate dev stack are preserved. No Cloudflare invocation, deployment,
access, credential grant, resource expansion or admission change occurred.

Normal completion still follows awaited work, completed response streams and
terminal reconciliation. This test adds no per-success process kill, immortal
waitUntil or undocumented terminate API requirement. No workflow RpcTarget stub
is returned by this HTTP fixture.

## Exact scope and reproduction

Assigned CT128, 2 CPU/8 GiB/no swap. Existing project bifrost-test-5dc8d94e;
provider 1 CPU/2 GiB/128 PIDs; internal 360-second wall/disk guard, 430-second outer
bound. Native API base 1d33150eb376b71f8a81b427920eda1b0447f829 plus the exact
protocol/pool overlays recorded in the receipt. Pinned image, Compose, engine
closure bc4eae48...6daf0, publisher-source bundle and wrapper are verified before
launch. Reuses cf 1.0.0-beta.26, Wrangler 4.156.0, workerd 1.20261023.1 and the
previously qualified Python 3.14.2/Pyodide 314.0.6 runtime without package changes.

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/stream-interruption-v2/ct128-canonical-failure.py --stream-interruption --revision 2
node spikes/cloudflare-pyodide/backend/verify-stream-interruption.mjs
```

All four runtime phases exit 0; supervisor 63.397 s includes stack/setup/cleanup,
not pure startup, workflow CPU or isolate memory. Raw
[receipt](../results/stream-interruption/v2.json) includes every command, packet,
workspace and API overlay hash and independent container stop readback. API
source is unchanged in this increment; the 44 native inventory cases remain
bound to their existing exact source proof.

Preflight preserved two diagnosed failures. V1 Ruff found a fixture-scope
execution_id reference; the corrected packet uses str(execution.id). V2's first
Pyright container exhausted its Node heap under the 512 MiB checker cap, before
runtime launch. A checker-only 1 GiB bound with Node old-space 768 MiB completed
with 0 errors/warnings; no diagnostic suppression, guest expansion or provider
limit change. Ruff also passes. See the preflight/OOM/quality records beside the
runtime receipt. This checker memory requirement is not Python Worker memory.

## Remaining integration boundary

Recovery here is explicit trusted-fixture use of the real canonical functions,
not automatic provider-loss detection or proof of deployed failure accounting.
The missing-terminal case is now characterized locally. The
[automatic recovery follow-on](AUTOMATIC-RECOVERY.md) now proves existing API
recovery after real relay silence and exposes the remaining live-relay,
per-invocation failure/deadline gap. Deployed limit enforcement/accounting,
noncooperative cancellation, credential/egress isolation and authorized runtime
admission remain unaccepted. Public Pyodide admission stays disabled.
