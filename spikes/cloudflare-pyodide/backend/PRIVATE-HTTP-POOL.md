# Reusable private HTTP workflow adapter

`private_http_pool.py` extracts the physical adapter from the two-tenant fixture.
`PrivateHttpWorkflowPool` composes the actual upstream `ThinRemoteProcessPool`,
the companion transport receipt journal, per-attempt request collection and
bounded HTTP framing. The fixture now delegates to that class instead of
implementing its own native routing/result path.

The trusted host supplies a registered canonical owner, the upstream result
consumer, parent memory/reservation callbacks and explicit per-run manifests.
The upstream workflow consumer still claims the run and mints its scoped
identity. This component does not register a worker, authorize a user, issue a
token, schedule business work or retry uncertain effects.

## What the component enforces

- Manifest paths/digests are bounded and snapshotted. The native context must
  already match the bound workflow hash. Missing hashes, inline code, unsupported
  context flags, more than 30 seconds and stale credential routes are refused
  before native dispatch. Endpoint/session headers are validated up front using
  the same HTTP component rules; redirects/cookies/ambient proxies stay disabled.
- Context and active metadata are snapshotted before suspension. Identical
  duplicate calls await the original route; changed calls are refused.
- The companion receipt is retained **before** native dispatch can send a POST.
  Cancellation of the routing caller leaves independently owned dispatch and
  collection intact. Stream loss preserves the failed receipt and native busy
  obligation; it does not manufacture failure, settle a handle or launch again.
- Result reconciliation traverses the actual native pool callback and upstream
  consumer under real owner fencing. A consumed stale result is not a claim
  that its outcome replaced canonical state. Persistent services reuse the
  native profile's refusal before effects.
- Retirement requires all companion receipts acknowledged through that consumer,
  then settled collection tasks. It clears private plans/attempt mappings and
  rejects further dispatch. Retirement is not cancellation or CPU-stop proof.

## Observed qualification

CT128 V2 runs two real engine/SDK workflows through this class and one native
pool. Both fresh children reject a peer config read (403), read their own config
and write their own document (70/78). Independent API reads observe both effects
while the workflows wait on fixture-only release flags. The first run then
raises a real redacted ValueError. Lost ACK, ownership-generation rotation and
stale-owner rejection leave the peer pending and authorized (200), while the
failed run's credential returns 401. Duplicate dispatch now calls the extracted
adapter itself and reuses its receipt. The peer then succeeds with 78; both
credentials return 401. Native obligations and PostgreSQL accounting follow
2 → 2 → 1 → 0, and retirement/owned-container cleanup complete.

Seventeen focused native tests pass. Six new adapter tests use the actual native
pool with mocked ownership/Redis boundaries and HTTP byte fixtures; they are
transport proof, not SDK compatibility evidence. The separate real run above
uses actual ownership/claims, API authorization, engine, SDK and result consumer.
Ruff and Pyright pass on all changed Python files. Initial V1 stopped at an
unused-import lint error before live workflow invocation; its source/diagnostic
are preserved. No retries or timeouts were changed to make tests pass.

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/private-pool-v2/ct128-canonical-failure.py --private-pool --revision 2
node spikes/cloudflare-pyodide/backend/verify-private-http-pool.mjs
```

[Raw receipt](../results/private-pool/v2.json) binds exact module/workspace/API
hashes and dependency image. Supervisor 72.523 seconds includes setup, explicit
credential-free prewarm, workflow barriers and cleanup. It is not startup, CPU
or billing evidence. Provider CPU/memory metrics remain null. Same CT128 2 CPU/
8 GiB/no swap and provider 1 CPU/2 GiB/128 PIDs; no resource expansion, cloud
invocation/deployment, access grant or production change occurred.

## Remaining integration gates

This is a reusable **private routing component**, not an admitted production
backend. The base workflow consumer initializes `content_hash` to None. This
fixed fixture explicitly supplies the known published hash before calling the
adapter; the adapter never invents or fills a missing pin. A normal host still
needs authoritative API/workspace manifest preparation tied to the attempt.
The Worker endpoint still has the original fixed-fixture admission policy.
Binding a plan here does not make other workflow shapes admitted there.

Canonical worker registration/heartbeats and control reconnect remain supplied
by the qualification host. Lease rotation uses real PostgreSQL ownership, but
the signed control socket is not exercised by this combined run. Automatic
startup must not invoke the Linux configure/package/process commands for this
backend. No such startup or public profile selection is enabled here.

Cold concurrent initialization, deployed combined lifecycle, arbitrary untrusted
admission and abnormal CPU cancellation/resource settlement remain unaccepted.
Required work/streams settle normally without demanding isolate destruction;
Cloudflare owns isolate lifetime. See [lifecycle assessment](LIFECYCLE.md).
