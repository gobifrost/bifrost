# Worker Scaling and Idle-Floor Experiment

Status: tooling under review; no scaling observations yet. This is an isolated
benchmark of the captured, unmodified production image. It does not authorize
platform fixes, new PRs, deployments or production policy changes.

The two series answer different hosting questions. Fixed worker counts measure
how much additional successful throughput the same workload gains per worker.
An elastic pool measures the latency and resource cost of starting workers and
keeping them alive between bursts.

## Controls

All arms retain the full catalog and the same shared components from the 4 GB
profile: API 1250m/640Mi, client 100m/32Mi, Redis 150m/64Mi, PostgreSQL
1000m/768Mi, and Pooler 100m/64Mi. Every worker gets 650m/1024Mi. Standard and
largest worker sizes are equal; the idle timeout stays 300 seconds.

Use one common namespace quota, 5200m CPU and 5664Mi memory, large enough for
four workers. The quota stays the same across arms; actual admitted requests,
Pod lifetimes and measured use are recorded separately. This is larger than
the original 4 decimal GB envelope. Node images remain cached, so a cold arm
measures Pod/process startup on an existing node.

Read the effective stored policy through the supported authenticated
`GET /api/admin/kubernetes/execution` before and after measurement. Retain only
warm floor, ceiling, standard/largest memory and idle timeout. A requested
setting is not proof that the controller applied it.

## Elastic Series

Compare `(warm, maximum)` pairs `(0,1)/(1,1)`, `(0,2)/(1,2)`, and
`(0,4)/(1,4)`. Each arm gets a fresh tenant. After all setup, observe the idle
floor; fixture creation and preflights may already have started workers.
Zero requires no live worker DB states, no live worker Pods, and observed zero
unfinished work. Warm floors require matching Ready Pod/worker identities,
fresh heartbeats and usable idle admission budgets.
Terminal Pod metadata may remain for the controller's 600-second Job TTL;
retain it as evidence and count it as stopped only when container observations
also show no running container. Missing container state remains unknown.

Measure one `wf-wait` request, return naturally to the idle floor, then submit
the same concurrent burst of 32 requests. The existing deterministic workflow
waits two seconds and returns `{"waited_s": 2}`. Reconcile every unique arrival
with durable records and validate that exact result and its 60-second
send-relative deadline. Preserve rejected, missing, duplicate, late and failed
requests. Observe the return to the configured floor after the burst without
shortening the cooldown or killing workers.

Report first-request and burst timings, actual worker launch/Ready/retirement
observations, queue drain, Pod lifetimes, admitted resource allocation, and
sampled idle/startup/load resources. A ceiling of four does not establish that
the burst required or reached four workers. Heartbeat `active_runs` is reported
state, not an exact active-child trace. Sequential probes and their sampling
gaps bound the timing precision.

## Fixed Series

Use `warm=maximum=N` for N of 1, 2 and 4. The initial scaling workload is the
existing SDK-heavy mix: five config reads, one table query and one insert per
request. This deliberately includes shared API/database work; its scaling
curve can reveal a shared-service limit. It is separate from the representative
mixed-load hosting campaign.

Each arm begins with the existing correctness preflight. Each offered-rate
trial then gets a fresh reset/seed, effective-policy readback, and an observed
exactly-N Ready idle floor. Begin at 1 request/s, grow by 1.25, and use two
bounded bisections after the first failure. Use the unchanged ramp windows,
arrival seed and gates. A declared 16 request/s ceiling bounds the search; a
passing ceiling is a lower bound. An invalid or inconclusive window stops that
arm without a replacement or automatic retry.

Confirm only the highest passing ramp rate, once, after another fresh reset.
Use the existing strict latency, throughput, backlog, reliability, host and
tenant-fault gates. A failed confirmation stays a failure; do not try lower
rates until one passes. Every sampled lifecycle checkpoint must retain the
configured Ready count and matching worker/Pod identities. Preserve raw
windows before checking collector validity.

Compare confirmed successful throughput against N=1, including speedup,
speedup/N, throughput per total allocated CPU/memory, and observed CPU cost per
correct completion. Keep offered rates, successful event rates, latency and
reliability separate. Do not interpolate an accepted capacity from failed,
inconclusive or host-invalid windows. A flat curve is evidence to investigate
the shared API, database, admission or scheduling path; it is not by itself a
reason to add worker resources.

## Hosting Interpretation

Zero warm workers still retains the API, database, Redis, storage and shared
services. Retain the startup/cooldown allocation between bursts. Monthly demand,
burst frequency and idle-gap distribution are required to project worker
lifetimes. Worker retirement reduces reserved capacity; a lower node bill also
requires placement and node-scaling evidence.

The captured source defaults are warm 1 and maximum 6, not a verified live
policy. The earlier optional live read did not complete. Keep the deployed
policy unknown until a successful read provides evidence.

Validation and execution must wait for the existing capacity, stock-retention
and sampler-validation jobs to finish under the shared host lock. The active
benchmark checkout and its imported modules stay frozen throughout those jobs.
