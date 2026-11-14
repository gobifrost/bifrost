# CPU limit placement: local API qualification

Hypothesis: applying the same 1,000 ms CPU limit on `getEntrypoint(null,
{limits: ...})`, in addition to WorkerCode, might change the deployed Python
quota behavior. Cloudflare documents both placements and says the lower limit
applies: https://developers.cloudflare.com/dynamic-workers/usage/limits/.

This increment qualifies the documented API shape on actual local workerd and
fixes an observer defect before a new cloud experiment. **It does not verify
deployed quota enforcement, cancellation, or physical termination.** Execution
admission remains disabled. Historical cloud evidence that Python caught
CpuLimitExceeded and subsequently emitted an effect remains unresolved.

## Experiment log

| Experiment | Change | Command | Observed result | Next |
| --- | --- | --- | --- | --- |
| local-v1 | Same fixed finite CPU loop, code limit versus additional entrypoint limit, independent native callback receiver | `timeout 160 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/cpu-placement-v1/ct128-cpu-placement.py` inside CT128 | Both loops completed. Source defect: normal completion omitted after; independent receiver saw only before. Receipt and original module/driver snapshots retained. | Correct normal completion signal; do not interpret absence as cessation. |
| local-v2 | Dedicated fixed Python diagnostic records after on normal completion and catch; reports actual ABI | `timeout 160 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/cpu-placement-v2/ct128-cpu-placement.py` inside CT128 | Three fresh isolates, all HTTP 200, independent before/after for completion control and both CPU cases. Both loops returned 19999999900000000; no quota exception fired locally. Supervisor 46.270 s, no OOM, owned container stopped. | Qualify the corrected probe on the existing isolated Cloudflare Worker with an independently read authenticated runtime-log observer. |

Source base: 80cb0d6220bcc38d58699b305ac78530eacd2dca plus the exact overlay
hashes in results/cpu-placement/local-v2.json. The offline verifier matches all
seven tested packet files to current source. V1 source differs intentionally;
its two original executable modules are preserved in local-v1-source.json.

CT128 remained 2 CPU / 8 GiB. Each test container used one CPU, 1 GiB RAM with
no additional swap, 256 PIDs, network=none and no external published ports.
Only its loopback observer was reachable. Free disk preflight required 5 GiB;
observed approximately 15 GiB. Both owned v1/v2 containers are stopped and their
evidence/volumes remain. The existing development stack was left running.
No heavy laptop Docker, credentials, cloud invocation, deployment, new guest,
resource expansion, runtime settings, or production effects occurred.

Actual runtime: Python 3.14.2, Pyodide 314.0.6,
SOABI cpython-314-wasm32-emscripten. Publisher-source bundle SHA256
3c3fd5a4179230e21e018e28b0e735e7c4abd8fe8e4ff39d2fd6bb2ea3a2e260;
the existing cache wrapper was integrity checked. cf CLI 1.0.0-beta.26 and
Wrangler 4.156.0 are the existing pinned development dependencies. The cached
dependency image is verified against its exact ID before starting.

## Reproduction and remaining boundary

Stage these files into an exclusive guest inbox cpu-placement-v2/packet:
cpu-placement.mjs, cpu-placement-entry.mjs, cpu-placement-driver.mjs,
cpu-placement.test.mjs, and the three files in cpu-placement-fixture. Stage
ct128-cpu-placement.py one directory above packet. The supervisor refuses an
existing run directory; do not delete/reuse evidence to repeat an experiment.
It invokes `cf dev` inside the constrained guest container using the existing
verified bundle cache. The complete Docker command is retained in the receipt.

Offline checks from the repository root:

```sh
node --test spikes/cloudflare-pyodide/backend/cpu-placement.test.mjs spikes/cloudflare-pyodide/backend/termination-evidence.test.mjs spikes/cloudflare-pyodide/backend/termination-observer.test.mjs spikes/cloudflare-pyodide/backend/timed-termination.test.mjs
node spikes/cloudflare-pyodide/backend/verify-cpu-placement.mjs
```

17 offline tests passed. The supervisor also passed Ruff inside the pinned guest
image with network disabled and 256 MiB RAM. Broader suites were not rerun: no
platform engine, dispatcher, database model, SDK, or frontend changed.

The native receiver persists across all three calls and correlates fixed case
IDs, accepting at most eight events per case. This catches late previous-case
effects instead of discarding them when a response returns. Both CPU loops took
about 16–17 seconds between callbacks on the native monotonic clock; these are
wall intervals, not measured CPU time. No absence, response, exception, or
successful control can produce a physical-stop acceptance flag.

recordCpuEffect is the prepared parent-only adapter to the existing BiFrost
runtime-log route. Its fixed destination, fixed event tags, no-redirect behavior,
credential placement and rejected-write behavior have unit coverage only.
It has **not** been qualified against that live API in this increment. The child
receives only the effect capability and globalOutbound=null; no run JWT.
cpu-placement-entry.mjs is strictly a loopback local fixture and must not be
deployed as the guarded cloud endpoint.

The next cloud test needs a separate guarded parent wrapper, run-scoped synthetic
identity, independent API log readback, explicit fresh-child/request/cost/expiry
reservation, and immediate secret/public endpoint cleanup. A caught exception
followed by independently observed effects is a counterexample. No later effect
is still insufficient to certify physical stop. No BiFrost backend routing or
untrusted admission may be enabled from this local result.
