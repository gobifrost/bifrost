# Run retention

Finished workflow runs, finished agent runs and events are deleted once they are
older than an administrator-set window (30 days by default, or kept forever).
Cost and resource history survive the deletion. Code:
`api/src/services/run_retention/`, `api/src/jobs/platform/run_retention.py`,
`api/src/jobs/schedulers/run_retention.py`. It replaces the old fixed 30-day
event cleanup (`event_cleanup`).

## What is deleted

Age is measured from when the run finished (`completed_at`), or, for events,
from `created_at`. The cutoff is `job start - days`.

| Table | Deleted when | Goes with it |
| --- | --- | --- |
| `executions` (workflow runs) | status is Success, Failed, Timeout, Cancelled, CompletedWithErrors or Stuck, and `completed_at < cutoff` | `execution_logs` (database cascade) |
| `agent_runs` | status is completed, failed, budget_exceeded, timeout or cancelled, `completed_at < cutoff`, and the run has **no child run** | steps, verdict history, flag conversations (cascade) |
| `events` | `created_at < cutoff` | event deliveries (cascade); `agent_runs.event_delivery_id` is set to NULL |

Flagged agent runs and runs with a verdict are deleted like any other.

**Never deleted:** Scheduled, Pending, Running and Cancelling workflow runs;
`paused` agent runs; an agent run that still has any child run (parents go only
after their children, in a later batch of the same run or a later run); and
anything at all when the window is "keep forever".

## What is kept: every run is counted exactly once

A finished workflow run is counted in `executions` while it exists and in
`workflow_run_daily` after it is deleted. The job adds the run to the daily table
in the same transaction that deletes it, so a crash neither loses nor
double-counts it. Nothing else writes that table, and there is no backfill: with
"keep forever" it stays empty.

`workflow_run_daily` holds one row per UTC day (of `coalesce(started_at,
completed_at)`), organization, workflow (id and name) and status, with the run
count, total duration, total CPU seconds, the peak CPU / process RSS / memory
maxima, and AI cost and call totals. Organization and workflow ids have no
foreign keys, so history outlives both. Historical readers (the usage report,
the resource report's workflow view, `/api/metrics/workflows`, the all-time
totals) read kept runs and this table together through `workflow_run_source`.

`ai_usage` rows outlive their runs. The two foreign keys to `executions` and
`agent_runs` are gone; `execution_id` and `agent_run_id` remain as plain id
stamps. Just before a run is deleted the job stamps `ai_usage.workflow_id` or
`ai_usage.agent_id` from it, so cost still groups by workflow and agent after
the run is gone. Side effect: deleting an *agent* no longer deletes its AI cost
rows; they appear under "Deleted agent".

Workflow AI usage rows carry no organization (`organization_id` is NULL); that is
unchanged, and the run's organization is not copied onto them.

## Setting

`system_configs`, `category = 'run_retention'`, `key = 'policy'`, value
`{"days": <int|null>}`. `days` is **30 to 3650**, or `null` for keep forever. No
row means 30 days, for new and existing installs alike; there is no migration
row. The minimum is 30 because the dashboards and the resource report show up to
30 days of individual runs and the window must never be shorter than what they
display.

**Settings → Maintenance → Run history** (Platform Admin). The field saves when
you leave it. The card shows the oldest kept run, the rolled-up history, and the
last run.

- **Preview** queues the job as a dry run: how many workflow runs, agent runs and
  events a run would delete now. It writes nothing.
- **Run now** queues a real run (same job as the daily one). It is deduplicated:
  pressing it while one is queued or running reuses that job.
- **Shortening the window** (a smaller number, or turning "Keep forever" off) opens
  a confirmation with the exact counts from a live preview. Confirming saves;
  the deletion happens at the next run, not at save. **Deletion is permanent and
  there is no archive.** Lengthening the window never needs confirmation, but it
  cannot bring back anything already deleted.

Each change is audited as `settings.run_retention.update` (before and after
values). Any signed-in user can read the single number through
`GET /api/run-retention`; it feeds the "Removed after N days" wording.

## The daily job

`run.retention` is scheduled by the scheduler leader at **03:00 UTC** and uses the
shared platform-job system: progress and status are in the notification stream
and under Diagnostics → Scheduler. It takes the resource lock `run.retention`
(one run at a time, manual runs included), times out after 2 hours, makes up to
3 attempts, and is retried only after a runner loss.

