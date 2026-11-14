# Source change after authenticated preparation

The fixed source-change control prepares entry/helper pins through the actual
run-authenticated API, then changes only the failing fixture's helper in owned
Redis/S3 source storage. The helper would return 999 if loaded. An independent
authenticated API read must confirm the new content/hash while the bound plan
retains the original digest. The unchanged peer uses a different helper.

The failing workflow imports its helper eagerly, before its function makes any
configuration/table SDK calls. The control requires an actual failed engine
result, absence of its document under an independently authenticated API read,
and only source-lookup/engine-log traffic in its captured API calls. The driver
also requires the specific `Workspace module digest mismatch` diagnostic in the
engine result or a bounded private provider-log observation. It never publishes
raw provider logs or treats an arbitrary early failure as integrity evidence.

The peer must still reach its real SDK upsert, remain pending and authorized
while failure ACK loss/owner reconnect is reconciled, then commit Success/78.
Native/worker resource obligations must settle 2 → 2 → 1 → 0, with no re-launch
of either attempt. The existing module integrity gate, engine and SDK are used
unchanged. The experiment changes fixture source, not Worker admission, runtime
configuration, credentials, grants or production data.

This is an **eager-import** negative control. It does not establish an atomic
whole-workspace revision or guarantee that a late dynamic import fails before
earlier authorized effects. Workflow errors are not transaction rollback. A
changed source must not be silently loaded or a failed attempt automatically
re-run; already-started external effects remain subject to upstream semantics.
Arbitrary untrusted admission and deployed CPU settlement remain unaccepted.

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/source-change-v4/ct128-canonical-failure.py --source-change --revision 4
# Exact-candidate bounded stability check, separate fresh fixtures:
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/source-change-v5/ct128-canonical-failure.py --source-change --revision 5
node spikes/cloudflare-pyodide/backend/verify-source-change.mjs
```

Same assigned CT128 2 CPU/8 GiB/no swap, isolated project, provider 1 CPU/2 GiB/
128 PIDs, cached dependency image, cf CLI/workerd and pinned runtime bundle.
The Python preparation/pool helpers are unchanged from the 23-case API-preparation proof;
those cases need not be rerun to exercise this new real-engine mutation case.
Targeted Ruff/Pyright cover the changed fixture and driver. No cloud call,
resource expansion, laptop Docker or production change is involved.

## Failed predecessors and bounded fix

V1 failed the fixture's blanket nonempty-log requirement. The actual upstream
worker returns `WorkflowLoadError`, `logs=[]` and a persisted error when import
fails before workflow tracing. The fixture now accepts zero logs only for that
exact envelope, keeping canonical status/error checks and the healthy peer's log
requirement. No engine logs or semantics were fabricated.

V2 failed the held-peer check. V3 retained a diagnostic: native busy count was
still 2, but the peer had completed with `BifrostAPIError`, later SDK log requests
were 410, and its original cutoff was already 10.784 seconds past. This was not
accepted as successful isolation. A temporary nonempty V2 cleanup readback was
reconciled read-only to the exact SeaweedFS container exiting; its original raw
receipt remains unchanged. No volumes or uncertain locks were deleted.

The bounded transport fix keeps the same digest validator but represents its
known mismatch as HTTP 409 plus structured denial evidence. It avoids throwing
that expected failure across the Python/JS bridge. Upstream 401/403 responses
and unrelated/provider-limit exceptions retain their behavior. The existing
30-second cutoff, held-peer barrier, resource bounds and admission rules are
unchanged. Three Node tests cover this response boundary; they are not deployed
CPU-limit enforcement proof.

V4 and identical-candidate V5 both pass the full real control. The changed helper
is independently present (API 200), yet the child receives recorded digest-denial
409, produces `WorkflowLoadError` and makes no config/table SDK call. Its document
is independently absent (404) before terminal consumption. Canonical Failed has
zero workflow logs, matching upstream's early-load semantics. The peer remains
pending with 23.845 and 24.447 seconds of original budget left at the diagnostic,
survives failure ACK replay/reconnect, and succeeds with 78 after release.
Both attempts retain normal fencing, credential revocation and 2 → 2 → 1 → 0
accounting/retirement. These two local observations do not establish a universal
latency or arbitrary-error isolation guarantee.

All failed receipts/sources and the two passing receipts are retained under
[results/source-change](../results/source-change/). Supervisor totals 102.704 and
94.861 seconds include setup, explicit prewarm, barriers and cleanup; they are
not pure startup, CPU or cost. Provider remains running/no OOM before owned
cleanup, and both passing receipts have no remaining owned running containers.
Deployed combined acceptance and physical CPU cancellation remain unqualified.
