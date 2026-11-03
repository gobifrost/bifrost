# Two real attempts through one native pool

The fixed guest qualification now constructs one `ThinRemoteProcessPool`, one
canonical worker/launcher and one workflow consumer for two tenant runs.
Each run has its own trusted slot containing credentials, pinned two-module
manifest, invocation, result, native route and original execution metadata.
There is no single token/result field shared by the two collectors.

Both actual engine/SDK workflows check builtins, module globals, environment
and a common temporary path before effects, then write a canary in each fresh
child. They read their own configuration, reject a peer-tenant read with 403,
and upsert their own document. An independent authenticated API reader observes
70 and 78 while both workflows wait on separate fixture-only release flags.
No globals are manually cleared. This is a local fixed-code contamination
control, not acceptance of arbitrary untrusted Python.

The fixture releases the failing workflow first. Its actual ValueError passes
through the real engine and canonical consumer; runtime credentials are redacted
from logs/errors. ACK loss is injected after that canonical commit, keeping both
native obligations held. Owner reconnect rejects a stale-owner replay. Current-
owner replay preserves status/result/completed_at/log IDs and settles just the
failed route. The peer remains pending, its scoped credential still works (200),
and the failed run's credential is revoked (401). Duplicate peer dispatch reuses
the original receipt instead of invoking Python again.

The second workflow is then released and commits Success/78. Both credentials
return 401 after terminal reconciliation. The companion transport journal retires
only after ACKs from the actual upstream consumer; the request mux then refuses unsettled collections, drops settled
slots and rejects subsequent binding/requests. Pending/failed collection guards
have focused native tests. Retirement is not a physical-cancellation operation.

Lease generation changes use the real PostgreSQL ownership functions. The
control connection/reconnect is supplied by this trusted fixture, rather than
the full signed WebSocket control host. This is not combined control-session
authentication or public profile startup acceptance. `AttemptJournal` remains
a companion transport receipt cache, not a new upstream API or business queue.

## Evidence and limits

The initial static check found one callback-name collision before any workflow
invocation. Its exact source and diagnostic are retained. V2 passes the real
two-run contract. V3 additionally verifies worker liveness/reservation accounting
as native busy obligations move 2 → 2 (lost ACK) → 1 → 0. V4 requalifies the
final pinned packet after clarifying that the journal is a companion transport
component rather than an upstream API. This accounts for held
work rather than treating a canonical failure as physical settlement.

The run uses the existing credential-free prewarm once, before minting run
credentials. This deliberately preserves the separately documented cold
concurrent Pyodide initialization failure; it is warm provider concurrency,
with fresh per-run children. Existing fixture admission still allows only the
same two-module shape and original 30-second cutoff. No admission, limit or
security setting was expanded. Metrics not measured by the provider remain
null; supervisor wall time includes setup/prewarm/barriers/cleanup and is not
startup, CPU, billing or a Containers comparison.

Same assigned CT128, 2 CPU/8 GiB/no swap. Provider 1 CPU/2 GiB/128 PIDs. Same
isolated test project, image, cf CLI/workerd and integrity-pinned runtime bundle.
No cloud invocation/deployment, new credentials/grants or shared API source edit.
The provider stays running before owned-container cleanup. Volumes and the
separate development stack are preserved.

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/shared-pool-v4/ct128-canonical-failure.py --shared-pool --revision 4
node spikes/cloudflare-pyodide/backend/verify-shared-native-pool.mjs
```

The raw [V2](../results/shared-pool/v2.json), [V3](../results/shared-pool/v3.json)
and [V4](../results/shared-pool/v4.json)
receipts contain source/workspace/image hashes, actual results and SDK statuses.
Eleven focused request/HTTP tests and targeted Ruff/Pyright qualify the helper
and fixture changes. This remains a private, explicitly bound test adapter:
public runtime admission/startup, a reusable deployment adapter and combined
deployed canonical lifecycle acceptance are still outstanding. Deployed abnormal
CPU cancellation/resource settlement remains unproven. No per-success isolate
destruction is required or inferred.

The later [private adapter extraction](PRIVATE-HTTP-POOL.md) now supplies reusable
routing/receipt/reconciliation code to this fixture. Public startup and normal
authoritative manifest preparation remain outstanding; the historical receipts
above stay bound to their original fixture source.
