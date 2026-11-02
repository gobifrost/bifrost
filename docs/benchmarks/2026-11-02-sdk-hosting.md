# SDK Hosting Comparison After Query and Pool Repairs

At the same hosting allocations and a 4/s offered SDK workload, both saved envelopes completed every measured arrival correctly. The candidate reduced tail latency and API CPU use; worker memory stayed essentially flat. This is an efficiency gain within the existing resources, not a certified new maximum rate or a worker-memory optimization.

| Envelope | p95 Before → After | API CPU, Cores Before → After | Main Connections Opened Before → After, ~60s | Correct Within Deadline, Both Arms |
|---|---:|---:|---:|---:|
| 4 decimal GB | 3.178s → **0.567s** | 0.718 → **0.520** (27.5% lower) | 218 → **0** | 472/472 |
| M / 8 GiB | 2.412s → **0.548s** | 0.696 → **0.513** (26.2% lower) | 195 → **0** | 472/472 |

The 4/s latency confidence intervals also lie on opposite sides of the frozen 1.5s target in both envelopes. Throughput already kept up in the controls: the practical improvement here is shorter waits and less CPU for the same offered work. The short windows leave backlog-drift confidence inconclusive, so neither candidate is represented as a sustainable-capacity certificate.

## Controls and Scope

Control source is current main at experiment start, `457134dffb8ec7af12b0a9be437e66a3bc7b7400`. The measured candidate is `960856144b713b732d38d47fcf164b37cc719452`, image `bifrost-api:kind-a74e45295998-sdk-release-r1`, immutable host image ID `sha256:110fa0cf2f25b2c2f4e4979f89cebbe98da8e2ab5f2b0de1f8ac0d126bccfe09`. Both images use the same dependency base and parameter-free SQL/pool counter hook. All 1,320 candidate source/hook files were verified as UID 1000 without network access. Actual API and two-worker image identities were verified for every arm; the init, API and future-worker image references were also checked in the deployment template.

The frozen harness is `2e3e9ca2bb1c72052098c915d001fc9602a1c082`. Each canonical profile starts fresh; its order is control 2/s, control 4/s, candidate 2/s, candidate 4/s, retaining history within that profile. Both arms use one API process, PgBouncer, unchanged component limits, and an explicitly amended warm=max=2 worker policy (idle 300s). Every window has 30s warm-up, 120s measurement, 60s drain and at most 120s additional recovery. Counters run for about 60s beginning 45s after the action starts. The owned Docker test stack was stopped throughout timed load. No timed window was replayed.

The declared seed has 1,100 workflows, 701 tools, 50 data providers and 200 documents; after harness preparation the observed catalog has 1,860 functions. Each measured workflow performs five absent config reads, one query returning 20 documents, and one insert. One initially empty organization-scoped write table was created before the timed windows; its documents accumulate across the ordered arms. This isolates the throughput comparison from first-create races. No AI or Halo calls, app builds, cold-worker starts, worker-linearity test, or production rollout is included.

## Every Declared Window

| Envelope | Arm / Offered Rate | Correct Within 60s | Correct Completion Events/s | Send-to-Correct p95 | Validity |
|---|---|---:|---:|---:|---|
| 4gb-capacity | control-r2 | 221/221 | 1.850 | 0.439s | No flags |
| 4gb-capacity | control-r4 | 472/472 | 3.975 | 3.178s | No flags |
| 4gb-capacity | candidate-r2 | 221/221 | 1.850 | 0.345s | external CPU above 1.5 cores in 6/23 intervals |
| 4gb-capacity | candidate-r4 | 472/472 | 3.942 | 0.567s | No flags |
| m-capacity | control-r2 | 221/221 | 1.850 | 0.449s | No flags |
| m-capacity | control-r4 | 472/472 | 3.967 | 2.412s | No flags |
| m-capacity | candidate-r2 | 221/221 | 1.850 | 0.359s | No flags |
| m-capacity | candidate-r4 | 472/472 | 3.942 | 0.548s | No flags |

All 2,772 measured arrivals were accepted and completed correctly within deadline. All eight windows observed exactly two ready workers, zero counter errors, and zero duplicate executions, terminal failures, restarts, OOMs, evictions or quota denials. All work drained, and both owned benchmark namespaces were removed. The 4 GB candidate 2/s cell retains its external-CPU invalidity flag; do not use that cell for a causal speed ratio. Both 4/s pairs and all M cells have no validity flags.

Poisson scheduling yields 221 measured arrivals at the nominal 2/s rate and 472 at 4/s. Completion-event counts can include warm-up arrivals finishing inside measurement, so their rates need not equal the measured arrival-cohort rate. The 2/s cohorts are below the frozen 400-observation minimum; all 120s windows are shorter than the normal capacity confirmation. Maximum capacity and long-run drift remain unproved.

## Resource and Connection Cost

No installation knob or mandatory pooler is introduced. The shared engine retains the existing bounded main allowance: default 5+10 becomes effective 15+0. Custom bounded peaks and SQLAlchemy unlimited sentinels retain their semantics. Dispatch stays 4+6 and liveness 1: the default peak remains **26 connections per API process**, while possible idle retention rises from 10 to 20. Connections open lazily. The existing worker 1+2 allowance still retains three connections through the shared policy. See the [same-image reuse experiment](2026-11-02-sdk-connection-reuse.md) for separate direct-Postgres evidence and the retained-session tradeoff.

