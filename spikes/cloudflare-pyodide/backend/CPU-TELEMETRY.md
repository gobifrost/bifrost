# CPU time from Cloudflare: source survey and delivery path

Question: can Cloudflare's own observability deliver per-execution CPU time back into
Bifrost, replacing the `null` metrics we report today?

## What is available (docs checked 2026-11-12)

| Source | Carries CPU time? | Per invocation? | Retrieval | Notes |
|---|---|---|---|---|
| **Logpush `workers_trace_events`** | ✅ `CPUTimeMs`, `WallTimeMs` | ✅ one record per invocation | Push to R2/S3/HTTP | Includes `ScriptName`, `ScriptVersion`, `EventType`, `Logs`, `Outcome` |
| Workers Logs (observability) | ✅ shown in invocation logs | ✅ | Dashboard; Logpush/OTel to export | 7-day retention, 20M logs/month included on Paid |
| OpenTelemetry export | ⚠️ trace/log export, CPU field not documented as a metric | ✅ batched | OTLP endpoint | Cloudflare's recommended path for third parties |
| Tail Workers | ❌ documented `TailItem` has no cpu/wall fields | ✅ | push | Good for logs/outcome, not CPU |
| GraphQL Analytics (`workersInvocationsAdaptive`) | ✅ cpu time quantiles | ❌ aggregated | Query API | Fine for dashboards, not per-execution accounting |

## Correlating a record to a Bifrost execution

Trace events do not carry our execution id. Correlation options, cheapest first:

1. **Log marker + Logs field.** The child (or its supervisor) emits one structured
   `console.log({bifrost_execution_id})`. The trace record's `Logs` array carries it, and the
   same record carries `CPUTimeMs`. Join on that value. Requires a marker line per attempt.
2. **ScriptTags.** `ScriptTags` are user-defined per script; useful only if each attempt
   deploys its own script (it does not — we reuse one executor + dynamic children).
3. **Request URL.** Our attempts post to a fixed `https://attempt.invalid/`, so the URL is
   constant; not a key.

## Shape of the delivery (not yet built)

CPU time is inherently **post-hoc**: it is known only after the invocation ends, so it cannot
ride the result envelope the executor already returns. That means:

- the invocation completes and Bifrost records the result as today, with metrics `null`;
- the trace event arrives later (Logpush batch or OTel batch);
- a consumer writes CPU/wall time onto the execution record as a **separate, idempotent
  update**, never as a change to status/result/error.

That ordering matters: a late CPU update must never resurrect or re-classify a terminal
result.

## Decisive probe (next)

1. Enable observability on the executor; run one short workflow whose child logs a
   `bifrost_execution_id` marker.
2. Determine empirically whether **dynamic child** invocations appear as their own trace
   records (the docs describe dynamic dispatch for Workers for Platforms, not `LOADER.load`),
   and whether the marker survives into `Logs`.
3. Capture one record end to end and compare `CPUTimeMs` against the known wall time.

If the child does not appear as a trace record, the fallback is the supervisor's own
invocation time — a real number, but it includes marshalling and gaps, so it must be labelled
as supervisor CPU rather than workflow CPU.

## Probe result (measured 2026-11-12, spike `1968d106a`)

One real engine/SDK workflow (`json_transform` + one `config.get`), observability enabled at
`head_sampling_rate: 1`, reverted afterwards.

| Question | Answer |
|---|---|
| Per-execution CPU available? | ✅ **Yes**, at the parent invocation: `$workers.cpuTimeMs` = **1757 ms**, `$workers.wallTimeMs` = **10610 ms** |
| Dynamic child its own invocation record? | ❌ No. No script name of its own; 5 invocation records in the window were all the parent script (parent POST + 4 `DevApi` service-binding calls) |
| Dynamic child visible at all? | ✅ As a **nested OTel span** (dataset `otel`): `url.full = https://attempt.invalid/`, `durationMS = 9980`, `traceId`/`parentSpanId` pointing at the parent POST span |
| Child CPU/wall attributes? | ❌ **Absent.** The child span carries duration only |
| Log-marker correlation? | ❌ Unproven generally: no `console.log` records appear for *any* worker in this account's telemetry, including two others with identical log settings |

