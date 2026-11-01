# Real provider result after canonical lost-worker recovery

The local workerd engine/SDK performed its real config/integration reads and one
successful document upsert, independently read back as 70. Before consuming its
collected successful result, the isolated fixture called upstream's
`end_runs_of_lost_worker` and `announce_lost_runs` for that exact test worker.
This injects the canonical lost-worker decision; it does not detect or interrupt
a host, kill a running isolate, or prove deployed recovery.

The real upstream recovery transaction committed `Failed`, no result, and its
normal `EXECUTOR_LOST` error. Access and refresh credentials each returned 401
**before** delivery of the late provider success. Delivering that actual result,
losing the callback ACK, rotating the owner session and replaying it preserved
the same Failed status, empty canonical result and completion timestamp.
No new dispatch was created; one physical launch and one successful SDK upsert
were observed. A stale event being consumed/acknowledged is not success acceptance:
the receipt explicitly sets `canonical_terminal_consumption_verified` false and
records `canonical_recovery_status` as Failed.

## Log recovery boundary

The initial run passed fencing/revocation but sampled zero persisted logs after
replay, despite two successful SDK log API calls and two logs in the collected
provider result. Its receipt and logs are retained as `initial-*`.

The follow-on fixture invoked the existing bounded
`recover_terminal_execution_logs([execution_id], limit=1)`. It persisted the two
actual Redis-stream log rows. A second drain flushed zero, preserving identical
row IDs. No custom log store, queue, recovery algorithm or production source
change was introduced. This qualifies the **explicit existing drain path**, not
automatic recovery scheduling or a universal exactly-once log guarantee.

## Evidence and reproduction

Final receipt SHA-256:
`62bcd195b85b1d5a9d63713e4ffdbc8a3c514e90f43662b1af05f94a22bfce97`.
Initial SHA-256:
`4d6a3851fed0056fa300d7b8eb0754d2e38ad7f1c2a98a1ccc701145490637b3`.
Host-spool and local hashes matched. Exact fixture/runtime/workspace digests,
commands, API observations and cleanup are in `../results/workerd-loss-recovery/`.
The updated fixture is opt-in; existing modes retain their success assertions.

API source `f417d03f5c8d5e3a182e68f69e2bf601a3b60125`, prepared runtime
`785b560b9973020e1c13401383e1a73a87a54940`, existing workerd/Pyodide
`1.20261023.1`/`314.0.6` and verified source-built bundle cache were reused.
CT128 remained 2 CPU / 4 GiB/no swap; provider one CPU/2 GiB/128 PIDs. The
supervisor retained 900-second wall and 24-GiB used-disk guards. Initial and
follow-on supervisors exited zero in 57.51 and 69.69 seconds respectively.
Owned containers stopped, volumes retained, no OOM, zero cloud invocations.
Targeted Docker Ruff and offline receipt verification passed.

Stage `canonical-local.py` and `ct128-workerd-loss-recovery.py` at the supervisor's
exclusive packet path and run it inside the already authorized guest. It checks
source hashes/clean checkout and refuses existing run paths or active containers.
Do not overwrite evidence directories. The exact recorded Compose command ends:

```sh
python /qualification/canonical-local.py --integration-probe --integration-secret-probe --worker-loss-probe
```

Offline checks:

```sh
node spikes/cloudflare-pyodide/backend/verify-workerd-loss-recovery.mjs
node spikes/cloudflare-pyodide/backend/verify-workerd-benchmark.mjs
```

The historical benchmark verifier now reads its source from immutable tested
commit `4434ffe73dcac497c1f7ce668bb5e77659364faa`, rather than treating this
later optional fixture mode as the source of an earlier measurement.

Remaining: automatic loss detection/revocation latency, actual interrupted
provider/whole-host recovery, authenticated isolate-handle adoption, physical
termination and deployed acceptance. This fixture does not reopen cloud access,
enable execution admission, replace canonical ownership or authorize shared-file
changes.
