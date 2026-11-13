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

## Consequence for Bifrost

If this works, `metrics.cpu_total_seconds` stops being `null` for Cloudflare executions —
unblocking the capacity-planning and cost-attribution gap that currently forces every estimate
to be a guess. It does not, by itself, make provider memory measurements available.
