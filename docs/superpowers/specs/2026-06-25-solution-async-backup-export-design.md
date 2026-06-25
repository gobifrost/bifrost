# Solution Async Backup Export Design

## Problem

Solution Backup export currently runs inside the HTTP request. The API builds a temporary ZIP and returns it as the response, while the browser waits with a loading state. This proves the export writer can stream large payloads with bounded memory, but it is the wrong operational model for real backups:

- The user cannot leave the page and return to download the artifact.
- Browser/network failures lose the result.
- There is no export history or durable job status.
- Notification Center cannot show progress or a ready-to-download action.
- Multi-GB backups depend on a single long-lived browser request.

Package export, which omits runtime state, can stay as a synchronous direct download until evidence shows it needs the same treatment.

## Goals

- Backup export is a scheduler-owned asynchronous job.
- Backup artifacts are stored durably until a configurable expiration.
- Users can watch progress in Notification Center and return later from Solution detail.
- Completed jobs expose a download action from both Notification Center and Solution detail.
- Expired artifacts are cleaned up by the scheduler and are no longer downloadable.
- Existing streaming/Zip64/encrypted payload behavior is preserved for large files.
- Playwright covers the primary operator journey end to end.

## Non-Goals

- Do not make Package export asynchronous in the first implementation.
- Do not store backup passwords.
- Do not build resumable partial downloads in the first implementation.
- Do not move execution to RabbitMQ workers unless the scheduler path proves insufficient.
- Do not redo the large-file proof as part of this UX/job design.

## User Experience

### Export Dialog

The current Package/Backup dialog remains the entry point.

When the user chooses Package, the UI keeps the existing direct download behavior.

When the user chooses Backup and confirms:

1. The UI calls the new create-export-job endpoint.
2. The dialog closes after the job is accepted.
3. A toast says the backup export was queued.
4. Notification Center shows a progress item.
5. Solution detail shows the new job in the Exports tab.

Backup copy should make retention explicit, for example: "Backups are kept for 7 days."

### Solution Detail Exports Tab

Solution detail gets a dedicated "Exports" tab. It must be durable enough that Notification Center is not the only way to retrieve a completed export.

Each row shows:

- Filename or generated backup label.
- Status: queued, running, completed, failed, expired.
- Mode and selected contents: configuration values/secrets, files, table data.
- Created time.
- Expiration time for completed jobs.
- Size when complete.
- Error text when failed.
- Download button when complete and not expired.

The list is scoped to the current Solution install.

### Notification Center

Backup jobs create/update a notification for the requesting user:

- Queued: "Backup queued"
- Running: "Building backup"
- Completed: "Backup ready"
- Failed: "Backup failed"
- Expired notifications are not required; the durable Exports list handles historical state.

The completed notification includes a Download action. The action uses the authenticated frontend download handler for the export-job download endpoint. The notification may expire normally; the export artifact follows the export retention setting.

## Backend Design

### Data Model

Create a `solution_export_jobs` table.

Fields:

- `id UUID primary key`
- `solution_id UUID not null references solutions(id) on delete cascade`
- `requested_by UUID not null references users(id)`
- `mode string not null`, initially always `full`
- `include_values boolean not null`
- `include_files boolean not null`
- `include_data boolean not null`
- `status string not null`: `queued`, `running`, `completed`, `failed`, `expired`
- `filename string not null`
- `storage_key string nullable`
- `size_bytes bigint nullable`
- `notification_id string nullable`
- `error text nullable`
- `created_at timestamptz not null`
- `started_at timestamptz nullable`
- `completed_at timestamptz nullable`
- `expires_at timestamptz nullable`
- `updated_at timestamptz not null`

The job row must not store the plaintext password. To let the scheduler perform encryption later, the API must store only a short-lived encrypted password envelope for queued jobs. The envelope uses the server-side encryption key material already configured for the instance, is deleted from the row as soon as the scheduler claims the job, and is never returned by API contracts.

### Configuration

Add a configurable retention setting:

- Name: `SOLUTION_EXPORT_RETENTION_HOURS`
- Default: `168`
- Meaning: completed export artifacts are downloadable until `completed_at + retention_hours`.

The UI can display the equivalent duration from the API response. The first implementation can use static copy if the setting is not surfaced through configuration endpoints yet, but backend enforcement is authoritative.

### API Endpoints

Keep the current synchronous endpoint for Package export:

- `POST /api/solutions/{solution_id}/export?mode=shareable`

Change Backup export to use new endpoints:

- `POST /api/solutions/{solution_id}/export-jobs`
  - Admin only.
  - Body includes password and selected backup contents.
  - Validates that at least one backup content type is selected.
  - Creates the job row in `queued` status.
  - Creates the initial notification.
  - Returns the job summary.

- `GET /api/solutions/{solution_id}/export-jobs`
  - Admin only.
  - Lists recent export jobs for that Solution, newest first.

- `GET /api/solutions/export-jobs/{job_id}`
  - Admin only.
  - Returns one job summary.

