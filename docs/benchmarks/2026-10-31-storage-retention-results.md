# Storage and Run Retention — October 31, 2026

The guarded local rehearsal passed. The normal retention PlatformJob deleted
all 42 explicitly aged fixture executions and their 329 execution-log rows.
Allocated PostgreSQL storage remained about 403 MiB immediately afterward.
Deletion removes retained records; it does not immediately reduce provisioned
volume cost or return PostgreSQL's allocated space to the filesystem.

## Measured Results

This fresh `4gb-capacity` tenant contained the full seeded catalog, one API,
Postgres, Pooler, Redis, client and permitted workers. The fixture ran one small
success, one intentional failure, twenty repetitive 5,000,000-byte results and
twenty seeded high-entropy results of the same length. Each large execution
wrote eight log lines. This is storage evidence, not a sustainable-rate test.

| Result Shape | Executions | Raw Payload Bytes | Stored Result Column Bytes | Mean Column Bytes per Execution |
|---|---:|---:|---:|---:|
| Small success | 1 | — | 71 | 71 |
| Intentional failure | 1 | — | 0 | 0 |
| Repetitive 5 MB | 20 | 100,000,000 | 1,146,375 | 57,318.75 |
| High-entropy 5 MB | 20 | 100,000,000 | 100,001,518 | 5,000,075.9 |

Column sizes come from `pg_column_size(result)`. They exclude other row fields,
indexes, logs, WAL and free space. The two large-result shapes differ by about
87 times in stored column size despite identical raw payload lengths.

| Storage Frame | Database Bytes | Retained WAL Bytes | PostgreSQL Allocated Bytes |
|---|---:|---:|---:|
| Seeded, before fixture | 40,549,399 | 150,994,944 | 216,137,728 |
| After small success and failure | 40,885,271 | 150,994,944 | 216,473,600 |
| After repetitive results | 42,638,359 | 150,994,944 | 218,226,688 |
| After high-entropy results | 146,750,487 | 251,658,240 | 423,006,208 |
| Immediately after retention | 146,816,023 | 251,658,240 | 423,071,744 |

These accounting views overlap and must not be added. Visible current object
payloads stayed at 1,981,658 bytes across 42 objects. This fixture's result
payloads were retained in PostgreSQL; that observation does not make application
assets, artifacts, audit archives, object versions or backups free.

The canonical policy was 30 days. The dry run reported 42 eligible workflow
runs; the actual job reported 42 deleted, with no continuation needed. The
rehearsal verified zero remaining fixture executions and zero corresponding
log rows. It retained all original terminal records and a SHA-256 receipt before
backdating only those explicit fixture IDs. It changed no retention policy.

## Planning Implications

At one million retained results of these exact shapes, the measured result
columns alone would occupy about 53.38 GiB for repetitive content or 4,656.68 GiB
for high-entropy content. These are illustrative stocks, not monthly growth
measurements or estimates of a typical customer's data.

The benchmark provisions a 20 GiB database volume and permits 50 GiB of requested
storage through its namespace quota. The quota is a placement ceiling, not the
billable provisioned size. A budget that reserves 20% of 20 GiB could hold at
most roughly 299,725 repetitive or 3,435 high-entropy fixture result columns;
actual capacity is lower because the database also stores everything else.
Execution rate alone cannot determine storage cost.

The local-path volume does not enforce nominal capacity or cloud IOPS. Audit
and artifact cleanup, backup/restore, archived WAL, object versions, sustained
storage growth and cloud calibration remain separate measurements. No immediate
post-deletion shrink, long-term reclamation rate or cloud bill reduction is
claimed.

## Reproduction and Evidence

Run from the guarded measurement worktree at clean harness
`963b328339d96d44b74547505da8731a6c63d0da`, with the retained candidate image:

```bash
python3 scripts/bench/retention.py --profile 4gb-capacity \
  --api-image bifrost-api:kind-a74e45295998-tablefix-d2f25fc48 \
  --out /home/jack/bifrost-bench-evidence/runs/retention-next-declared-run
```

The command owns the shared benchmark host lock through cleanup. It requires a
fresh output directory and resets only the verified local benchmark namespaces.
The API image's host ID is
`sha256:fac2a947ca81c8572c83ee6ece92384ccf39f8f4abd657646a8f69b2ba8042a8`.

The private artifact is `runs/retention-current-20261031-r1/` under
`/home/jack/bifrost-bench-evidence/`. Its final `result.json` SHA-256 is
`6c5f84206352eddd4faf06faf6a82184182399061de8c09d9de73a1e2d5058d3`;
the immutable pre-backdate terminal receipt SHA-256 is
`a971088ca02586d3e39fce22b0d1dcf333bcda6132dc01455f32b2f9acab5532`.
Both maintenance jobs succeeded and namespace cleanup was verified at
20:19:10 UTC. Raw payloads and execution IDs remain outside Git.
