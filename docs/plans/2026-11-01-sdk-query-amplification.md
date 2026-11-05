# Reduce SDK Query Amplification

The approved scope is to reduce repeated SQL and API work in the measured SDK path. Keep database pool sizes, authorization epoch/run-liveness checks, resource limits, and workflow behavior unchanged. Work stays local for review; this task does not authorize publishing or merging a PR.

## Changes

1. Record an already-enforced access decision using its captured identity and trace. Load current authority only for notes requiring a new judgment; retain audit attribution, deduplication and unresolved-object behavior.
2. Use targeted reads for one config key and represent empty merged config results in the existing Redis cache. Preserve scope precedence, external restrictions, secret checks, versioned invalidation, and create/update/delete visibility. Inspect concurrent cache fills and invalidation.
3. Route Redis wake hints to relevant workflow, platform, service or package consumers. Preserve commit-before-wake ordering, durable Postgres ownership/claims, real package notifications and existing reconciliation sweeps.

## Verification

Run focused existing and new unit coverage for all three shared paths, config/audit live boundary tests and relevant worker/package tests; run API pyright/ruff. Do not change public DTOs or authorization semantics. Review publisher/listener consumers before completion.

Compare current main (457134dff) with the combined candidate using four declared SDK-only windows: current/candidate at4/s and current/candidate at8/s, one API and six warm workers. Retain main pool5+10, dispatch4+6 and liveness1 throughout. Use the same counter hook, fixture, resource envelopes. Switch both API and worker images to the arm’s immutable revision and verify all six ready workers before load, so both publishers and listeners exercise the typed wake contract. Replace idle worker Jobs between arms after draining prior work. Save statement fingerprints/counts, whole-cohort outcomes, latency, event-time completions, backlog, pool occupancy, resources, fault deltas and validity flags. These fixed windows do not determine maximum capacity or certify linear worker scaling.

Use the frozen capacity harness and guarded local Kind cluster. Precreate the write-table fixture outside offered load, apply the previously recorded helper-only scheduling amendment, capture action results before probe validation, and preserve failures without replaying windows. Clean up owned test/benchmark resources before reporting results.

## Completed Checkpoint

The three repairs are implemented in local commit `4747fc458`; 381 distinct unit cases, nine live cases and API quality checks passed. All four declared windows completed and owned resources were cleaned up. At 8/s, correct event throughput rose from 1.36/s to 2.61/s with unchanged pools, but the API still saturated and most arrivals missed their deadline. The candidate 4/s arm retains an external-host-CPU invalidity flag. See the [results, limitations and reproduction evidence](../benchmarks/2026-11-01-sdk-query-repairs.md). The remaining API overload/connection churn needs a separately scoped review; no PR or deployment is authorized by this checkpoint.
