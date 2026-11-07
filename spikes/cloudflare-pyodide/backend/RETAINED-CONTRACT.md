# Companion Retained-Terminal Adoption

The private adapter can now consume a trusted parent's retained HTTP terminal
after its original collection failed. It does not rerun the workflow or replace
the original failed receipt. The caller must authenticate the physical evidence
source and establish terminal settlement; this method is not that transport,
not a public result endpoint, and not an existing upstream API.

`reconcile_retained()` validates bounded evidence, execution identity, a bounded
opaque runtime handle and any previously observed handle. It snapshots the
evidence and refuses changed replay. Live-session and route-principal guards
precede adoption. Pending/normal collections cannot use this recovery path.

Acceptance still traverses the real native result handler and canonical
consumer under the owner fence. Failed callbacks retain the result and native
capacity. Only acknowledged acceptance marks the failed mux collection as
reconciled, settles the deadline task and allows eventual launcher retirement.
Normal-return ACK means consumed, possibly stale; it does not assert that this
outcome replaced canonical state or that an isolate was destroyed.

The journal now has a generic trusted-transport retained-terminal operation.
Its existing upstream launcher-event method validates that event and delegates
to the same owner/acceptance logic. HTTP evidence is not assigned a fabricated
launcher `_event_id`. No PostgreSQL writes, credentials, scheduling or new
business-job queue are implemented in the companion.

## Tests and Evidence

Existing CT128 (`bifrost-pyodide-test-01`), unchanged 2 CPU/8 GiB; native checkout
`1d33150eb376b71f8a81b427920eda1b0447f829`; dependency image
`sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b`.
Unit/quality containers are network-disabled, 0.5 CPU/1 GiB/64 PIDs. The existing
resource-bounded disposable PostgreSQL test stack uses one job at a time.

`ct128-retained-contract.py` checks guest/source/image/disk/project and known
prototype hashes, preserves the old prototype source, temporarily mounts the
candidate for the real PostgreSQL tests, stops only its test project and restores
the original source. Raw commands, source hashes, JUnit and unit/quality output
are in `../results/retained-contract/`.

```sh
timeout -k 5 300 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/retained-contract-v1/ct128-retained-contract.py
node spikes/cloudflare-pyodide/backend/verify-retained-contract.mjs
```

Observed:

- 48 targeted native unit tests pass (2.367 seconds), including retained adoption
  after lost ACK, immutable replay, wrong handle/run/boolean rejection, stale
  owner refusal, unchanged original ReadError receipt and one physical POST.
- All 38 actual PostgreSQL-backed journal tests pass via
  `./test.sh tests/unit/dev/test_pyodide_attempt_journal.py -q`; no skips/errors.
  This checks the existing launcher path after extraction, canonical identity,
  fencing and result semantics against the real disposable database.
- Ruff and Pyright pass, zero diagnostics, including the guest runner.
- All commands exit zero. Owned running containers after cleanup are empty.
  The source overlay is restored and independently read back at its prior hash
  `f8c05fc45b351e25d87f00f6a2a2cd536244400c92cd602c9f94f1bac3fbbf9c`.
  Supervisor total is 115.392 seconds including stack lifecycle, not runtime latency.

No Cloudflare invocation, deployment, resources, package changes, credentials,
grants or runtime/security-setting changes. No shared upstream source changed.

## Remaining Acceptance

The new adoption unit tests supply synthetic trusted evidence and double the HTTP
transport; the PostgreSQL tests do not run Pyodide. Neither proves authenticated
HTTP retention around a real engine/SDK invocation. The next experiment must
retain the actual provider stream through terminal settlement while dropping its
ACK to the original collector, then exercise this operation with that evidence
without another execution request. Retention remains process-local; losing both
transport/evidence requires canonical recovery, not speculative replay.

Deadline callback failure/recovery and oversized context preflight also need
targeted qualification. Public admission stays disabled. Supported startup and
deployed abnormal cancellation/CPU enforcement remain unproven. This adds no
anonymous evidence trust or arbitrary-workflow readiness claim.