1. On first start it reads the setting and fixes `cutoff = start - days` in the
   job checkpoint. A resumed attempt uses the same cutoff, whatever the setting
   says now. If the setting is "forever" it ends with `{skipped: "keep_forever"}`.
2. It deletes in three phases, oldest first: workflow runs (1,000 per batch), agent
   runs (500 per batch, leaves only), events (1,000 per batch).
3. Each batch is one short transaction that first locks the job's own row with its
   current lease (`SELECT ... FOR UPDATE`), then rolls up, stamps and deletes
   exactly the selected ids with a Core `DELETE`, and checks the deleted count.
   A runner that lost its lease deletes nothing.

**Per-run cap: 100,000 rows per table.** When a table reaches it the result has
`continues: true` ("continues tomorrow") and the next day's run picks up where
this one stopped. The first rollout on a large install spreads over several
nights this way, which also gives autovacuum room; steady state is only the
previous day's finished runs. The oldest-first order means the backlog shrinks
steadily; "oldest kept run" on the Settings card is the progress indicator.

## Failure codes and endings

Nothing is deleted outside a transaction that also rolled the run up and holds
the lease. A failed batch changes nothing.

| Code / ending | Meaning | What to do |
| --- | --- | --- |
| `run_delete_mismatch` | A batch deleted a different number of rows than it selected. The batch was rolled back (so were the rollup and the `ai_usage` stamps). Retryable; the job retries up to its attempt limit. | Run now. If it repeats, something else is deleting from `executions`, `agent_runs` or `events` concurrently; look at the job log and at that writer. |
| `handler_error` | An unexpected error. See the scheduler log. Earlier committed batches stay committed. | Read the logs, fix, Run now. |
| Cancelled (lease lost) | Another runner took over the job. The old runner stops without changing anything, because every delete needs the current lease; the job continues under the new lease or is requeued. | Nothing. |
| Cancelled (manual) | A manual cancel stops the run between batches. Batches already committed stay deleted and rolled up. | Run now continues from the database. |

## Dangling references (expected)

Rows that mention a deleted run keep the id; nothing cascades. These can point at
runs that no longer exist:

- `executions.root_execution_id`
- `audit_logs.execution_id`
- `messages.execution_id`
- `event_deliveries.execution_id` (a delivery is deleted with its event, and an
  event is always older than the runs it started, so this one stays rare)
- `ai_usage.execution_id` and `ai_usage.agent_run_id`

Lineage is read from the parent only while the parent is running
(`child_lineage`), and delayed runs carry their own lineage, so no live path
needs a deleted run.

Where a removed run is referenced the UI says so instead of erroring or
retrying: the run pages show "This run isn't available. Finished runs are
removed after N days (retention). It may also be outside your access.", chat
tool cards fall back to the stored tool result, and agent timelines show "Run
details removed after N days (retention)". The server 404 for
`GET /api/executions/{id}` (and its `/result`, `/logs`, `/variables`),
`GET /api/agent-runs/{id}` and the MCP execution lookup ends with " Finished runs
are removed after N days." (omitted when runs are kept forever). Status and
response shape are unchanged, so CLI and MCP agents read the same text.

## Windows longer than the retention window

Agent stats default to 7 days; the API allows 90. A stats window longer than the
retention window counts **kept runs only**: agent runs have no daily rollup (their
cost lives on in `ai_usage`, grouped by the stamped `agent_id`, but run counts and
step totals for deleted agent runs do not). The dashboard 30-day chart starts
inside the 30-day minimum window, so it is never affected. Workflow reports
(usage report, resource report workflow view, `/api/metrics/workflows`, all-time
totals) keep their full history through `workflow_run_daily`.

## Disk

Deleting rows lets autovacuum make the space reusable inside the table files; it
does **not** shrink the files. After the first rollout `executions` (and
`execution_logs`, `agent_run_steps`) stay about their current size on disk, then
stop growing because new rows reuse the freed pages. Returning the space to the
operating system needs `pg_repack` or `VACUUM FULL` (which takes an exclusive
lock). That is a separate, manual decision; do it only if the disk is actually
short, in a maintenance window.

## Warnings

- **Shortening the window permanently deletes runs and events** at the next run.
  There is no undo short of restoring the database from backup.
