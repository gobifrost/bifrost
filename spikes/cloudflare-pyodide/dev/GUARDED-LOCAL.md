# Guarded canonical integration

The current guarded canonical entry now executes the actual upstream engine and
SDK through the native canonical fixture on CT128. V4 passes in 86.253 seconds
for the full lifecycle. Two tenant attempts finish Failed/Success, one launch
each. Actual SDK effects, workspace provenance, redaction, stale-owner rejection,
duplicate dispatch, lost-ACK/current-owner reconciliation, terminal accounting,
SDK close and fixture cleanup are checked by the existing fixture. Native busy
counts were `[2,2,1,0]`; ended access credentials return 401. No owned test
containers remain running. This is local workerd, not deployed Cloudflare.

The executor source snapshot is `8f06d8b06` (packet guarded-source-v3), native
API `1d33150eb376b71f8a81b427920eda1b0447f829`, engine f49d4ba99,
SDK139596497, exact bundle61d818a. The provider used cf dev, cached workerd/Pyodide,
1 CPU/2 GiB/128 PIDs on the isolated test network. The original dev stack and
Containers owner were not changed. This does not assert current-main API parity.

## Bounded failures and fixes

* V2 stopped at readiness: the local bridge computed expiry during module
  initialization. V3 initializes that synthetic guard on the first request.
* V3 reached Python warmup and both executions, then module fetch returned403:
  the exported RPC API entry read its original environment rather than the
  parent invocation's trusted origin. V4 snapshots that origin into the attempt
  slot. The API capability reads only its bound slot; production guard and
  upstream authorization remain unchanged. No network allowlist was broadened.
* Each failed run was allowed to finish cleanup before its successor. Receipts
  v2/v3 remain failures; no identical reruns or timeout extensions were used.

Completed command:

```sh
python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/guarded-source-v3/spikes/cloudflare-pyodide/backend/ct128-canonical-failure.py --staged-runtime --revision 4
node spikes/cloudflare-pyodide/dev/verify-guarded-local.mjs
```

Raw receipts/private fixture logs remain under owned `runs/staged-runtime-v2`,
v3 and v4. Exported summaries contain only acceptance fields plus raw-receipt
SHA256; `guarded-local-source-v4.json` records candidate source hashes. The
existing fixture refuses export if run credentials appear in its evidence.
Preparation/export scripts pass Ruff in network-disabled bounded containers.

CT128 root grew from40 to48GiB under Thomas's prior explicit LXC expansion
authorization after host identity/capacity readback. Free disk became14,295MiB;
the8GiB guard,2CPUs/8GiB RAM/no swap remain unchanged. No evidence or volumes
were deleted to force the run.

The local-only bridge is never a deployment entrypoint. Next: build the exact
guarded cloud entry with cf CLI and connect it to a matched authenticated API
origin for bounded deployed acceptance. Physical CPU cancellation, hostile-code
isolation and cloud resource accounting remain unqualified. Normal successful
completion does not require per-job isolate destruction.
