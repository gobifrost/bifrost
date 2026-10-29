# Idle database churn, step 2: before and after (2026-10-28)

Step 2 of `docs/superpowers/plans/2026-10-28-idle-database-churn.md`
(changes 5 and 3):

- **Scheduled tasks:** a finished scheduled task writes only its own state
  row, not all 23.
- **Telemetry frames:** worker telemetry frames skip the database ownership
  check while the API's lease is known to be current. Every other launcher
  message is still fenced in the database.

**Reproduce:** `./test.sh bench run --profile baseline --backend fake`, on
`main` at `4b84c9474` (before) and on this branch (after).

## Idle database load (one API, one worker, 60 s window)

| Measure | Before | After |
|---|---:|---:|
| Transactions/s | 31.9 | 28.7 |
| Rows written/s | 4.37 | 2.52 |
| `scheduler_task_states` rows written | 121 | not in the top tables |
| `execution_workers` scans | 730 | 646 |

The plan predicted about 3 transactions/s for this step; the measured
saving is 3.2.

**Noise:** idle WAL measured 2.0 KiB/s before and 4.7 KiB/s after, while rows
written fell 42%. Idle WAL depends on where a checkpoint lands in the window
(full-page images), and earlier pairs of runs on identical code differed by a
similar amount, so it isn't read as a change here.

## Runs

- **Results:** all 800 runs succeeded on both builds. `wf-noop` queue-to-done
  p50 was 126 ms before and 128 ms after.
- **Transactions per run:** unchanged within one or two per class.
- **Latency:** other classes moved in both directions because the host was
  shared with a Playwright run during both measurements, so latency here is
  not a comparison.
