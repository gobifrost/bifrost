# Recovery After a Lost Deadline Callback Receipt

The private adapter now distinguishes the original failed deadline task from a
later successful current-owner timeout-consumption receipt. Explicit
`reconcile_deadline()` records its owner only after the existing upstream timeout
consumer and both owner checks return normally. It does not rewrite the original
task, retry execution or imply that timeout won a stale-result race.

After actual terminal acceptance, both normal and retained-result paths join the
owned deadline task. An older callback exception no longer blocks that ACK if a
separate successful timeout receipt belongs to the current parent owner. Without
that proof, the exception still propagates. Cancellation is not caught as an
ordinary callback exception. The old failed deadline/collection receipts remain
readable, and native capacity still requires actual terminal consumption.

## Reproduced Defect and Fix

V1's native regression first loses the collection, then loses the deadline
callback receipt. A failed explicit recovery still raises. After successful
current-owner recovery, retained terminal acceptance formerly raised the old
`ConnectionError` anyway. The fail-first test exits 1 with:

`recovered canonical deadline still blocks retained settlement`

V2 uses a separate fenced consumption record, leaving the original receipt
failed. All 50 targeted unit tests pass (3.352 seconds), plus Ruff/Pyright with
zero diagnostics. The same regression verifies one POST, retained original
ReadError and deadline error, release only after actual terminal acceptance,
and retirement. An initial shell invocation named a nonexistent script path and
started no test; the corrected assigned-guest path produced the preserved V1
regression. There was no uncertain execution retry.

## Actual Upstream Engine/SDK Qualification

The finite late-result fixture also has `--deadline-recovery` mode. It keeps the
original 30-second native cutoff and 40-second finite async workload. The normal
peer finishes independently; the other run commits canonical Timeout but its
callback loses the ACK. The parent explicitly reconciles through the real
canonical consumer under its replacement PostgreSQL owner, then awaits actual
late success. Observed on CT128:

- Timeout persists before physical completion, with native/PG reservation held.
- Explicit recovery succeeds while the original deadline receipt still raises
  its injected error even after late-result consumption.
- Late success does not change Timeout, null result, error, completion timestamp
  or log IDs. Current owner succeeds; stale deadline owner is rejected.
- Independent API credential probe is 401; the late SDK write receives the
  existing egress gate's 410 and SQL confirms its document is absent.
- Each child launches once. The normal peer returns 78; accounting is 2,1,1,0.
  Both final credentials return 401, retirement and fixture cleanup pass, and
  owned running containers after cleanup are empty. Provider alive/no OOM before
  cleanup is separate from settlement evidence.

No per-success isolate destruction, logical cancellation or caught exception is
used as proof of physical CPU termination. This remains local workerd/Pyodide
evidence, not deployed enforcement or arbitrary-workflow acceptance.

## Reproduction and Provenance

Existing CT128: 2 CPU/8 GiB; native API
`1d33150eb376b71f8a81b427920eda1b0447f829`; engine
`f49d4ba99cebd24aa497f1244833b940e640885f`; image
`sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b`.
Unit/quality containers: network none, 0.5 CPU/1 GiB/64 PIDs. Local provider:
1 CPU/2 GiB/128 PIDs, cached cf CLI and the same pinned runtime. No package,
runtime/security-setting, credentials/grants, cloud invocation or deployment change.

Stage the four changed files over `retained-http-v1` using the checked-in scripts
in `../results/deadline-recovery/`. Then:

```sh
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/deadline-recovery-v2/ct128-canonical-failure.py --deadline-recovery --revision 2
node spikes/cloudflare-pyodide/backend/verify-deadline-recovery.mjs
```

Raw failed/passing outputs, exact commands, source hashes and actual engine/SDK
receipt are preserved there. Actual supervisor total is 112.124 seconds including
setup, prewarm, waits and cleanup; it is not startup or CPU measurement. Next:
oversized input preflight before native bookkeeping, then supported startup and
deployed retention/abnormal isolation gates. Public admission remains disabled.
