# Real canonical failure and the stdout boundary

Current follow-up2026-11-03: [CANONICAL-FAILURE-REPAIRED.md](CANONICAL-FAILURE-REPAIRED.md)
records a new passing current-upstream local workerd run with the repaired engine.
It verifies Failed/ValueError, redacted stdout directly in PostgreSQL, unchanged
log IDs after lost-ACK replay, one launch and access/refresh401. The historical
failed receipts below retain their original source/flags; deployed canonical
Cloudflare dispatch and physical termination remain unqualified.

Update2026-11-02: the shared-engine repair in [STREAM-CAPTURE.md](STREAM-CAPTURE.md)
now passes37 native tests plus actual guest Pyodide success/failure/redaction
controls. The historical canonical/cloud fixture below still uses its original
engine pin and remains a failed stdout qualification. Its receipts and assertions
were not rewritten. Credentialed persistence and deployed acceptance of the new
engine revision still require a subsequent fixed-source run.

A genuine scoped workflow reads config, prints its run credential, emits a
structured warning containing that credential, then raises ValueError containing
it before any document write. Actual engine/SDK execution returns ValueError,
redacts the structured log and exception, and preserves that error through the
normal fenced consumer into PostgreSQL. The canonical row is Failed with
`fixture failure credential=[REDACTED]`, not a harness AssertionError.
Logs persist; lost-ACK replay does not relaunch or rewrite the outcome. Access
use and refresh redemption both return 401 afterward; the workflow document is
absent (404), and the fixture worker/claims are cleaned.

**The historical full fixture exits 1:** stdout is absent from its pinned engine result.
The stdout assertion remains in place after canonical consumption/cleanup, so
that gap cannot be hidden by the successful error-path checks. Offline evidence
verification exits successfully only to confirm those observations, including
the failed qualification flag. It does not turn the backend green.

## Smallest demonstrated blocker

At the historical source pin, `engine.execute()` creates stdout/stderr StringIO buffers and
redirects into them, but neither buffer is read afterward. The structured logging
handler follows a separate capture/streaming path. Source inspection therefore
locates this boundary in shared pure Python engine logic, not a missing wheel,
native extension or Pyodide transport. No dependency compilation, replacement
engine or companion print-to-log behavior was introduced.

The initial fixture stopped at the missing-stdout assertion before completing
the real error callback. Its receipt and exact driver/observer snapshots are
retained. The next bounded change deferred only that assertion until after the
real ValueError was consumed/reconciled and cleanup verified. It still failed.
No assertion was dropped, skipped or converted into a passing compatibility
claim, and no unchanged retry or timeout increase was used.

The source-ownership query for a shared engine/worker fix was denied by the
existing Eddie caller with `source_run_not_active`, `safe_to_resend:false`,
request `090210bf-62f1-488c-b38f-df026906504f`. No automatic retry, alternate caller,
new credential or Containers steering was attempted. This is a coordination
limitation, not maintainer acceptance or a new platform authorization. Shared
engine/worker source and production behavior remain unchanged in this increment.

## Reproduce and inspect

With the existing healthy isolated API and authorized-local Wrangler supervisor:

```sh
node backend/canonical-refresh.mjs --run canonical-refresh-workflow-failure-terminal failure concurrent-runtime-manifest.json
node backend/verify-workflow-failure.mjs --commit d352dc663a0dfe40d38892a25b9b78725f0041ee
```

The first command deliberately remains a failed runtime qualification. It
refuses an existing receipt before invocation. The second is offline and checks
actual Failed state, original ValueError, redaction, no document upsert, one
launch, revocation, cleanup, source provenance and missing stdout. The prior
command typo `canonical-workflow-failure` was rejected by the filename preflight
before any invocation; the corrected fixed-prefix name is shown above.

`results/canonical-refresh-workflow-failure*.json` are actual local Worker/API
observations. Provider evidence is checked for both handed-down secrets before
publication; exposed evidence would be withheld. The fixture uses only temporary
test-stack run credentials, never production credentials. It reuses the current
cooperative package; no new native module or cloud invocation was needed.

Focused Ruff and JavaScript syntax checks pass. Whole-backend acceptance remains
incomplete: printed output is unqualified. Linux's physical timeout path kills
the process before reporting TimeoutError (`ProcessPoolManager` monitor and
`_report_timeout`); passing timeout metadata to the engine alone does not prove
a Pyodide stop guarantee. No Cloudflare physical-stop equivalent is established.
Reporting a logical timeout must not certify that the isolate stopped or that
later effects are impossible.
