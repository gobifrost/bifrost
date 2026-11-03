# RPC results: disposal works, pending work still completes

The pinned local Python Worker exposes `Symbol.dispose` on RPC promises and
returned objects. The earlier disposal probe inspected the loader's Worker handle
and entrypoint, which are different objects. Their missing disposers do not imply
that RPC result disposal is unavailable.

This matches [Cloudflare's lifecycle guidance](https://developers.cloudflare.com/workers/runtime-apis/rpc/lifecycle/):
dispose RPC return objects/promises when finished with them. Disposal manages
references/resources; normal completion does not require destroying an isolate.
The existing canonical prototype uses HTTP JSON responses, not returned RpcTargets,
so this experiment adds no new production transport or cleanup dependency.

## Actual local observations

* A Python dictionary RPC result arrives as a JavaScript Map on this pin. Reading
  `.value` silently produced undefined in v1; that invalid measurement is retained.
  The corrected fixture explicitly requires Map and reads its fixed fields.
  This is not a general RPC codec or proof of large-integer/recursive conversion.
* Returned Map value7 and a separate RPC promise both have disposers, and their
  explicit calls return successfully. A primitive7 has no disposer. No server
  memory reclamation or RpcTarget destructor timing was measured.
* In v4 the second I/O operation is held on an asyncio.Event: started2/after1.
  The client disposes the pending RPC promise, **then** sends a separate release
  RPC. The held call subsequently returns7 and after becomes2. Completion cannot
  race ahead of disposal because only that later RPC opens the gate.

Thus pending-promise disposal is not a stop receipt for this fixed local operation.
The parent context and Worker handle intentionally remain alive during observation.
This is not a disconnected-client/context-teardown test, CPU enforcement test,
deployed Cloudflare result, or claim that platform cancellation is absent.

## Evidence and reproduction

v1 records the property/marshaling mistake; v2 fixes Map extraction; v3 adds actual
ABI; v4 replaces timing-based completion with the causal release gate. All receipts
and predecessor sources are in `../results/rpc-results/`. Reproduce using the
new fixed mode of the existing credential-free harness:

```sh
ct128-sdk-probe.py --rpc-results --revision N
node backend/verify-rpc-results.mjs
```

Actual ABI: Pyodide314.0.6, CPython3.14.2, cpython-314-wasm32-emscripten,
emscripten-5.0.3-wasm32. Same source-built runtime bundle3c3fd5a4 and cached cf CLI
1.0.0-beta.26/workerd1.20261023.1. CT128 stays2CPU/8GiB; provider1CPU/2GiB,
network none/globalOutbound null, 120-second outer bound. Owned providers stopped.
Node syntax, Ruff and Pyright pass. The SDK/engine fields in these probe receipts
remain false because neither SDK execution nor workflow compatibility was tested.

No cloud invocation/deployment, new dependency, credential, grant, resource or
runtime-security setting. Admission remains disabled. Portable startup still
cannot claim Linux pressure/termination controls from these disposal observations;
deployed cancellation/isolation/memory qualification is independently required.