### What this means

- **Correlation key must come from the request, not from logs.** The parent invocation record
  carries `$metadata.trigger` and the request URL in `source.message`; giving each attempt a
  unique path (e.g. `POST /attempt/<execution_id>`) yields a deterministic join, unlike the
  unproven log marker.
- **The number we can deliver is supervisor CPU**, which includes child execution, module
  fetching and marshalling. It is a real, billable figure — it is not workflow CPU, and must be
  labelled that way.
- **Child-level CPU separation needs a Cloudflare change** (a cpu attribute on dynamic-child
  spans). Until then, per-execution attribution is the finest granularity available.

### Cost consequence of the measured number

1757 ms of CPU per fresh execution is three orders of magnitude above the "average Worker"
figure (2.2 ms). At $0.02 per million CPU-ms:

- ~1.8 ms-CPU-seconds per execution → 30 M included CPU-ms lasts **~17,000 executions/month**;
- 100,000 executions/month ≈ 175.7 M CPU-ms → ~$2.91 in CPU overage;
- the Dynamic Worker creation meter ($0.002/execution past 1,000/month) remains the dominant
  line item at **~$198** for the same 100,000 executions.

Startup CPU is billable for Dynamic Workers, so cold-start cost cannot be avoided by reuse
unless the fresh-isolate model changes.


## Consequence for Bifrost

If this works, `metrics.cpu_total_seconds` stops being `null` for Cloudflare executions —
unblocking the capacity-planning and cost-attribution gap that currently forces every estimate
to be a guess. It does not, by itself, make provider memory measurements available.

## RAM and cost basis

### RAM: not measurable, and not billed

Workers allocate a fixed **128 MB per isolate**; there is no per-invocation memory meter and no
memory dimension on the bill. Consequences:

- RAM contributes **zero marginal cost**, so there is no "memory cost basis" to recover.
- We cannot produce per-execution or per-tenant peak memory, ever, from this platform. The only
  memory signal is the `exceededMemory` outcome on a failed invocation.
- Any capacity model must treat memory as a fixed per-isolate reservation, not a measured
  quantity. Bifrost's Linux `peak_memory_bytes` semantics have no Cloudflare equivalent.

This is a durable incompatibility, not a missing integration.

### Cost: derivable from the same telemetry

Published rates (Workers Paid, verified 2026-11-12):

| Meter | Included | Overage |
|---|---|---|
| Requests | 10 M/month | $0.30 / M |
| CPU time | 30 M CPU-ms/month | $0.02 / M CPU-ms |
| Unique Dynamic Workers | 1,000 / month | $0.002 per Dynamic Worker per day |
| Workers Logs (invocation + custom) | 20 M events/month | $0.60 / M |
| Logpush | 10 M/month | $0.05 / M |

Service-binding calls incur no extra request fee, and CPU across both workers is counted once.

Per-execution cost formula, once `CPUTimeMs` is delivered:

```
cost = cpu_ms * 0.02/1e6 + requests * 0.30/1e6 + creation * 0.002
```

**The creation meter is the important one.** Cloudflare bills a Dynamic Worker per unique
(Worker ID, code) pair per day, and explicitly: `.load(code)` with no ID counts as **one Dynamic
Worker per invocation**. Our executor uses `LOADER.load(code)` precisely to guarantee a fresh
isolate per attempt, so:

- fresh isolation costs **$0.002 per execution** after the first 1,000/month;
- 10,000 executions/month ≈ **$18** in creation charges alone, versus ~$0.20 of CPU at 10 ms each;
- switching to a stable `get(id, ...)` would remove that charge but reuse the isolate — the
  exact isolation guarantee the hostile-canary suite validated.

That is a real design trade-off to surface to operators, not a rounding error: at low volume the
fresh-execution model is cheap; at high volume the creation meter dominates CPU by ~90×.

### Second-order cost of measuring cost

Enabling observability to obtain CPU time emits invocation logs, which are billed (20 M/month
included, then $0.60/M). Measurement is cheap at spike volumes but scales with executions, so a
production CPU-accounting design should sample or aggregate rather than log every attempt at
`head_sampling_rate = 1`.