| Envelope, 4/s | Namespace Charged Peak Before → After | Two Workers Combined Charged Peak Before → After | SQL in Counter Window Before → After | SQL / Correct Completion in Same Counter Window |
|---|---:|---:|---:|---:|
| 4 GB | 1517.1 → 1553.2 MiB | 619.4 → 618.3 MiB | 24,562 → 17,744 | 105.42 → 74.87 |
| M | 1595.2 → 1650.8 MiB | 664.2 → 664.5 MiB | 24,620 → 17,737 | 105.67 → 74.84 |

These are sampled lower-bound peaks with coverage retained in the baseline. Charged pod memory is not private/PSS or an idle-worker measurement. Namespace peaks increased by about 36 MiB and 56 MiB; retained connections and accumulated history prevent attributing that difference solely to the patch. This does not justify lowering worker limits. SQL ratios include background controllers and use matching counter-time completions; they are not the exact query cost of an individual workflow. In these profiles, engine connection events describe API connections to PgBouncer, while the separate observer records PostgreSQL backends.

The 4 GB envelope means **4 decimal GB / 4 CPUs**, not a commercial 4 GiB/two-CPU node. Kubernetes actually admitted 3.9 CPUs / 3.59 GiB for its seven tenant pods; M admitted 3.9 CPUs / 7.75 GiB within its 8 GiB quota. The admitted figures include PgBouncer’s injected 128 MiB init-container reservation, which is larger than its 64 MiB runtime container. Object storage, load generation, fake providers, image builds and cluster infrastructure remain outside these tenant allocations. No node-density or dollar-margin claim is made.

## Storage Baseline

| Candidate 4/s | New Execution / Document Rows, All Phases | New Audit Rows | Database Size Growth | Retained WAL Change | Retained Container Log Change | Visible Current S3 Payload Change |
|---|---:|---:|---:|---:|---:|---:|
| 4 GB | 584 / 584 | 1169 | 3.34 MiB | 16 MiB | 3.62 MiB | 0 bytes |
| M | 584 / 584 | 1169 | 3.15 MiB | 16 MiB | 3.62 MiB | 0 bytes |

Storage collection spans warm-up through drain and reconciliation (about 217s), so its 584 executions differ from the 472 measured arrivals. Gross database growth was about 5.9/5.5 KiB per new execution row in these two candidate intervals, including document/audit/background work and allocation/reuse effects. Retained WAL grows in file chunks; its delta is not WAL generation. Every arm’s frames and deltas are saved. This is not a monthly forecast or a retention/backup rehearsal. S3 versions/delete markers were unmeasured; this fixture writes no app artifacts or large results. Declared retention policies do not prove cleanup ran.

## Final Source and Verification

After measurement, [PR #1164](https://github.com/gobifrost/bifrost/pull/1164)’s repository savepoint/duplicate-error contract was consolidated into this release rather than landing a second migration. Final runtime source commit is `e5675cc43c8314d24fafa5a2e2eb2f8160fbb717`. Compared with the measured image, only `api/src/repositories/tables.py` and `api/src/routers/tables.py` changed, in metadata creation/error handling. All other archived runtime sources match the image hashes. All eight captured API logs contain zero metadata-create requests; the precreated-table workload does not enter those changed branches. Astra reviewed the exact delta and found no need to repeat unaffected hot-path measurements.

The [release-candidate plan](../plans/2026-11-02-sdk-release-candidate.md) records the earlier focused pool/TLS, worker lifecycle, cache/audit/wake, table and migration verification. After hosting cleanup, this final consolidation passed **36 focused unit/live cases** with the actual two-session repository race, usable losing transaction, SDK/REST contracts and a worker write.

```bash
./test.sh tests/unit/sdk/test_sdk_table_metadata.py tests/unit/routers/test_tables_policy_publish.py tests/unit/routers/test_tables_management_permissions.py tests/e2e/platform/test_sdk_tables_writes_local.py -v --durations=0
./test.sh quality api
```

Final API Pyright/Ruff passed with zero errors or warnings. The focused run took 8.94 seconds; its existing live worker-write happy path remains the sole worker job in that selection. Exact source-delta hashes and final verification receipts are retained in the baseline.

Astra approved the pool policy, locked non-destructive migration, shared conflict contract and exact conditional upgrade notice. No full backend/client/browser suite was run locally; the merge queue remains the complete-suite gate. Installations with duplicate ad-hoc names must resolve them before upgrading; the migration reports affected IDs and changes no data. Read-only production preflight found zero duplicate groups.

## Reproduction and Evidence

[Machine-readable baseline](baselines/sdk-hosting-2026-11-02.json) retains all eight windows, validity, deadline/event metrics, pool/SQL counters, resource coverage, admitted allocations, database clients, storage frames/deltas, source hashes and raw file hashes. Raw directory: `/home/jack/bifrost-bench-evidence/runs/sdk-hosting-20261102-r1`. Supervisor, reducer, image build proof and test receipts: `/home/jack/bifrost-bench-evidence/supervision/sdk-hosting-20261102`.

The raw `support/` directory preserves the helper scripts, hook, control manifest, source proof and verification inputs. Its `frozen-harness.git.bundle` (570,017 bytes; SHA-256 `6b7b198d080be167be56bb9578288eb3de76921f01d3cc8f249a8695d1f2bf73`) preserves the frozen experiment branch on top of published main `457134dff`; that expanded harness is not merged into this source PR. Import the bundle into a checkout containing that base before creating the frozen benchmark worktree. Reproduction also requires the declared immutable local images and guarded local Kind fixture. Adapt only environment paths/identity and a new output directory; preserve profiles, rates, seed, image sources, timing and invalidity rules. The supervisor refuses an occupied namespace, changed harness, wrong helper hash, unverified candidate or running owned test stack. It never uses the default production kubectl context.
