# Two-API Hosting Sizing Campaign

User authorization: size useful runtime capacity inside 4 decimal GB and 8 GiB, with two APIs, maximum useful workers and reserve. This campaign changes local benchmark fixtures only. It does not authorize platform fixes, production deployment, new installation knobs or platform PRs.

## Frozen Resources

Both profiles retain a four-CPU namespace ceiling. Two APIs each receive 750m CPU / 512 MiB. PostgreSQL receives 750m CPU and 768 MiB (4 GB) / 1536 MiB (8 GiB). Redis is 100m / 64 MiB, Pooler 100m / 128 MiB including its init reservation, and frontend 50m / 64 MiB. The 4 GB workers are 500m / 512 MiB each, maximum two. The 8 GiB workers are 250m / 1024 MiB each, maximum four. Final allocations reserve 500m CPU and about 0.73 / 1.25 GiB memory. These are candidates until actual admission, startup and loaded health are verified.

The larger profile uses its additional memory for more workers and database cache. Its CPU remains four cores, so a worker has less CPU and count alone does not imply linear CPU throughput. An 8 GiB two-worker / 500m-per-worker bridge compares the same total worker CPU with the four-worker / 250m arrangement.

Runtime source: merged commit 3603b2d3bd172eadf30a8516cc8fa6afc14f1ad5, immutable source worktree. Benchmark source starts from frozen harness 2e3e9ca2bb1c72052098c915d001fc9602a1c082 with only reviewed sizing profiles and multi-API measurement support. The existing local Kind owner remains the bench-capacity worktree; all operations retain its private kubeconfig/context/sentinel guard. Never use the default Kubernetes context. Own the canonical host lock through deployment, load and cleanup. No parallel timed loads.

## Finite Matrix

Fixed-worker SDK diagnostic cells: 4 GB at one/two workers; 8 GiB at one/two/four workers; and the 8 GiB two-worker CPU bridge. Each uses a fresh seeded tenant with 1,100 workflows, 701 tools, 50 providers and 200 documents. Before throughput work, validate normal workflow, config/table, wait, CPU, large-result and agent paths at the final two sizes; preserve failures and resource evidence.

Use bounded SDK offered rates from 2 through 16/s, factor two and one bisection after the first failure. Retain the existing latency, reliability, fault, backlog and host-validity gates. Stop an arm on invalid or inconclusive evidence without replacement. Diagnostic ramps have at least 300 seconds and the existing observation count per gated class. Only the highest passing rate at each final worker ceiling receives one fresh strict confirmation with existing 0.1% reliability and strict backlog gates. Never descend after a failed confirmation until a rate passes. A passing search cap is a lower bound, not a maximum.

Then exercise the existing representative mixed workload at 70% of the final SDK passing rate, capped at 4 external requests/s, with enough observations for its existing ramp gates. Report its own per-class successful rates and verdict; do not call it a strict mixed-capacity certificate. Fake model/provider delays and nested tool work remain separately labeled.

For both final sizes, compare warm minimum zero and one with unchanged maximum and 300-second idle cooldown: verified idle floor, first arrival, return to floor, 32-request wait-workflow burst and return to floor. Retain actual worker UIDs, launches, readiness, lifetimes and effective policy. Cached-node startup excludes node provisioning and image download.

At 70% of a confirmed SDK rate, delete exactly one captured API Pod during a bounded correctness run. Prove the other captured UID remains Ready, the deleted UID is replaced, both new Ready identities run the selected image, and requests through the Service reconcile. This is availability evidence separate from capacity. Preserve transient errors rather than adding retries.

## Deliverables

For each size: tested useful worker ceiling, confirmed or bounded SDK workflows/s, mixed-load observations, latency/queue/drain, per-component and total CPU/memory, throttling/health faults, SQL/connection pressure, database/WAL/log/object growth, cold/warm burst costs and API replacement behavior. Record hard allocation separately from observed use and reserve. Preserve every invalid/failed/incomplete cell, immutable profiles/images/source hashes, raw records and cleanup receipts. No pricing, arbitrary-customer workflow or full-HA claim follows from this local synthetic campaign.

## Bounded Resource Alternative

The initial 4 GB CPU-heavy probe produced `Runner memory pressure` failures on
512 MiB workers. Keep the original arm and its failure. Add exactly one resource
alternative: one 1024 MiB / 1000m worker, with the same total worker CPU, memory,
two APIs and namespace reserve as two 512 MiB / 500m workers. Run its terminal
probes and the same SDK ramp/strict confirmation. This is a fixture allocation
comparison, not a platform fix. Do not describe two 512 MiB workers as comfortable
for the heavier classes based on SDK-only successes. If the larger worker also
fails, preserve that limitation without another resource search.

Use that one-larger-worker 4 GB alternative for the mixed, warm-zero/warm-one
(maximum one) and API replacement arms. Keep the two-small-worker SDK diagnostic
and its failed memory probe. The originally declared 4 GB two-small-worker
elastic/mixed/availability follow-ons are superseded by this explicit resource
alternative; their unrun status must remain visible. The number of elastic arms
stays four. The 8 GiB follow-ons keep their original four-worker allocation.

## Host-Invalid Follow-up Confirmation

Higher host-invalid diagnostic steps remain invalid and stop their original
searches. The three originally planned strict confirmations were not run. A
separate, fresh strict check may confirm the highest valid diagnostic pass at
4/s for each final profile. It establishes a lower bound only: it does not
promote an invalid 6/s step, establish a maximum, retry a confirmation, or
descend after a failed confirmation. Keep the original R3 unrun entries and
their raw source artifacts unchanged. Run two-API availability only after this
new strict check passes.

R3 checkpointing rewrote result artifacts of about 20 MB for each observer
frame. The confirmation harness now appends and fsyncs every raw timeline frame
but saves the full result only when the checkpoint phase changes; explicit raw
result and final saves remain durable. This reduces benchmark observer overhead
only. It is not a runtime performance result. This amendment authorizes no
platform change, PR, or deployment.
