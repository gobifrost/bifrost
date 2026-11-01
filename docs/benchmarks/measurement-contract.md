# Namespace Benchmark Measurement Contract

The benchmark answers how much correct work a declared namespace can sustain,
what its users experience, and what resources must be paid for. A smaller
footprint is an improvement only with the corresponding latency, capacity,
correctness and recovery results. The accepted settled worker baseline remains
approximately 277 MiB; this is a comparison point, not a memory target to trade
against fast startup.

## Measurements and Denominators

| Purpose | Required evidence | Interpretation |
|---|---|---|
| Execution capacity | Scheduled, sent, HTTP accepted, durably created, terminal, correct, and correct by deadline per second, by class and workflow/agent family | Event counts use the half-open measured window. Arrivals from that window are separately followed through drain. Late successes consume resources and miss the latency contract. |
| Nested work | Actual platform terminal counts by workflow/tool type and agent status; execution log row count | Agent tool executions are real work, but are not additional externally offered requests. Status alone does not prove correct output. |
| App responsiveness | Action, submission, subscription, terminal/result observation and rendered result; sequential and concurrent variants | SDK inline, immediate-GET and WebSocket paths remain distinct. Database completion timestamps do not measure browser result delivery. |
| Startup and imports | First use and repeated use, dependency fixture hashes, worker/template lifecycle, import traces where instrumented | A synthetic graph matching file count/bytes does not reproduce Halo code or provider behavior. Spans may overlap and must not be added blindly. |
| Runtime resources | Simultaneous tenant charged memory and working set, per-component CPU seconds/cores, memory, throttling, worker count and pod UIDs | Sampled peaks are lower bounds. Component peaks from different times are not additive. Missing sampling coverage is explicit. |
| Worker scaling efficiency | Fixed-worker-count series with unchanged per-worker resources, shared-service allocations, runtime and workload; accepted successful rate and latency per count; total namespace requests and measured consumption | Report speedup `throughput(N) / throughput(1)` and worker efficiency `speedup / N`, plus cost/resource efficiency. Increasing an adaptive profile's memory envelope alone does not isolate worker scaling. Locate shared-service saturation rather than assuming linear gains. |
| Scale from zero | Zero versus one warm worker at matching maximum count and resources; first request, concurrent burst, worker readiness, queue drain, scale-down, idle/startup/loaded consumption | Report cold-start response penalties and idle savings separately from warm throughput. Source defaults, deployment overrides and observed runtime policy are distinct evidence. No production default change is authorized by the benchmark. |
| Idle cost | Clean/seeded and settled process/container memory; idle database transactions, writes and generated WAL | Preserve process private/PSS, RSS, working set and charged memory as different accounting methods. |
| Data growth | Database, relation/index/TOAST sizes, row counts, dead tuple estimates, vacuum timestamps, generated and retained WAL, PostgreSQL disk allocation | Generated WAL is a flow; retained WAL and relation/disk bytes are stocks. Database, indexes, WAL and data-directory allocation overlap. |
| Objects and logs | Current object count/payload by fixed prefix, local object-store disk allocation, retained container log bytes and rotation settings | Visible payload excludes old versions/delete markers. Local allocation is not cloud provisioned/billable storage. Highly repetitive output compresses differently from representative data. |
| Retention | Effective run/audit/artifact policies, cleanup job receipts, pre/post counts and sizes, isolated expiry rehearsal | A declared policy is not evidence that cleanup ran. DELETE and autovacuum need not reduce filesystem allocation. Retained backups remain separate. |
| Connection pressure | PostgreSQL states/waits, Pooler clients/servers/waits, Redis clients/memory/evictions | PostgreSQL backends are not application connection pools. Missing application checkout instrumentation remains unmeasured. |
| Network | Pod RX/TX/error counters with UID lifetimes; declared external payloads | Inter-pod traffic is not billable internet egress. Do not subtract across replacement or counter reset. |
| Reliability/recovery | Accepted-request union, terminal/lost/duplicate records, append-only effects, exact fault target/time, readiness and recovery bounds | A failed or unexercised drill remains a failure or missing evidence. Keep correctness-only and HA tiers separate from capacity. |
| Build budget | Source/dependency and SDK hashes, normal deployment PlatformJob, duration and requested/observed resources | Build preparation is outside steady runtime timing and must still appear in hosting cost. |

Capacity acceptance continues to use the frozen failure-aware latency,
reliability, backlog, tenant-fault and host-validity gates. Descriptive rates and
latency distributions supplement those gates. A short smoke, incomplete sample
or observational soak cannot certify a rate. The mixed confirmation plan uses
five immutable one-hour segments, with pooled per-class reliability and all
other gates retained per segment. Storage probes execute in the API pod and add observer CPU/memory during the soak; compare runs with the same probe cadence. Capacity storage frames sit outside the measured window. The two-hour soak runs at 70% of a passing
mixed rate and records minute metrics plus five-minute storage frames.

Quota-denial Events are sampled by UID before traffic, throughout load and
reconciliation, and at a final read. The ledger retains each UID's last count
after its Event expires and counts new UIDs and count increases. It stores only
allowlisted identity/count observations. A probe failure, malformed observation,
per-UID rollback or sampling gap over 30 seconds invalidates the window; a real
positive fault count still fails its gate. Events created and removed between
observations remain outside sampling coverage. Other pod-counter regressions
also invalidate measurement. Never clamp a negative endpoint delta to zero or
report it as a tenant failure.

## Reproducibility and Comparisons

Every run retains its resolved profile, workload version/source hash, catalog
and document cardinality, seeds, windows, runtime image identities, harness
revision and raw failures. Actual infra image identities are recorded separately
from the selected tenant API/worker image. Record pod UIDs and sampling coverage.
Never overwrite a failed attempt or select extra runs until an apparent pass.
Explain the exposing condition and repair before a new attempt.

The private saved portal baseline and its offline verification recipe are linked
from [the baseline guide](2026-10-31-benchmark-baseline-and-completion.md). Public
synthetic fixtures provide portable controls without copying customer source or
inputs. Before/after comparisons must name both artifacts, report absolute and
percentage changes, and distinguish controlled differences from external Halo
variability. Original production canaries and controlled local measurements
remain separate evidence.

## Hosting Model

Capacity cards consume SHA-pinned artifacts from matching profile, workload,
harness and immutable runtime images. They retain failed/incomplete evidence
without certifying it. Low/base/peak demand is an explicit customer assumption;
saturation multiplied by a month is not expected demand.

Calculate placement from actual allocatable CPU/memory, other reservations,
maximum permitted tenant requests, pod/PVC/storage limits and redundancy. A
namespace fitting its quota does not establish that it fits a cloud node. A zero
density is infeasible, not free. Price whole billed node time, idle/spare
capacity, provisioned volumes, retained objects/backups, control plane, load
balancing, builds, observability and network. Avoid charging in-node PostgreSQL
compute twice. Keep provider/model spend separate and show combined cost only
when its inputs are known.

Retention projections state initial stock, measured growth basis, demand,
retention period, backup/version assumptions and billing units. Short local
observations do not establish a permanent linear growth rate or backup ratio.
Unknown SKU price, request fees, customer demand or revenue remains an explicit
missing input; cost and margin remain unpriced where necessary. Local Kind
results support regression and allocation comparisons. Customer quotations
require cloud calibration and dated actual commercial inputs.
