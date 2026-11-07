# Actual upstream engine on guest Pyodide

Starting branch head: 4a53d9ceab3f51846d68d2aa6b62dfe6e8f819c1. Actual engine
source is pinned at 785b560b9973020e1c13401383e1a73a87a54940, SHA-256
b9bd1d04a17d9dfc6a9418550ffb361c734d99558bdd7c710f3c53b0fdbc06ab.
The earlier prepared runtime-source closure provides unchanged upstream Python
files and namespace initializers that avoid booting the server application.
No new upstream source edit, SDK stub, auth bypass or engine clone was used.

The existing official Pyodide 314.0.6 core and locked native dependencies were
reused within assigned CT128. The companion runtime_guard.require_runtime()
accepted the actual five-field ABI before upstream imports. Actual
src.core.module_cache_sync.mark_execution_child() prevented parent DB/cache
paths. Direct ExecutionRequest.func execution supplied public_url explicitly,
used transient/no_cache flags, and required no injected settings replacement.

Four fixed workflows use the actual bifrost.decorators.workflow decorator and
src.services.execution.engine.execute:

| Case | Actual observed result |
|---|---|
| Pure JSON transformation in an async callable | Success, total 6, correct context execution ID |
| Async yield plus parameters/context | Success, value 42, GLOBAL scope |
| Explicit success=false return | CompletedWithErrors |
| ValueError containing registered synthetic engine credential | Failed / ValueError, credential=[REDACTED]; marker absent from complete serialized output |

Only a synthetic non-authorizing marker was registered for redaction. No API
credential, mock SDK transport, SDK operation or platform execution claim was
supplied. This qualifies engine semantics on this guest interpreter, not
platform authorization, workspace HTTP loading, durable ownership or SDK calls.
Structured exception logs were returned and scrubbed. Stdout capture, resource
metrics, cancellation, physical stop and fresh-per-execution isolation are not
established by these four cases.

## Diagnosed fixture failure and correction

The initial fixture provided raw synchronous callables. The real upstream
engine awaited them, returning Failed / TypeError ('dict' object can't be
awaited). An async case and ValueError redaction already worked. The first
observer then failed while expecting a successful JSON result. A separate
diagnostic run preserved all four raw outcomes before assertions.

Source inspection confirmed both engine call sites await the callable, and the
SDK decorator attaches metadata without wrapping a sync function as a coroutine.
The fixture was corrected to async decorated workflows; upstream execution was
not changed to manufacture sync support. Separate run directories/container
names preserve the two failed receipts. This is a direct-call contract boundary,
not a claim that platform discovery uniformly rejects every sync workflow before
effects. Arbitrary execution admission remains disabled.

## Command, resource bounds and cleanup

```powershell
Get-Content -Raw spikes/cloudflare-pyodide/backend/ct128-engine-semantics.py | ssh -o BatchMode=yes -o ConnectTimeout=6 pve-t340 "pct exec 128 -- python3 -"
```

The source harness reserves runs/engine-semantics-resume-v3 under the exclusive
owner scratch and refuses an existing reservation. Its actual Docker command,
timestamps and source hash are in results/engine-semantics-resume/receipt.json.
It uses the previously verified image, network=none, 1 CPU/1024 MiB/no swap,
128 PIDs, 120 s subprocess timeout, no published ports and read-only source,
runtime and package mounts. Existing CT128 remains 2 CPU/4 GiB. No source or
private credential/cache from another owner was copied.

The corrected container exited 0. All owned containers are stopped, with no OOM;
failed containers, receipts, scratch and volumes remain. Heavy laptop Docker
stayed stopped. No cloud/R2 call, denied URL retry or baseline rerun occurred.

Offline independent verification binds the historical source bytes and checks
all four returned statuses, results and redaction while asserting SDK/cloud/stop
qualification flags remain false:

```sh
node spikes/cloudflare-pyodide/backend/verify-engine-semantics.mjs
```

The Cloudflare workerd bundle supply gate and trusted physical termination
remain unresolved. Successful official-Pyodide engine execution must not be
reported as deployed Python Worker acceptance or used to advertise capacity.
