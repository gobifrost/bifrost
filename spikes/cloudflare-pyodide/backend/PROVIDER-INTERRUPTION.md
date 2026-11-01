# Actual local provider-process interruption

This test stops the owned local Wrangler/workerd provider container **after** an
independent authenticated API read observes the workflow's real SDK-written
document as 70. The fixed workflow is still awaiting an async delay. The
supervisor stops only that container, and reads back exited status/exit 143,
PID zero and no OOM. This is provider-process failure injection, not a Dynamic
Worker per-isolate cancellation primitive or Cloudflare termination proof.

The actual collector receives HTTPX `RemoteProtocolError`, without a result
envelope. The transport journal remains failed/unacknowledged and explicitly
does not certify physical termination. PostgreSQL remains Running: a transport
exception does not invent a terminal workflow outcome.

The trusted fixture then calls upstream's existing `end_runs_of_lost_worker` and
`announce_lost_runs` for that exact test worker. Canonical state becomes Failed,
with its normal executor-lost message and no result. The durable SDK effect
remains 70; no new dispatch is created and the physical launch count stays one.
Access and refresh credentials each return 401 after canonical recovery.

The existing bounded terminal-log drainer persists the one pre-interruption SDK
log with its integration secret redacted. Repeating the drain flushes zero.
There was no provider result/event to ACK, no fabricated Success/Timeout result,
no automatic retry, no new authority queue and no production source changes.
The worker fixture and its claims are subsequently cleaned up.

This proves a local interrupted-provider/explicit-recovery boundary. Automatic
loss detection and revocation latency, whole-host loss, authenticated isolate
handle adoption, deployed recovery and per-isolate physical stop remain open.
Killing this Linux provider harness is not the missing physical-stop acceptance.

## Source, limits and commands

API source `f417d03f5c8d5e3a182e68f69e2bf601a3b60125`, prepared runtime
`785b560b9973020e1c13401383e1a73a87a54940`, existing workerd/Pyodide
`1.20261023.1`/`314.0.6` and verified source-built bundle cache. The two-file
workspace manifest remains pinned; fixture/runtime/workspace SHA-256 values and
all commands are in `../results/provider-interruption/receipt.json`.
Receipt SHA-256:
`00b1fcb9562540337d462a4253139390008ec4883203046c361b28e5669a6d87`.
Host-spool and local receipt hashes matched.

CT128 remained 2 CPU / 4 GiB/no swap. Provider: one CPU, 2 GiB, 128 PIDs, existing
isolated test-project network. The existing 900-second wall/24-GiB used-disk
guards remained. The supervisor completed in 58.179 seconds, stopped owned
containers and preserved volumes. No OOM, cloud invocation, new credentials or
runtime security-setting changes. Targeted Docker Ruff and offline verification
passed. Earlier baseline/benchmark modes were not rerun for this fixture-only
increment; historical verifiers retain their immutable source bindings.

The first attempt never exercised interruption: UID 1000 could not write the
new root-owned handoff directory. Its failed receipt/logs remain `failed-v1-*`,
SHA-256 `8ef530a6d0def00f77e76724f81595c0c1e5d6c05583f2f13cae8d3a7ef587c6`.
The correction gives only this new owned output directory the existing runner's
UID/GID and publishes the marker via an atomic no-overwrite link after writing
and closing it. The supervisor opens it without following symlinks and bounds
its content. Container users/capabilities, admission and runtime limits remain
unchanged. This was a diagnosed harness correction, not an unchanged retry.

Stage `canonical-local.py` and `ct128-workerd-provider-interruption.py` in the
supervisor's exclusive packet path and execute it inside the authorized guest.
It refuses active containers, changed source hashes and existing run paths.
The exact Compose invocation, mounts and environment are retained; it ends in:

```sh
python /qualification/canonical-local.py --integration-probe --integration-secret-probe --provider-interruption-probe
```

The handoff contains only a fixed boolean and value 70, never credentials.
Offline verification makes no invocation:

```sh
node spikes/cloudflare-pyodide/backend/verify-provider-interruption.mjs
node spikes/cloudflare-pyodide/backend/verify-workerd-loss-recovery.mjs
```

The prior late-success recovery verifier now uses its immutable tested source
at `81799e546ad55eecd94ae0130b5e1b0494780697`. This new failure-injection mode
does not retroactively qualify earlier source or the deployed backend.
