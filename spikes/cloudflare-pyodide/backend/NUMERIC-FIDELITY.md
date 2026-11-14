# JSON numeric fidelity

The canonical local adapter previously parsed workflow JSON into JavaScript
Numbers and serialized it again. That silently changed a real Python workflow's
`9007199254740993` result. V1's genuine upstream engine/SDK fixture failed the
exact-value assertion; its receipt and failure are preserved in
`../results/numeric-fidelity/`. This is a correctness defect, not an optional
Python package or missing WebAssembly wheel.

`lossless-json.mjs` uses native JSON.parse reviver source context and JSON.rawJSON
to forward numeric lexemes. Safe integer control fields remain Numbers. Other
numbers, including fractions/exponents and signed zero, remain opaque JSON values
for forwarding to Python. No independent parser or string-to-code evaluation is
introduced. Missing native support fails before fixture admission. The canonical
parent request and child-result boundaries use this adapter; the unrelated
diagnostic parser remains unchanged.

Specification: https://tc39.es/ecma262/multipage/structured-data.html#sec-json.rawjson
and https://tc39.es/ecma262/multipage/structured-data.html#sec-json.parse

## Evidence

Node tests on CT128, network disabled, .5 CPU/512 MiB/64 PIDs, cached dependency
image: `node --test backend/lossless-json.test.mjs`: three passed, no skips.
They exercise large positive/negative integers, a longer integer, fractions,
exponents, signed zero, safe integer controls, native syntax rejection and the
existing response byte bound.

V2: `ct128-canonical-failure.py --numeric-result --revision 2` passed with the
actual upstream engine, SDK, local Python Worker, canonical result callback,
duplicate dispatch, lost-ACK replay and independent peer. The exact integer
result survived. This establishes local workerd support for the native JSON
primitives, not deployed Cloudflare support. Engine/API/runtime/image pins and
packet hashes are retained in each raw receipt.

V3 passed with nested positive/negative integer and fractional
input parameters, checked inside Python before effects, returning the exact input
integer. Its result is recorded separately. Canonical Success preserved
`9007199254740993`; busy counts were 2,2,1,0 and both completed credentials returned
401. Failed ACK replay preserved state/logs, and owned container cleanup completed.
Supervisor 73.885 seconds includes setup/prewarm/SDK waits/cleanup, not pure startup.
Ruff and Pyright passed on the changed fixture files. The combined eight focused
Node stream/output/numeric tests passed afterward. Verify source-bound evidence
with `node backend/verify-numeric-fidelity.mjs` from the spike directory.

No Python runtime, ABI, dependency wheel, resource, credential, deployment or
admission setting changes. Peak memory and parser overhead are unmeasured.
This fixes the canonical fixture transport only; it does not establish JSON
fidelity for every demo entrypoint or arbitrary Python objects. Byte bounds and
oversized terminal failures remain. Full startup and deployed abnormal
cancellation/isolation qualification remain open.
