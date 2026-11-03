# Original Deadline Supervision in the Private Adapter

The private adapter starts an owned deadline task when the native pool exposes
its `ExecutionInfo`, before waiting for the provider's launch ACK. The timer uses
that original native start/time limit. A current-lease resolver selects the
canonical owner at cutoff; reconnect does not restart the clock. Reporting uses
the actual upstream consumer's timeout envelope, never native handle release.

Normal canonical result acknowledgment resolves a separate settlement future
and joins the timer. Lost collection or ACK leaves that future pending. An
expired timer reports logical timeout while physical capacity remains held.
Actual late results first reconcile the expired deadline, then traverse the
native result handler; upstream still decides stale-result consumption and
idempotency. `reconcile_deadline()` permits explicit current-owner recovery after
an uncertain timeout callback. It neither retries execution nor stops an isolate.

The registry lock now protects reservation rather than the launch-ACK wait.
Duplicate callers await the same retained future outside that lock. This fixes
a deadlock exposed by the pre-ACK timeout/retirement test.

## Evidence and Limits

Existing CT128, 2 CPU/8 GiB; no cloud calls or runtime/security-setting changes.
Native checkout `1d33150eb376b71f8a81b427920eda1b0447f829`; engine
`f49d4ba99cebd24aa497f1244833b940e640885f`; dependency image
`sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b`.
Unit containers: network disabled, 0.5 CPU/1 GiB/64 PIDs. Actual local provider:
1 CPU/2 GiB, cached cf CLI and pinned Pyodide runtime, unchanged from prior proof.

Raw evidence is in `../results/owned-deadline/`:

- V1 hangs at the new pre-ACK timeout/retirement test. The registry lock/ACK
  wait forms a deterministic wait cycle. Source and partial output retained.
  The soft `timeout 50` did not finish Docker reliably; the exact owned
  `pyodide-auto-delivery-unit-v1` container was stopped with `docker stop -t 3`.
  The observed command ended with exit 1. This is harness cleanup, not physical
  executor cancellation evidence. No volumes or unrelated containers removed.
- V2 moves the ACK wait outside the lock, adds a hard-kill grace to the test
  harness (`timeout -k 5 50`), and passes 39 targeted tests in 1.232 seconds.
  The real one-second pre-ACK timer invokes TimeoutError while routing remains
  pending, settlement remains pending and native busy count stays one. After
  the fixture supplies ACK/result, normal consumption releases it without
  another POST. Separate test verifies current owner selection at cutoff.
- Ruff and Pyright pass with zero diagnostics for the changed helper/adapter
  and tests. The v2 script records exact Docker commands and pins.
- `auto-delivery-v2/receipt.json` is the actual two-tenant engine/SDK qualification
  with this candidate. Both effects occur under real run credentials; error
  ACK loss/reconnect/replay remains idempotent, successful peer automatically
  persists 78, both terminal credentials return 401, native/PG accounting is
  2,2,1,0, retirement passes and owned running containers after cleanup are empty.
  Provider remains alive/no OOM before cleanup. Supervisor 74.026 seconds includes
  setup, prewarm, fixture barriers and cleanup; it is not startup/CPU measurement.

Reproduce the unit run using the checked-in v2 guest script after staging its
named packet. Actual normal-path command:

```sh
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/auto-delivery-v2/ct128-canonical-failure.py --auto-delivery --revision 2
```

This real run covers normal/error completion with timers enabled. It does not
exercise actual timeout/late-result behavior through the newly integrated private
adapter. Prior real deadline-helper receipts prove that separate helper path;
they are not substituted for this missing integration test. Next is a finite
late-result fixture demonstrating canonical Timeout, revocation and retained
capacity before actual settlement, with no overwrite afterward. CPU termination,
deployed enforcement, supported public startup and untrusted admission remain
unqualified. Admission stays disabled.
