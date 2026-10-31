# Performance and capacity benchmark

Repeatable measurements of one Bifrost build inside a constrained hosting
envelope, on the worktree's own Kind cluster running the production images.
Methodology (reviewed): `docs/plans/2026-10-29-capacity-benchmark-methodology.md`.
Measurement definitions and hosting denominators: [measurement contract](../../docs/benchmarks/measurement-contract.md).
Saved portal/import baselines, offline verification, replay recipes and remaining
completion gates: [baseline guide](../../docs/benchmarks/2026-10-31-benchmark-baseline-and-completion.md)
and [machine-readable record](../../docs/benchmarks/baselines/portal-2026-10-31.json).
The [storage and retention result](../../docs/benchmarks/2026-10-31-storage-retention-results.md)
records the measured payload-shape difference and canonical deletion rehearsal.
Earlier design: `docs/superpowers/specs/2026-10-27-performance-benchmark-suite-design.md`.

```bash
./test.sh bench up                                    # cluster, images, CloudNativePG, tenant (profile m)
./test.sh bench run --profile m                       # execution classes: sequential + bursts, memory, DB
./test.sh bench run --profile m-smoke                 # quick end-to-end check of the same tenant
./test.sh bench capacity --profile m-capacity --mix sdk-heavy   # sustainable arrival-rate search
./test.sh bench confirm --profile m-capacity --mix sdk-heavy --rate 3.90625 --api-image bifrost-api:kind-...  # one strict confirmation
./test.sh bench drill --profile m-capacity --mix mixed --rate 1 --kind redis-empty-restart  # D4 correctness-only
./test.sh bench down                                  # delete the cluster and the images
```

Each run writes `result.json` (schema version 3) and `report.md` to
`/tmp/bifrost-bench-<utc>-<profile>-...` (or `--out DIR`). The result records
the git SHA, image IDs, Kind and CloudNativePG versions, the full profile, the
envelope arithmetic, host load and any other Kind nodes on the host.
Non-finite metrics use JSON `null` plus an explicit `non_finite_numbers`
sidecar identifying their path and meaning. An unbounded failure latency is
preserved as unbounded, never interpreted as zero. Reports decode this
representation; existing version 1/2 artifacts remain readable and unchanged.

## Topology

| Namespace | Contents | In the envelope |
|---|---|---|
| `bench-tenant` | API, client, worker Jobs, CloudNativePG `Cluster` + `Pooler`, Redis | Yes: `ResourceQuota` + `LimitRange`, every container `requests == limits` |
| `bench-infra` | Driver/sampler, fake model, fake external API, SeaweedFS | No |
| `cnpg-system` | CloudNativePG operator (pinned, checksum-verified) | No |

The tenant is the `k8s/` deployment base plus `manifests/tenant` and the
profile's resource patches (no scheduler, `maxSurge: 0`). Before anything is
applied, `envelope.py` proves the profile's maximum occupancy (fixed
components + `max_workers` worker Jobs) fits the quota; after deploy, every
admitted pod is reconciled against the quota's recorded usage.

## Safety

`guard.py` fails closed: every command checks that the kubeconfig, context,
API server address, node and Docker container are this worktree's local Kind
cluster and that the sentinel written at creation matches. Before workloads
start, an egress probe proves the `NetworkPolicy` blocks a reachable external
address from both benchmark namespaces. Every destination the tenant can reach
(database, Redis, S3, AI connections) must be a benchmark Service. Real model
providers are not used.

## Classes (`run`)

| Class | What it measures |
|---|---|
| `wf-noop` | Platform overhead: dispatch, claim, result |
| `wf-sdk` | 5 config reads, 1 table query, 1 table insert through the API |
| `wf-wait` | A 2 s simulated wait |
| `wf-cpu` | ~20 MB JSON build and parse |
| `wf-large` | ~5 MB result |
| `agent-chat` | One model turn, no tools |
| `agent-tools` | Direct mode: three workflow tools |
| `agent-codemode` | Automatic mode: the same tools through CodeMode |
| `chat-first`, `chat-followup` | The chat page's first and follow-up messages |

