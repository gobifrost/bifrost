# Queue-Position Publication Causal Test

This is a benchmark-only causal comparison. It evaluates the candidate runtime
`bifrost-api:kind-a74e45295998-queue-position-off-r1`, whose only intended
runtime difference is a no-op `publish_queue_positions` path, against the
saved N2 Redis-100m control at
`/home/jack/bifrost-bench-evidence/runs/resource-curve-20261102-r2/result.json`.
It is not a platform fix, deployment, or certification.

Run the bounded N2 arm with a fresh output directory and the archived fixture:

```bash
python3 scripts/bench/queue_position_causal.py \
  --owner-root /home/jack/GitHub/bifrost/.worktrees/bench-capacity \
  --out <fresh-candidate-output> --table-fixture <archived-fixture.py>
```

The wrapper delegates directly to `resource_curve.py`, retaining its canonical
host lock, reset/reseed flow, runtime provenance, arrival seeds, SQL/storage
captures, validity gates, and cleanup. It fixes the base profile to
`hosting-8gib-fixed-workers-1`, the allocation to two 650m/1GiB workers, and
rates to 2/s, then 4/s, then 8/s. The 2/s and 4/s cohorts must precede 8/s to
match the control's warm-state history. It preserves the 30-second warmup,
300-second window, 60-second drain, Redis 100m, two API replicas at 750m/512Mi,
and PostgreSQL at 750m/1536Mi. The existing first non-pass/invalid/inconclusive
stop rule applies; there is no 16/s trial or further matrix.