- `GET /api/solutions/export-jobs/{job_id}/download`
  - Admin only.
  - Returns the artifact when status is `completed` and `expires_at` is in the future.
  - Returns 404 when missing.
  - Returns 409 when queued/running/failed/expired.

The download endpoint should stream from object storage or use an existing storage-to-response primitive. It must not load the full artifact into memory.

### Scheduler Processing

Add `api/src/jobs/schedulers/solution_export_jobs.py`.

It runs at a short interval, for example every 30 seconds, and processes a bounded batch of queued jobs.

Claim pattern:

- Select queued rows ordered by `created_at`.
- Use `FOR UPDATE SKIP LOCKED`.
- Limit the batch.
- Mark selected rows `running`, set `started_at`, update notification.
- Commit the claim before building artifacts.

Build pattern:

- Reuse the existing export/capture service logic.
- Stream Solution-owned file payloads exactly as the current export path does.
- Write the completed ZIP to object storage under a job-specific key.
- Store `storage_key`, `size_bytes`, `completed_at`, `expires_at`, and `status=completed`.
- Delete or null the encrypted password envelope after claim/use.
- Update notification with `status=completed`, result metadata, and download action.

Failure pattern:

- Catch per-job exceptions so one bad export does not stop the scheduler batch.
- Set `status=failed`, persist a sanitized error, clear password envelope, and update notification.

Restart behavior:

- Jobs in `queued` remain queued.
- Jobs in `running` older than a configured stale threshold are reset to `queued`.
- Artifact writes use a temporary object key and are promoted to the final `storage_key` only after the job row can be marked `completed`, so re-running a stale job is idempotent from the user's perspective.
- Cleanup also removes stale temporary export objects.

Cleanup pattern:

- Add a scheduler cleanup function that finds completed jobs whose `expires_at` has passed.
- Delete the object storage artifact.
- Mark the row `expired` and clear `storage_key`.
- Run cleanup on a longer interval, for example hourly.

## Frontend Design

### Services

Extend `client/src/services/solutions.ts` with:

- `createSolutionExportJob(solutionId, options)`
- `listSolutionExportJobs(solutionId)`
- `getSolutionExportJob(jobId)`
- `downloadSolutionExportJob(jobId)`

Keep `exportSolution()` for Package direct download.

### Dialog Behavior

`ExportSolutionDialog` keeps Package/Backup choices.

- Package calls existing direct export.
- Backup calls `createSolutionExportJob`.
- Pending state labels should distinguish "Downloading..." from "Queueing backup..." if both modes share the same dialog button.

### Solution Detail

Add an Exports tab with React Query polling while any job is queued or running. Polling stops when all visible jobs are terminal.

Rows must be responsive and avoid cutting off long filenames. Download buttons use icons and accessible labels.

### Notification Action

Notification Center already supports metadata-driven actions. Completed export notifications use a new `download_solution_export` action that calls the export-job download service with auth headers and triggers a blob download.

## Security And Privacy

- Backup passwords are never stored in plaintext.
- Password envelopes are not exposed in contracts, logs, notification metadata, or errors.
- Failed job errors are sanitized.
- Download endpoint requires admin access and verifies the job belongs to an existing Solution visible to the caller.
- Export artifact storage keys are not exposed as direct public URLs.
- Shareable Package exports continue to omit runtime values, file payloads, and table rows.

## Testing Strategy

### Backend Unit And E2E

- Contract tests for export job DTOs.
- Migration/ORM tests for `solution_export_jobs`.
- Endpoint tests:
  - create backup job validates password and selected contents.
  - list returns jobs for the requested Solution only.
  - download blocks queued/running/failed/expired jobs.
  - download returns completed artifact.
- Scheduler tests:
  - claims queued jobs with skip-locked semantics.
  - completes a job and stores artifact metadata.
  - failure marks one job failed without blocking another job.
  - cleanup expires old artifacts.
- Streaming regression coverage should assert scheduler export uses the existing chunked writer path.

### Frontend Unit

- Dialog routes Package to direct export and Backup to job creation.
- Solution detail renders queued/running/completed/failed/expired export rows.
- Download button calls the export-job download service.
- Notification Center handles the completed backup download action.

### Playwright

Add a happy-path admin spec:

1. Install or seed a Solution with at least one small file.
2. Open Solution detail.
3. Start a Backup export including files.
4. Assert the dialog closes and a notification appears.
5. Assert the Exports tab shows queued/running, then completed.
6. Click Download from the Exports tab and verify a ZIP download.
7. Start another Backup export and verify the completed notification exposes Download.

The Playwright test should use small payloads for speed. Large-file memory proof remains covered by backend/unit/manual evidence and should not be re-run in browser e2e.

## Acceptance Criteria

- Backup export no longer holds a browser request open while building the ZIP.
- User can leave Solution detail, return, and download a completed backup before expiration.
- Notification Center shows backup progress and a completed download action.
- Expired artifacts are not downloadable and are cleaned from object storage.
- Package export still works as direct download.
- Targeted backend tests pass.
- Targeted frontend tests pass.
- Full client Playwright suite includes and passes the backup export journey.
