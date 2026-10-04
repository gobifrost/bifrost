# Audit retention

Audit events (`audit_logs`) move from the database to a verified archive in
object storage once they are old enough, so the table stays small without
losing history. Archives are deleted after a second, longer window, or kept
forever. Code: `api/src/services/audit_retention/`,
`api/src/jobs/platform/audit_archive.py`, `api/src/jobs/platform/audit_query.py`.

## What it does

1. After `hot_days` an event is **archived**: written to object storage as a
   gzip JSONL segment.
2. The segment is read back and checked against its checksum and the ids it was
   built from (**verified**).
3. Only then is the event **deleted from the database**, in the same
   transaction that records the segment in the catalog (`audit_archive_segments`).
4. After `archive_days` the archived segment is deleted. A segment goes once its
   *newest* event is older than the window. If `archive_days` is unset, archives
   are kept forever.

Ages are measured from when the event happened (`created_at`), not from when it
was archived.

## Defaults

| Install | `hot_days` | `archive_days` |
| --- | --- | --- |
| New install | 90 | 365 |
| Existing install (the upgrade found audit events) | 90 | keep forever |

The upgrade migration writes the existing-install policy explicitly, so
upgrading never deletes history. `archive_days` must be at least `hot_days`.
`hot_days` is 1–3650.

The policy is one `system_configs` row (`category = 'audit_retention'`,
`key = 'policy'`). No row means the new-install defaults.

## Configure it

**Settings → Maintenance → Audit Retention** (Platform Admin). Edits save as you
go. The card also shows the oldest event in the database, what the archive
holds, and the last run.

- **Preview** runs the job as a dry run: how many events would be archived
  (by day) and how many events would be deleted. The deletions include events
  still in the database that are already older than the archive window: one
  run archives them and then deletes them. Nothing changes.
- **Run now** queues a real run. It is the same job as the daily one. A manual
  run is deduplicated, so pressing it while one is queued or running reuses that
  job.
- **Shortening the archive window** (a smaller `archive_days`, or turning off
  "Keep archives forever") opens a confirmation first. It shows the number of
  events the change will delete, archived or still in the database, from a live
  count. Confirming saves the setting; the deletion happens at the next run, not
  at save.

Each settings change is audited as `settings.audit_retention.update`.

## The daily job

`audit.archive` runs at 02:30 UTC. It uses the shared platform-job system, so
progress and status appear in the notification stream and under
Diagnostics → Scheduler. One run at a time (`max_concurrency = 1`); timeout 2
hours; 3 attempts in total, retried only after a runner loss.

Phases:

1. **Archiving.** The cutoff (`now - hot_days`) and the expiry
   (`now - archive_days`) are frozen in the job checkpoint when it first starts.
   The job takes the oldest events before the cutoff in batches (up to 5,000
   rows or 64 MB), groups them into one segment per organization and UTC day,
   and for each segment: upload, read back, verify, then catalog and delete in
   one transaction. The transaction first locks the job row with its current
   lease; a runner that lost its lease deletes nothing.
2. **Expiring archives.** Skipped if `archive_days` is unset. Deletes expired
   segment objects and their catalog rows 100 at a time, inside the
   lease-holding transaction. If an object delete fails, the catalog change
   rolls back. Objects deleted earlier in that page are already gone while
   their catalog rows come back; the next run repeats those deletes, which is
   safe, and no export can read in between because of the shared lock.
3. **Export cleanup.** Deletes exported files older than 7 days, and any export
   file with no matching job. It runs at the end of a successful, non-dry
   archive run.

**After a crash or runner loss** the job is requeued with its checkpoint. The
windows stay as the first attempt set them. Segments already committed are gone
from the database, so nothing is archived twice. The segment that was in flight
is rebuilt, uploaded again to the same key, and verified before anything is
deleted. A segment is named by its checksum, so identical rows give an
identical key.

A day can have more than one segment (large days span batches; late events for
an archived day get their own segment).

## Storage layout

```
_audit/v1/org=<uuid|global>/day=<YYYY-MM-DD>/<sha256>.jsonl.gz
_audit_exports/<job-id>.jsonl.gz
```

`org=global` holds events with no organization. `<sha256>` is the checksum of
the file itself.

Each segment is gzip (deterministic, `mtime=0`) of newline-separated JSON
objects, sorted by `(created_at, id)`, keys sorted, compact separators. One
line is one event:

