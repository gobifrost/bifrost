# Excluded workspace import roots

Preparation previously accepted a declared workspace `json.py`, `bifrost.py` or
other upstream-excluded import root and attached a digest to it. Upstream's
VirtualModuleFinder declines those roots before workspace lookup, so that pin
cannot establish which code a Python import executes.

The companion now imports upstream's actual `STDLIB_PREFIXES` exclusion set and
refuses such declarations before opening the SDK HTTP client. Nested declarations
are checked by their root. This preserves upstream import precedence rather than
adding a competing module loader or copying a maintained allowlist. The imported
module runs only in the trusted native parent; preparation remains outside the
Pyodide workflow. No wheel build is relevant to this pure-Python policy check.

## Evidence

Fail-first CT128 packet `inbox/import-provenance-v1`: seven reserved-root subcases
failed to reject, while the six existing preparation tests passed. V2 adds the
guard; `python -m unittest -v test_workspace_prepare test_private_http_pool`
passes 26 tests in 3.403 seconds. The regression requires no HTTP requests for
`json`, `json.decoder`, `bifrost`, `bifrost.tables`, `src`, `os` and `httpx`.
Existing tests preserve ordinary packages, namespace descendants, hash pinning,
authorization denials, redirects and response bounds. Ruff/Pyright pass for both
changed files. Tests ran in the cached image with .5 CPU/1 GiB/64 PIDs and no network.

Actual unchanged engine/SDK control:
`ct128-canonical-failure.py --api-prepare --revision 89` exits0 on CT128. This uses
the new preparer with genuine workspace source/resolution APIs and run identity.
Both SDK effects were independently observed before release; the first controlled
failure, peer Success78, lost-ACK/current-owner replay, stale-owner rejection,
credential401 revocation and accounting2,2,1,0 passed. Owned containers stopped,
volumes retained. Supervisor72.118s includes setup/work/cleanup, not startup.

Raw receipt and readback hashes: `../results/import-provenance/`. API/native source
and packaged engine commits are distinct pins. Their actual excluded-root sets
both contain112 identical names. Run `node backend/verify-import-provenance.mjs`
from the spike directory for source-bound checks. No cloud call occurred.

## Remaining boundary

This rejects provably misleading declarations, not every installed-package
collision or unsupported dependency. It does not discover dynamic imports,
statically prove effects, inspect arbitrary Python capabilities or establish an
untrusted-code sandbox. No general dependency admission claim is made. Each new
package still needs pure-Python/native-wheel/OS-contract classification and
appropriate real runtime tests; runtime inventory alone is insufficient.
Admission remains disabled pending startup, deployed isolation, memory and
abnormal-cancellation acceptance. No upstream platform source changed.