- **Downgrading** (`alembic downgrade 20261005_audit_retention`) recreates the
  `ai_usage` foreign keys with `ON DELETE CASCADE`. To do that it first **deletes
  every `ai_usage` row whose run was already deleted by retention**, exactly as the
  old cascade would have. That cost history is gone after a downgrade (restore
  from backup if it matters). It also drops `workflow_run_daily`, the added
  `ai_usage.workflow_id` / `agent_id` columns and the two paging indexes, and
  re-adds the (never-written) `execution_metrics_daily.total_ai_*` columns.

## Migration `20261006_run_retention`: recovery

The migration is not atomic. It creates `workflow_run_daily`, drops the two
`ai_usage` foreign keys, adds `ai_usage.workflow_id` and `agent_id`, and drops
`execution_metrics_daily.total_ai_*` in one transaction. Then, in an
`autocommit_block`, it commits that transaction and builds two indexes with
`CREATE INDEX CONCURRENTLY`: `ix_executions_completed_id` and
`ix_agent_runs_completed_id`. They are built without `IF NOT EXISTS` on purpose, so
an interrupted build fails loudly instead of being silently kept.

If an index build fails or is cancelled (timeout, lock wait, disk full, the init
container killed), the database is **half-migrated**: the new table exists, the
foreign keys are dropped, the columns are changed, `alembic_version` still names
`20261005_audit_retention`, and a failed concurrent build can leave an **INVALID**
index. Re-running `alembic upgrade head` will fail at `create_table`
(already exists), and the old API code must not be started against this schema
until it is resolved. Do not delete rows from `alembic_version` by hand.

Diagnose:

```sql
SELECT c.relname, i.indisvalid
  FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
 WHERE c.relname IN ('ix_executions_completed_id', 'ix_agent_runs_completed_id');
```

No row means that index was never created; `indisvalid = false` means it is
INVALID.

**Recovery A: finish the migration by hand (preferred).** Run each statement
on its own, outside a transaction block (`psql` autocommit; no `BEGIN`):

```sql
DROP INDEX CONCURRENTLY IF EXISTS ix_executions_completed_id;   -- only if INVALID or partial
DROP INDEX CONCURRENTLY IF EXISTS ix_agent_runs_completed_id;   -- only if INVALID or partial
CREATE INDEX CONCURRENTLY ix_executions_completed_id ON executions (completed_at, id);
CREATE INDEX CONCURRENTLY ix_agent_runs_completed_id ON agent_runs (completed_at, id);
```

Drop an index first only when the diagnostic query shows it INVALID; leave a
valid one alone and skip its `CREATE`. Confirm both are `indisvalid = true`, then
mark the migration applied:

```bash
alembic stamp 20261006_run_retention
```

Then restart `bifrost-init` and the API.

**Recovery B: reverse the DDL.** Drop any INVALID index as above, then undo the
earlier DDL by hand at revision `20261005_audit_retention`: re-add the
`execution_metrics_daily.total_ai_input_tokens`, `total_ai_output_tokens`,
`total_ai_cost` and `total_ai_calls` columns (nullable); drop `ai_usage.workflow_id`
and `ai_usage.agent_id`; recreate `ai_usage_execution_id_fkey` and
`ai_usage_agent_run_id_fkey` (`ON DELETE CASCADE`, to `executions.id` and
`agent_runs.id`); drop `ix_workflow_run_daily_day` and `workflow_run_daily`. Run
the foreign key recreation only while no retention run has happened (the job is
not registered until the migration lands, so none has), or it will fail on
orphaned `ai_usage` rows. Then `alembic upgrade head` again and watch the index
builds, ideally in a quiet period.

Both indexes exist for the retention job's paging query; without them its batches
scan the whole table and the job is slow.

## Smoke check after a deploy

`prod_smoke/checks/<date>_run_retention.sql` covers: the policy row (none means
30 days); the last three `run.retention` jobs with status and counts; the oldest
finished workflow run and agent run (within 31 days, or a shrinking backlog while
the cap still applies); `workflow_run_daily` `sum(run_count)` and `max(day)` (the
sum must grow by exactly the workflow runs deleted); the `ai_usage` row count
(must not drop between runs); `n_dead_tup` and `last_autovacuum` for `executions`;
and the oldest event.
