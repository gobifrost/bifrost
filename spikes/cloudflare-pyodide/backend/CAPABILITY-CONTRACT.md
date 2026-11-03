# Private workload requirement binding

`PrivateHttpWorkflowPool.bind_manifest` now requires a keyword-only
`requirements` list/tuple. The companion vocabulary is `json`, `async-workflow`,
`pinned-workspace`, `bifrost-http-sdk`, and `platform-fetch-https`. An explicit
empty declaration is valid for code needing none of these capabilities; omission
is an error. Unknown, duplicate, malformed or over-32-item declarations fail
before the plan is stored, native bookkeeping, journal reservation or HTTP
dispatch. In particular, custom CA, mTLS, proxy, Unix-socket and subprocess
requirements are refused. Error text does not echo input.

The host supplies this declaration for reviewed code under the bound source
hashes. The adapter snapshots it, prohibits rebinding the execution, and includes
the complete plan in its duplicate-attempt fingerprint. No caller-owned list can
change it after binding. No canonical context or remote wire schema is changed.
This is a **private companion proposal**, not an existing upstream scheduling or
authorization API. It introduces no queue, token issuer, direct DB update or
automatic Linux fallback.

This is a declaration check, not code discovery or a Python sandbox. Arbitrary
Python can conceal a capability requirement or mutate runtime objects. The check
does not establish physical cancellation, isolation, resource enforcement, or
default-SDK TLS equivalence. Untrusted/public admission remains disabled. The
transport options negative result remains in TRANSPORT-OPTIONS.md; no shared
SSLContext whitelist or transport rewrite was introduced.

## Evidence

* The new contract regression initially failed with nine TypeErrors because the
  companion had no requirements argument. This is missing-interface evidence,
  not a claim that the predecessor accepted those new arguments.
* CT128 native Docker checks: 25 adapter tests pass, including unsupported/mixed
  declarations before effects, omitted declaration, mutation/rebinding controls,
  duplicate delivery, ownership, deadlines, retained outcomes and cleanup.
  Ruff/Pyright pass for the final changed source.
* Actual `ct128-canonical-failure.py --sdk-refresh --revision 3` exits 0 in77.189s
  (whole harness lifecycle). Receipt records both declarations and source hashes;
  real upstream engine/SDK config/table calls, refresh/redaction, stale-owner
  rejection, lost-ACK replay, duplicate delivery, revocation, accounting[2,2,1,0]
  and fixture cleanup pass. No owned test containers remain. Volumes retained.

Raw evidence is in `../results/capability-contract/`; run
`node backend/verify-capability-contract.mjs` to check it. Native API source stays
1d33150, packaged engine f49d4ba, SDK overlay139596497, pinned runtime Pyodide
314.0.6/CPython3.14.2/cpython-314-wasm32-emscripten. Guest allocation stays
2CPU/8GiB; real provider1CPU/2GiB, focused checks .5CPU/1GiB, bounded wall times.
No heavy laptop test, cloud action, new dependency, credential or setting change.

The next canonical integration gap is refusal retirement: an authorized no-send
outcome needs upstream-owned acknowledgement/recovery before releasing a claimed
attempt. PRELAUNCH-RETENTION.md records that open seam. This declaration check
does not invent a terminal acknowledgement or make that gap disappear.
