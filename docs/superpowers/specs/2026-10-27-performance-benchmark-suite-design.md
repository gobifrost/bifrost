# Performance Benchmark Suite — Design

Status: **Draft for Jack's review.** Nothing in this document has been built.

## 1. Why this exists

Bifrost is going to be sold as a hosted service: one Kubernetes namespace per
customer, each running a full stack. To price it, budget cloud spend, and tell
customers what to expect, we need to be able to say:

> "With *these* resources, a tenant gets *this many* concurrent executions of
> *this kind* of work, at *this* latency, and their database grows *this fast*."

We cannot say that today. Between 2026-10-23 and 2026-10-27 about twenty
performance PRs landed (the #769 unified-runtime stages, #1066 thin runner,
#1069–#1093 follow-ups). Every one was validated with a one-off local control
and carried the caveat "not a production promise." The result:

- A local broad-workload run put the API at **~560 MiB** (#1066 report). That
  number was accepted.
- Production now sits at **~760 MiB** per API pod. Nobody can reproduce the
  difference locally.
- Throughput problems that did not exist before #1066 appeared once workflow SDK
  calls, worker claims, and agent runs moved into the API process. Scaling API
  pods out to absorb that load looks like the worker scaling problem moved to the
  API.
- Jack suspects that if we started over with "lightweight and fast" as the
  premise, some of the current design would not survive. There is currently no
  way to prove or disprove that.

The root process problem is the absence of a **fixed, repeatable workload run
against a fixed, production-shaped environment**. Without one, individual
findings cannot be ranked, regressions cannot be caught, and capacity cannot be
promised. This suite is that fixed reference.

## 2. Questions the suite must answer

Every part of this design exists to answer one of these five. A part that
answers none of them should be cut.

| # | Question | Experiment |
|---|---|---|
| Q1 | Where exactly does the API's memory go, and which part is justified? | E1 Memory attribution |
| Q2 | What does one tenant cost before it runs anything? | E0 Footprint floor |
| Q3 | For a given resource envelope, what workload does a tenant sustain within an acceptable latency? | E2 Capacity search |
| Q4 | How fast does storage grow, and does retention hold it steady? Does memory creep over hours? | E3 Soak |
| Q5 | Is design A better than design B (e.g. the pre-#1066 release against today, or a prototype that moves agents or SDK serving back to workers)? What does adding one API replica or one worker actually buy? | E4 Architecture comparison, E5 Scaling curve |

## 3. Goals, non-goals, anti-goals

**Goals**

- One command produces a reproducible result for one build in one resource
  envelope, as machine-readable JSON plus a human-readable report.
- Results from two builds or two configurations can be compared like-for-like,
  with the same seed data, workload, envelope, and cluster.
- The tenant topology under test is exactly the hosted topology (section 4).
- The memory-attribution result adds up: its components sum to the measured
  container working set within 10%. If they don't, the run says so.
- Customer-facing numbers come from a real cloud cluster, using the same
  harness that runs locally on Kind.

**Non-goals**

- Not a merge-queue gate. It runs on demand and on a schedule (nightly or
  weekly), not on every PR.
- Not a load test of external providers (real LLMs, real PSA/RMM APIs). Those
  are replaced by deterministic in-cluster fakes with configurable latency.
- Not a profiler for production. Intrusive diagnostics (native sampling, heap
  walks) never run against production. The 2026-10-26 incident, in which a
  profiler caused 12 failed executions, is the reason.
- Does not build alternative architectures. It measures whatever builds you
  give it. Prototypes such as "agents on workers" are separate spikes that the
  suite then evaluates.
- Not a replacement for unit or E2E correctness tests.

**Anti-goals: any of these would make the suite a failure even if it runs**

- Numbers that cannot be reproduced. Two runs of the same build in the same
  envelope must land within a stated tolerance (section 9.4). Otherwise the
  suite is noise generator #21.
- Measuring an empty, quiet stack and calling it representative. That is
  exactly how the 560 MiB versus 760 MiB gap arose. Seed data and workload
  history are first-class inputs.
- A harness so slow or fragile that nobody runs it. The standard run must
  finish in about two hours, and the quick attribution run in about 30
  minutes.
- Recreating a bespoke deployment. The suite deploys the real manifests with
  overlays; it does not keep a parallel copy of the stack.
- Silent fallbacks (per CLAUDE.md). If a measurement source is unavailable, the
  run fails loudly; it never substitutes a different measurement.

## 4. Fixed constraints (decided by Jack)

1. **Tenant topology is API, Client, and Worker.** There is **no scheduler**.
   Worker capacity comes from two supported sources, and an allocation profile
   may use either or both:
   - **Worker Deployment replicas:** a fixed, always-on floor. The production
     manifest ships `replicas: 0`, but normal replicas remain supported
     (Jack, 2026-10-27).
   - **API-launched worker Jobs:** elastic capacity, governed by the Settings →
     Kubernetes policy: `warm_workers` (default 1), `max_workers` (6),
     `worker_memory_mib` (2048) and `largest_worker_mib`. Workflows get learned
     memory requests, so Job sizes vary.

   The `k8s/local` overlay also runs a scheduler; the benchmark overlay does
   not.
2. **The Kubernetes design does not change.** The suite measures the current
   API/Client/Worker design and variants of it; it does not propose a new
   topology.
3. **Object storage is managed cloud storage outside the tenant** (DigitalOcean
   Spaces in production). It is not part of the tenant's resource envelope. It
   is metered as bytes stored and requests made.
4. **Postgres runs in the tenant namespace on CloudNativePG (CNPG).** The reason
   is cost: managed cloud databases start at about $100/month each. Postgres
   resources therefore **count toward the tenant envelope**, and its sizing is
   part of the price.

**My calls, flagged for review:**

- **Redis and PgBouncer also run in the tenant namespace and count toward the
  envelope.** The stack requires both. PgBouncer is provided by CNPG's `Pooler`
  resource rather than our own `pgbouncer.yaml`, to match how a CNPG tenant
  would actually be deployed.
- The **CNPG operator** runs once per cluster in its own namespace, outside any
  tenant's envelope, as it would in hosting.

## 5. Environment

### 5.1 Cluster-agnostic harness

The harness takes a kubeconfig and a cluster profile; it does not assume Kind.

- **Local profile (default):** the existing Kind harness
  (`scripts/kubernetes/local-kind.sh`, `./test.sh kubernetes ...`), extended
  with a bench overlay. Used for development, attribution (E1), A/B
  comparisons (E4), and regression tracking.
- **Cloud profile:** the same harness pointed at a disposable managed cluster
  (DOKS, since production runs on DigitalOcean). Used to calibrate local
  results and to produce any number quoted to a customer or used for pricing.

Kind on one workstation shares CPU, disk, and network with the host, and has no
CPU steal or cross-node latency. Local absolute numbers are trusted for
comparisons (A against B on the same host); only cloud-profile numbers are
trusted as absolutes.

### 5.2 Namespaces

| Namespace | Contents | Counts toward envelope |
|---|---|---|
| `bench-tenant` | API, Client, worker Jobs (warm and on-demand), CNPG Cluster + Pooler, Redis | **Yes**: enforced by `ResourceQuota` + `LimitRange` |
| `bench-infra` | Load driver, fake LLM, fake external API, metrics collector, local S3 stand-in (Local profile only) | No |
| `cnpg-system` | CNPG operator | No |

The tenant namespace's `ResourceQuota` is the envelope. Kubernetes then
refuses anything that would exceed it, including on-demand worker Jobs, which
is exactly the hosted behaviour we want to observe.

### 5.3 Object storage

- **Cloud profile:** a dedicated, disposable Spaces bucket per run.
- **Local profile:** SeaweedFS in `bench-infra` (outside the envelope). It is a
  stand-in for correctness only; its latency is not representative.

Both record bytes stored and request counts at the start and end of each run.

### 5.4 Resource envelopes

The envelope is the namespace quota. *How* it is divided among components is
an **allocation profile**, which is a run input; finding the best allocation
for an envelope is itself a use of the suite. Proposed starting points (my
calls; these exist to be measured and revised, not trusted):

The worker column below uses Jobs only. Allocation profiles that swap some or
all of that for fixed Deployment replicas are compared in E5.

| Envelope | Quota (CPU / memory / Postgres disk) | API | Worker Job policy (warm / max × size) | Postgres (CNPG) | Redis, Pooler, Client |
|---|---|---|---|---|---|
| S | 2 vCPU / 4 GiB / 20 GiB | 1 × (0.75 CPU, 1 GiB) | 1 / 1 × 1 GiB | 1 instance (0.5 CPU, 1 GiB) | ~0.25 CPU, ~256 MiB total |
| M | 4 vCPU / 8 GiB / 50 GiB | 2 × (1 CPU, 1 GiB) | 1 / 2 × 1.5 GiB | 1 instance (1 CPU, 2 GiB) | ~0.3 CPU, ~400 MiB total |
| L | 8 vCPU / 16 GiB / 100 GiB | 2 × (1.5 CPU, 1.5 GiB) | 1 / 4 × 2 GiB | 1 instance (2 CPU, 4 GiB) | ~0.4 CPU, ~640 MiB total |

The worker policy is set in Settings → Kubernetes for each run. Each envelope
must fit at least its warm worker plus all fixed components, or the run fails
before measuring. With the default 2 GiB worker, envelope S could never start
one, which is why it uses 1 GiB workers. Per-Job CPU is whatever the controller
sets; the builder confirms it and adjusts the arithmetic.

**Requests versus limits (my call).** The quota caps both `requests.*` and
`limits.*`. The controller sets a Job's memory request from
`BIFROST_POOL_WORKER_MEMORY_REQUEST_MIB` (256 in the Kind config) and its limit
from `worker_memory_mib`, so by default Jobs overcommit the node. For priced
runs the bench sets that existing environment knob equal to the worker size,
so every pod reserves what it may use. No product change is needed. Reports
also state the overcommitted "as shipped" footprint for comparison. The same
rule applies to the API: its repository manifest requests 256 Mi while using
about 750 MiB, and a cost model built on requests like that is wrong by
design.

High-availability Postgres (two or more CNPG instances) is an envelope variant,
not the default, because it doubles database cost; the suite should be able to
price it.

### 5.5 Builds under test

A run names one image reference per component (normally a single
`bifrost-api` image for API and Worker, plus the client image) and a set of
environment overrides. A comparison run (E4) names two such variants. The
harness:

- records the image digests, the git SHA, and the overrides in the result;
- refuses to compare variants whose topology differs (e.g. one needs a
  scheduler). This is a hard error, not a warning.

## 6. Seed data

### 6.1 Why seed data is required

#1083 proves the point: 701 workflow tools in production retained about 43 MiB
that an empty local stack never showed. Data-proportional memory is invisible
without production-shaped data.

### 6.2 Tenant shape profiles

A **tenant shape** is a set of entity counts, each with size distributions
where relevant:

- organizations, users, roles
- workflows (count, parameter-schema size), workflow tools exposed to agents
- agents (count, tools per agent, prompt size), MCP-exposed tools
- forms, apps (count; built bundles are optional)
- tables (count, rows per table, document size)
- configs, integrations and mappings
- historical executions and logs (rows), for query-path realism

Profiles:

- **`small`**: a modest single-MSP tenant.
- **`prod-today`**: counts taken from production (counts only, no content),
  collected by a read-only query script that is part of the suite. Real names,
  prompts, and payloads never leave production; this repository is public.
- **`large`**: roughly 3× `prod-today`, for headroom planning.

The seeder generates synthetic entities with deterministic content from a fixed
random seed, through the public API/CLI rather than direct database writes,
so seeding exercises the same code paths a real tenant does. Seeding happens
before measurement and is excluded from timing.

## 7. Workloads

### 7.1 External fakes (in `bench-infra`)

- **Fake external API:** an HTTP service with configurable latency
  distribution, response size, and error rate. It stands in for PSA, RMM and
  Graph calls, which is where real MSP workflows spend most of their time.
- **Fake LLM:** an OpenAI-compatible chat-completions endpoint (streaming and
  non-streaming, tool calls, token usage) with scripted, deterministic
  responses and configurable latency. The existing fixture
  (`api/scripts/thin_provider_fixture.py`, built on `scheduler_fixture_server`)
  already serves OAuth, embeddings, MCP and video. The builder extends it with
  chat completions. Real providers are never called.

### 7.2 Workload units

Workflows are ordinary workspace workflows, deployed into the tenant before a
run.

| ID | Unit | What it stresses |
|---|---|---|
| W1 | No-op workflow | Dispatch, claim, and result overhead |
| W2 | SDK-heavy: N config/table reads and writes | API SDK serving (auth + policy + DB) |
| W3 | IO-wait: K calls to the fake external API, 0.2–5 s each | Concurrency slots and worker memory per waiting execution (**the realistic MSP shape**) |
| W4 | CPU/memory: parse and transform a generated 10–50 MB JSON | Worker CPU/memory limits and admission |
| W5 | Large result (1–30 MB) | Result transfer and API-side reassembly |
| W6 | Log-heavy: L log lines of S bytes | Database growth per execution |
| A1 | Agent run via the fake LLM: M turns, T tool calls into W2/W3 workflows | Agent execution in the API process |
| A2 | Agent-run summary | Summary work in the API |
| U1 | Simulated UI user: page loads, list/search calls, notification WebSocket | API REST and WebSocket load from humans |
| H1 | Webhook/event-triggered workflow | Event ingress path |

### 7.2a Execution-time classes (Jack, 2026-10-27)

Every run reports execution times for a fixed set of **classes**: one workflow
or agent per class, run on its own so its numbers aren't mixed with others.
For each class: N sequential runs on an idle stack (latency floor), then a
concurrent burst (latency under contention), giving queue wait, run time and
end-to-end p50/p95/max. The memory of the API and workers is snapshotted after
each class, so the same run also attributes memory growth to a class.

| Class | Unit | What its time means |
|---|---|---|
| `wf-noop` | W1 | Pure platform overhead: dispatch, claim, result |
| `wf-sdk` | W2 (5 config reads, 1 table query, 1 table insert) | SDK round trips through the API |
| `wf-wait` | W3 (2 s simulated external wait) | Slot occupancy, not CPU |
| `wf-cpu` | W4 (~20 MB JSON transform) | Worker CPU and memory |
| `wf-large` | W5 (~5 MB result) | Result transfer and storage |
| `agent-chat` | One model turn, no tools | The "simple AI request" path |
| `agent-tools` | Direct mode, three workflow-tool calls | Agent loop plus tool dispatch |
| `agent-codemode` | Automatic mode, same tools through CodeMode | CodeMode sandbox in the agent's process |

**Model backends.** Agent classes run against either:
- `fake`: the in-cluster deterministic OpenAI-compatible model. Exactly
  repeatable; it isolates platform time from model time.
- `deepseek`: real DeepSeek through its OpenAI-compatible API, with the key
  read from Jack's personal 1Password vault at run time and never written to
  disk or to the repository. Model latency varies, so these numbers are
  reported with the model's own time separated where the run records it.

**Reproducibility.** A run is fully described by a checked-in profile file
(seed counts, classes, N, concurrency, backend) plus the build under test. The
result JSON records both, so `./test.sh bench run --profile <name>` on another
day or build produces a directly comparable result.

### 7.3 Workload mixes

A mix gives each unit a share of the arrival rate, plus a fixed number of
background UI users.

- **`msp-typical`** (default): dominated by W3, some W2, light A1, steady U1.
  The builder proposes initial weights; Jack confirms them against real
  production execution history (section 12).
- **`sdk-heavy`**: W2-dominated. Isolates the "API is the SDK bottleneck"
  question.
- **`agent-heavy`**: A1/A2-dominated. Isolates the "agents in the API" question.
- **`prod-replay`**: weights derived from the last 7 days of production
  execution counts by workflow type and trigger (counts only).

### 7.4 Load driver

The driver runs in `bench-infra` as an **open-loop** generator: it starts work
at a target arrival rate whether or not earlier work has finished. Closed-loop
"N virtual users" generators hide queueing collapse, which is exactly what we
need to see.

- It triggers work the way real tenants do: REST execute, webhooks, agent
  invocation, and UI calls, all authenticated as seeded users.
- It records client-side latency for synchronous calls.
- **Execution latency comes from the database**, not the driver: queue wait =
  `started_at − created_at` and run time = `completed_at − started_at`, read
  from `executions` for workflows and from `agent_runs` for agent work. This
  measures what a customer experiences, independent of driver timing.
- **Queue depth is sampled live:** a count of `work_dispatches` rows by family
  every 5 s. Claiming deletes the row, so nothing can be reconstructed
  afterwards.
- **Builds on the existing harness.** `scripts/kubernetes/` already contains:
  - `pressure_test.py` and `pressure_driver.py`: the #769 drill with floor,
    learning, sizing, ramp-with-kills and drain phases;
  - `pool_spike.py`: sets the worker policy;
  - `sync_latency_bench.py`.

  The suite reuses their workflow registration, submission and policy code.
  The pressure test stays as the resilience drill; the benchmark is the
  measurement run. One change is required: `pressure_driver.py` runs *inside
  an API pod* via `kubectl exec`, which would charge the driver's own memory
  and CPU to the component under measurement. The benchmark driver runs in
  `bench-infra` instead.
- **My call:** the driver is Python (asyncio + httpx), living in the repo with
  the rest of the suite, rather than k6. It reuses our auth and CLI client
  code and adds no JavaScript load-test toolchain. If it cannot reach the
  required arrival rates on one pod, it runs as N pods.

## 8. Measurement

### 8.1 Sources

| What | Source | Cadence |
|---|---|---|
| Per-container working set, RSS, CPU | Kubelet summary API (`/stats/summary`). Working set = usage − inactive_file, the same definition as the earlier reports | 2 s |
| OOM kills, restarts, evictions, quota denials | Kubernetes events + pod status | Continuous |
| API process internals | **Memory probe** (8.2) | 10 s, and at every experiment step |
| Execution latency and outcomes | Postgres: `executions`, `agent_runs` | End of each step |
| Queue depth | Postgres: `work_dispatches` row count by family | 5 s, live |
| Worker Jobs | Count, sizes, pending/unschedulable, quota denials | 5 s |
| Postgres size | `pg_total_relation_size` per table, `pg_database_size`, WAL generated (`pg_current_wal_lsn` delta), PVC usage | Start/end of each step + every 5 min in soak |
| Postgres health | `pg_stat_database` (commits, temp bytes), connection counts, Pooler stats | 30 s |
| Object storage | Bytes and object count by prefix; request counts from the driver/proxy | Start/end of each step |
| Redis | `INFO memory`, `INFO clients` | 30 s |
| Driver | Requests sent and completed, client latency, errors | Per request (aggregated) |

Missing sources fail the run (anti-goal: no silent fallback).

### 8.2 API memory probe

This is a small diagnostic in the API, **off unless an environment variable
enables it**, and reachable only by a platform admin. It is the one product
code change the suite requires.

Cheap fields (safe to sample every 10 s):

- `/proc/self/smaps_rollup`: RSS, PSS, anonymous memory
- Python allocator: `sys.getallocatedblocks()`, pymalloc arena totals
- glibc: `mallinfo2()` in-use versus free bytes, arena count
- `sys.modules`: count plus a stable hash; the full name list on request
- thread count, asyncio task count, open file descriptors
- connected remote-worker sessions, UI WebSocket connections, DB pool checked
  out/overflow, Redis pool in-use

Heavy fields (opt-in per call, never on a timer):

- object-type histogram (top N by count and shallow size)
- `malloc_trim(0)` followed by re-reading the cheap fields, to separate
  *allocator-retained* memory from *live* memory

Comparing the module list from production with a local warm process shows
exactly which first-use imports production has paid for; that comparison is
read-only and costs microseconds. **Whether the cheap fields may be enabled in
production is Jack's decision** (section 12). The heavy fields never run in
production.

Worker processes are measured from the container (cgroup) only. They are thin,
and container memory is what we pay for.

## 9. Experiments

### E0 — Footprint floor (Q2)

Fresh tenant, with seed data, no traffic. Record each component's working set
at four points: just ready; after 10 idle minutes; after one pass of every
workload unit (a "fully warm" process); and after 30 idle minutes following a
soak. Output: the **per-tenant fixed floor** in CPU-idle and memory, per
component, which is the cost of a tenant that does nothing.

### E1 — Memory attribution (Q1). Runs first, about 30 minutes

On a fresh API (production image, no `--reload`, production environment
flags), apply a fixed staircase, probing the API (cheap fields, plus the heavy
trim read) after each step:

1. Ready, no seed
2. Seed `prod-today`
3. 5 UI users, 2 minutes
4. 500 W2 SDK calls
5. One A1 agent run per provider type the fake LLM imitates
6. One W5 30 MB result
7. W6 log burst plus a document-query burst
8. **Repeat steps 3–7 a second time**
9. 10 minutes idle

The output is a table whose rows sum to the final working set:

| Component | How it is derived |
|---|---|
| Import baseline | Step 1 |
| Data-proportional | Step 2 − Step 1 (live, after trim) |
| First-use imports | Module-list growth plus its live delta (step 5 especially) |
| Live workload state | Live-after-trim growth during steps 3–7 not explained above |
| Allocator retention | Before-trim minus after-trim |
| **Leak** | Any live growth during the second pass (step 8) |

If the rows do not sum to the measured working set within 10%, the report says
so and lists the residual. The staircase then runs against production's actual
numbers (760 MiB) and the reconciliation shows what production has that the
local run lacks (more connected workers, more UI sockets, longer uptime). Those
gaps become new staircase steps, not guesses.

### E2 — Capacity search (Q3)

For one envelope × mix:

1. Start at a low arrival rate. Hold each step for 10 minutes (5 warm-up, 5
   measured).
2. Increase the rate by about 25% per step until any acceptance limit
   (section 10) is breached for the measured window, or the quota blocks
   capacity growth.
3. The **sustained capacity** is the last passing step. Rerun that step once to
   confirm it.
4. Record which limit broke first and which component was saturated (CPU
   throttling, memory pressure/OOM, database connections, queue growth). That
   bottleneck is often more valuable than the number itself.

Reported both as **executions per minute** and as **peak concurrent
executions**, since W3-style work is defined by how many can wait at once.

### E3 — Soak and storage growth (Q4)

Run at 70% of the E2 sustained capacity for 4 hours (cloud profile: 24 hours
when quoting). Measure:

- memory slope per component after the first hour. A non-zero live-memory
  slope fails the soak;
- Postgres growth per table, WAL rate, and object-storage growth;
- whether retention holds growth flat. Run retention has a 30-day minimum,
  audit and artifact retention are set in days, and all three run on a daily
  cron. Steady state therefore cannot be observed within a soak, and the
  bench does **not** change the product to compress them (my call). Instead:
  - Before the soak, the seeder writes back-dated history older than each
    window.
  - The existing manual retention triggers (`api/src/routers/maintenance.py`)
    then run; where a family has no manual trigger, the builder reports it
    rather than adding one.
  - The run measures rows and bytes deleted, the size after vacuum, and the
    retention job's own cost.

  The capacity card's steady-state size is **modelled** (bytes per execution ×
  rate × window), checked against that back-dated deletion, and labelled as
  modelled.

### E4 — Architecture comparison (Q5)

Run E1 + E2 (and E3 on request) for variant A and variant B, with an identical
seed, mix, envelope, and cluster, interleaved as A, B, A, B to cancel out host
drift. The report shows the deltas and whether they exceed run-to-run noise
(section 9.4).

The first planned comparisons are hypotheses to settle, not conclusions:

| Hypothesis | A | B |
|---|---|---|
| H1: #1066 moved the scaling bottleneck from workers to the API | Last release before #1066 | Current `main` |
| H2: running agents in the API process is the largest avoidable API memory and CPU cost | Current `main` | Spike: agents claimed by workers |
| H3: serving workflow SDK calls in the API makes API CPU the throughput ceiling | Current `main` | Spike: SDK served beside the workflow, as before #1066 |
| H4: allocator retention is reclaimable without a latency penalty | Current `main` | `main` + allocator tuning (e.g. trim-on-idle) |

Spikes H2–H4 are built separately, if Jack wants them, and are not part of this
suite. Answering "is there a design we wouldn't keep?" with evidence means
running these comparisons.

### E5 — Scaling curve (Q5)

Starting from envelope M, vary one dimension at a time and rerun E2 for
`msp-typical` and `sdk-heavy`:

- API replicas 1 → 2 → 3, with the worker fixed
- worker policy `max_workers` 1 → 2 → 4, then `worker_memory_mib` 1 → 2 GiB,
  with the API fixed
- the same worker memory as fixed Deployment replicas instead of Jobs, which
  trades elasticity for no Job start-up latency
- Postgres CPU/memory one step up and down

Output: **marginal capacity per added unit of resource**, e.g. "+1 API
replica (1 CPU, 1 GiB) buys +X executions/min of `sdk-heavy` but only +Y of
`msp-typical`." This directly measures whether API scale-out is now doing the
job worker scale-out used to.

### 9.4 Reproducibility

- Every experiment records cluster profile, node shapes, image digests, seed
  profile and random seed, mix, envelope, allocation, and harness version.
- **The noise floor is measured, not assumed:** the standard run repeats the
  E2 final step and E1 once. Reported differences smaller than twice the
  observed run-to-run spread are labelled "within noise."
- Proposed tolerance for "reproducible": two identical runs within ±5% on
  sustained capacity and ±5% (or ±20 MiB, whichever is larger) on settled API
  memory. A harness that cannot hold this on Kind is a harness bug to fix
  before results are used.

## 10. Acceptance limits

Proposed defaults (my calls); Jack confirms or replaces them (section 12). A
step passes only if all of these hold over its measured window:

| Limit | Proposed default |
|---|---|
| Queue wait p95 (`started_at − created_at`, immediate-trigger work) | ≤ 2 s |
| SDK call p95 (W2, per call, client-side) | ≤ 150 ms |
| UI API p95 (U1) | ≤ 500 ms |
| Execution error rate (excluding injected fake-API errors) | ≤ 0.1% |
| OOM kills / lost executions / container restarts | 0 |
| Queue depth | Not growing across the window (slope ≈ 0) |
| Postgres connections | Below pool limits, no waits longer than 1 s |

## 11. Outputs

Each run writes a directory (local `/tmp/bifrost-bench-<run-id>/`, cloud runs
also uploaded to the run's bucket) containing:

- `result.json`: inputs, environment, per-experiment metrics, pass/fail per
  limit, bottleneck classification. Its schema is versioned so results can be
  compared over time.
- `report.md`: human-readable summary, starting with the **capacity card**.
- raw time series (CSV/JSONL), probe snapshots, Kubernetes events, and
  Postgres size snapshots.

**Capacity card**: the deliverable for pricing and customers, one per
envelope × mix:

> **Envelope M (4 vCPU / 8 GiB / 50 GiB Postgres disk), `msp-typical`, seed
> `prod-today`.** Sustains **X executions/min** with up to **C concurrent**
> executions at queue-wait p95 ≤ 2 s. First bottleneck: API CPU. Idle
> floor: **F GiB** memory. Postgres grows **G MB per 1,000 executions**, or
> **D GB/month** at this rate with 30-day run retention, against a steady-state
> size of **S GB**. Object storage: **O MB per 1,000 executions**. Measured on
> DOKS `s-4vcpu-8gb` nodes, build `<sha>`, 2026-MM-DD.

A comparison run adds an A/B table with deltas and noise labels.

Selected results (capacity cards and E4 conclusions) get committed under
`docs/benchmarks/` as dated reports, following the existing convention. Raw
data stays out of git; the existing reports already commit 100k-line JSON files
and should stop doing so.

## 12. Decisions Jack still needs to make

These change what gets built or what the numbers mean:

1. **Acceptance limits** (section 10): are these defaults the promises you'd
   make to customers?
2. **Production shape and config**: may the suite read, from production
   (read-only, counts and settings only, no content):
   - 7 days of execution counts by workflow type and trigger, to derive
     `prod-replay` and sanity-check `msp-typical`;
   - entity counts for the `prod-today` seed;
   - the running configuration E1 must match: API replica count, Settings →
     Kubernetes worker policy, the API env/ConfigMap (including DB pool
     sizes), typical connected-worker and UI-socket counts, and whether
     production Postgres is managed or in-cluster?

   Without these, E1's gap analysis is guesswork.
3. **Production probe**: may the cheap probe fields (section 8.2) be enabled
   on one production API pod for E1 reconciliation?
4. **Cloud profile budget**: is a disposable DOKS cluster (a few hours per
   calibration run, roughly the cost of the node sizes in section 5.4)
   acceptable, and is DOKS the right provider to calibrate against?
5. **Envelopes S/M/L**: are these the sizes you would sell, or do you have
   target price points the envelopes should be derived from?
6. **Where it runs**: Kind needs a host with roughly the L envelope plus about
   4 vCPU / 8 GiB of headroom. Is that your workstation, or a dedicated VM?

## 13. Final deliverables and when we stop

Each phase ends with a committed, dated report under `docs/benchmarks/` and a
stop for Jack's review. A phase is done when its exit check passes, not when
the code exists.

| Phase | Deliverable | Exit check |
|---|---|---|
| 1 | **Baseline report** for today's build: execution times per class (section 7.2a) with the fake model and with DeepSeek; API and worker memory after each class (the per-class attribution); worker idle memory; the per-tenant idle floor. `./test.sh bench run --profile baseline` reproduces it. | Two runs on the same build agree within ±10% on class p50 times (fake model) and ±20 MiB on settled API memory. |
| 1b | **Agent runs back on the workers** (Jack, 2026-10-27): autonomous agent runs, including CodeMode, execute in short-lived worker child processes; summaries stay in the API. Proven with the Phase 1 profile, before and after. | The API's settled memory after the agent classes falls; worker idle memory stays within a few MiB of the baseline; each worker returns to idle after every run; agent class times don't regress beyond the stated bound. |
| 2 | **Capacity cards** for envelopes S and M × `msp-typical` and `sdk-heavy` (Kind); the **H1 verdict** (pre-#1066 against `main`): did the bottleneck move to the API, with numbers; the **scaling curve**, i.e. what +1 API replica, +1 worker replica and +1 worker Job each buy. | Every card's sustained capacity reproduces within ±5%; the first bottleneck is named for each. |
| 3 | **Customer-quotable cards** from a disposable DOKS cluster + Spaces (S, M, L); the **storage model** (bytes per execution, monthly growth at a given rate and retention); the soak result (no live-memory slope). | The cloud and Kind cards differ by a stated, explained factor; the soak passes. |

**The overall final deliverable** is the Phase 3 set, a sizing table you can
price from ("envelope M sustains X executions/min, costs F at idle, grows
the database G GB/month"), plus a `./test.sh bench` command that reruns it on
any build. Anything not needed for those three exit checks is out of scope.

**Phase 1 environment (my call, for speed).** Attribution measures the API
process, which does not depend on how Postgres is deployed. Phase 1 therefore
runs on the existing Kind harness and its Postgres; CNPG, quotas and the
envelopes arrive in Phase 2, where they matter.

**Host limits (my call).** This workstation has 8 cores, 62 GB of RAM and about
36 GB of free disk. Envelopes S and M run locally; L runs only in the cloud
profile. Each run must clean up its images and volumes, because an earlier
profiling run filled this disk.

## 14. Build order

Each phase stands on its own and answers questions before the next starts.

1. **Phase 1, baseline (answers Q1, Q2 and class execution times):** bench
   profile on the existing Kind harness (no scheduler, one API replica,
   production allocator settings); external memory reads (`smaps` +
   kubelet stats, no product change); seeder with the `prod-today` workflow
   counts; the execution-time classes; fake model and DeepSeek backends;
   `result.json` + report. Then **1b**: agent runs back on the workers,
   measured with the same profile.
2. **Phase 2, capacity (Q3, Q5):** all workload units and mixes, the fake
   external API, the open-loop driver at scale, E2, E4 (starting with H1), E5,
   capacity cards.
3. **Phase 3, soak and storage (Q4):** E3, retention verification, storage
   model, cloud profile on DOKS + Spaces, the first customer-quotable
   capacity cards.

## 15. Left to the builder

- Directory layout and command surface. My suggestion is
  `./test.sh bench <experiment> [--envelope M] [--mix msp-typical] [--variant ...]`,
  reusing `local-kind.sh` for cluster lifecycle.
- Fake LLM implementation (extend `thin_provider_fixture.py` or a sibling).
- The CNPG operator version (pinned) and how the init/migration job targets
  CNPG.
- Initial `msp-typical` weights, pending decision 2.
- How the probe is exposed (route versus control-channel message) and its
  generated-artifact updates (operation inventory, OpenAPI digest, access
  list). It must remain disabled by default and admin-only.
- Whether the driver needs more than one pod, decided by measurement.
