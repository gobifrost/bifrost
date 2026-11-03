# Opaque workflow handles through the upstream pool

The real ThinRemoteProcessPool now executes the existing fixed engine/SDK
qualification through local workerd/Pyodide using a Worker-returned opaque
invocation handle. Canonical claim, real Redis context/active leases, result
consumption, lost-ACK reconnect, credential revocation and cleanup pass without
inventing a Linux PID. This is a programmatic private-adapter qualification;
the public control host still rejects the experimental profile.

## Physical boundary implemented

The pool constructor accepts an explicit runtime_profile, defaulting to the
unchanged linux-process-v1 contract. Unknown profiles raise before effects.
The experimental cloudflare-python-v1 profile accepts a bounded opaque
runtime_handle instead of a PID. It is never inferred from response fields.
Malformed/ambiguous ACKs retain the uncertain route, including its load count,
for existing canonical inventory/result reconciliation rather than relaunch.

Workflow inventory validates the canonical claim as before, adopts the opaque
handle, and refuses handle replacement or renewed identity/start/timeout on an
already held route. Non-workflow inventory and persistent services are refused
for this profile. Linux ACKs, inventory behavior and process diagnostics remain
unchanged. No database schema, placement rule, credential version, public DTO or
new job/status endpoint is introduced.

Opaque invocations appear in the existing internal heartbeat's runtime_invocations
metadata with truthful busy/pool counts, while OS process diagnostics retain
integer PIDs. Missing invocation memory/CPU values are null. The controller's
cgroup sample is not an isolate-memory or provider-capacity qualification.
The existing admin process DTO/UI does not yet render invocation details.

Physical cancellation is **not supported or advertised** for the candidate.
It raises before changing Busy to Killed or sending an invented terminate
command. The route/resource obligation remains until terminal reconciliation.
The public control host continues to reject this signed profile before admission;
an internal constructor selection is not workload authorization or a negotiated
production runtime. Untrusted admission remains disabled.

## Actual engine/SDK path

The companion's fixed physical-pool probe supplies an HTTP transport to the real
pool, retaining upstream scheduling/auth/consumer/result ownership. It prepares
only fixture manifests/configuration; it does not execute a replacement engine.
Native dispatch stamps the actual workflow_deadline, writes its normal Redis
records and fences the credential generation. The transport snapshots that
context and invokes the unchanged packaged upstream engine/SDK.

The local Worker parent returns its fresh invocation UUID alongside the completed
result. This is a parent-assigned invocation identifier tied to its actual load,
not a Cloudflare isolate ID, remote termination capability, or disposal receipt.
The native pool binds it and delivers the retained result through its actual
handle_event/on_result path. A fixture bridge exercises the existing journal's
lost-ACK sequence; the real consumer commits Success/70 once and revokes the run
credential. The opaque route is removed only after that terminal path succeeds.

Observed: one launch, one successful SDK upsert, independent document70,
cross-tenant403, credential401 after terminal consumption, parent secret absent,
credential redaction, pinned workspace code hash and null isolate metrics.
Lost-ACK replay preserves log IDs. No native fork/PID is used for the workflow.

The direct HTTP transport returns its ACK **after workflow completion**. It does
not yet provide prompt dispatch acceptance, active inventory from a restarted
provider, or production cancellation/accounting guarantees. The existing handler
gate is not bypassed for public work: this is the explicitly scoped fixed local
fixture under its real canonical claim; no business workflow is admitted to the
candidate through the public control host.

## Experiments, sources and commands

| Hypothesis/change | Exact assigned-guest command | Observation |
| --- | --- | --- |
| Core representation needs an explicit non-PID profile | `timeout 480 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/physical-handle-v1/ct128-physical-handle.py` | Before: one expected failure because the old pool has no runtime_profile option. After:67 selected cases pass, no skips/errors/failures; Ruff/Pyright pass. 167.379s supervisor wall time. |
| Real engine/SDK can use that upstream pool path | `timeout 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/physical-pool-v1/ct128-canonical-failure.py --physical-pool` | Exit0, 63.901s supervisor time. Native routing, actual workerd execution and canonical replay/cleanup pass. No cloud invocation. |

Source base eca8fdc99af47916a6549ee10888e967a0109d00 plus exact packet hashes in
`results/physical-handle/` and `results/physical-pool/`. Native API checkout is
1d33150eb376b71f8a81b427920eda1b0447f829 with previously recorded owned overlays
and these verified protocol/pool overlays. Their hashes were read back before
and after the real execution; post-run readback is retained. Current source and
the packet/readback hashes are checked by the verifier. Packaged engine remains
f49d4ba99cebd24aa497f1244833b940e640885f, runtime closure
bc4eae48a21ab788f99bf3a554ca16f676a93549ba96bcdb99f433ed46e6daf0.

Assigned CT128 remains2CPU/8GiB/no swap. Same bounded Compose, cached dependency
image and verified publisher-source Pyodide bundle/cache wrapper; source overlays
are checked before replacement. Provider container one CPU/2GiB/128PIDs/no extra
swap, isolated owned network. cf CLI beta.26 delegates local execution to
Wrangler4.156.0/workerd. Provider reports no OOM; test containers stop, volumes
and dev stack remain. Peak memory/cost/CPU are not newly measured. No laptop
Docker, cloud deployment/invocation, new persistent access credentials/grants, spending, resource
expansion, runtime-security settings or production effects.

Shared-source conflict evidence was reconciled with Eddie; the known separate
Containers worktree still has no dirty remote-control paths at1e64c1e82. No edits
or steering of that owner. This is not maintainer acceptance or ownership release.

```sh
node spikes/cloudflare-pyodide/backend/verify-physical-pool.mjs
```

Broader platform/UI suites were not run; normal review/CI remains required.
Next: durable operator-authorized runtime/eligibility binding and prompt accepted/
started/result transport, while preserving resource obligations on interruption.
Actual deployed cancellation, capacity/accounting and combined signed-control
acceptance remain separate blockers to a first-class untrusted executor.
