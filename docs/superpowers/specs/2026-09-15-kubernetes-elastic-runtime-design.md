# Kubernetes Elastic Runtime Design

**Status:** Broad design; isolated App-build delivery in progress
**Issue:** [#769](https://github.com/gobifrost/bifrost/issues/769)  
**Date:** 2026-09-15

## Current delivery priority (2026-09-16)

The immediate user requirement is a warm scheduler for lighter work plus
on-demand, resource-isolated build pods, initially around 1 GiB. The earlier
worker-scaling experiment and 52% worker-memory reduction are useful foundations,
but do not themselves satisfy that requirement.

The next delivery implements the controller-assigned Kubernetes Job option for
existing build-class PlatformJobs. PostgreSQL placement and attempt ownership
remain authoritative. An independent scheduler reconciliation loop manages
remote attempts without consuming the two ordinary local execution slots;
the build pod supervises the existing handler and reports its own memory.
Application deploy and SDK rebuild are the initial job types. Existing local
execution remains the default. See the [bounded implementation plan](../plans/2026-09-15-kubernetes-build-jobs.md).

The rest of this document remains the broader design, not a statement of
implemented functionality. Bifrost Settings profiles, general lane routing,
versioned live policy transitions, remote workflow runners, and automatic worker
scale-in are not prerequisites for this first isolated-build delivery and must
not be advertised as completed by it. Issue #769 remains open for that broader
work. Worker-memory measurements and earlier local-scaling evidence are in
[the results report](../../runbooks/worker-memory-reduction-results.md).

## Decision

Bifrost will spike an optional **Elastic Runtime** for Kubernetes. The design
keeps RabbitMQ on the latency-sensitive dispatch path, introduces a small and
stable set of execution lanes, and uses KEDA to vary the number of executor
pods assigned to each lane. PostgreSQL remains authoritative for durable work
and runtime policy; Redis is a low-latency policy-change notification channel.

The existing scheduler image gains a Kubernetes-aware control capability. The
spike does not introduce a new Bifrost orchestrator image. A Kubernetes
installation always retains at least one lightweight trigger/control scheduler,
while workflow workers and isolated execution capacity may scale to zero.

The first experiment must prove the scaling mechanics and measure their cost
before Bifrost commits to the larger execution-model migration.

## Motivation and evidence

Bifrost currently combines work discovery, queue consumption, execution
supervision, and warm execution state in a small number of long-lived process
roles:

- The API publishes workflow and agent work to RabbitMQ.
- A worker consumes several queues and uses a local process manager to run work.
- The scheduler elects a trigger leader, polls PostgreSQL for PlatformJobs, and
  runs claimed PlatformJobs as child processes inside the scheduler cgroup.
- The worker starts a template process which preloads the execution engine and
  forks a fresh child for each execution. This protects isolation and improves
  warm execution latency, but makes the idle worker expensive.

Production observations on 2026-09-15 establish the problem:

| Role | Replicas | Observed working set per pod | Configured request / limit |
| --- | ---: | ---: | ---: |
| API | 2 | 596-609 MiB | 256 MiB / 1 GiB |
| Scheduler | 3 | 174-237 MiB | 256 MiB / 1 GiB |
| Worker | 6 | 475-612 MiB | 512 MiB / 2 GiB |

The six workers therefore held roughly 3.1 GiB at the sampled moment. One
sampled worker reported `memory.current` of 573,431,808 bytes (547 MiB) and a
historical `memory.peak` of 962,879,488 bytes (918 MiB). Its parent process
reported 295,853 KiB PSS (289 MiB), the prewarmed execution template reported
125,211 KiB PSS (122 MiB), and the multiprocessing resource tracker reported
11,730 KiB PSS (11 MiB). These process numbers are attribution clues, not
additive accounting. `memory.current`, `memory.peak`, Kubernetes working set,
and process PSS are different measurements and must not be compared as though
they were interchangeable.

One sampled scheduler reported `memory.current` of 185,569,280 bytes and a
historical `memory.peak` of 481,406,976 bytes. Recent SDK-update PlatformJobs also showed
that application builds can drive a scheduler close to its 1 GiB limit.

### Initial worker-memory attribution

The sampled worker's cgroup memory accounting was:

| Category | Bytes | Approximate MiB |
| --- | ---: | ---: |
| Anonymous application memory | 438,595,584 | 418 |
| File-backed cache | 73,097,216 | 70 |
| Kernel memory | 61,173,760 | 58 |
| Of which slab | 59,663,560 | 57 |

A clean-process import probe in the API test image produced this resident-memory
growth:

| Stage | Process RSS |
| --- | ---: |
| Python startup | 9 MiB |
| Bifrost configuration imported | 38 MiB |
| Database layer imported | 91 MiB |
| RabbitMQ layer imported | 96 MiB |
| Workflow consumer imported | 121 MiB |
| Entire worker application imported | 123 MiB |
| Database initialized, mappers configured, and all consumers constructed | 144 MiB |

This establishes several facts without over-claiming causality:

- Kubernetes and the container abstraction are not responsible for the bulk of
  the footprint; the cgroup contains real application, file-cache, and kernel
  allocations.
- Importing the current general-purpose worker and constructing all consumer
  families creates a material floor before useful work begins.
- The separately preloaded execution template and multiprocessing tracker add a
  second fixed process baseline.
- The production parent is substantially larger than the clean constructed
  worker. The remaining growth occurs during live pool startup and operation
  and must be attributed with staged cgroup/PSS measurements in the spike.
- Packages present on the container filesystem do not consume resident memory
  merely because they are installed. A smaller image primarily improves pull
  time and attack surface unless it also changes imports and process topology.

The runtime must preserve the existing ability for a short workflow to execute
and finish in approximately 300 ms when warm capacity is available. Elasticity
that inserts a controller hop into that path is unacceptable.

## Goals

1. Preserve the current API-to-RabbitMQ-to-warm-worker path for interactive
   executions.
2. Allow Kubernetes operators to choose zero, one, or several warm workers and
   scale above that minimum under queue pressure.
3. Run high-memory or isolated work in a compatible pod rather than admitting
   it into an undersized scheduler or worker.
4. Keep existing Docker and fixed-replica Kubernetes installations behaviorally
   compatible when Elastic Runtime is not enabled.
5. Make Elastic Runtime policy configurable in Bifrost after Kubernetes grants
   the required capability.
6. Reduce fixed worker and scheduler memory by separating control, warm-pool,
   and one-shot execution costs.
7. Establish a migration path toward one internal execution substrate without
   immediately collapsing PlatformJob, workflow, and agent domain contracts.

## Non-goals for the spike

- Shipping the production settings UI.
- Deploying the experiment to production.
- Replacing RabbitMQ, Redis, or PostgreSQL.
- Scaling the API, database, RabbitMQ, Redis, or object storage to zero.
- Turning every domain record into a public PlatformJob.
- Guaranteeing a 300 ms response when the selected pool has zero warm replicas.
- Selecting final resource limits before measuring the lean runner.
- Building a feature-specific worker, status API, progress channel, or job
  table.

## Terminology

### Work metadata

Every dispatchable unit carries metadata used for placement:

- `origin`: `api`, `schedule`, `event`, `platform`, or `system`;
- `latency_class`: `interactive` or `background`;
- `resource_class`: `micro`, `standard`, `high_memory`, or `build`;
- `isolation`: `pooled` or `dedicated`;
- durable domain record identifier and attempt/fencing information.

The public domain object remains a workflow execution, agent run, or
PlatformJob. Placement metadata is an internal execution concern.

### Execution lanes

A lane is a durable RabbitMQ queue representing a service-level and resource
contract. The initial lane set is intentionally small:

| Lane | Intended work | Default capacity |
| --- | --- | --- |
| `interactive` | API-triggered workflows and other latency-sensitive work | Warm pooled workers |
| `background` | Scheduled workflows, summarization, and routine deferred work | Elastic pooled workers |
| `high-memory` | Memory-intensive PlatformJobs and executions | Elastic large workers or one-shot Jobs |
| `isolated` | Work requiring one execution per pod | One-shot Jobs |

Job-type proliferation must not create queue proliferation. A registry policy
maps each job definition and origin to one of these lanes.

Logical lanes do not imply that every existing queue should be collapsed. The
spike must first record the current transport contract for every work family:

| Current transport | Execution location | Current acknowledgement | Elastic activation concern |
| --- | --- | --- | --- |
| `workflow-executions` | Forked one-shot child behind the worker pool | Parent returns after dispatch, before child completion | Queue depth omits already acknowledged executions |
| `agent-runs` | Async task in the worker process | After the run handler returns | Does not share the workflow child-pool capacity limit |
| `agent-summarization` | Worker process | After handler completion | Must not be stranded when workflow depth is zero |
| `agent-summarization-backfill` | Worker process | After handler completion | Separate queue intentionally protects live traffic |
| `agent-tuning-chat` | Worker process | After handler completion | Needs its own activation signal or compatible pool |
| `package-installations` fanout | Every live worker | On each subscriber | Exclusive auto-delete queues retain nothing at zero replicas |
| PlatformJob PostgreSQL rows | Scheduler-supervised child | Durable row state, lease, and heartbeat | Every scheduler replica currently claims all eligible types |

The implementation design must extend this matrix with producer, admission
limit, retry, fairness, timeout, and cancellation ownership before changing a
consumer. Docker compatibility is defined by reproducing this ownership matrix,
not merely by subscribing one process to every logical lane.

### Executor pools

An executor pool is a pod template, subscription set, internal concurrency,
and scaling policy. A pool may subscribe to one or more compatible lanes. A
running pod's subscription set stays stable; policy changes drain old pods or
consumers and replace them with the new generation.

KEDA controls how many pods exist. RabbitMQ decides which eligible consumer
receives a message. Bifrost decides the lane and which pools are eligible.

## Architecture

### Control plane and images

The existing `bifrost-api` image continues to support different commands for
API, scheduler, worker, and one-shot runner roles. The spike adds Kubernetes
client support and a lean `run-one` entry point to the same image. Installed
packages in an image do not consume resident memory by themselves, so the
spike must first test whether strict import hygiene is sufficient. A separately
published slim executor image is justified only if measurements show that image
pull time, import topology, or attack surface cannot meet the target using a
separate build target from the same source tree.

KEDA remains an independently installed, open-source Kubernetes add-on. Its
controller images are not embedded in or maintained by Bifrost.

At least one scheduler/control pod remains running in Elastic Runtime. It:

- participates in the existing fenced trigger-leader election;
- materializes due schedules as work;
- reconciles Bifrost runtime policy into namespaced Kubernetes and KEDA
  resources;
- reports Kubernetes capability and convergence into Bifrost;
- observes one-shot Job lifecycle for Bifrost-specific status and cancellation;
- runs only locally permitted lightweight PlatformJob lanes.

The control pod does not route interactive requests and is not an additional
hop between the API and RabbitMQ.

### Capability versus policy

Kubernetes configuration grants capability. Installing KEDA and the Bifrost
ServiceAccount, Role, and RoleBinding permits Elastic Runtime. The scheduler
uses in-cluster authentication and registers a renewable capability record in
PostgreSQL. No Kubernetes URL, username, password, kubeconfig, or long-lived
administrator token is configured in Bifrost.

Bifrost owns desired runtime policy. Once a healthy Kubernetes capability is
registered, a platform administrator may select a profile and tune its bounded
settings. The policy is stored in PostgreSQL with a monotonically increasing
generation. Kubernetes Deployments, Jobs, and KEDA resources are generated
state labeled and owned by Bifrost.

The initial profiles are:

| Profile | Warm interactive workers | Background workers | Heavy/isolated |
| --- | ---: | --- | --- |
| Consumption | 0 | Scale from 0 | One-shot, scale from 0 |
| Flex | 1 | Scale from 0 | One-shot, scale from 0 |
| Dedicated | Configured minimum | Configured minimum or elastic | Configurable elastic maximum |

Flex is the recommended default because it preserves warm interactive latency.
Consumption explicitly accepts cold-start latency for the first request.

### Dispatch and durability

The latency-sensitive workflow path remains:

```text
API -> RabbitMQ interactive lane -> warm worker -> local one-shot child
```

There is no Bifrost controller lookup or Kubernetes API call on this path.

New execution work uses an at-least-once notification contract:

1. The producer persists the domain state or durable dispatch intent.
2. It publishes a small envelope containing the durable identifier and work
   metadata.
3. A transactional outbox or equivalent durable reconciliation record closes
   the crash window between database commit and RabbitMQ publication.
4. The consumer obtains a fenced attempt lease before executing side effects.
5. Duplicate envelopes become harmless attempts to claim already-owned or
   terminal work.

The current workflow path stores pending context in Redis and creates the
PostgreSQL execution row in the consumer. Migrating that path to a fully durable
pre-dispatch intent is a later execution-foundation step, not a prerequisite
for the KEDA scaling proof. The spike must not claim stronger workflow
durability than the current implementation provides.

Cold execution must distinguish four clocks: queue deadline, pod-startup
deadline, execution timeout, and synchronous caller-wait timeout. The existing
pending workflow context expires after one hour, while synchronous callers wait
for the workflow timeout plus a bounded buffer. The spike must define whether
work remains runnable after a caller times out and must keep queue plus startup
delay within the retained-context window.

### Warm capacity and scale-out

A KEDA `ScaledObject` targets each pooled worker Deployment. The configured
warm count becomes `minReplicaCount`; the configured ceiling becomes
`maxReplicaCount`. Operators must not maintain a conflicting manual replica
count because the autoscaler owns the target Deployment's desired replicas.

The current worker uses `max_concurrency` for RabbitMQ prefetch and `max_workers`
for workflow child capacity. These are separate limits. A workflow message is
currently acknowledged after dispatch to a child, before execution completes,
so RabbitMQ queue depth alone cannot represent active execution pressure. A pod
may also hold delivered messages while waiting up to 30 seconds for a local
slot. The spike must measure broker-ready, delivered-but-waiting, executing,
and completing work separately and choose an explicit KEDA metric and target.
If unacknowledged depth is included, use a RabbitMQ protocol and KEDA scaler
configuration that exposes it; this still does not count acknowledged children.

Safe elasticity requires execution-aware admission and drain semantics. Memory
rejection, slot timeout, and shutdown must not acknowledge or discard work that
has not obtained durable execution ownership. Scale-down must stop new
admission, wait for both consumer tasks and active executions, and define what
happens when an execution exceeds the pod termination window.

With warm capacity, RabbitMQ immediately gives work to an existing consumer.
With zero warm capacity, the message waits while KEDA activates the Deployment.
If the new pod cannot fit an existing node, a separately configured cluster
node autoscaler may provision a compatible node. Bifrost reports `waiting for
capacity` while the pod remains unscheduled.

### One-shot and high-memory execution

KEDA `ScaledJob` resources may provide homogeneous one-shot capacity for the
`isolated` and initially the `high-memory` lanes. KEDA observes a queue and
creates Jobs from a fixed template; it does not inspect a message, bind that
message to a particular Job, or select per-message resources. Therefore each
physical queue used by a `ScaledJob` must have one fixed resource, dependency,
security, and capability contract that satisfies every message on it.

The initial proof uses one queue and one fixed Job template. Each pod:

1. starts the lean `run-one` entry point;
2. consumes at most one compatible envelope and exits cleanly within a bounded
   interval if the queue is empty;
3. obtains the durable attempt lease;
4. runs the existing shared execution handler;
5. records terminal state;
6. acknowledges the RabbitMQ message; and
7. persists the claimed work-ID-to-Kubernetes-Job-UID relationship for
   diagnostics and cancellation; and
8. exits.

"One pod per message" is a Bifrost consumer contract, not an exact scheduling
guarantee supplied by KEDA. If Bifrost later needs a unique resource request per
work item, the controller must claim the durable item and create an assigned
Kubernetes Job directly instead of using `ScaledJob` for that lane.

PlatformJob remains the canonical durable record and retry authority for
non-workflow platform operations. Kubernetes Job retry is disabled for these
units so Kubernetes and Bifrost do not create multiplicative attempts.

Before a PlatformJob may move to RabbitMQ or Kubernetes, the spike must define
one shared attempt state machine covering eligibility, notification, atomic
claim, concurrency and resource-lock admission, running, heartbeat, deferred or
waiting completion, retry with `available_at`, terminal completion, and
infrastructure observation. Local scheduler claimers must exclude work owned by
elastic transport without bypassing the existing priority, per-type concurrency,
resource-lock, memory, retry, and cancellation policies. The remote supervisor
must own lease renewal, timeout, cancellation, process termination, and
fenced finalization just as the scheduler parent does today. Kubernetes OOM or
pod-loss observation may update an attempt only while its lease token remains
current. Fencing protects Bifrost state; it cannot make arbitrary external side
effects exactly once.

The homogeneous pod template declares the resource request and limit for its
pool's resource class. A pod cannot be resized to match a three-megabyte
application heap: the Python interpreter, imported Bifrost runtime, allocator,
and system libraries establish a real baseline. The goal is to measure and
minimize that baseline, then request baseline plus predicted workload memory
and safety margin rather than reserving the full current worker size.

### Lean execution path

The current worker pays for several responsibilities at idle: six RabbitMQ
consumers, database and Redis clients, the process supervisor, monitoring and
heartbeat loops, installed-package discovery, and a template process that
preloads the SDK and execution engine. A one-shot pod must not start that full
supervisor/template topology merely to run one item.

The spike will create or prototype a strict-import `run-one` path with these
properties:

- stdlib-only module entry until the envelope and execution kind are known;
- no warm template process;
- no multiprocessing resource tracker or local process pool;
- only the selected handler's dependency graph is imported;
- a minimal trusted supervisor that owns the database, RabbitMQ, Redis, and
  Kubernetes-facing lifecycle for one execution;
- untrusted workflow code in a separate child or execution-scoped protocol
  that cannot inherit database, broker, S3, or signing credentials;
- separate cgroup-v2 `memory.current`, `memory.peak`, sampled working-set,
  anonymous, file, kernel, and process-PSS reporting;
- the same credential isolation and durable completion semantics as the shared
  runner contract.

Trusted PlatformJob handlers and untrusted workflow payloads may share the
attempt vocabulary and container image, but they must not be forced through an
identical credential boundary. Memory measurements apply after preserving the
current execution-scoped token and environment-scrubbing guarantees.

The experiment records, rather than assumes, attainable floors. The evaluation
targets are:

- control-only scheduler steady idle working set at or below 256 MiB;
- warm interactive worker steady idle working set below 300 MiB;
- lean one-shot supervisor plus protected payload runner pre-workload working
  set at or below 128 MiB;
- resource-class requests derived from measured baseline plus predicted peak
  and margin;
- no statistically meaningful regression in the existing warm workflow path.

Missing a target does not invalidate the experiment. It requires an attributed
measurement and a recommendation: improve imports/process structure, introduce
a separate image build target, revise the budget, or reject the design.

### Lean scheduler path

The scheduler can become materially smaller without splitting into multiple
Bifrost container images. A clean import probe measured approximately 9 MiB at
Python startup, 104 MiB after configuration, database, and Redis pub/sub
infrastructure, 153 MiB after importing the PlatformJob registry, and 154 MiB
after importing the complete scheduler entry point. The current registry
eagerly imports every PlatformJob handler merely to expose its definition,
adding roughly 48 MiB in this probe before any PlatformJob runs.

The target design separates lightweight definition metadata from handler
loading. The scheduler may inspect lane, timeout, retry, cancellation, and
resource policy without importing handler implementations. A local or one-shot
runner loads only the selected handler after it owns an attempt. Combined with
removing high-memory execution from the scheduler cgroup, this permits one
control scheduler process to remain always available without maintaining a
fleet of large scheduler replicas.

Multiple scheduler replicas remain an availability choice, not an execution
scaling mechanism. Only the fenced leader fires triggers and reconciles Elastic
Runtime; followers provide hot control-plane failover. Executor pools and Jobs,
not scheduler replicas, supply workload capacity.

### Package and runtime dependencies

The existing package-install broadcast is an invalidation/update signal, not
the only source of dependencies. A fresh worker already installs requirements
from durable storage before its execution template becomes ready. However,
when no worker subscriber exists, the exclusive auto-delete broadcast queue
cannot notify a future pod, and ephemeral pods cannot depend on mutations made
to a previous pod filesystem.

The target model is immutable or durably hydrated runtime state. Every new pod
must obtain the required dependency set from its image or a versioned durable
artifact during startup. A dependency-set fingerprint over resolved artifacts,
runtime compatibility, and platform version becomes part of pool and Job
identity. Requirements text alone is insufficient. A pod must not consume work
until hydration succeeds; the current behavior of reporting installation
failures while continuing startup is not acceptable for an elastic pool.

The spike pins one dependency artifact, tests an update while replicas are at
zero, and measures startup hydration. It does not redesign package distribution
completely.

## Policy reconciliation

Runtime policy changes use a two-phase generation protocol.

For local-to-elastic movement:

1. Store a pending policy generation.
2. Reconcile and validate every new KEDA resource, Deployment, Job template,
   ServiceAccount, and prepared-but-disabled consumer required by that
   generation.
3. Confirm that each lane has at least one valid capacity provider, including
   scale-from-zero providers.
4. Create immutable generation-specific queue bindings or an equivalent
   admission fence so old consumers cannot receive work classified for the new
   resource or dependency contract.
5. Atomically mark the generation active in PostgreSQL; producers then publish
   new work against that generation while older queued work retains its prior
   ownership contract.
6. Enable prepared consumers only after they observe the active generation and
   require every claim to validate generation and executor capabilities.
7. Publish a Redis `execution_policy_changed` notification containing only the
   generation identifier.
8. Schedulers and workers fetch authoritative policy, stop accepting new
   deliveries for relinquished lanes, and drain in-flight work.
9. Executors report their applied generation through renewable heartbeats.
10. The controller marks the policy converged after old ownership and queued
    generation have drained, then garbage-collects superseded resources.

For elastic-to-local movement, establish and confirm local subscriptions before
pausing elastic scale-out. In-flight executions always finish where they began.
Rollback leaves the previously active generation intact until the replacement
passes admission checks. Controller reconciliation uses monotonic generations
and compare-and-set ownership so a stale controller cannot overwrite newer
desired state. `ScaledJob` updates use gradual rollout or immutable generated
resources; active Jobs are never terminated merely to apply policy.

Redis notification loss does not lose configuration. Every executor reads the
active generation on startup and compares it during its normal renewable
heartbeat. A missed notification therefore delays convergence but cannot make
the prior policy authoritative indefinitely.

## End-to-end behavior

### Existing installation, no Elastic Runtime

- The compatibility policy is created implicitly.
- Existing workers consume all workflow and agent queues they consume today.
- Schedulers execute all registered PlatformJob types locally under current
  admission controls.
- No KEDA resource or Kubernetes permission is required.
- Operators see no new runtime behavior.

The lane abstraction may be introduced internally, but its default mapping must
reproduce existing ownership.

### Flex with one warm worker

- The interactive Deployment maintains one replica.
- API-triggered workflows enter the interactive lane and retain their current
  direct RabbitMQ delivery.
- The chosen scaler combines visible queue pressure with Bifrost execution
  occupancy so acknowledged children do not disappear from capacity accounting.
- Background and isolated capacity may remain at zero.
- Scale-down is disabled for the production path until execution-aware draining
  has been proven; consumer drain alone is insufficient.

### Consumption with no warm worker

- The interactive Deployment is at zero.
- The first message remains durable in RabbitMQ.
- KEDA detects activity and activates the Deployment.
- The pod starts, subscribes, consumes, and executes.
- The UI and metrics distinguish queue wait, pod scheduling, image pulling,
  runtime startup, and execution time.
- After cooldown, the pool returns to zero.

### Scheduled workflow routed to background

- The scheduler leader materializes the due execution and publishes it to the
  background lane.
- It does not execute that workflow itself.
- Warm background capacity consumes immediately when configured; otherwise
  KEDA creates it.
- Interactive worker slots remain reserved for interactive work.

### High-memory SDK update

- The PlatformJob registry assigns the build resource class and high-memory
  lane before publication.
- The lightweight scheduler is not subscribed to that lane.
- KEDA creates a Job pod from the fixed template for that class-specific queue.
- The runner claims the PlatformJob lease, builds, records cgroup peak, and
  exits.
- OOM termination maps to a structured `memory_pressure` failure rather than a
  generic lost runner.

## Failure and safety behavior

| Condition | Required behavior |
| --- | --- |
| KEDA absent during enablement | Refuse to activate Elastic Runtime and explain the missing capability. |
| KEDA fails after activation | Preserve queued work, report degraded capacity, and do not silently assign incompatible local executors. |
| No schedulable node | Keep the pod pending, separate scheduling wait from execution timeout, and expose the Kubernetes reason. |
| Controller restarts | Reconcile desired generation and deterministically named resources without duplicating work. |
| Redis notification missed | Executor observes the active generation on startup or heartbeat and converges. |
| Duplicate RabbitMQ envelope | Fenced durable claim admits at most one current attempt. |
| Pod is OOM-killed | Record `memory_pressure`, requested/limited memory, attempt, and retry eligibility. |
| Policy changes during execution | Existing attempt finishes under its original generation; only new delivery ownership changes. |
| User cancels work | Apply the job type's cancellation policy; when running cancellation is allowed, stop new delivery, terminate the owning Job/pod, and fence late completion. |
| Scale-down begins | Stop admission, cancel RabbitMQ consumer tags, wait for consumer tasks and active executions, then exit; define an explicit outcome rather than killing acknowledged work when grace expires. |

The Kubernetes-aware scheduler receives namespace-scoped permissions only for
Bifrost-managed resources. Created execution pods use a separate unprivileged
ServiceAccount and cannot create pods themselves. Heavy or untrusted execution
must not share the control scheduler's Kubernetes credential boundary.

## Configuration surface

Kubernetes installation answers whether Bifrost may manage elastic capacity.
Bifrost settings answer how that capacity behaves.

The initial Bifrost settings model contains:

- enabled state;
- profile: Consumption, Flex, or Dedicated;
- warm and maximum replicas per pool;
- lane-to-pool assignments;
- resource-class request and limit overrides within administrator-defined
  cluster bounds;
- cooldown and maximum pending duration;
- optional node selectors, tolerations, and priority class chosen from
  operator-allowed values.

The settings API must expose desired generation, applied generation, capability
health, convergence state, and actionable errors. Kubernetes resource details
remain an advanced diagnostic rather than becoming required product knowledge.

## Testing and measurements

### Unit and contract tests

- Metadata-to-lane routing is deterministic.
- Every registered PlatformJob has an explicit execution classification.
- Pool subscription policy cannot leave an active lane without a provider.
- Policy generation activation follows the two-phase ordering.
- Duplicate envelopes and stale leases are fenced.
- Transport acknowledgement, attempt completion, and domain completion are
  tested as separate state transitions.
- Admission rejection and scale-down cannot lose an acknowledged execution.
- Kubernetes Job specifications enforce resource, security, retry, timeout,
  and cleanup policies.
- Docker compatibility policy reproduces existing ownership.

### Local Kind experiment

Extend `test.sh` with an opt-in Kubernetes lifecycle that creates a Kind
cluster, installs KEDA, loads locally built images, applies Bifrost resources,
and collects events/logs on failure. The normal Compose development and test
loops remain unchanged.

The spike scenarios are:

1. Baseline the existing warm worker workflow latency and idle memory.
2. Scale the current worker Deployment from zero on the existing
   `workflow-executions` RabbitMQ queue as a diagnostic, explicitly observing
   its current early-acknowledgement and drain limitations.
3. Repeat with one warm replica and compare latency distributions.
4. Saturate bounded per-pod concurrency and verify horizontal scale-out.
5. Reproduce the unsafe current drain boundary, then prove the corrected
   execution-aware drain without losing or duplicating work.
6. Run a synthetic execution through the lean `run-one` path and measure
   pre-import, ready, peak, and post-completion memory.
7. Execute one application SDK update as a high-memory PlatformJob pod.
8. Restart the controller and delete an execution pod during work to validate
   reconciliation and fencing.
9. Submit a pod too large for current nodes and verify pending-capacity
   reporting without starting its execution timeout.
10. Exercise policy transition ordering between local and elastic ownership.
11. Publish agent, live-summary, backfill-summary, and tuning work independently
    at zero replicas and verify that no current queue is stranded.
12. Update a dependency while replicas are zero, then verify that a new pod
    hydrates the pinned artifact before accepting work.

Record for every scenario:

- submission-to-consumer latency;
- pod activation, scheduling, and image-pull time;
- execution duration;
- `memory.current` and `memory.peak`;
- sampled Kubernetes-compatible working set and sampled maximum;
- anonymous, file, kernel, and process PSS attribution;
- sampling interval, image digest, concurrency, and startup stage;
- parent/template/child proportional memory where available;
- RabbitMQ ready and unacknowledged counts;
- pod and node count over time;
- terminal domain status and attempt ownership;
- queue deadline, pod-startup deadline, execution timeout, and caller-wait
  timeout as distinct values and outcomes.

Latency acceptance uses p50, p95, and p99 under both idle and background load,
including rollout. It also compares aggregate memory at equal throughput: a
one-shot runner may lower idle memory while using more memory per concurrent
execution than the shared fork template.

### CI direction

After the local proof succeeds, GitHub Actions can create a disposable Kind
cluster on its Docker host, load the pull-request images without publishing
them, install KEDA and Bifrost resources, run the Kubernetes contract suite,
and upload pod events and logs on failure. The Kubernetes suite remains a
separate targeted command until its runtime and reliability justify inclusion
in `./test.sh pre-pr`.

## Spike boundaries and sequence

### Experiment 0: Current contract inventory

Complete the producer/consumer matrix for all worker queues and PlatformJobs.
Record acknowledgement, retry, admission, active-work accounting, timeout,
cancellation, fairness, credentials, and shutdown ownership. This is a design
artifact and targeted contract-test set, not a transport migration.

### Experiment 1: Existing worker elasticity diagnostic

Use the current RabbitMQ workflow queue and worker image unchanged except for
test observability. Measure zero-to-one, one-to-many, and warm latency with KEDA,
and deliberately demonstrate the current early acknowledgement, saturation,
admission-rejection, and scale-down behavior. This establishes what KEDA can
control while making clear that the unchanged worker is not yet safe to scale
down automatically.

### Experiment 1b: Execution-aware worker elasticity

Resolve acknowledgement ownership, active-execution accounting, rejection, and
draining. Repeat the Experiment 1 termination and saturation cases. Production
worker elasticity cannot proceed unless this experiment is safe.

### Experiment 2: Lean one-shot runner

Extract the smallest trusted supervisor plus protected payload path capable of
running one deterministic workflow and exiting. Preserve execution-scoped
credentials and keep platform credentials outside arbitrary workflow code.
Attribute import and runtime memory, compare it with the current parent/template
topology, and decide whether strict import hygiene or a separate image build
target is warranted.

### Experiment 3: Isolated PlatformJob

Define the minimal PlatformJob attempt and remote-supervision protocol, then add
the execution classification and Kubernetes runner seam needed to execute one
SDK-update PlatformJob from one homogeneous, fixed-template queue. Keep
PlatformJob as the durable owner and verify eligibility, notification, claim,
resource locks, deferred/waiting behavior, retry, status, progress,
cancellation policy, fencing, timeout, OOM, work-ID-to-Job-UID binding, and
cleanup.

### Experiment 4: Policy reconciliation

Prototype a versioned runtime policy and demonstrate prepare-disabled,
generation-fenced activation, notify, drain, rollback, gradual Job rollout,
and convergence. This experiment may use an administrative test endpoint or
fixture rather than production UI.

## Migration path toward one execution substrate

The migration proceeds behind existing domain APIs:

1. Introduce execution metadata, lanes, and resource-class policy while
   preserving current queue ownership.
2. Make worker consumer selection explicit and add the lean one-shot runner.
3. Route isolated PlatformJobs through the shared envelope and runner boundary.
4. Move remaining PlatformJob execution classes onto lane-based dispatch where
   justified, retaining PostgreSQL PlatformJob durability and visibility.
5. Introduce a common internal `WorkItem`/`ExecutionAttempt` contract for
   dispatch, fencing, infrastructure status, resource measurements, and
   cancellation.
6. Adapt workflows and agents to that attempt contract without changing their
   domain-specific streaming, results, permissions, or public endpoints.
7. Remove superseded direct claim or bespoke delivery paths in the same changes
   that migrate their final consumers; do not retain indefinite dual systems.

“Jobs are jobs” therefore becomes true at the placement and attempt layer. It
does not require pretending that a short synchronous workflow and a durable
administrative PlatformJob have identical product semantics.

## Go/no-go decision

The spike recommends production implementation only if it demonstrates all of
the following:

- unchanged warm-path topology and no meaningful latency regression;
- reliable zero-to-one and saturation scale-out behavior;
- execution-aware acknowledgement, admission, scale-down, retries,
  cancellation, and controller recovery;
- fixed-template placement or an explicitly chosen controller-assigned Job
  architecture for every one-shot lane;
- preserved credential isolation for arbitrary workflow code;
- useful memory reduction from the lean one-shot path;
- explicit capacity-degraded behavior without unsafe fallback;
- an operable installation and policy model that does not require Kubernetes
  credentials inside Bifrost configuration;
- a bounded migration that removes old paths as ownership moves.

If KEDA activation latency, worker startup, dependency hydration, or execution
durability cannot satisfy these requirements, retain fixed warm workers and
limit Kubernetes elasticity to isolated PlatformJobs until those blockers are
resolved.
