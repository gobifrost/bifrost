# Retained result reconciliation after the original cutoff

Actual guest engine/SDK completion before the deadline can be reconciled after
it without rerunning the workflow or granting a new execution window. The
existing upstream consumer accepts the retained result under the replacement
owner; a stale owner is refused, and lost terminal ACK replay does not duplicate
logs or effects. Access and refresh credentials are rejected after canonical
terminal consumption. This is a local transport qualification, not deployed
Cloudflare cancellation or combined signed-control/isolate admission.

## Implementation and empirical result

The fixed LocalPool now snapshots its dispatch context before any seeding/API
awaits. Transport serialization no longer reads that mutable caller dictionary
later. Its original ExecutionInfo, context deadline and receipt remain attached
to the same route. No new queue, token format or platform terminal semantics.

The existing response-loss fixture gains `--deadline-retained-response`:

1. Real engine/SDK upserts and independently reads value 70 before the cutoff.
2. Native proxy waits beyond that cutoff, retains the actual result using
   upstream Launcher.send_terminal, then drops the HTTP response.
3. The collector observes RemoteProtocolError and leaves canonical state Running.
4. A real database-backed owner generation rotation occurs. A duplicate dispatch
   returns the original uncertain receipt; its forbidden relaunch factory is
   never called. The original started_at/timeout still produce the same cutoff.
5. Stale-owner consumption is refused. The current owner consumes the retained
   result through the actual WorkflowExecutionConsumer; canonical Success/70 is
   persisted. No timeout terminal had previously won this race.
6. The first ACK send fails. Launcher retains its event; a duplicate consumption
   and subsequent upstream ACK receiver drain the event without another log row.
   The cutoff remains unchanged and launch count remains one.
7. Actual access/refresh calls return 401 after terminal consumption. Fixture
   run/worker/claims cleanup and stopped test containers are independently checked.

The control socket remains explicitly simulated, while ownership, consumer,
PostgreSQL/Redis, upstream retention/ACK code, engine, SDK and HTTP effects are
real. Linux templates are never started by the retention fixture. A received
result is not an assertion about abnormal CPU termination or peak memory.

The inherited credential-boundary negative control also writes/reads a separate
synthetic value 71 after expiry using the **trusted native observer**, bypassing
the child gate. That is not a demonstrated child escape or duplicate SDK upsert.
See [the original cutoff qualification](CANONICAL-DEADLINE.md). Its response-loss
receipt is preserved independently, against its exact historical source.

## Exact run

```sh
timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/deadline-retained-v1/ct128-canonical-failure.py --deadline-retained
```

Exit 0, 104.219 seconds supervisor wall time, original cutoff
2026-11-03T03:07:34.464867+00:00. Receipt: `results/deadline-retained/v1.json`.
Source base 37f33d13a8b451fad4897e041c9b1375380f2dfb plus exact packet hashes.
API source 1d33150eb376b71f8a81b427920eda1b0447f829 with previously qualified owned
overlays; packaged engine f49d4ba99cebd24aa497f1244833b940e640885f, closure
bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0 unchanged.
The new source verifier binds changed fixture/runner and capability files to the
receipt, matching execution/worker identities and workspace hash provenance.

Existing assigned CT128 identity/capacity rechecked: 2 CPU / 8 GiB / no swap,
about 6.5 GiB available memory and 15 GiB free disk. Provider one CPU / 2 GiB /
128 PIDs / no additional swap; existing isolated test network, bounded Compose,
cached image and verified runtime bundle/cache wrapper unchanged. cf CLI
beta.26 delegates local execution to Wrangler 4.156.0/workerd. No OOM; owned
test containers stopped, volumes/dev stack preserved. No new dependency or ABI
change, laptop Docker, cloud invocation/deployment, spending, credential/grant,
security setting, resource expansion or production effect.

```sh
node spikes/cloudflare-pyodide/backend/verify-deadline-retained.mjs
node spikes/cloudflare-pyodide/backend/verify-deadline-response-loss.mjs
```

Guest Ruff passes for changed Python fixtures. Existing 30 focused admission /
egress / refresh tests pass; broader platform/UI suites were not rerun. REST CI
read at 03:06:42 UTC was rate-limited (403); no alternate identity was used.
Normal CI/review remains required for the published head.

## Next actual integration gap

The result path can preserve authority across transport loss, but production
remote dispatch still requires positive Linux PID ACK/inventory semantics.
Neither this fixture nor the companion journal negotiates a provider-neutral
handle or approves untrusted workloads. The next code increment should address
that physical boundary after checking shared-source conflicts with the separate
Containers owner. Abnormal deadline effects and deployed provider resource
enforcement/accounting remain separate acceptance gates. Admission stays disabled.

Conflict reconciliation at 03:09 UTC: the known Containers worktree at
1e64c1e82daeed40e29a30c7af628a6e26b8a6d6 has no dirty targeted remote-control or
process_pool files. Eddie's passive reply 19728485-bbcd-49fa-905d-f9824821df98
reports no overlapping plan through that owner's reported messages12644, with
explicit limits: no certification of unreported plans or exclusive ownership
release. No owner was steered and no shared control source was edited in this
increment. This is conflict evidence, not new authority or maintainer acceptance.
