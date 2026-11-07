# Upstream SDK #1217 qualification

Read-only upstream Git head was139596497d57b219ddcc3616aa66ec58e1b3cf3c on
2026-11-03. Its common ancestor with the qualified native contribution1d33150 is
025f6c019928d8be78bfe47f1cb62702b4ad2226. The contribution's resource-observer,
public-URL, stream-capture and result-envelope seams are not on upstream main yet;
their appearance as deletions in a branch-to-main diff is not an upstream rollback.
No shared-source merge or Containers-owner edit was performed.

Upstream #1217 changes SDK client HTTP-pool/process ownership and adds
`bifrost._tls.shared_ssl_context`. The source overlay stages the exact changed
root SDK modules (`client.py`, `_tls.py`, `_workspace_lock.py`) from139596497,
without rewriting them or replacing the separately qualified engine contribution.
Other dependency and server changes are not silently claimed qualified.

## Dependency and runtime evidence

| Component | Classification | Actual evidence |
|---|---|---|
| New SDK `_tls` module | Pure Python, new import dependency | Unchanged source imports certifi; v2 fails with ModuleNotFoundError before any workflow/network capability. |
| certifi2026.4.22 | Missing pure-Python/data wheel, not missing PyEmscripten native wheel | Exact `py3-none-any` publisher wheel, unchanged sources/CA data; v3/v4 imports and SDK constructor pass under local Python Worker. No native compilation or ABI retagging is relevant. |
| certifi.where/contents | Package API test, not a whole upstream test suite | v4 reads238641bytes, SHA25616be3f6f…f799f8, and passes the file into the actual SDK SSL-context construction. |
| SSL context certificate-store semantics | Material compatibility boundary | Same CA data gives120CAs on native CPython, zero reported by local Workers/Pyodide. Zero alone does not prove platform HTTPS verification is absent. |
| HTTPX JavascriptFetchTransport/AsyncJavascriptFetchTransport | Fetch-based transport contract | Bundled constructors accept verify/cert/proxy/uds/etc then `pass`; `_do_fetch` forwards headers/body/method/signal to JS fetch, not an SSLContext. Custom CA/client-certificate/proxy semantics are not established and must not be silently treated as supported. |

The exact public wheel URL is
https://files.pythonhosted.org/packages/22/30/7cd8fdcdfbc5b869528b079bfb76dcdf6056b1a2097a662e5e8c04f42965/certifi-2026.4.22-py3-none-any.whl
and SHA256 is3cb2210c8f88ba2318d29b0388d1023c8492ff72ecdde4ebdaddbb13a31b1c4a,
matching requirements.lock and the publisher PyPI JSON. Wheel135707bytes; CA
payload238641bytes. Source hashes and wheel bytes are preserved in the guest;
no whole private cache was copied. This is bundle/data overhead, not measured
isolate peak memory. No new native extension, ABI requirement or package patch.

The underlying missing custom-TLS hook is in the fetch transport. Compiling a
certificate library alone would not make `js.fetch` consume Python SSLContext,
so no speculative SSL compilation or replacement TLS stack was attempted.
Default platform HTTPS validation is separate and not disproved by this probe.

## Preserved attempts

* v1: local CLI failed readiness because the pinned workerd wrapper needed its
  existing `/spike` mount. EPIPE preserved; owned container stopped, no OOM.
* v2: corrected only the missing local wrapper mount. Actual Python Worker
  reports ssl importable, certifi absent, new SDK import fails for certifi.
* v3: exact verified pure wheel added. Imports/SDK construction pass.
* v4: adds certifi API and CA-store observations; imports/construction pass,
  zero CA-store count remains visible. The old v2/v3 `sdk_compatible` field refers
  only to the explicitly scoped import/constructor probe; v4 names that field
  `sdk_import_constructor_passed`. No operation-compatibility conclusion follows.

All probes use cached `cf dev`, network-disabled container1CPU/2GiB/128PIDs,
existing2CPU/8GiB CT128, original pinned Pyodide314.0.6/CPython3.14.2/
Emscripten5.0.3 ABI and source-built bundle. The child has globalOutbound=null,
no run identity, no API credentials and no workflow code. A fixed unusable
synthetic string is used solely to construct the SDK client. Each owned provider
was stopped and read back not running; volumes and prior receipts remain.
Ruff/Pyright and Node syntax checks pass for the new harnesses.

Raw receipts and bundled transport source/hash are in `../results/sdk1217/`.
Reproduce the source overlay with
`node backend/prepare-sdk-overlay.mjs 139596497d57b219ddcc3616aa66ec58e1b3cf3c`;
stage the verified wheel with `add-certifi-overlay.py`; run the assigned guest
`ct128-sdk-probe.py --revision N` only against a new owned packet/run path.
Verify published evidence using `node backend/verify-sdk1217.mjs`.

Follow-on SDK1217-EXECUTION.md now stages this SDK with full publisher license/
metadata and passes real engine/API config/table operations. Client lifetime,
refresh and error-path qualification under the update remain next. Qualify or
explicitly reject unsupported TLS
options before effects. This probe does not establish those semantics. Actual
deployed execution remains separately approval-gated; no Cloudflare account call,
credential grant, runtime/security-setting change or admission occurred here.