| Field | Notes |
| --- | --- |
| `schema` | `audit.v1` |
| `id`, `created_at` | `created_at` is UTC ISO 8601 with microseconds |
| `organization_id`, `user_id`, `resource_id`, `execution_id` | UUID or null |
| `action`, `resource_type`, `outcome`, `source` | as in `audit_logs` |
| `operation_id`, `surface`, `ip_address`, `user_agent` | as in `audit_logs` |
| `details` | JSON object or null |
| `actor_email`, `actor_name`, `organization_name` | **Snapshots** taken at archive time; the user or organization may later be renamed or deleted |

The catalog table `audit_archive_segments` has one row per segment:
`organization_id`, `day`, `object_key`, `schema_version`, `row_count`,
`byte_size`, `sha256`, first and last `created_at` and id, `archived_at`, and
the writing `platform_job_id`. `organization_id` and `platform_job_id` have no
foreign keys, so segments outlive the organization and the job. Exports and
expiry both walk the catalog, not the bucket.

## Export

The **Export** button on the audit page (Platform Admin only) queues an
`audit.query` job that writes archived and current events to one `.jsonl.gz`
file, with the same line format. The same job is available through
`POST /api/audit/exports`, which is how Operators export.

- Range: at most 366 days per export. Filters: optional organization and
  action prefix.
- **Platform Admins** export everything.
- **Operators** (roles that read access checks) have no Export button; they
  call `POST /api/audit/exports`. A non-admin request must set `action` to a
  value starting with `access.check`, otherwise it gets 403. Rows are limited
  to organizations the caller reaches; asking for another organization
  returns 403.
- Only the person who requested an export can download it. Downloading
  someone else's export, or one that has not finished, returns 404.
- Downloads expire **7 days** after the job finishes (410 Gone). Run it again.
- If the requester's reach changed since the export was made, the download
  returns 403 "Your access changed since this export was made; run it again."
  Platform Admins are never refused for this.
- Exports and the archive share one lock, so an export never overlaps an
  archive run (and vice versa). An export can wait behind a running archive.

The audit page banner (Platform Admins only) shows the oldest event in the
database and the date the archive covers through.

## Failure codes

In every case below, **nothing is deleted unless it was verified**: the database
delete and the catalog row commit together, only after read-back verification.
Failed attempts leave data where it was.

| Code | Meaning | What to do |
| --- | --- | --- |
| `archive_storage_unavailable` | Object storage is not configured (`audit.archive` or `audit.query`). Nothing was archived, deleted or exported. | Configure object storage, then Run now. |
| `archive_verify_failed` | The segment read back from storage did not match its checksum or ids. Nothing was deleted. The job fails with `error_retryable` set; it is not retried within the run. | Check object storage health, then Run now. The segment is rebuilt and re-uploaded. |
| `archive_delete_mismatch` | The delete removed a different number of rows than the segment holds. The transaction rolled back, so the catalog row and the delete both reverted. | Run now. If it repeats for the same segment, look at the job's checkpoint (`pending.key`) and at anything else deleting from `audit_logs`. |
| `archive_corrupt` | An **export** read an archived segment that failed its checksum or could not be decoded. The archive itself is not modified. | Restore that object from backup or bucket versioning (the catalog has its `object_key` and `sha256`). Do not delete the catalog row. |
| `archive_missing` | An **export** found a catalog row whose object is not in storage. | List the prefix of that `object_key` to confirm the object is really gone. If it is, delete that catalog row: the events it described are no longer stored. This typically follows an expiry that was interrupted while the archive window was being lengthened. |
| `handler_error` | An unexpected error, including a storage error mid-run. See the API/scheduler log. Nothing unverified was deleted. | Read the logs, fix, Run now. |

Other endings:

- **Cancelled**: a manual cancel stops the run at the next checkpoint. Segments
  already committed stay archived; the next run continues from the database.
- **Lease lost**: another runner took over. The old runner stops without
  changing the job, and deletes nothing, because every delete needs the
  current lease. The job then continues under the new lease or is requeued.

## Warnings

- **Shortening the archive window permanently deletes archived events** at the
  next run (the confirmation dialog shows the count). There is no undo short of
  restoring the bucket from backup. Setting it back does not bring them back.
- **Downgrading after the first archive run** drops the catalog and leaves the
  objects under `_audit/v1/`. Archived events can then be recovered only by
  reading those objects directly.
- **Uncataloged objects can exist.** If an upload succeeded but its commit then
  failed, and the rows later changed (so a rebuilt segment has a different
  checksum), the first object stays in storage with no catalog row. It is
  ignored by exports, and expiry never removes it, because expiry walks the
  catalog. Remove strays manually if they matter; they can be found by
  listing `_audit/v1/` and comparing with `audit_archive_segments.object_key`.
