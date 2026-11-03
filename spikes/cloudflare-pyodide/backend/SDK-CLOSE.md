# Awaited SDK completion cleanup

The local runtime now awaits upstream's public `get_client().close()` in a finally
block after the engine finishes, before constructing the terminal response. It
does not replace SDK pool ownership or close arbitrary user-created clients.
No background waitUntil task, loop shutdown, process kill or isolate-destruction
requirement is introduced.

The response marks only `X-Spike-SDK-Close-Returned: true`; the companion retains
that fact as `runtime_cleanup.sdk_close_returned`, outside the canonical engine
result. That diagnostic means the awaited SDK call returned. It is not a physical
termination assertion. A close exception propagates and prevents that response;
this change does not silently suppress cleanup failures or invent an engine result.
Interruption/exception recovery during close remains separately unqualified.

## Actual local proof

The credential-free updated-SDK probe (`ct128-sdk-probe.py --revision 5`) creates a
real async pool and its loop-owned cleanup task. The task is pending before
upstream `close()`. After awaiting close, the actual async/sync HTTP clients report
closed, the task reports done and the loop-client registry is empty. A second
close returns. No mocked SDK close, network operation or run credential is used
in this isolated primitive test.

Then real engine/SDK workflows pass with the same completion wrapper:

* Updated SDK139596497 plus exact pure-wheel candidate:
  `ct128-canonical-failure.py --sdk-update --revision 2`, exit0,72.265s supervisor.
* Previously qualified SDK bundle:
  `ct128-canonical-failure.py --api-prepare --revision 91`, exit0,72.698s supervisor.

Both versions perform real config/table SDK operations; both controlled failure
and successful peer responses record awaited close returning. Canonical error and
Success78 remain correct. Lost-ACK/current-owner replay preserves row/log IDs;
stale owner and duplicate dispatch checks pass; ended credentials401, reservations
2,2,1,0, canonical accounting and cleanup pass. No owned containers remain running.
These durations include setup/prewarm/work/cleanup and are not startup/CPU latency.
Ruff/Pyright pass for the changed Python harness; actual workerd execution tests
the embedded Python/response-header path.

Source-bound receipts: `../results/sdk-close/{probe,updated,baseline}.json`.
Run `node backend/verify-sdk-close.mjs` from the spike directory. Earlier receipts
remain bound to their historical helper source rather than being relabeled as
this implementation.

This closes one normal completion obligation for the supported fresh-child,
single-loop workflow subset. It does not establish arbitrary background-work
containment, custom TLS behavior, cross-host recovery, deployed cleanup or abnormal
CPU/I/O cancellation. Public admission remains disabled. No Cloudflare account
call, grant, spending or runtime/security-setting change occurred.
