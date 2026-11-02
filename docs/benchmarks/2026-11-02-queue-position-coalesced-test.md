# Queue-Position Coalesced Refresh Diagnostic

This declaration defines a benchmark-only N2 diagnostic for the candidate image
`bifrost-api:kind-a74e45295998-queue-position-coalesced-r1`. It targets the
8-workflows/second cohort after the required 2/s and 4/s warm-state cohorts.
The measurement is not a production deployment, capacity claim, or certificate.

The candidate is expected to move queue-position refresh out of admission and
claim while preserving durable queue state, normal Pending/Running/terminal
status events, current execution authorization, and the UI's queued indication.
The test must not be run until the candidate's exact runtime source root and
branch are recorded with the result provenance; they are forthcoming at this
declaration stage.

Run the bounded N2 arm with a fresh output directory and the archived fixture:

```bash
python3 scripts/bench/queue_position_coalesced.py \
  --owner-root /home/jack/GitHub/bifrost/.worktrees/bench-capacity \
  --out <fresh-candidate-output> --table-fixture <archived-fixture.py>
```

The wrapper delegates to the canonical `resource_curve.py` runner and retains
its host lock, reset/reseed flow, runtime provenance, arrival seeds, SQL/storage
captures, validity gates, and cleanup. It fixes the base profile to
`hosting-8gib-fixed-workers-1`: two fixed 650m/1GiB workers, Redis at
100m/64MiB, two API replicas at 750m/512MiB, and PostgreSQL at
750m/1536MiB. Cohorts run at 2/s, 4/s, then the 8/s diagnostic target, with a
30-second warmup, 300-second measurement window, and 60-second drain. The
existing first non-pass, invalid, or inconclusive stop rule applies; there is
no 16/s trial or wider matrix.
