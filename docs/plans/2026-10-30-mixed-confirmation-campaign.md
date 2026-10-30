# Mixed Confirmation Campaign

Status: implementation and focused unit verification complete. **Not measured**.

This campaign can certify one frozen mixed-workload rate without holding the
shared host for one continuous five-hour confirmation. It does not change
runtime behavior, the existing capacity search, or `search.confirm`, which
remains the single-window confirmation path.

It complements the [capacity benchmark methodology](2026-10-29-capacity-benchmark-methodology.md)
and records evidence for the [hosting baseline ledger](../benchmarks/2026-10-30-hosting-baselines.md).
A certified result is one input to capacity planning; it is not by itself a
customer cost, density, availability, or SLA claim.

## Fixed campaign

The campaign has exactly five independently reset segments. Each has:

- a 90-second warm-up;
- a 3,600-second measured window;
- a 120-second drain;
- the frozen mixed offered rate of **2.236 executions/s**;
- the same resolved profile, runtime image provenance, harness revision, workload
  mix, thresholds, and fake-service configuration;
- a distinct, predeclared deterministic arrival seed.

The five frozen seeds use the existing shared confirmation rule:
`search.confirmation_seed(profile.capacity, index)`, for the exact indices
`0..4`. For the current profile that is `profile.capacity.seed + 1000 + index`.
The manifest records those derived values before the first reset; its example
seed values below are illustrative, not a second randomization policy.

Before the first reset, the harness writes an immutable campaign manifest with
an atomic create. Its identity includes the resolved-profile hash, profile
name, image provenance and digests, harness source revision, mix, rate,
threshold source and values, the exact five indices and seeds, and each
segment's output path. A manifest already present is never overwritten.

A `confirm-segment` operation may run only one declared, unstarted index. It
takes the campaign manifest and index; it cannot choose a rate, profile, image,
seed, output path, or replacement index. Before it changes the cluster, it
verifies every frozen input against the manifest, resets and deploys the
tenant, runs the declared segment, saves its complete result atomically, then
tears the tenant down and releases the host lock. That release deliberately
leaves gaps for authentication work between segments.

The external supervisor owns the shared host flock for exactly one
`campaign-preflight` or `confirm-segment` invocation and keeps it through that
operation's verified cleanup. The harness does not acquire a nested flock, so
it cannot deadlock an existing supervisor; the next operation is a separate
invocation after the supervisor releases that lock.

The campaign never turns an incomplete segment into another attempt. A partial,
missing, or invalid segment remains in the manifest/result as such. The campaign
runs all five segments when it is safe to do so even after a failure; an
operator safety stop makes the campaign incomplete rather than creating a
replacement segment.

Before any segment, the one `campaign-preflight` operation uses the same
immutable manifest to perform the existing 30-second representative correctness
prefix after its own fresh reset and full seed. It retains raw sent and durable
evidence, bounded logs, and verified cleanup in the campaign evidence
directory. It runs every configured mixed class with the shared confirmation
prefix seed. A failed, partial, or invalid preflight is retained and blocks all
segments; it is never overwritten, retried, or replaced. A passing preflight
does not establish sustained capacity and is followed by a new reset/full seed
for each one-hour segment.

## Gates

Every segment keeps the existing non-reliability capacity gates unchanged:

- at least 400 observations for every configured class with at least 10% mix
  weight;
- offered-to-correct throughput;
- failure-aware queue and end-to-end p95 latency, including their confidence
  intervals;
- strict backlog-drift gate;
- zero tenant faults; and
- host and infrastructure validity checks.

Segment reliability is reported as `pending_pooled`, while preserving raw
arrivals, correct completions, failures, unfinished requests, per-class
durable outcomes, and the complete existing step verdict. It does not receive
a weaker per-segment reliability pass.

After exactly five completed segments, a separate read-only aggregate command
verifies the manifest and result hashes, includes every declared segment once,
and pools the raw counts by eligible class. It certifies reliability only when
each class has actual pooled `n >= 2995` and its exact one-sided
Clopper-Pearson 95% upper failure bound is at most `0.001`. Expected arrival
counts never certify a class. The denominator is the existing raw
offered-arrival record for each request scheduled inside the measured window;
an unsent or otherwise non-correct arrival is a failure. The aggregate also
retains submitted, HTTP-accepted, and durably completed counts separately for
interpretation. A contradictory `correct` record without a send timestamp
invalidates the campaign rather than increasing its reliability evidence.

The aggregate passes only when all five segments pass every non-reliability
gate and every pooled class passes reliability. A failed non-reliability gate
fails the campaign. A partial, missing, or invalid segment produces
inconclusive/invalid. Pooled reliability uses the existing
`capacity.reliability_evidence`: an observed failure fraction above the limit
fails; an upper bound above the limit with an observed fraction within it is
insufficient/inconclusive. Too few observations cannot pass. There
is no optional stopping: no pass after an early subset, no replacement,
rerun-until-green, failed-segment exclusion, sixth segment, or extension.

## Evidence shape

The campaign result should retain both the immutable plan and each complete
segment result:

```json
{
  "campaign_manifest": {
    "plan": "mixed-confirmation-go-5-v1",
    "segments": 5,
    "measured_window_s": 3600,
    "rate_per_s": 2.236,
    "resolved_profile_sha256": "...",
    "image_provenance": {},
    "harness_git_sha": "...",
    "indices": [0, 1, 2, 3, 4],
    "seeds": [20262029, 20262030, 20262031, 20262032, 20262033],
    "output_paths": ["..."]
  },
  "segments": [
    {
      "index": 0,
      "seed": 20262029,
      "status": "pass|fail|invalid|partial|inconclusive",
      "step": {}
    }
  ],
  "pooled_reliability": {
    "wf-noop": {
      "observations": 0,
      "failures": 0,
      "upper_95": null,
      "limit": 0.001,
      "status": "pass|fail|inconclusive|invalid"
    }
  },
  "overall": "pass|fail|inconclusive|invalid"
}
```

The saved segment `step` remains the existing raw result shape so the report
can show each segment's gates, resource samples, outcomes, and validity
evidence without a second runner or a parallel measurement format.

Each segment also reuses direct confirmation's idle database window and shared
container/process snapshot before warm-up, retains the database/WAL counter
window spanning warm-up, measurement and drain, and saves the post-drain
snapshot before teardown. Image provenance, host resource samples and all
partial request/outcome checkpoints remain with that segment. A post-drain
snapshot does not assert that unfinished work is quiescent; outcome/backlog
evidence must be read alongside its memory figures.

## Claim and limits

A pass supports this precise claim: five predeclared, fresh-state,
one-hour measured windows at 2.236/s met the frozen non-reliability gates, and
their pooled actual per-class counts met the stated 95% 0.1% failure bound.

The pooled binomial interval assumes the reset windows are sufficiently
stationary and exchangeable under the frozen profile, image, harness, and
workload contract. The campaign does not prove that assumption, statistical
independence, a general customer population, continuous five-hour behavior, or
an SLA. It does not pool latency percentiles or backlog trajectories across
resets; each window must pass its own latency and drift gates.

The methodology's separate two-hour soak at 70% of a confirmed mixed rate
remains the continuous-run check for memory slope, unfinished work,
connections, and database/WAL/object growth. It is distinct evidence and does
not establish five continuous hours at the certified rate.
