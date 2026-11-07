# Opaque inventory is not a Linux process-exit receipt

Source inspection during the next provider-interruption qualification found two
Linux-only assumptions still reachable through the experimental opaque pool:
an omitted route generated ProcessCrashError and released the handle; a rejected
workflow entry sent a Linux terminate command directly through the transport.
Neither is a supported settlement/termination contract for a Pyodide invocation.

The candidate now fences the owner, then refuses reconciliation if an opaque
held route is omitted, before result callbacks, handle release or snapshot
application. A rejected live opaque entry also refuses reconciliation without
sending terminate. Existing
canonical authorization/recovery still owns workflow status and revocation;
inventory cannot invent a result or assume that computation/resources stopped.

Normal completion remains different. A launcher-retained terminal entry must
remain replayable after the canonical commit loses its ACK. Such an entry does
not trigger a Linux termination command or require per-success isolate death.
It passes through reconciliation so the existing terminal event/ACK path can
consume the stale replay and release its references. Known held routes remain
held until that terminal path succeeds; the inventory entry alone releases none.
Linux inventory omission, crash reporting and rejected-child termination retain
their existing behavior. The public host still rejects the experimental profile.

## Regression evidence

The first fixed guest run reproduced two defects: opaque omission and rejected
live inventory failed their new tests on the old pool, while the Linux omission
control passed. After the initial guard, 42 selected native cases passed with
Ruff/Pyright. That intermediate guard also refused completed terminal inventory.
It was not published as the final implementation: reconnect trace inspection
identified the terminal-replay distinction and added a fourth regression.

V2 tests that fourth case against the intermediate source first, then qualifies
43 cases. A fifth regression checks owner-fencing priority against that source;
V3 moves the omission guard after the existing owner fence. The selected consumers
include native dispatch/ACK, cancellation refusal, opaque handles and Linux
inventory. Receipts preserve exact source hashes, failing
JUnit cases, corrected cases and commands. No test is skipped or retried unchanged.

| Revision | Exposing regression | After correction | Supervisor wall |
| --- | --- | --- | --- |
| V1 | 2 failures/3 cases: omission and invented terminate | 42 pass | 158.203 s |
| V2 | 1 failure/4 cases: completed terminal replay | 43 pass | 155.660 s |
| V3 | 1 failure/5 cases: owner-fence error priority | 44 pass | 154.654 s |

All final cases pass with no skips/errors; Ruff and Pyright pass for the shared
pool and regression module. Supervisor time includes setup/test/reset/cleanup,
not workflow startup or execution latency. Raw receipts, XML and immutable
intermediate source snapshots are in [results](../results/inventory-uncertainty/).
All owned test containers stop; volumes and the separate dev stack are preserved.

```sh
timeout 480 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/inventory-uncertainty-v3/ct128-inventory-uncertainty.py --revision 3
node spikes/cloudflare-pyodide/backend/verify-inventory-uncertainty.mjs
```

Assigned CT128 remains 2 CPU/8 GiB/no swap. Uses the existing bounded Compose
project bifrost-test-5dc8d94e and cached dependency image
sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b.
Native base 1d33150eb376b71f8a81b427920eda1b0447f829 retains the existing owned
overlays; the supervisor refuses changed pool/protocol/Compose/image identities
before copying the new test or source. Each phase shares a 400-second internal
wall bound; source/test/runner are an exclusive three-file packet.

Before editing, the Containers checkout still read 1e64c1e82daeed40e29a30c7af628a6e26b8a6d6
with no dirty remote_pool.py or physical-handle test path. This change is confined
to this contribution branch and assigned guest. No other owner was steered.

Historical physical-pool and stream-loss verifiers now read the API sources from
their immutable tested commits. Old receipts are not rewritten to imply they
tested this source. This increment changes native inventory behavior; it makes
no new claim about the previously deployed Worker or current cloud acceptance.

## Remaining frontier

Refusing an unsupported reconciliation keeps uncertainty visible; it is not a
new automatic physical settlement mechanism. The
[provider-interruption follow-on](STREAM-PROVIDER-INTERRUPTION.md) now qualifies
explicit canonical recovery before any terminal event reaches the launcher,
while keeping the native invocation obligation separate.
Negotiated/authorized runtime admission, deployed resource enforcement/accounting
and untrusted isolation remain open. No cloud invocation, resource expansion,
production setting, credential or admission change is introduced.
