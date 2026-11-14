# Terminal output budget experiment

Required invocation work returning is different from delivering its engine result.
The experimental adapter now reports `ExecutorFrameTooLargeError` when a consumed
terminal envelope exceeds the existing 262144-byte NDJSON frame budget. It drops
the oversized payload, reports `execute_returned: true` and
`engine_outcome_delivered: false`, and closes the stream with ordinary cleanup.
Effects may already exist. This must not trigger automatic workflow replay.

This is a proposed executor delivery failure, not equivalent support for large
Linux workflow results. Execution exceptions, serialization errors, interrupted
responses, and child responses exceeding the separate 1 MiB reader budget retain
their existing uncertain collection behavior. Collection still requires EOF.
No isolate destruction, CPU-stop, or deployed Cloudflare claim follows.

## Guest evidence

CT128, `/opt/bifrost-tests/pyodide-ddf316c1/inbox/output-budget-v1` preserves
the old implementation and regression. `node --test output-budget.test.mjs`
exited 1: the oversized terminal incorrectly produced `error` rather than the
explicit result; the execution-exception negative control passed.

V2 at the corresponding `output-budget-v2` path ran:

```sh
docker run --rm --network none --cpus .5 --memory 512m --memory-swap 512m \
  --pids-limit 64 -v "$PACKET:/qualification:ro" -w /qualification \
  bifrost-test-api-dev:bifrost-test-7ddfac36 \
  node --test invocation-stream.test.mjs output-budget.test.mjs
```

Exit 0, five passed, zero skipped, 336.829 ms test harness duration. The tests
cover completion/EOF/once-only cleanup, cancellation with continued work,
redacted provider errors, bounded oversized-output failure and an execution
RangeError remaining distinct. Containers used `--rm`; packet evidence remains.
Tested implementation SHA256:
`7f1b0297249aaa59e0db97b19ad2f9d144af8460890457caecc67ee066bf83ed`.

## Genuine upstream integration

`ct128-canonical-failure.py --oversized-result --revision 1` passed on CT128.
The first real engine workflow performed its SDK table write, then returned
300000 payload characters. The bounded failure traversed the actual upstream
terminal callback: canonical Failed, null result, explicit output-budget error,
and one persisted redacted log. The peer remained authorized while live and
completed Success with value 78. Both final credentials returned 401.

Injected post-commit ACK loss, owner reconnect, stale-owner rejection and replay
preserved canonical state/log IDs. Native reservations were 2,2,1,0; real
PostgreSQL accounting and fixture cleanup passed. Provider was alive without OOM
before owned cleanup; no owned containers remained running. Supervisor duration
73.625 seconds includes stack setup/prewarm/work/cleanup, not startup latency.
Ruff and Pyright passed for both changed Python fixture files.

Raw receipt: `../results/output-budget/receipt-v1.json`, including exact source,
runtime, image and packet hashes. Check with `node backend/verify-output-budget.mjs`
from the spike directory. The engine/SDK and native API pins are separate fields;
this does not establish parity with a newer upstream main.

Admission remains disabled. Larger child responses, memory exhaustion, full
native startup, deployed retention and abnormal cancellation remain open gates.

## Dev executor path

`dev/executor/output-budget.mjs` enforces the same 1 MiB retention budget on
the Python-entry child envelope: oversized output is dropped and reported as
`ExecutorBodyTooLargeError` with `terminal_body_too_large` /
`engine_outcome_delivered: false`, never parsed as a workflow result.
Covered by `dev/executor/output-budget.test.mjs` (`node --test`).
