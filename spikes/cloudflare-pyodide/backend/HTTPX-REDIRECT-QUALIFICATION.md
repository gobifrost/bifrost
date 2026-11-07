# HTTPX redirect semantics prerequisite

Prototype 0.1.0. This is a local transport qualification, not canonical
engine/SDK execution, deployed HTTPS acceptance or admission enablement.

The preceding raw pyfetch fixture used explicit manual redirect mode. Actual
HTTPX 0.28.1 uses its pure-Python jsfetch transport. That transport's _do_fetch
omits redirect mode, allowing JavaScript to follow before HTTPX can apply its
own policy. The original test returned final 403/200 responses with empty
history and original URLs, despite follow_redirects=True. The parent origin
gate still refused the unapproved destination; no origin bypass was observed.
Preserved v5 observation records the mismatch and independent server requests.

This is pure-Python behavior, not a missing PyEmscripten wheel or OS facility.
Compilation cannot fix an omitted fetch option. httpx_redirect_patch.py checks
the exact transport source SHA256, takes the actual upstream _do_fetch source,
adds only redirect:manual to its fetch options, and compiles that function in
the original module environment. No maintained HTTPX transport clone is added.
This patch is installed only in the fixed qualification entrypoint; it is not
part of the engine runtime or production startup. Source drift fails closed.

The actual v6 test passes with the same pinned runtime closure used by the
preceding staged-runtime execution proof: bundle61d818ae, SDK139596497,
enginef49d4ba, Pyodide314.0.6, Python3.14.2,
cpython-314-wasm32-emscripten. The supervisor records all input hashes; the
bundle/provenance validator is checked separately against the packet after the
run, rather than claiming it was a new pre-execution gate in this supervisor.

Observed: direct POST200; cross-origin302 then403 with history[302] and final
unapproved URL; same-origin307 then200 with history[307], final URL, POST body
and vendor Authorization preserved. Exactly four approved forwards reach the
independent loopback HTTP server. No external DNS/TLS, real credentials or
BiFrost API operation is involved. The entire container has network disabled.
Deadline/close, redirect loops, 301/302 method rewriting, no-follow behavior,
sync HTTPX, sensitive-header stripping across two approved origins, and real
engine/SDK integration are not qualified by these three cases.

## Reproduction and artifacts

Existing CT128 allocation: 2CPU/8GiB/no swap. Provider:1CPU/2GiB/128PIDs.
Cached image33cd92be, existing hash-checked workerd bundle3c3fd5a4 and wrapper
9f9ad4a4. cf CLI1.0.0-beta.26, Wrangler4.156.0. No dependency installs.

Prepare a new exclusive packet using approved-https-fixture configs and the
approved HTTPS candidate/deadline modules. Use the existing qualified staged
runtime files (generated-runtime/runtime-code/sdk-cleanup-code/provenance).
Copy approved-httpx-entry.mjs as project/backend/approved-https-entry.mjs and
approved-httpx-probe.mjs as project/backend/approved-https-probe.mjs. Generate
project/backend/httpx-redirect-code.mjs with:

```sh
node stage-httpx-redirect.mjs <packet>/project/backend/httpx-redirect-code.mjs
```

The existing supervisor mode is reused, without a new mode or implicit fallback:

```sh
timeout -k 5 120 python3 <packet>/ct128-sdk-probe.py --approved-https --revision 6
node spikes/cloudflare-pyodide/backend/verify-httpx-redirect.mjs
```

Guest packet: /opt/bifrost-tests/pyodide-ddf316c1/inbox/approved-https-v6.
v4 first exposed empty history; the supervisor retained cleanup but did not retain
the failed driver's stdout. v5 adds exclusive observation-file capture and
reproduces the same mismatch. v6 adds the pinned patch and passes. The v5 driver
source snapshot is retained with the observation. Providers v4/v5/v6 stopped;
existing dev stack and volumes untouched. No CPU/memory overhead measurement is
claimed; the receipt's whole lifecycle wall time is not startup alone.

Next: expand redirect semantics coverage and incorporate the qualified transport
change into the private canonical fixture before advertising direct HTTPS.
Public admission remains disabled and no Cloudflare invocation occurred.