Times come from the platform's own timestamps; memory from the API's
`/proc/1/smaps` and the kubelet; database counters from a read-only query
issued from `bench-infra`, never from inside a tenant pod.

## Capacity (`capacity`)

An open-loop driver sends seeded Poisson arrivals at each offered rate; every
request carries a `bench_request_id`, so outcomes are reconciled from the
database even when a submission timed out after committing. Each step passes
only if throughput, backlog drift (residual block bootstrap), failure-aware
latency per class, per-class reliability and tenant-fault gates all pass
(`capacity.py`). Host contention that the tenant did not cause invalidates a
step instead (`hostmetrics.py`). The search ramps by 1.25×, bisects twice and
reports the bracket.

`confirm` is a confirmation-only capacity run: it resets and reseeds the
tenant, saves one 30-second correctness preflight using the confirmation's
first arrival seed, and stops on incomplete coverage or incorrect durable
outcomes. A passing preflight is followed by a second reset and full reseed
before the unchanged strict confirmation at the supplied rate. The preflight
does not establish capacity or warm the measured tenant. Its raw requests,
outcomes, fault counters and immediate log tails are retained separately.
The result remains `kind: capacity`, records the single entry in
`confirmations`, and sets `sustainable_rate` only when that entry passes. Use
an explicit `--api-image` when comparing builds; the result and report record
the selected tag and its local immutable Docker image ID before deployment.

Host code is stdlib only. Tests: `python3 -m unittest discover -s scripts/bench/tests`.

`redis-empty-restart` is D4 recovery evidence at a fixed 1/s, not a capacity
measurement or a claim about 50% of a sustainable rate.  It writes its initial
and final durable-request reconciliation, including every matching execution
or agent row, and records the Redis emptyDir/sentinel recovery proof.

## Observational Soak

`python3 scripts/bench/soak.py --campaign-manifest <passed-campaign.json>
--out <new-directory>` runs a two-hour soak at exactly 70% of the confirmed
mixed rate. It accepts only a fully passing immutable five-segment campaign,
rechecks its frozen profile/image inputs, and rejects rate/profile overrides
or existing output. The standalone command owns
`/tmp/bifrost-capacity-host.lock` through cleanup; do not wrap it in another
flock. Programmatic `soak.run()` callers must hold that same lock themselves.

It checkpoints raw arrivals/outcomes before teardown and samples memory,
PostgreSQL backend connection states and current S3 payload bytes/count once
a minute. It reports post-30-minute memory slopes and signed database/object
growth and generated WAL per 1,000 durably terminal executions, including
failures in that denominator. Lifecycle gaps remain visible. PostgreSQL backend
sessions are not application/pooler pool measurements; current S3 payloads
are not physical allocation, retained versions, archives or billable storage.
This is observational evidence, not another capacity certification.


## Storage and Hosting Evidence

Capacity steps now retain before/after storage frames. Soaks additionally retain
five-minute frames with database/relation/index/TOAST sizes, retained WAL,
PostgreSQL and object-store allocation, current object payload by fixed prefix,
container logs and rotation settings, retention policies, row cardinalities,
PostgreSQL waits, Pooler/Redis counters and pod network counter lifetimes.
Generated WAL, retained WAL, physical disk and object payload are different
measurements; overlapping sizes must not be added.

`execution_rates` reports actual per-second event rates separately from the
completion/arrival stability ratio. Its arrival cohort follows requests through
drain, preserving acceptance ambiguity, duplicates, failures and late successes.
`resource_usage` reports measured-window CPU/memory by component with coverage.

The offline hosting calculator uses a SHA-pinned, passing five-segment mixed
campaign and optional matched soak. An additional standalone mixed confirmation
is optional and must match the campaign's rate and frozen inputs. SDK-heavy
rates remain separate workload measurements in their benchmark reports:

```bash
python3 scripts/bench/hosting.py --scenario <scenario.json> --out <new-directory>
```

See `docs/benchmarks/hosting-scenarios.example.json` for the commercial input
shape. Example assumptions are illustrative; unknown node prices, demand,
provider spend and revenue are never silently treated as zero. The output
includes `capacity-card.json` and `capacity-card.md`, with certification status,
placement feasibility, retained storage, cost categories and missing inputs.
