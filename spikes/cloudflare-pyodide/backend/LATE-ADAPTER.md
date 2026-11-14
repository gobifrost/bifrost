# Integrated Timeout and Late Result Qualification

The private adapter's real deadline/reconciliation path now has a local
engine/SDK qualification. Two tenant workflows use genuine upstream run-scoped
credentials and API-derived pinned source. One finishes normally. The other
writes its initial document, waits a finite 40 seconds, attempts another SDK
write after cutoff, and returns success. Its declared native limit remains
30 seconds. The parent reconnects before cutoff, without extending that cutoff.

CT128 v2 verifies:

- The normal peer independently reaches Success (78).
- The original timer, under the replacement owner, commits canonical Timeout
  before the finite child's collection settles. A stale owner cannot report it.
- Native capacity and the actual PostgreSQL worker reservation still account for
  the pending child. An independent API credential probe returns 401.
- The late SDK table write receives exactly 410 from the existing local egress
  capability gate. Independent SQL inspection finds no `late-denied` document.
- The real child returns success (70), with engine duration 45,168 ms. Automatic
  terminal consumption leaves the canonical Timeout, null result, error,
  completion timestamp and log IDs unchanged, then releases physical capacity.
- Busy counts are 2,1,1,0. Each workflow launches once. Both final credentials
  return 401, journal/mux retirement passes, fixture ownership cleanup passes,
  and no owned test containers remain running. The provider was alive/no OOM
  immediately before explicit harness cleanup.

This is finite async work continuing after logical timeout, not a CPU-stop test.
The scoped egress gate is a tested local fixture capability, not proof that all
arbitrary Pyodide code or deployed network paths are equally constrained.
No physical cancellation, deployed isolation, public startup or untrusted
admission readiness follows. Normal successful jobs require completion and
resource settlement, not destruction of an isolate. Admission stays disabled.

## Reproduction and Evidence

Guest: existing CT128 `bifrost-pyodide-test-01`, 2 CPU/8 GiB, unchanged native API
checkout `1d33150eb376b71f8a81b427920eda1b0447f829`, engine source
`f49d4ba99cebd24aa497f1244833b940e640885f`. Dependency image
`sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b`.
Provider: 1 CPU/2 GiB/128 PIDs, existing pinned runtime and cached cf CLI local
`dev` invocation. Quality container: network disabled, 0.5 CPU/1 GiB/64 PIDs.
No package/runtime/security settings, account resources, spending or grants changed.

Stage the two changed fixture files over the prior `auto-delivery-v2` packet in
the uniquely named guest path, using the checked-in v2 quality script in
`../results/late-adapter/`. Then:

```sh
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/late-adapter-v2/ct128-canonical-failure.py --late-adapter --revision 2
node spikes/cloudflare-pyodide/backend/verify-late-adapter.mjs
```

Raw receipts preserve commands, source/fixture hashes, SDK observations, results
and cleanup. V2 Ruff/Pyright pass with zero diagnostics. The 39 prior targeted
tests remain evidence for identical adapter/helper/test hashes; they were not
rerun for this fixture-only change. Actual v2 supervisor wall time 112.335 seconds
includes setup, prewarm, fixture waits and cleanup, not pure startup or CPU time.
CPU, peak isolate memory and billing were not measured.

V1 is retained as failed evidence, not discarded: its test required a 401 at the
SDK write, but the existing `createAttemptEgress` intentionally returns 410 before
forwarding an expired request. It failed at that assertion (exit 1) and cleaned
up. V2 requires the exact 410, separately retains the API 401 requirement, and
adds no runtime workaround or relaxed status alternatives.

Next source boundary to reproduce: pre-ACK `httpx.TransportError` can propagate
from collection into the native pool's generic definitive-failure branch. A
started HTTP invocation may then be uncertain rather than effect-free. Add a
fail-first test and retain native capacity for uncertain started collection;
do not treat this source inspection alone as a reproduced defect. Supported
startup/registration and deployed abnormal CPU enforcement remain later gates.
