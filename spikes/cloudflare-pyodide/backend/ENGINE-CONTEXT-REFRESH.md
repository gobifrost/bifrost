# Current engine/context closure qualification

The current checked-out engine/context delta executes an async workflow under
the exact managed Pyodide runtime in local workerd. This advances source
compatibility while the independent physical-termination blocker remains open.
No cloud endpoint was reopened and no run credentials were used.

Source pin:1796c6fcf68964aa2ff36421ccc7be697d6322f7. The separate experimental
overlay carries engine.py, worker.py and bifrost/_execution_context.py from that
commit over the existing61d818 runtime artifact. It does not alter or promote the
qualified/deployed artifact. The engine and context are executed here; worker.py
is staged for subsequent qualification but its run_execution path is not called.

| Fresh local Python child | Observed outcome |
|---|---|
| Unchanged qualified baseline, legacy metadata | Success, JSON value28/public URL |
| Current engine only, original SDK context | TypeError, expected negative control |
| Current engine/context together | Success, JSON value28, explicit public URL, agent/origin and original-user identity preserved |

The original context lacks newly supplied constructor fields. The negative
control and joint overlay establish the dependency coupling; no replacement
ExecutionContext or engine semantics were implemented. The workflow uses an
actual async function and asyncio scheduling through upstream execute(), not
successful imports alone. It makes no SDK operation and performs no network I/O.
Caller metadata is synthetic and is not authorization evidence.

All three source updates are pure Python; the existing466-module closure and
native wheels remain unchanged. Source byte increases versus the qualified
artifact: engine440, worker388, context755 (1,583bytes total). This is source size,
not resident-memory overhead. Pyodide314.0.6, CPython3.14.2 and SOABI
cpython-314-wasm32-emscripten were observed in each case. No compilation is
needed for these pure-Python deltas; no package was replaced or installed.

CT128 used its existing2CPU/8GiB allocation. The probe container used1CPU/2GiB,
128PIDs, network none, no additional swap, empty task HOME and read-only cached
packages/runtime. Child CPU1000ms, zero subrequests and globalOutbound=null.
Readiness bound45s; three sequential child requests have30s client bounds and
a100s outer controller bound. Runv2 completed in21.769s, then stopped its owned
container. Existing dev containers, volumes and uncertain locks were preserved.

Runv1 failed readiness with EPIPE: the pinned cache wrapper invokes workerd at
/spike/node_modules, but that mount was missing from this new harness. No Python
case ran. Its failed receipt/log remains. Runv2 adds the required read-only cache
mount and captures diagnostics without repeatedly printing Docker stderr. It
does not change the wrapper, runtime bundle, timeout or source candidate.

Exact command via existing pve-t340/pct access:

```sh
python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/engine-context-v1/spikes/cloudflare-pyodide/backend/ct128-engine-context.py --revision 2
```

Raw logs/receipts: runs/engine-context-v1 andv2 under the same owner root.
Committed sanitized receipts: engine-context-v1.json and engine-context-v2.json.
Ruff passes both Python fixtures; JS syntax and verify-engine-context.mjs pass.
The verifier binds source hashes to the tested commit, not future moving source.

Next: qualify this separate candidate through the actual run-scoped worker/SDK
fixture before changing the accepted runtime pin or attempting deployed parity.
Whole-current-main dependency compatibility, refreshed SDK/cloud execution and
physical cancellation are not established by this probe. The earlier deployed
canonical success remains pinned to its earlier engine/SDK. Quota/abort physical
stop counterexamples remain unchanged; repeating them without a new supported
mechanism would not justify arbitrary-code admission.
