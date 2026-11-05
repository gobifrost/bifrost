# Connection Reuse After the SDK Query Repairs

Continue the approved local performance investigation without merging the three SDK repairs or changing platform defaults. Both arms run application revision `4747fc458a7ae5af064c58bbc4f6618086d61129`, using the already verified immutable candidate image. No production change, PR or merge is part of this comparison.

## Question and Intervention

The repaired SDK path still opened 382 main-pool connections in a 60-second counter window at 8/s. The main pool allows 15 simultaneous connections, but retains only five; returned overflow connections are discarded. Test whether retaining the existing main-pool allowance removes that churn and improves completed work.

Declare exactly two SDK-only windows at 8/s: main pool 5 retained + 10 overflow, followed by 15 retained + 0 overflow. Dispatch remains 4+6 and liveness 1. The per-API peak ceiling remains 26. The all-pool idle retention capacity can increase from 10 to 20; record that cost explicitly. This comparison evaluates reuse, not a deployment-wide connection-budget guarantee.

## Controls and Measurements

Use the same frozen capacity harness `2e3e9ca2b`, guarded local Kind cluster, one API process, six warm workers, direct PostgreSQL, SDK workload, catalog seed and helper resource amendment as the saved query-repair experiment. Both API and workers use the same image in both arms. Replace idle worker Jobs symmetrically before each arm and verify all six images. Preserve seeded history between arms.

Each arm has 30 seconds warm-up, 90 seconds measurement and 60 seconds drain, with at most 120 seconds additional recovery before the next arm. SQL/pool counters run for about 60 seconds starting 45 seconds after the action starts. A 125-second database observer records backend occupancy. Retain whole-cohort deadline correctness, event throughput, latency/missing outcomes, backlog, pool events, physical connection creation, database clients, API/DB/Redis/worker CPU and memory, storage, validity and fault evidence. Label busy connection snapshots separately from idle retention capacity. The previous connection-budget probe already measured the post-burst retention difference using this unchanged pool factory; this two-window comparison does not repeat that budget test.

No timed-window replays or tuning the declared rates to obtain a passing result. A fixed overload window is not a sustainable-capacity certification. Preserve host-contaminated or incomplete observations with their flags. Reuse the prior passing focused tests and source/image checks because no runtime source changes in this experiment; verify the unchanged source and a stopped owned test stack before load.

## Separate Read-Only Review

Inspect at most three shared SDK paths for connections held across non-database awaits or repeated session checkout. Distinguish physical connection creation from pool checkout and query count. Retain token epoch/run-liveness checks and access evidence. Record source hypotheses separately from measured findings; do not implement another platform change in this comparison.

Clean up owned benchmark namespaces, save immutable raw hashes and a concise result baseline, then report gains, retained-connection cost and the next evidenced opportunity.

## Completed Checkpoint

Both declared windows completed on the same verified image. Main-pool openings fell from 388 to zero in the counter window, and all API-pool openings fell from 389 to three. Observed correct event throughput rose from 3.12/s to 6.39/s; the retained arm completed 725/725 measured arrivals within 60 seconds. The current-setting arm retains its external-CPU invalidity flag, so the exact speed ratio remains descriptive. The retained arm has no validity flags. Both drained and all owned benchmark resources were removed.

The 26/API simultaneous allowance stays fixed; all-pool idle retention can rise from 10 to 20. No runtime source, installation default, PR or merge changed. The [results and further source hypotheses](../benchmarks/2026-11-02-sdk-connection-reuse.md) preserve the cost, outcomes, limitations and reproduction evidence. No merge is required to continue local work.
