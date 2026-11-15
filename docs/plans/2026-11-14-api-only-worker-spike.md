# API-only worker spike for #1400

Requested by Jack while preparing Cloudflare worker capacity: preserve local
workflow performance, remove platform-service credentials from remote workers,
and qualify the same worker image locally and on Cloudflare. This is an
experiment, not approval of a production architecture or a merge candidate.

## Boundaries to preserve

The API-side remote control host already owns PoolWorker, dispatch, admission,
lease fencing and workflow completion. Its database imports are appropriate;
they are not evidence of physical-worker database access. Workflow children
already scrub platform credentials and use authorized SDK HTTP calls. Their
execution protocol should remain unchanged unless measurements expose a reason
to change it.

The physical launcher still signs hello with BIFROST_SECRET_KEY. Its eager agent
template opens PostgreSQL to warm the model catalog. Agent children initialize
database pools and run AgentRunConsumer, which uses database sessions, model-key
decryption, Redis cancellation and result publication. Platform-job children
run platform.runner, which reads/decrypts job payloads, runs backend handlers,
and writes progress/checkpoints/results directly. These are the remaining
boundaries, not missing container tooling.

## Proposed execution contract

Extend the existing worker-control and runtime-credential mechanisms. Keep
PlatformJob and WorkDispatch authoritative. Do not add a second job system,
generic SQL/ORM proxy, or separate cloud-specific execution implementation.

1. Give a launcher an installation-, incarnation- and capability-scoped worker
   credential. It must not possess the installation signing key. Reconnect,
   revocation, expiry and rolling-upgrade compatibility need explicit tests.
2. Prepare immutable execution inputs on the API, bound to the existing claim,
   lease token and control-session generation. Fetch large source/artifact
   bundles through authorized, bounded transfers rather than individual files.
3. Keep expensive computation in the worker. For builds, use the existing
   SolutionAppBuilder.compile_dist boundary; upload and atomic activation stay
   under the existing platform job's authority. Cancellation must terminate
   compiler subprocesses and stale attempts must not publish deployments.
4. For agents, separate the model loop from conversation/step persistence,
   model credential resolution, tool authorization and cancellation. Bootstrap
   a run once; batch state/events where safe. Check current authorization at
   each effect. Do not replace the existing agent with a one-shot AI call or
   move all agent computation into the API to make worker memory look smaller.
5. Inventory other registered job types by required capabilities. A database
   maintenance job and a compiler need different execution inputs even when
   they share an image. Report unsupported types explicitly until converted.

Placement is separate: reuse registered job types and existing build-class
toggles, plus Normal/Fast Path semantics. Proposed choices are existing workers,
Cloudflare, or Cloudflare when busy. No operator-selected memory threshold is
needed. Pure Cloudflare capacity must work with zero baseline workers.

### Why workflows should not pay another round trip

Keep workflow preparation, its existing scoped runtime credentials, source
loading, SDK HTTP client, result transport and completion path intact. A scoped
launcher credential changes admission/reconnect, not each workflow SDK call.
Prepare new agent/build input snapshots within their existing launch exchange;
do not add a new universal bootstrap call to every workflow. Measure indirect
effects from API contention and launcher startup independently.

### Agent service boundary

Both autonomous runs (execution/autonomous_agent_executor.py) and conversational
runs (agent_executor.py) need the same service boundary. The model loop takes a
prepared agent/history/tool/model-capability snapshot and an effect interface,
instead of an AsyncSession factory and Redis client. The effect interface
requests authorized tool/knowledge operations, model inference without handing
the worker installation/provider secrets, and ordered event/usage persistence.
It must preserve live authorization checks; a startup snapshot is not perpetual
permission. Existing control-channel cancellation replaces the child's direct
Redis cancel watcher. Summaries use this boundary as well. This is the largest
refactor and cannot be proven by changing the agent template's catalog warm-up.

### Build service boundary

The API-side job retains the existing payload, lease, source resolution,
checkpoint and activation logic. The worker executes only the canonical
compiler with the exact SDK artifact and source bundle, returning artifacts and
build provenance. Transport work is still owned by the original PlatformJob;
it must not enqueue a second user-visible deploy job or a hidden workflow.
The compiler probe below uses workflow dispatch only as an existing measured
execution harness. It is not the proposed production placement mechanism.

### Delivery slices after the experiment

1. Scope launcher authentication and remove privileged eager startup dependencies.
2. Separate compiler inputs/results from backend storage and activation; extend
   the existing platform-job runner/control transport with fenced compute stages.
3. Extract the agent service boundary for autonomous, conversational and summary
   consumers; verify ordinary tools, CodeMode, approvals, cancellation and usage.
4. Reconcile the existing Cloudflare provider stack with these capabilities and
   record live acceptance against the exact candidate image.
5. Add stored onboarding and placement settings, reporting real capability
   support rather than advertising every job merely because it shares an image.

The contract/conformance foundation can proceed independently where its scope
allows. Do not hold its existing consumers hostage to the agent extraction.

## Experiments and evidence

Baseline source: a4fe1ab3c (current origin/main at spike creation). Record image
identity, machine resources, source diff and workload settings with every run.

- Existing performance tests: first/warm echo and SDK workflows (11 samples
  each), SDK config throughput (160 reads), deterministic agents and summaries.
- Compiler feasibility: four real canonical compiler invocations in the
  existing API-scoped workflow child, asserting absence of platform secrets
  and identical artifacts. This proves a compute seam only, not app-deploy
  placement, activation, failure recovery or a complete API-only launcher.
- Candidate: run the identical workflow workload after each actual boundary
  change; separately test concurrent workflows while agents/builds are active.
  Preserve raw samples; distinguish submission-to-start, body execution,
  completion and caller latency. Small samples do not establish p99 latency.
  Jack's acceptance guidance: an 80 ms to 300 ms change would be concerning;
  80 ms to 150 ms is less severe but still seems unnecessary. Investigate any
  repeatable local workflow overhead, using absolute and tail latency rather
  than a blanket percentage allowance. These examples are guidance, not a
  universal 150 ms target or permission to spend a 70 ms latency budget.
- Cloudflare: use a disposable provider and pinned image, prove two simultaneous
  admitted workers, run supported job types with physical identity receipts,
  and test interruption, uncertain start, restart and physical cleanup. No
  deployment with DB tunnels qualifies as the API-only target.
- Cost: record worker lifetime, CPU, provisioned memory, disk, network and
  Worker/DO activity. Separate estimated marginal cost from account billing
  receipts and shared plan minimums. Include warm-idle cost.

The original 1400 stack still needs its own contract and review gates. Retain
contributor ownership and draft holds. Measurements here do not approve those
PRs or prove all registered jobs can run on Cloudflare.
