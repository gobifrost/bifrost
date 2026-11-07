# Deadline capability gate: new requests versus uncertain effects

Follow-on: [canonical deadline binding and actual response uncertainty](CANONICAL-DEADLINE.md).
The original relative-cap experiment below is retained as its own tested stage;
current canonical mode requires the dispatcher's absolute cutoff.

The local companion now refuses new API requests once its effect window expires
or its invocation completes. Actual guest Pyodide continued computation and
caught three expiry failures; all three requests were refused before reaching
the independent effect sink. This is capability fencing, not physical CPU stop.

The real upstream engine/canonical failure and lost-ACK replay path also passes
with this gate installed. It retains one launch, Failed/ValueError semantics,
11 persisted redacted logs, access and refresh credential rejection (401) after
terminal consumption, and fixture cleanup. No Cloudflare invocation occurred.

## Scope and implementation

`attempt-egress.mjs` checks the trusted parent clock on every dispatch. It does
not depend on a timer firing while Python is CPU-bound. Expiry/close is permanent
for that capability; requests cannot reopen it. Each attempt has a separate gate.
The existing `authorized-local-entry.mjs` applies it after the fixed destination
allowlist and before network dispatch, including credential refresh requests.
Upstream remains the authenticator/authorizer; the gate does not parse JWTs,
mint credentials, accept results or own scheduling/recovery.

This local fixture caps the window using its existing admitted timeout_seconds
at parent entry. It is not negotiation of the canonical absolute deadline.
Production integration must bind the cutoff to authenticated canonical attempt
ownership; children must not choose or extend it. The diagnostic's module-local
Map is not durable provider inventory and is not deployed admission.

A deterministic unit counterexample holds a dispatched write, closes the gate,
then allows that write to commit and loses its response. The failure propagates
without retry or invented rollback. Therefore this gate cannot claim that *all*
effects cease at the cutoff: an already-authorized request can still commit.
Actual external uncertainty needs upstream reconciliation; arbitrary external
services cannot be made transactional by rejecting the workflow result.

## Experiment log and exact sources

| Hypothesis/change | Command in assigned CT128 | Observed result / next |
| --- | --- | --- |
| Clock check can fence new effects even while Python survives rejection | `timeout 210 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/egress-v1/ct128-request-signal.py --fixture egress` | Two cases pass; 13.015 s supervisor wall time. Completion accepts before/after; late case accepts only before, then three 410s after a 20-million-iteration loop. Python returns checksum 199999990000000. |
| Existing canonical path remains compatible | `timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/egress-canonical-v1/ct128-canonical-failure.py --egress-gate` | Harness exit 1: missing canonical-local.py/task_cleanup.py in packet. Test command exits 2 before workflow launch. Receipt retained; cleanup passes. |
| Complete packet plus fail-fast file preflight | Same helper under inbox/egress-canonical-v2 with `--egress-gate --revision 2` | Exit 0, 64.370 s; genuine engine/canonical failure, terminal reconciliation and cleanup pass. Next: bind effect cutoff to canonical deadline and qualify in-flight uncertain-effect recovery under that identity. |

Guest identity rechecked: CT128 / bifrost-pyodide-test-01, 2 CPU / 8 GiB, no swap,
16 GiB disk free and approximately 6.5 GiB memory available before runs. No guest
resources changed. Simple runtime probe: one CPU / 1 GiB / no additional swap /
256 PIDs, network=none, local loopback observer. Canonical provider: one CPU /
2 GiB / 128 PIDs on the existing isolated test network; the pre-existing bounded
Compose project is stopped afterward, with volumes retained. Neither provider
reported OOM; no owned test containers remained running. Dev stack preserved.

Source base d65d055a1f49f8633b4e808ebff6a461297249d9 plus packet SHA256s in
`results/egress-gate/pyodide-v1.json` and `canonical-v2.json`. The canonical API is
1d33150eb376b71f8a81b427920eda1b0447f829 with the previously qualified owned
overlays; packaged engine is f49d4ba99cebd24aa497f1244833b940e640885f, closure
bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0.
The canonical fixture/cleanup sources are pinned from 80cb0d622, rather than
silently substituting current edits. The verifier binds changed capability and
runner files to the new receipt and preserves the old qualification against its
historical source.

Actual ABI: Python 3.14.2 / Pyodide 314.0.6 / cpython-314-wasm32-emscripten.
cf CLI beta.26 delegates local execution to Wrangler 4.156.0 and cached workerd
1.20261023.1. Existing bundle/cache-wrapper and image pins are rechecked by the
runner; no dependency replacement, build patch or new package is involved.
This test adds no peak isolate memory or billed CPU measurement.

## Verification and remaining gates

```sh
node --test spikes/cloudflare-pyodide/backend/attempt-egress.test.mjs spikes/cloudflare-pyodide/backend/fixture-admission.test.mjs spikes/cloudflare-pyodide/backend/refresh-gate.test.mjs
node spikes/cloudflare-pyodide/backend/verify-egress-gate.mjs
node spikes/cloudflare-pyodide/backend/verify-canonical-failure-repaired.mjs
```

26 focused tests pass. Guest Ruff on the changed canonical runner passes.
Broader platform/UI suites were not rerun for this companion-only increment.
No laptop Docker, cloud deployment/invocation, credentials/grants, resource or
security changes, spending or production effect. GitHub REST CI read returned
rate-limit 403 at 2026-11-03T02:49:57Z; no alternate identity or repeated request
was used. Normal CI remains independently required for the published head.

Untrusted admission stays disabled. A returned result, expiry rejection or token
revocation is not a physical-stop receipt. The next useful local increment is
canonical deadline binding and an actual SDK write-response-loss/reconciliation
case at that boundary. Deployed provider resource enforcement/accounting and
combined signed-control-to-Cloudflare canonical execution remain separate gates.
