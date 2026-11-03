# Updated upstream SDK: real engine/API execution

The exact SDK overlay from139596497d57b219ddcc3616aa66ec58e1b3cf3c now executes
real workflows through the qualified engine contribution, rather than only
importing/constructing a client. Server/native source remains1d33150 and engine
source remainsf49d4ba; this is not a merge or full parity claim with upstream main.

`merge-sdk-overlay.mjs` verifies the original bundle hash, exact SDK/wheel pins,
each overlaid module hash and preservation of all453 untouched modules. The full
pure wheel now retains license, METADATA, WHEEL and RECORD resources. The candidate
has466 modules, serialized JS15287043bytes and SHA256
61d818aef7f66a129951c7eb0cc4dc638277da35e635e13965b90f8a23575731.
Serialized source size is not compressed deployment size or peak isolate memory.
No native build, ABI retagging, package rewrite or engine semantic change.

The initial merge attempted to overwrite a root-owned baseline file from the
container and failed EACCES. That artifact stayed unchanged. The bounded fix
writes a separate candidate in the existing task-owned writable directory and
changes only the local test copy's runtime import to select it. No permission,
identity, runtime-security setting or access-control change was used.

CT128 command:

```sh
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/sdk-execution-v1/ct128-canonical-failure.py \
  --sdk-update --revision 1
```

Exit0. Both real engine/SDK workflows load their API-pinned workspace modules,
read own config, reject peer scope403 and write independent table documents.
The controlled first failure and successful peer value78 traverse the actual
native pool/canonical result consumer. Lost terminal ACK/current-owner reconnect
preserves canonical state and log IDs; stale-owner submission rejects. Duplicate
peer dispatch reuses the original attempt. Failed credentials return401 while the
peer remains200; both ended credentials return401. Native reservations2,2,1,0,
real canonical accounting and fixture cleanup pass. Provider remained running
without OOM until owned cleanup; no owned containers remained running.

Supervisor73.121s includes setup/prewarm/SDK waits/cleanup, not pure startup.
Ruff and Pyright pass for changed Python harness/staging code. The unchanged
SDK source and real operations supply the runtime evidence; imports alone do not.

Raw receipt and complete overlay manifest are in `../results/sdk-execution/`.
Run `node backend/verify-sdk-execution.mjs` from the spike directory. The local
runtime is the existing Pyodide314.0.6/CPython3.14.2/Emscripten5.0.3 ABI. No
Cloudflare deployment/invocation, new grant/credential, spending or shared
Containers-owner file change occurred.

Remaining: explicit new SDK client/loop lifetime cleanup qualification, refresh
and error paths under this SDK version, and TLS-option admission. This ordinary
virtual-API test does not validate custom CA/client-certificate/proxy settings or
the TLS certificate-store discrepancy recorded in SDK1217-QUALIFICATION.md.
Do not enable admission or claim deployed canonical acceptance from it.
