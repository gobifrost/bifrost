# Native loopback tail qualification

The documented WorkerCode.tails callback works in the existing pinned local
workerd runtime, including real Python execution. It provides a separate native
event channel; **it does not by itself certify physical stop or canonical
terminal acceptance**. No cloud/account logging was enabled, no Tail Worker
resource was created, and the deployed executor remains closed.

Normal completion does not require physical isolate destruction. Await required
work, finish streams, return the result and release returned RPC object stubs.
The fixture's fetch/JSON path returns no workflow RPC object. See the current
[lifecycle assessment](LIFECYCLE.md) for separate abnormal cancellation,
capability fencing, reconciliation and resource/accounting requirements.

Official sources:
- https://developers.cloudflare.com/dynamic-workers/api-reference/#tails
- https://developers.cloudflare.com/workers/runtime-apis/handlers/tail/

The docs distinguish producer outcome from HTTP status, including later
waitUntil failures. This experiment tests callback lifetime and event order
rather than assuming that an HTTP response marks the end of execution.

## Experiments

| Revision/change | Command in CT128 | Result | Next |
| --- | --- | --- | --- |
| v1: loopback tail without retained parent observer/child handle | `timeout 210 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/tail-observer-v1/ct128-request-signal.py --fixture tail-observer` | JS completion produced an ok tail; JS waitUntil produced neither background callback nor tail in the observation window. Driver stopped before Python. | Bound observer/handle retention |
| v2: retain both parent observer context and child handle until tail or five-second observer deadline | Same command under inbox/tail-observer-v2 with `--fixture tail-observer --revision 2` | All five controls produced native tails; supervisor 27.296 s, zero OOM, container stopped | Qualify equivalent per-run provider callback in a separately capped cloud window |

V1 is preserved, including the original entrypoint. The combined v2 change does
not isolate whether parent lifetime or handle retention alone is necessary.
The local Map is diagnostic bookkeeping, not a cross-isolate/cloud authority or
business queue. Tail metadata is sent independently before resolving any Map
entry. Late child callbacks are still emitted after the Map entry is removed.
The five-second retention deadline starts at request setup; it is not an executor
deadline, and does not promise a five-second post-response grace for a long run.
Do not use this fixture as a production lifetime policy.

Actual v2 observations on the independent native monotonic clock:

| Case | Observed order / result |
| --- | --- |
| JS completion | before, after, native_tail(ok) |
| JS waitUntil | HTTP response observed at 14.751 ms; background_after at 164.208 ms; native_tail(ok) at 166.403 ms |
| Controlled JS I/O abort | before, parent_abort_requested, native_tail(ok); caller received AbortError; no after in window |
| Python I/O | before, after, native_tail(ok); returned actual Python 3.14.2 / Pyodide 314.0.6 / cpython-314-wasm32-emscripten ABI |
| Python finite CPU | same ABI, checksum 19999999900000000, after then native_tail(ok); about 16.259 s between before/after |

The I/O abort's native outcome was ok, not canceled. Outcome alone therefore
cannot classify cancellation here. A tail after waitUntil is stronger evidence
than a response, but neither that field nor missing callbacks establishes
physical quiescence of arbitrary CPU code. CPU cancellation was not exercised
by this local run. No canonical late-result acceptance was tested.

cpuTime and wallTime were reported as zero even for the long CPU computation;
cpuTimeMs/wallTimeMs were absent. Raw fields are retained without unit inference.
These local zeros are not usable CPU/wall measurements or cost evidence.
Follow-up read-only inspection of the already available official source at
7caf6dee5bfe9af1b7710ddf66222900007af080, api/trace.c++:222-223, shows TraceItem
converts cpuTime/wallTime to milliseconds. The exact excerpt is retained as
metric-units-source.txt. This establishes units for that pinned implementation,
not that these local zero values are meaningful or that cloud metrics were read.

## Security, provenance and checks

Only fixed outcome, event timestamp, counts and numeric fields are retained.
Request headers/URL, raw logs and exception text are never copied. The child
cannot invoke the native tail channel through its Effects capability. The fake
synthetic-tail-secret console/header canary is absent from the independent tail
records; raw local CLI logs deliberately retain the fake console canary. This
does not claim production stdout/exception redaction acceptance.

No scoped Bifrost credentials or SDK requests were used. Fresh children receive
only a restricted effect binding, globalOutbound=null and unchanged 1000 ms CPU /
four-subrequest settings. Python code is the unchanged CPU-placement diagnostic,
not an independently maintained workflow engine or a new engine/SDK acceptance.

Source base c871d4abfab3e36c8da8055eb0948b63b222a88b plus exact packet hashes in
results/tail-observer/v2.json. All eight packet files match current source. The
existing publisher-source bundle/cache wrapper and dependency image were
integrity checked. cf CLI 1.0.0-beta.26 delegated local execution to existing
Wrangler 4.156.0. The shared local qualification helper now accepts a closed
fixture selector and bounded revision number, reusing the same resource guards
instead of creating another runtime supervisor. Historical receipts retain
their original source/command pins and exclusive paths must not be reused.

CT128 remained 2 CPU / 8 GiB. Serial test containers used one CPU, 1 GiB / no
additional swap, 256 PIDs, network=none, 5 GiB free-disk guard, 45-second readiness
and 120-second driver bounds. Both v1/v2 containers stopped, no volumes removed,
dev stack preserved. Network-disabled Request.cf metadata lookup warnings are
retained. No laptop Docker, deployment, spending, credentials, grants or
production effect. Helper Ruff and 17 selected offline tests passed. Broader
suites were not rerun for this companion-only diagnostic increment.

```sh
node --test spikes/cloudflare-pyodide/backend/tail-observer.test.mjs spikes/cloudflare-pyodide/backend/request-signal.test.mjs spikes/cloudflare-pyodide/backend/termination-observer.test.mjs spikes/cloudflare-pyodide/backend/timed-termination.test.mjs spikes/cloudflare-pyodide/backend/termination-evidence.test.mjs
node spikes/cloudflare-pyodide/backend/verify-tail-observer.mjs
```

Any separately authorized deployed experiment must correlate a native tail to a fresh authorized run,
keep the observer and run identity live long enough, and compare uncanceled and
canceled noncooperative CPU controls. The CPU fixture must continue despite
observer/cancellation exceptions, rather than accidentally stopping because an
effect call failed. Retain independent events and native outcome/metrics, with
null for unavailable data; establish their lifecycle guarantees before treating
any of them as a physical-stop receipt. Custom invocation-limit exceptions do
not establish absent plan-level enforcement; neither does this local emulator
run. Canonical fencing, recovery and cleanup remain separate acceptance gates.
No undocumented terminate API is required. Untrusted admission stays disabled.
