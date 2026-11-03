# Prompt acceptance with awaited invocation streaming

The private local adapter now sends an opaque accepted frame before the real
workflow result is collected. The actual upstream ThinRemoteProcessPool binds
that handle while collection is pending, counts the invocation as busy without
a fake PID, then delivers the retained result through the canonical consumer.
Public admission remains disabled. This is not a new upstream wire API.

Normal completion follows Cloudflare's documented response lifetime: required
work is awaited while the response body remains open, then the result frame and
EOF are consumed. No waitUntil or per-success isolate destruction is required.
See [Context](https://developers.cloudflare.com/workers/runtime-apis/context/)
and [RPC lifecycle](https://developers.cloudflare.com/workers/runtime-apis/rpc/lifecycle/).
This HTTP path returns no workflow RpcTarget stub. Future RPC object results
must release their returned stubs explicitly; disposal is not a CPU-stop receipt.

The stream has two identity-bound NDJSON frames, each at most262144 bytes. The
reader validates run/handle identity and frame order. Truncation, a provider
error, oversized frames, extra frames or lost EOF require reconciliation.
Collected terminal evidence survives a subsequent disconnect. There is no
automatic relaunch or second authoritative queue. Provider error frames expose
only allowlisted error classes, never raw exception text or credentials.

Canceling the stream closes the fixture's future egress capability and removes
its parent bookkeeping once. It cannot undo an in-flight effect or certify that
Python computation ceased. The unit negative control deliberately lets work
continue after cancellation. The real pool retains its existing uncertain-route
and terminal-ACK ownership semantics; physical cancellation remains unqualified.

## Observed qualification

Assigned CT128, 2 CPU/8 GiB/no swap; provider capped at 1 CPU/2 GiB/128 PIDs. Existing
disposable project bifrost-test-5dc8d94e, native base 1d33150eb376b71f8a81b427920eda1b0447f829
with the previously qualified opaque-pool overlays. Pinned engine f49d4ba99,
closure bc4eae48...6daf0, cf 1.0.0-beta.26, Wrangler 4.156.0,
workerd 1.20261023.1, Python 3.14.2/Pyodide 314.0.6. No dependency/runtime change.

Command inside the guest:

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/stream-pool-v1/ct128-canonical-failure.py --stream-pool
```

Exit 0; all four phases pass. One launch, real authorized config/module/table SDK
calls, independently read document 70, cross-tenant 403, redaction and canonical
Success persist. Lost terminal ACK/reconnect reconciles without a second launch
or duplicate successful SDK write. Run credential 401 after terminal consumption;
native opaque route removed after terminal ACK. All owned test containers
stopped, volumes and separate dev stack preserved. No cloud invocation.

Accepted frame observed at native monotonic 3288395.670977459; terminal collection
at 3288395.855739252. This 184.762 ms gap proves ordering in this sample, not startup
latency or provider CPU time. Supervisor wall 64.966 s includes stack/setup/cleanup.
Isolate CPU/memory remain unmeasured. Raw receipt and exact packet hashes:
[v1.json](../results/stream-pool/v1.json).

Focused verification: three Node stream tests; four Python framing tests in the
cached native image with network disabled/256 MiB; guest Ruff on the changed
Python files and Pyright on the new decoder/tests; both historical opaque-pool
and current stream receipt verifiers. Exact focused checks and test hashes are
in [focused-checks.json](../results/stream-pool/focused-checks.json).
The 67 native pool cases are retained from their unchanged shared-source proof,
not rerun as this increment's deliverable.

```sh
node --test spikes/cloudflare-pyodide/backend/invocation-stream.test.mjs
node spikes/cloudflare-pyodide/backend/verify-physical-pool.mjs
node spikes/cloudflare-pyodide/backend/verify-stream-pool.mjs
```

Next safe increment: qualify a lost stream after acceptance against retained
canonical ownership, without redispatch or renewed deadline. Restarted-provider
inventory, authorized runtime negotiation, deployed enforcement/accounting and
untrusted isolation remain separate acceptance gates. No cloud reopening,
spending or untrusted admission is authorized by this result.
