# Local fixture admission before runtime effects

`authorized-local-entry.mjs` invokes `admitFixture` before creating a Dynamic
Worker, constructing the API capability or adding the diagnostic-map entry.
The accepted surface remains the fixed qualification workflow and its one pinned
workspace helper. It checks canonical-mode worker UUID, matching execution UUIDs,
both SHA-256 module digests and entry content hash, a 1–30 second workflow limit,
bounded credential shape and local concurrency capacity. Inline scripts,
services, admin context, solution remapping and artifact workspaces are rejected.

This is a fixture compatibility check, not authorization, general workload
classification or a sandbox. A caller-provided hash does not establish trusted
provenance; the test driver supplies it from its pinned fixture. The endpoint is
local-test-only and must not be deployed as a production dispatcher. The upstream
API continues to validate run identity, role, tenant and terminal credential
revocation. No untrusted caller can become trusted by setting a request flag.

The existing canonical driver claims before calling this endpoint. This change
therefore rejects before provider effects, **not before canonical claim**. A
production provider still needs authority-side eligibility before scheduling,
with immutable artifact/dependency provenance and verified provider capabilities.
No scheduler or shared remote-pool code is changed here.

## Evidence

```sh
node --test backend/fixture-admission.test.mjs
```

Eighteen tests cover the accepted fixed shape and rejected contexts, identities,
digests, durations, credentials and capacity. Rejections never invoke the runtime
callback. The callback encloses both Dynamic Worker and API-capability creation
in the actual entrypoint. Thirty-one focused Node tests pass across admission,
cold reproduction, module integrity and bounded transport.

An actual local Wrangler request with an otherwise valid fixed shape, synthetic
noncredential and 1800-second duration returned HTTP 422 with reason `duration`.
It was not retried as another execution mode. The historical warm receipt remains
verifiable against its exact source:

```sh
node backend/canonical-isolation.mjs --verify canonical-isolation-warm --commit 4c2f09017f441811f5761abcd458652657909a60
```

The updated entrypoint also passed the real two-tenant canonical qualification:
`results/canonical-isolation-admission.json` pins the gate, entrypoint, driver,
journal and runtime source. It records one credential-free warmup and two fresh
local Python runs, values 70/78, peer 403s, one launch per attempt, terminal
credential 401s, owner/worker heartbeats, lost-ACK reconciliation, state canaries
and zero remaining rendezvous keys. Verify without invocation:

```sh
node backend/canonical-isolation.mjs --verify canonical-isolation-admission
```

After this run the exact authorized-local supervisor was stopped and its test
network attachment removed. No cloud invocation or credential grant occurred.

No AST/import scan is claimed to prove arbitrary Python safety. The 30-second
field bound is a compatibility condition, not an uncatchable physical deadline.
CPU physical termination, cold concurrency and deployed run-scoped acceptance
remain unresolved. Unsupported workloads must not silently fall back to Linux;
upstream must select another eligible provider through its canonical contract.
