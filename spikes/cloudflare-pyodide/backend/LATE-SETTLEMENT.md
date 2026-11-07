# Late finite completion after canonical Timeout

The real upstream engine/SDK now runs a fixed 40-second async workflow through
the experimental opaque pool while the native original-deadline task reports
Timeout at 30 seconds. The provider remains alive. No workflow terminal envelope
is fabricated to settle the handle, and no per-job process kill is requested.

Observed on CT128:

1. A genuine run-scoped SDK read/upsert writes 70 before the cutoff. An independent
   authenticated API read confirms it. Cross-tenant config access returns 403.
2. The owned deadline task commits canonical Timeout and revokes the run token
   (401) while collection is still pending and the native busy count remains 1.
3. The actual finite Python workflow returns Success/70 after its async delay.
   Required work and the result stream/EOF settle; the same opaque handle is
   bound to the validated response. The engine wall duration is 40137 ms, not CPU.
4. The real result consumer treats that success as stale. Canonical Timeout,
   null result, completed_at and persisted log IDs remain unchanged.
5. The first settlement ACK is deliberately lost after the consumer returns.
   The pool still holds the handle. Journal replay then returns through the real
   pool callback and removes the handle/busy count without another workflow launch.

The provider daemon is independently read back as still running/no OOM before
fixture cleanup. That demonstrates invocation settlement for this finite,
awaited-work fixture without requiring isolate or provider destruction. It is
not arbitrary untrusted-workload isolation or abnormal CPU cancellation proof.
The supervisor subsequently stops owned test containers as harness cleanup;
volumes and the separate dev stack remain.

One late SDK log upload is refused 410 by the original-deadline egress gate. The
single pre-deadline log stays persisted; late stale completion/replay creates no
duplicate log or successful SDK upsert. The original SDK effect remains 70.
Do not infer general cancellation from this refused request or from EOF alone.

## Fixture and exact commands

The new fixed probe uses a fixture-owned config row with standard test-world
teardown, so teardown does not need the deliberately revoked run credential.
The workflow still reads configuration through the real SDK/API. No stronger
runtime credential, authorization bypass or adapter database write is introduced.

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/late-settlement-v1/ct128-canonical-failure.py --late-settlement
node spikes/cloudflare-pyodide/backend/verify-late-settlement.mjs
```

Same CT128 allocation 2 CPU/8 GiB/no swap; provider 1 CPU/2 GiB/128 PIDs. Existing
isolated project bifrost-test-5dc8d94e, dependency image, cf CLI/workerd and
publisher-source bundle remain pinned. The native pool/protocol overlays and
packaged engine closure are unchanged. Ruff/Pyright pass on the new fixture;
the 10 deadline-helper cases remain bound to their unchanged tested source.

[Raw receipt](../results/late-settlement/v1.json) records four successful phases,
all source/workspace/API hashes, actual result, SDK request statuses and provider
state. Supervisor 102.800 s includes stack/setup, deliberate 40-second await and
cleanup. It is not pure startup, provider CPU or cost evidence. CPU/memory metrics
remain null. No cloud invocation, deployment, spending or admission change.

## Remaining frontier

This qualifies normal finite completion after logical timeout through the existing
result/ACK path. It does not provide settlement for a permanently lost response
or noncooperative CPU work, nor production profile startup/admission. The fixed
qualification still supplies the native transport inside its test harness.
The next useful implementation step is extracting that transport into a small
reusable companion with immutable per-attempt state, then proving concurrent
invocations under one relay without cross-execution state or collateral timeout.
No new authoritative queue or silent fallback is warranted.
