# Performance and capacity benchmark

Repeatable measurements of one Bifrost build inside a constrained hosting
envelope, on the worktree's own Kind cluster running the production images.
Methodology (reviewed): `docs/plans/2026-10-29-capacity-benchmark-methodology.md`.
Earlier design: `docs/superpowers/specs/2026-10-27-performance-benchmark-suite-design.md`.

```bash
./test.sh bench up                                    # cluster, images, CloudNativePG, tenant (profile m)
./test.sh bench run --profile m                       # execution classes: sequential + bursts, memory, DB
./test.sh bench run --profile m-smoke                 # quick end-to-end check of the same tenant
./test.sh bench capacity --profile m-capacity --mix sdk-heavy   # sustainable arrival-rate search
./test.sh bench confirm --profile m-capacity --mix sdk-heavy --rate 3.90625 --api-image bifrost-api:kind-...  # one strict confirmation
./test.sh bench down                                  # delete the cluster and the images
```

Each run writes `result.json` (schema version 2) and `report.md` to
`/tmp/bifrost-bench-<utc>-<profile>-...` (or `--out DIR`). The result records
the git SHA, image IDs, Kind and CloudNativePG versions, the full profile, the
envelope arithmetic, host load and any other Kind nodes on the host.

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
tenant, then applies the same strict confirmation criteria at one supplied
rate. Its result remains `kind: capacity`, records the single entry in
`confirmations`, and sets `sustainable_rate` only when that entry passes. Use
an explicit `--api-image` when comparing builds; the result and report record
the selected tag and its local immutable Docker image ID before deployment.

Host code is stdlib only. Tests: `python3 -m unittest discover -s scripts/bench/tests`.
