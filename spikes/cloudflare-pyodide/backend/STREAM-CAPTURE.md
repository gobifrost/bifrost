# Shared engine printed-output repair

Deployed follow-up: [dev/STREAM-LOGS.md](../dev/STREAM-LOGS.md) now records actual
Cloudflare success-path stdout/stderr delivery and PostgreSQL persistence using
real run credentials and this exact engine revision. It does not replace the
historical canonical failure fixture or prove canonical cloud dispatch.

The later [canonical failure repair](CANONICAL-FAILURE-REPAIRED.md) separately
qualifies actual current-upstream local claim/consumer failure and stdout
persistence, including lost-ACK replay and credential revocation. It retains the
old failed receipt and still does not qualify deployed canonical Cloudflare
transport or physical termination.

The recorded canonical failure fixture exposed a shared engine defect: execute redirected stdout/stderr into StringIO objects but never read them. This is pure Python execution logic, not a missing wheel or an unsupported operating-system dependency. The fix belongs in the upstream engine, rather than a companion print implementation that Linux would not share.

The isolated contribution replaces the discarded buffers with one capture context. On normal return or an exception it restores the original process streams, collects complete and partial printed lines, applies the existing credential/header sanitizer using secrets registered during execution, and appends the rows to existing result logs. Stdout has level info; stderr has level error. Literal printed level prefixes remain literal text. Rows retain the existing workflow/script source. The same sanitized messages use the existing authenticated runtime-log transport, so canonical persistence need not invent another log endpoint. Transport exceptions are nonfatal and only their type is reported; their potentially secret-bearing text is withheld.

Capture is flushed at the execution boundary, not streamed per character. Order within each captured stream is retained; cross-stream/structured-log chronological order is not reconstructed. This does not change supervised service execution, add a new output DTO, solve process-global stream concurrency or bound the existing StringIO memory use. Current executors must still provide fresh per-run execution environments. Physical termination and canonical cloud dispatch remain separate incomplete requirements.

## Verification boundaries

The first new test fixture omitted the required organization argument. Its five TypeError failures are preserved under before-v1 and are not stdout compatibility evidence. Corrected before-v2 executes the unchanged engine: four missing-output assertions fail, while the structured logging control passes. No failed assertion was removed. The after tests use that same corrected fixture.

`ct128-stream-capture.py` uses the existing owned CT128 checkout/project, cached API image and bounded Compose services; no laptop Docker, production resources or cloud invocation. Exact commands:

```sh
python3 ct128-stream-capture.py
python3 ct128-stream-capture.py --after
python3 ct128-stream-capture.py --pyodide
```

The native path runs repository ./test.sh, including capture, runtime credential redaction, execution-contract and logging-lock consumers, followed by focused Ruff/Pyright. The network-disabled guest Pyodide path loads the actual modified engine module against the previously qualified pinned closure and ABI. It uses an empty execution ID to disable delivery and makes zero SDK calls: it proves returned print/error/redaction semantics only. Real credentialed log persistence and actual deployed Workers execution of this modified engine require separate subsequent qualification. Historical deployed receipts retain their original engine/source pins.

Shared-file reconciliation: the known Containers worktree's engine.py/worker.py were clean. Eddie's conflict report a512e660-4958-4621-98a5-56704661fa4b found no conflicting planned engine/test edit through its retained owner evidence; it explicitly was not ownership clearance or maintainer acceptance. No other owner's files, branch or runtime resources were changed.

Observed: all37 native cases passed, with no failures/errors/skips; Ruff and Pyright passed on engine.py and the new tests. Supervisor elapsed103.866s includes stack lifecycle. Actual guest Pyodide returned Success70 with redacted stdout/stderr and Failed/ValueError with redacted pre-error output. ABI: Python3.14.2, Pyodide314.0.6, Emscripten5.0.3, cpython-314-wasm32-emscripten. The network-disabled process was capped at1CPU/1GiB/no swap/128PIDs/120s; it exited0 without OOM and was stopped. That process cap is not a128MiB isolate memory measurement. Native project cleanup left no running containers; dev services/volumes remained. Raw receipts, JUnit and exact source hashes are in results/stream-capture. Run `node backend/verify-stream-capture.mjs` to verify them against source.
