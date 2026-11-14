# Oversized child response consumption

The canonical parent retains at most 1 MiB of child response content. Previously,
exceeding that limit canceled the reader before EOF; even completed workflow work
therefore had an uncertain terminal delivery. The previous behavior remains
exercised as a negative control.

`terminal-response.mjs` discards buffered content after the retention bound and
continues reading with a fixed 4 MiB total drain ceiling. Only actual EOF yields
an explicit `ExecutorBodyTooLargeError`, `response_eof: true` and
`engine_outcome_delivered: false`. Effects may already exist. No original workflow
success/failure is inferred, no payload is truncated into a success, and no retry
is introduced. A body above the drain ceiling or a read failure remains uncertain;
reader cancellation is resource cleanup, never completion evidence.

This is an experimental mechanical delivery failure through existing canonical
failure handling, not parity with Linux support for larger results. The response
body can be discarded without decoding malformed oversized JSON; the reported
fact is EOF and failure to deliver the engine outcome, not validation of that
discarded outcome. Parent memory is bounded by retained chunks plus runtime-owned
stream chunks; no measured isolate peak-memory claim is made.

The existing original workflow deadline, egress cutoff, native HTTP collection
timeout and outer guest harness timeout remain. The drain does not add a new
execution window, background owner or durable queue. A hanging stream stays
uncertain; this helper does not establish physical CPU termination. Admission
remains disabled.

## Verification

CT128, cached dependency image, network-disabled .5 CPU/512 MiB/64 PIDs:

```sh
node --test backend/terminal-response.test.mjs backend/lossless-json.test.mjs \
  backend/invocation-stream.test.mjs backend/output-budget.test.mjs
```

Thirteen tests passed without skips. Controls distinguish prior early cancel,
consumed oversized EOF, a never-ending response exceeding the drain ceiling,
transport failure after the retention bound, numeric fidelity and malformed
fitting JSON. The existing invocation-cancel control demonstrates continued work,
not physical stop. Ruff and Pyright pass for the changed Python fixtures.

The genuine fixture mode `ct128-canonical-failure.py --oversized-body --revision 1`
returns 1200000 payload characters after actual SDK effects. Its separate raw
receipt establishes canonical terminal behavior: 1202232 response bytes consumed,
Failed/null result/output-budget error, persisted redacted logs, successful
independent peer value 78, both completed credentials 401, unchanged state/logs
after lost-ACK/current-owner replay, stale-owner rejection and reservations
2,2,1,0. Provider stayed alive without OOM before cleanup; no owned containers
remained running. Supervisor duration 73.507 seconds includes setup and cleanup,
not startup latency. Raw source/runtime/image/packet hashes are in
`../results/body-budget/receipt-v1.json`; check with
`node backend/verify-body-budget.mjs` from the spike directory.
All work remains guest-only, with no cloud invocation or runtime-setting change.
