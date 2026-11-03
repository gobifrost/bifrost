# Authenticated physical runtime identity

The upstream control hello now supports an optional signed runtime_profile.
Omitted profiles retain the exact legacy Linux bytes/signature and wire shape.
The host accepts explicit linux-process-v1, refuses other profiles before
database admission/owner acquisition, and refuses unsigned top-level profile
declarations. A profile cannot silently request Linux supervision for an isolate.

This is a new isolated contribution, not an existing upstream API or accepted
Cloudflare backend. Linux launchers still omit the field; PID ACKs, inventory,
supervision, credentials, handler negotiation and canonical authority are
unchanged. No Pyodide profile is accepted and no new runtime is advertised.
Opaque handles still require a subsequent negotiated implementation.

## Source and compatibility

`Hello` validates a bounded ASCII profile and includes a non-null declaration in
its canonical signed bytes. `SignedHello` retains the field during decoding and
verification. Legacy serialization omits it, including for job/static/external
identities. The existing HMAC domain/key remains unchanged; there is no new key,
credential, grant or control bypass. An old API will reject a new explicit
profile's proof instead of interpreting it under the old contract. Existing
Linux launchers do not emit an explicit profile and remain compatible.

`RemoteControlHost.connect()` rejects unsupported or unsigned profiles before
canonical worker admission, static-worker registration, or owner acquisition.
Only the existing Linux physical contract is currently supported. Runtime
identity is orthogonal to credential protocol and execution-handler versions;
a signature does not grant workload eligibility.

Shared scope: protocol.py and host.py only, plus a focused unit test. The
separate Containers worktree at 1e64c1e82 was clean for these paths before edits;
Eddie's reconciled passive evidence reported no overlapping plan. That is
limited conflict evidence, not exclusive ownership release or review approval.
No Containers branch/resources, placement, recovery or production platform changed.

## Experiment log

| Revision/change | Command in assigned CT128 | Observed result |
| --- | --- | --- |
| v1: fail-first plus source overlay | `timeout 480 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/runtime-profile-v1/ct128-runtime-profile.py` | Old source ignores an injected profile: expected AuthenticationError is not raised (one failure). Patched source passes 72 protocol/host/credential tests, no failures/errors/skips. Live-control invocation then exits127 because its --volume argument follows the service name. Test stack stopped. |
| v2: fix Compose argument ordering; retain identical-source native tests | Same helper under runtime-profile-v2 with `--resume` | Explicit Linux accepted; unsupported signed/unsigned and bad signature close1008. A post-nonce invariant fails. Original fixture advertised normal execution handlers despite asserting empty handlers. The exact failed sub-invariant was not captured by that revision. Stack stopped; no acceptance claimed. |
| v3: make auth-only fixture advertise handlers=[] and add invariant diagnostics | Same helper under runtime-profile-v3 with `--resume --revision 3` | All live controls pass, state remains starting, claims0, no inventory/child invocation. Ruff/Pyright pass. Retained72 test evidence is bound to identical protocol/host/test hashes; no unit rerun to obtain green. Stack stopped. |

V1's original runner and v2's original runner/control fixture are retained as
source snapshots, along with receipts and fail-first/passing JUnit XML. V3
addresses the fixture's unintended handler advertisement; it does not claim
which uninstrumented v2 assertion was the immediate failure. Diagnostics now
identify handler/state/owner violations without printing signed hello material.
No timeout increase, retry, skip or weakened invariant was used.

Actual live v3 results: signed Linux accepted at generation1; reconnect reaches
generation2; unsupported signed profile, unsigned profile, invalid signature,
nonce replay, stale generation and superseded socket close1008. Empty handlers
and starting state preserved, zero claims, no Linux templates started, fixture
worker removed. This is actual WebSocket/API/PostgreSQL ownership evidence,
not only protocol imports or test doubles.

Source base f7a692552f71f5d9f89349fc07ba3933518bad60 plus packet hashes in
`results/runtime-profile/`. Guest API checkout remains 1d33150eb376b71f8a81b427920eda1b0447f829
with verified pre-existing owned overlays and these two shared source overlays.
The runner verifies baseline bytes before applying the first overlay; resumed
phases require exact source/test hashes and retained test receipt SHA256.

Existing CT128 allocation stays2CPU/8GiB/no swap. Hash-guarded bounded Compose,
image sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b,
owned project/scratch, 8GiB free-disk guard and explicit supervisor wall bounds
are preserved. V1 elapsed184.701s; v3 elapsed90.907s. Quality-only sidecar is
network-disabled, one CPU/256MiB/64PIDs. Peak memory is not measured. All owned
test containers stop, volumes/dev stack remain. No laptop Docker, cloud
deployment/invocation, spending, runtime-security setting or production effect.

```sh
node spikes/cloudflare-pyodide/backend/verify-runtime-profile.mjs
node spikes/cloudflare-pyodide/backend/verify-control-auth.mjs --commit 0c3241789a15e418d02ca8eb6fbd8fac8016edef
```

The native commands/selected tests and quality checks are in the receipts.
Broader platform/UI suites were not run. Normal CI/review remains required;
the disposition remains an author draft. Next implement negotiated opaque
workflow handles and inventory semantics without accepting unsupported services,
faking PIDs or turning profile authentication into pre-claim authorization.
Untrusted admission and deployed resource/cancellation qualification remain held.
