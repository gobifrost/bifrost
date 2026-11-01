# Concurrent canonical local qualification

Result: warm local two-tenant execution passes. Cold concurrent startup remains
unsupported in this tested workerd version. This is not cloud acceptance or
physical termination evidence.

The [minimal cold reproducer](COLD-CONCURRENCY.md) now reproduces the same
native failure without BiFrost, packages or credentials.

`results/canonical-isolation-warm.json` records two overlapping provider request
intervals, separate fresh Dynamic Workers, separate canonical attempts and tenant
identities. Both execute the upstream engine and SDK against the isolated API.
They import pinned workspace helpers, read their own configuration, write and
independently read table values 70 and 78, and receive 403 on the other fixture
tenant's actual config. Builtins, environment, stdlib-module and temporary-file
canaries are absent before effects. Each attempt launches once despite duplicate
delivery, reconnects after a lost terminal acknowledgement, retains canonical
logs, and rejects its credential after terminal consumption. Fixture worker FK
cascade and all eight test-only rendezvous keys are verified cleaned.

The driver now uses upstream static registration, readiness and heartbeats,
alongside remote-owner renewal. Its 1 GiB capacity is a synthetic test scheduling
input, not measured Worker capacity or permission to route production work.
The Redis rendezvous is bounded test synchronization, not a business-job queue.

## Experiments and provenance

| Hypothesis / change | Command | Observed boundary / next experiment |
|---|---|---|
| Cold simultaneous tenants can share the supervisor | `node backend/canonical-isolation.mjs --run` | Failed; local workerd reports `kj/table.c++:49 inserted row already exists in table`. Initial receipt and exact driver/coordinator snapshots are preserved. |
| Native registration and heartbeat remove that failure | `node backend/canonical-isolation.mjs --run canonical-isolation-liveness` | Failed with the same native startup error. Heartbeats repair a fixture lifecycle omission; they do not fix the runtime race. Peer teardown also encountered config cleanup 401/FK residue. Postmortem worker-lost state after owner release does not establish the original auth failure's cause. Own test stack was reset after retaining evidence. |
| Credential-free initialization permits concurrent fresh runs | `node backend/canonical-isolation.mjs --run canonical-isolation-warm --prewarm` | Passed with one credential-free local initialization plus two real local tenant runs. No retries inside the coordinator. Next: isolate the cold runtime race in a minimal provider reproduction. |

The native C++ runtime error is neither a pure Python dependency nor a missing
PyEmscripten wheel. The same pinned dependency closure executes in the warm case;
no package wheel has been identified whose compilation would fix this failure.
Rebuilding the local host runtime would not qualify Cloudflare's deployed runtime.

Run from this spike directory, after starting the existing isolated test stack,
stopping its ordinary worker/scheduler and attaching the local supervisor to that
stack's network. Use the Wrangler authorized-local configuration on port 8799.
This fixture is explicitly local-only; do not deploy its test API bridge.
Receipt names cannot be overwritten. Verification is read-only:

```sh
node backend/canonical-isolation.mjs --verify canonical-isolation-warm --commit 4c2f09017f441811f5761abcd458652657909a60
node backend/verify-canonical-local.mjs --commit a00ae73e98a65dfd1f1001bda595754ce51f2580
```

The second command verifies the earlier single-run receipt against its immutable
source, rather than pretending the revised driver is the same source.

After this qualification the exact authorized-local Wrangler supervisor was
stopped, port 8799 returned ECONNREFUSED, and its test-network attachment was
removed (bridge only). No cloud invocation, reopening or credential grant occurred.
The isolated test stack remains available; other owners' stacks are untouched.

## Remaining acceptance gates

Fresh-state canaries prove these fixed trusted workflows only. They do not make
arbitrary user code safe. CPU physical termination remains a material blocker;
logical cancellation and provider-reported errors cannot substitute for it.
Run-scoped engine/SDK execution is still local, not deployed end-to-end. Cold
concurrency, provider capacity measurement, authoritative interruption recovery,
explicit workload admission and ordinary CI/security review remain required.
