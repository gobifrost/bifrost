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

Next: exercise this failure through the genuine upstream engine/SDK and canonical
terminal callback, including existing effects and independent peer completion.
That integration is not established by these transport tests. Admission remains
disabled; abnormal cancellation and deployed enforcement remain separate gates.
