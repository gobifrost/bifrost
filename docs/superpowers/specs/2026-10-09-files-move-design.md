# `files.move` / `files.move_many` for Solution and org file locations

## Problem

The file SDK can read, write, list, stat, sign, and delete files in custom
locations (for example a Solution's `docs` location), but it cannot move one.
Relocating a file today means read → write → delete, which buffers the whole
object in the API process and fails for large objects. The editor's
`/api/files/editor/rename` does a server-side S3 copy + delete, but it is
superuser-only and hard-wired to the `_repo/` workspace.

Driving case: the RTM DMS Solution stores document files at
`documents/campuses/{facility}/types/{doctype}/{pdf|download}/{doc_id}/{file}`
in its `docs` location, and file policies grant healthcare users read by path
prefix. Moving a document or a whole building (hundreds of files, some
multi-GB) to another campus must move the files to the new campus prefix so
access follows them.

## Goal

Two operations for non-workspace, cloud-mode locations:

- `files.move(source, destination)` — one file.
- `files.move_many(items)` — a batch, with a result per item.

Both run as one durable `PlatformJob` type, so no copy is bound by an HTTP or
SDK timeout. Authorization happens at enqueue with the caller's principal, as
`docs/architecture/platform-jobs.md` requires. Bytes move server-side (no
buffering, no size ceiling), and a source is never deleted until its
destination is written and verified.

## Non-goals

- **Workspace (`_repo/`) moves.** `FileStorageService.move_file` (editor
  rename) owns workspace entity, module-cache, and file-index side effects.
  `location="workspace"` → 400.
- **Local mode.** Moves run in a scheduler child process where the API
  process's local filesystem is meaningless. The request models and SDK
  signatures have no `mode` field; moves are cloud-only.
- **Cross-location or cross-scope moves.** Source and destination are paths in
  one location under one effective scope (one Solution install or one org).
- **Prefix (folder) moves.** Callers name explicit pairs; RTM's paths carry
  campus and document type but not building, so a prefix cannot express "this
  building".
- **Version guards.** No `expected_version`: the existing content version is a
  sha256 of the full object (`shared/file_access.py` `get_file_stat`), which
  would reintroduce full-object reads.
- **Cross-item atomicity.** Each item is atomic on its own; one failed item
  does not undo or stop the others.
- **Web SDK methods.** The browser must not poll platform jobs; App callers
  invoke a workflow, which uses the Python SDK. Adding `moveMany` to the web
  SDK (with WebSocket completion) is a follow-up if an App needs it directly.

## Caller surface

### Python SDK (`api/bifrost/files.py`)

```
files.move_many(
    items: list[{"source": str, "destination": str}],   # 1..500
    location: str,                                       # required
    on_conflict: "error" | "rename" = "error",
    scope: str | None = None,
    timeout_seconds: float = 14400,                      # the job timeout
) -> list[{"source", "destination", "path", "renamed", "adopted", "status_code", "error"}]

files.move(
    source: str,
    destination: str,
    location: str,                                       # required
    on_conflict: "error" | "rename" = "error",
    scope: str | None = None,
    timeout_seconds: float = 14400,                      # the job timeout
) -> str   # final destination path; raises on failure
```

Both enqueue through `engine_request` and then poll
`GET /api/platform-jobs/{job_id}` with short requests until the job is
terminal — the `artifacts.create_video` pattern (`api/bifrost/artifacts.py`).
Each request is short, so the SDK's 30s client timeout never bounds a copy.
`/api/platform-jobs/{job_id}` is already on the worker engine-socket allowlist
(`worker_sdk_http.py`); the two new enqueue paths are added to
`FILES_ROUTE_PATHS`. On deadline the SDK raises `TimeoutError` naming the job
id ("the move may still be running").

The default wait equals the job timeout (4 h), so the SDK never gives up on a
job that is still allowed to run. A calling workflow must set its own
`timeout_seconds` (default 30 min, configurable up to 24 h) high enough for
the moves it waits on — including time queued behind other moves in the same
location and scope (see *Policy*). The operation docs state this, and RTM's
relocation workflow sets it explicitly.

`files.move` enqueues a one-item job through `POST /api/files/move` (a thin
route with a single-item request model over the same enqueue service) and
raises the item's error with its status code. Every `files.move` call is its
own job (a job row, a notification, a child process), so the docstrings steer
callers moving more than one file to `files.move_many`. `location` is required on both,
unlike the other file methods, because their `workspace` default is the one
value move rejects.

### HTTP

- `POST /api/files/move-many` — `FileMoveBatchRequest`
  (`items`, `location`, `scope`, `on_conflict`) → `202 PlatformJobAccepted`
  with `Location: /api/platform-jobs/{job_id}`.
- `POST /api/files/move` — `FileMoveRequest`
  (`source`, `destination`, `location`, `scope`, `on_conflict`) → same 202,
  enqueuing a one-item job.

Both handlers commit the job row (and deny-audit rows), publish its initial
update, and only then return the 202, as the existing enqueue routes do
(`routers/github.py`); the SDK polls immediately, and the yielded `get_db`
dependency would otherwise commit only after the response is sent.

Request-level failures reject the whole call before enqueue: empty or > 500
items, or summed source + destination path length over 200,000 characters
→ 422; `location="workspace"` or a name `validate_location_name`
rejects → 400; the Solution does not declare the location → 404. The request models carry no `mode` field; moves are always
cloud mode.

The job result (in `PlatformJobPublic.result`) is
`{"results": [FileMoveResult, ...], "moved": n, "failed": m}`, in request
order, including the items rejected at enqueue. `FileMoveResult` = `source`,
`destination` (requested), `path` (final; set on success), `renamed`,
`adopted` (true when an interrupted earlier move was completed — see
*Recognizing an interrupted earlier move*), `status_code` (200, 400, 403, 404, 409, 500), `error`. A 403 result's `error`
is the literal `"forbidden"`, matching the `/signed-urls` precedent. `error`
is a short code plus a message of at most 500 characters and never repeats S3
keys (they derive from `source` / `destination` and the location and scope);
`path` is set only when it differs from `destination` (a rename). With the
path cap, the worst-case result is about 1 MB.

### CLI (`api/bifrost/commands/files.py`)

`bifrost files move SOURCE DESTINATION --location L [--solution INSTALL] [--rename] [--timeout S]`
(`--solution` targets a Solution install's files, resolved and passed as
`?solution=` exactly as `files read` / `files stat` do)
enqueues and follows the job with the shared `poll_platform_job` helper
(`api/bifrost/platform_jobs.py`). `poll_platform_job` returns on job
`succeeded` regardless of item outcome and raises on `failed` with only the
error message, so the command itself:
- prints the final path and exits 0 only when `results[0].status_code == 200`;
  otherwise prints the item's status and error and exits non-zero;
- on job failure, catches the helper's exception, re-reads
  `GET /api/platform-jobs/{job_id}` once, and prints the stored checkpoint
  (`in_flight` included) before exiting non-zero — `poll_platform_job` raises
  with only the error message and discards the body;
- passes `--timeout` (default 4 h, the job timeout) instead of the helper's
  20-minute default, with the helper's existing "may still be running"
  wording on expiry.

No CLI command for `move-many`; a batch is an SDK primitive.

## Authorization (at enqueue)

Enqueue runs in the request with the caller's `UserPrincipal`, in the same
order `sdk_delete_file` uses:

1. Resolve effective scope (Solution context wins) — `resolve_effective_scope`.
2. `require_declared_solution_file_location`, then, for a Solution install,
   the shared inbound gate `inbound_allowed_for_context` that file reads
   apply: a sealed install refuses an outside caller with the audited 403
   `deny_file_policy` raises (the whole request, not per item).
3. Per item, before any existence probe: path validation, then file policy
   with denial audit. A path is a 400 result if `source == destination`, if
   `resolve_s3_key` rejects it, or if it is empty, ends in `/`, or contains
   an empty (`//`) or `.` segment — `resolve_s3_key` alone accepts those and
   would create a scope-root or folder-marker object — or if the destination
   path, plus 7 characters of rename headroom (` (1000)`), would exceed
   `FileMetadata.path` (2000) or its S3 key would exceed `s3_key` (2500),
   which would otherwise fail at the metadata step after a full copy. (The
   request-level 200,000-character path cap, above, bounds the terminal
   result — `publish_platform_job_update` broadcasts the whole result on the
   requester's and admins' notification channels.) Then:
   - `read` on the source (without it, delete + write would let a caller move
     an unreadable file into a path they can read),
   - `delete` on the source,
   - `write` on the requested destination.
   Denials use `authorize_file_policy` (returns a bool) and
   `emit_file_policy_deny` per denied check, not `require_file_policy` /
   `deny_file_policy`, which raise and commit on the first denial and so
   cannot record a result and continue. The audit rows commit with the job
   row.

   **Enqueue cost.** This is the one request still bounded by the SDK's 30 s
   client timeout, and it runs up to three policy evaluations per item. Each
   evaluation today repeats `install_org_id`, policy-row loading, policy-ref
   resolution, and claim pre-resolution. The enqueue service resolves the
   install org once per request, and memoizes policy rows and pre-resolved
   claims through an optional request-scoped cache object that
   `authorize_file_policy` passes into `FilePolicyService.is_allowed`, which
   uses it internally. The cascade stays in the canonical evaluator; nothing
   is re-implemented in the enqueue service. Inside a run, each
   `authorize_file_policy` also triggers `check_file`, which evaluates again
   for the run user (`access_check_policies.py`); it receives the same cache.
   The e2e suite times a 500-item enqueue through the workflow engine-socket
   path (the slower one, because of `check_file`) against a realistic policy
   set; if it does not fit comfortably inside the timeout, the item cap is
   lowered to what does and the cap is stated with that measurement.
4. Items that fail step 3 are recorded as results and left out of the job's
   work list. A job is always enqueued — even when every item was rejected,
   in which case its work list is empty and it completes immediately with
   those results — so every caller always gets `202` and one result shape.
   An empty-work-list job is enqueued with no `resource_lock_key` and the
   default priority, so a rejected request is never queued behind a running
   move in the same scope.

The payload stores, per authorized item, its request index, the source, and
the requested destination (the job re-derives S3 keys with `resolve_s3_key`);
plus the location, effective scope,
`solution_id`, install `organization_id`, `on_conflict`, the requester, the
policy principal enqueue evaluated (`policy_user_id` and its
`policy_authorities`, from `enforced_policy_principal`), and the pre-recorded
results of rejected items — enough to run after the request context is gone.
It holds no credentials.

Rename mode: enqueue authorizes only the requested destination, and a
candidate name can fall under a different policy (a file-level policy on that
exact name, or a requested destination with its own). So when step 3 picks a
new candidate, the job evaluates `write` on that candidate with
`FilePolicyService.evaluate` for `policy_user_id` with that user's current
roles (`load_policy_user_principal`) and the recorded authorities. A denial is
an audited per-item 403 `forbidden`, before any copy. The same check runs
before a numbered candidate is resumed or adopted: a copy's markers name its
source and row, not whose authorization created it, so a retry by a different
user could otherwise finish an interrupted move at a name that user may not
write. On that denial the copy, the source, and its metadata row are kept.

For a workflow caller, the principal at enqueue is the workflow engine's
principal, exactly as for `files.delete` today; the healthcare-user policy
semantics above are what direct API callers get. Platform-job visibility for
engine-created jobs (shared engine identity) is a known platform-wide issue
being fixed separately and is out of scope here.

Policy and claims are evaluated at enqueue (and again only for a renamed
destination the job picks). A grant revoked while a job is queued does not
stop its authorized items — the same semantics every other platform job has.

## Job: `files.move` (`api/src/jobs/platform/file_move.py`)

### Policy

```
PlatformJobPolicy(
    timeout_seconds=4 * 60 * 60,
    max_attempts=1,
    retry_on_runner_loss=False,       # a half-done item is not safely repeatable
    allow_running_cancellation=False, # cancel only while queued
    min_memory_headroom_mb=128,       # copies are server-side; no bytes in memory
)
```

Enqueued with `priority=50` (claims order by priority descending, default
100), so OAuth refresh, webhook renewal, and Solution deploys are claimed
ahead of a queued move.

Concurrency is per location and scope, not platform-wide:
`resource_lock_key = "files.move:" + sha256("{location}:{effective scope}")`
(hashed because location names have no length limit and the column is
`String(255)`), which the
scheduler enforces at claim time across replicas. Moves into one Solution's
`docs` location run one job at a time. The job runs items sequentially, and
a multi-GB copy takes on the order of minutes, so the operation docs give
batch-sizing guidance: a batch that would exceed the 4 h timeout should be
split (retries are idempotent, so a timed-out batch can simply be
re-submitted). A one-file move waits behind a
running building move in the same location — the expected latency for that
case, stated in the operation docs. Moves for other installs and orgs
proceed. Capacity impact: each running move holds one scheduler slot for its
duration; with many tenants moving at once, moves can occupy every slot on a
replica, and the platform-jobs capacity guidance (scale replicas when queue
wait grows) applies.

Cancellation is only available while the job is queued. The scheduler stops a
running job by terminating its process (SIGTERM, then SIGKILL after 5 s), not
cooperatively, so a running move is only ever stopped by timeout or runner
loss — both handled as **runner loss mid-item** below. Revisit cooperative
cancellation if operators need it.

Enqueue metadata: `title` = "Move N files in {location}" (location
truncated to fit `String(200)`), `resource_type` = `"file_location"`,
`resource_id` = `"{location}:{effective scope}"` truncated to `String(255)`,
`action_url` = none, and the standard platform-job notification.

Dedupe key: sha256 of (requester, the policy principal's user id and
authorities, location, effective scope, `on_conflict`, ordered items). The
requester is the acting user who sees the job; the policy principal
(`enforced_policy_principal`: a runtime's run user, plus `platform_admin` for
a Full Access workflow) is who the checks ran as, so two runs by one acting
user with different run users or powers never share a job. The enqueue
service computes the key from the request and the principal and looks for
an active job with that key **before** running any policy checks, so an
identical retry reuses the job without re-evaluating policy or writing
duplicate deny-audit rows. `enqueue_platform_job` still applies the key under
its own advisory lock, which covers two first-time requests racing. A
consequence, stated in the operation docs: retrying an identical request
while the first job is still queued returns that job, including any per-item
403s it recorded, even if the policy has since been fixed.

### Per-item execution

The handler runs items sequentially. Each item opens its own
`get_db_context()` (the runner passes no session; precedent
`workspace_bundle_import.py`), and ends with `commit` (success) or `rollback`
(anything else), so one failure cannot leave a session in a failed state for
later items.

**No exception escapes an item.** Every per-item failure — including DB
errors such as a dropped connection — becomes that item's result row (500 with
the error code), and the loop continues. A failure outside any item (for
example the checkpoint write raising) is raised as
`PlatformJobFailure(code, message, result=...)` carrying the compact
checkpoint state (below). The runner's generic `handler_error` path stores no
result, which would erase the record of completed moves, so it must never be
reached. The one exception that propagates as-is is `PlatformJobCancelled`,
which `save_checkpoint` raises when the job's lease was lost; the scheduler
already owns that outcome.

**Systemic failure.** A storage outage must not run every item to the end:
with botocore's default connect timeout and retries, each S3 call can take
minutes to fail, so 500 items would run into the 4 h timeout while holding a
scheduler slot and the scope's lock. After **3 consecutive** items fail with
a storage-level error (connection or endpoint errors, `AccessDenied` /
`InvalidAccessKeyId` / `SignatureDoesNotMatch` on the bucket, 5xx after
retries), the job stops and raises
`PlatformJobFailure("storage_unavailable", ..., result=<results so far, with
every remaining item marked 500 "not_attempted">)`, so callers and the
notification see a failed job and the full per-item picture.

1. **Lock.** `lock_file_mutation` (`pg_advisory_xact_lock`) on the source and
   destination paths in sorted order. These are transaction-scoped locks,
   compatible with transaction-mode PgBouncer. How long they are held depends
   on the copy path, so an API writer can never be pinned for hours:
   - **Single-part (≤ 5 GiB):** one transaction holds the locks from step 1
     through step 8, including the copy. The copy is bounded (one
     `CopyObject`), so an API `files.write` / `files.delete` on either path
     waits at most that long; its SDK caller gives up at 30 s, but the API
     request keeps its pooled connection until the lock frees. Writes to a
     file that is mid-move are expected to be rare and this is accepted.
   - **Multipart (> 5 GiB):** the locked transaction covers steps 1–3 and
     then ends (rollback; nothing was written). The copy runs unlocked; the
     S3 preconditions guard it instead (`CopySourceIfMatch` on every part for
     the source, `IfNoneMatch="*"` on complete for the destination). Steps
     5–8 run in a new transaction that re-takes both locks (source and final
     destination, sorted) and, before anything else, `HeadObject`s both:
     - destination missing, or its markers are not this item's → 409
       `destination_changed`; nothing is cleaned up (there is no copy of ours
       left, or the object there is someone else's);
     - source missing (deleted by a writer during the copy) → cleanup of our
       copy, 404 — the step-6 "no row, insert one" branch must never run
       here, or the deleted file would reappear at the destination;
     - source ETag changed → cleanup, 409 `source_changed`;
     - otherwise continue to step 5.
   The single-part hold depends on the deployment not setting PgBouncer
   `idle_transaction_timeout`/`query_timeout` or Postgres
   `idle_in_transaction_session_timeout` below the longest single-part copy;
   the repo's PgBouncer config sets none, but production values live outside
   the repo and the operation docs state the dependency.
2. **Source.** `HeadObject` the source. (The new `head_object` helper's
   contract: a `ClientError` whose code is `404` / `NoSuchKey` / `NotFound`
   returns `None`; anything else raises. `HeadObject` never raises
   `s3.exceptions.NoSuchKey`, so the existing `except NoSuchKey` pattern in
   `read_uploaded_file` must not be copied, and `file_exists` — which swallows
   every error as "missing" — must not be used. On AWS, `HeadObject` on a
   missing key returns 403, not 404, unless the credentials have
   `s3:ListBucket` on the bucket; 403 is not treated as missing, so the
   operation docs and deployment guidance state that `s3:ListBucket` is
   required.) Record its size, ETag, content type,
   and user metadata. If the source object is missing, check for an
   interrupted earlier move to **adopt** (see *Recognizing an interrupted
   earlier move*); if that does not apply → 404.
3. **Destination.** `HeadObject` the requested destination. If it exists and
   is this item's own copy from an interrupted run, **resume** (see
   *Recognizing an interrupted earlier move*) — in either mode, targeting the
   requested destination. Otherwise:
   - `error` mode: exists (and not ours) → 409 `{"reason": "file_exists", "path": ...}`
     (no version fields, so no read of the destination).
   - `rename` mode: split with `os.path.splitext` (`report.pdf` →
     `report (1).pdf`, `.env` → `.env (1)`, `a.tar.gz` → `a.tar (1).gz`).
     List the destination directory with `ListObjectsV2`
     (`Prefix` = the full resolved key prefix `"{location}/{scope}/{dir}/{stem} ("`,
     `Delimiter="/"`, following continuation tokens), keep only keys matching
     `^{re.escape(stem)} \((\d+)\){re.escape(ext)}$`. Before choosing a
     number, `HeadObject` each matching candidate: if one carries this item's
     markers, it is this item's copy from an interrupted run and is resumed
     (or adopted, from step 2) exactly as for the requested destination. A
     matching candidate is locked and `HeadObject`ed again before it is
     resumed or adopted: a writer can replace it between the first head and
     the lock, and its file must never be resumed (then cleaned up) or adopted.
     Otherwise take the lowest `n ≥ 1` not in that set. This is one
     `HeadObject` per existing `stem (n)ext` sibling — O(siblings), normally
     zero or a few. Lock that candidate and `HeadObject`
     it; if it now exists (a writer raced the listing), try the next `n`.
     Stop after 1000 attempted numbers with 409
     `{"reason": "no_available_name"}`.
4. **Copy** server-side with `CopySourceIfMatch=<source ETag>`, so the bytes
   copied are the bytes inspected in step 2. A failed precondition (412: the
   source was replaced since step 2) → cleanup, 409 `source_changed`, the
   same result as step 7's re-check. A 412 from `CompleteMultipartUpload`'s
   `IfNoneMatch="*"` is different: an object appeared at the *destination*,
   so the upload is aborted and the item reports 409 `file_exists` (no
   cleanup — there is no copy of ours to delete). AWS can also answer a
   concurrent conditional complete with 409 `ConditionalRequestConflict`,
   mapped the same way. A source that disappears mid-copy (`NoSuchKey` / 404
   from `CopyObject` or `UploadPartCopy`) → abort or cleanup, 404.

   ETags (in `CopySourceIfMatch`, the `bifrost-move-etag` marker, and every
   comparison) are stored and compared verbatim as `HeadObject` returns them,
   quotes and multipart `-N` suffix included.

   The copy is **self-identifying**: it carries the source's system metadata
   (`ContentType`, `ContentDisposition`, `ContentEncoding`,
   `ContentLanguage`, `CacheControl`, `Expires`) and user metadata from step
   2, plus three move markers: `bifrost-move-row` = the source metadata row's
   `id` (empty if none), `bifrost-move-source` = sha256 of the source S3 key
   (a hash, because S3 caps user metadata at 2 KB and keys can be 2500
   characters), and `bifrost-move-etag` = the source ETag. The markers stay on
   the object after the move; they are harmless and a later move replaces
   them.
   - ≤ 5 GiB: `CopyObject` with `MetadataDirective=REPLACE` and those
     values.
   - > 5 GiB: multipart upload created with those values;
     `UploadPartCopy` with `CopySourceRange` and part size
     `max(256 MiB, ceil(size / 10000))` (within S3's 5 MiB–5 GiB part limits
     and 10,000-part cap) and `CopySourceIfMatch` on every part;
     `CompleteMultipartUpload` with `IfNoneMatch="*"` so it cannot overwrite
     an object that appeared at the destination meanwhile;
     `AbortMultipartUpload` on any failure. `IfNoneMatch` on
     `CompleteMultipartUpload` needs botocore ≥ 1.35.x, so `aiobotocore`
     (unpinned today in the repo-root `pyproject.toml:127`; no lock file is
     committed) gets a minimum version that
     bundles it.

   Residual, stated in the operation docs: presigned uploads take no lock, so
   a browser upload can land at the destination between step 3's check and a
   single-part copy, and the copy overwrites it (`CopyObject` has no
   destination precondition on S3). The multipart path refuses instead.
5. **Verify** with `HeadObject` on the destination. First, ownership: the
   object must carry this item's copy markers (`bifrost-move-source` and
   `bifrost-move-row`). A presigned upload takes no lock and can replace the
   destination after a single-part copy; missing or foreign → roll back, 409
   `destination_changed`, nothing deleted (the multipart path makes the same
   check after re-taking its locks; a resumed copy already matched its
   markers). Then content length equals the source size. ETags are not
   compared — a copy of a multipart-uploaded object legitimately gets a
   different ETag. Mismatch → cleanup (below), 500
   `copy_verification_failed`.
6. **Metadata**, with Core `delete()` / `update()` / `insert()` statements
   only. The Solution write guard (`install_solution_write_guard`) rejects
   ORM flushes that dirty a row with `solution_id` set, and every RTM file
   row is one; Core statements bypass that guard by design, as
   `delete_metadata` already relies on.
   - Rows are keyed exactly as `record_file_write_metadata` keys them:
     Solution rows by `(solution_id, location, path)`; other rows by
     `(organization_id, location, path)` **with `solution_id IS NULL`**, where
     `organization_id = _scope_to_org_id(location, scope)`. The
     `solution_id IS NULL` filter is required: the partial unique indexes let
     a Solution row and an org row share `(organization_id, location, path)`,
     and the existing non-solution `delete_metadata` predicate omits it.
     `_scope_to_org_id` and its `_SCOPE_SKIP` sentinel (private in
     `file_storage/service.py`) move to `shared/file_paths.py` so both
     services import them.
   - Delete any existing row at the **final** destination path (a stale row
     whose object is gone would otherwise violate the unique index), then
     update the source row in place: new `path` and `s3_key`, `updated_by` =
     requester, `updated_at` = now; `id`, `created_by`, `created_at`, size,
     sha256, and content type are kept. If the source has no row, insert one
     for the destination from step 2's values (`sha256` NULL, `created_by` =
     requester). Executed, not yet committed.
7. **Delete source.** First `HeadObject` the source again: its ETag must still
   equal step 2's. Presigned browser uploads (`/signed-url` PUT +
   `/complete-upload`) take no advisory lock, so a user can replace the source
   mid-copy; deleting then would destroy the new upload. ETag changed →
   cleanup, 409 `source_changed` (the new upload stays where it is). Then
   delete with a delete that raises on failure (the existing
   `delete_raw_from_s3` swallows all errors and cannot be used) and
   `HeadObject` to confirm the source is gone.

   A presigned upload can also land on the destination after step 5 has
   verified our copy there and before this delete. The move still succeeds:
   step 5's verification is the move's linearization point, so the outcome
   (the upload at the destination, the source gone) is the one the upload
   would produce if it landed just after the move finished — an ordinary
   overwrite. Closing that window would need a second, unaddressable copy of
   every source until the end of the move.

   **Once the delete has been issued, the destination copy is never cleaned
   up** — a delete can succeed server-side while the client sees a timeout,
   and deleting the copy then would lose both. On any error after issuing it,
   `HeadObject` the source:
   - gone → continue to step 8 (success);
   - anything else (still present, or the head itself fails) → roll back the
     row, keep the copy, 500 `source_delete_unconfirmed`. "Still present" is
     not proof the delete failed: a delete the client saw time out can still
     be applied after the head. A retry resolves either way: if the source is
     gone it adopts the copy, if present it resumes.

   On a **resume** the re-check differs, because an earlier run's
   unconfirmed delete can land after this run's step 2: a missing source is
   never cleaned up but finished as an adopt (markers and size guard
   re-checked; commit, publish, `"adopted": true`), or, if the guards fail,
   rolled back with the copy kept, 500 `source_missing_during_resume`; a
   failing source head rolls back and keeps the copy too. If the adopt's own
   destination head raises, the item reports `storage_unavailable` or
   `internal_error` (by the error) and the copy is kept.

   Residual: an upload landing between the re-check and the delete
   (milliseconds) is still lost; stated in the operation docs.
8. **Commit**, then publish `file_change` `delete` (source) and `write`
   (final destination).

**Recognizing an interrupted earlier move.** A destination object is "this
move's copy" only when its markers match: `bifrost-move-source` equals the
sha256 of this item's source key **and** `bifrost-move-row` equals the `id`
of the metadata row currently at the source path. Size is never used as
evidence. The row id is what makes this safe: after a completed move, the
source path's old row now lives at the destination, so a later file at the
source path has a different row id and can never match.

- **Adopt** (source object missing, step 2). Steps 7 and 8 are separate, so a
  timeout or runner loss can land between them: the source object is deleted,
  the copy exists, and the rollback leaves the row at the source path. If a
  row exists at the source path and the **requested** destination's markers
  match, and the row's `size_bytes` (when set) equals the destination's
  content length, the item skips steps 3–5 and 7, runs step 6 against the
  requested destination, commits, publishes, and reports success with
  `"adopted": true`. An earlier run under `on_conflict="rename"` may have
  left the copy at a numbered candidate, and the interrupted run's mode is
  not recorded, so the same candidate scan as step 3 always runs, whatever
  this retry's `on_conflict`, and a matching candidate is adopted (after the
  numbered-destination write check). Otherwise → 404. Markers are the identity; the size check is
  only a guard that rejects a copy of an older version when the row has
  since been overwritten in place (`upsert_metadata` keeps the row id).
  Neither tells two same-size versions apart, and the row stores no durable
  version identity, so when more than one candidate passes, the item fails
  500 `recovery_ambiguous` and every copy is kept for an operator.
- **Already moved** (source object missing **and no row at the source
  path** — only then; a row at the source path means adopt or 404). A
  retry of an item that in fact completed — the job died after its commit, or
  the caller re-submits it — finds the source gone and the row moved. If the
  destination (requested, or a numbered candidate) carries
  `bifrost-move-source` = sha256(source key) and `bifrost-move-row` = the id of
  the row now at that destination, the item is a no-op success with
  `"adopted": true`. Retrying any item is therefore idempotent, as
  `docs/architecture/platform-jobs.md` expects of retries.
- **Resume** (source present, destination exists, step 3). If an earlier run
  died during or right after its copy, the orphan copy at the requested
  destination has matching markers and `bifrost-move-etag` equal to the
  source's current ETag. The item then treats that object as its own copy:
  it skips step 4 and continues from step 5, instead of reporting 409 or
  renaming around it. Under rename the orphan may be at a candidate; step 3's
  candidate scan finds it. A marker match with a different ETag (the source was
  replaced since) is a normal 409 / rename. A resumed copy is never cleaned
  up: a failed resume (any failure in steps 5–7, with the usual result code)
  rolls back and leaves the copy for a later retry or an operator, because the
  earlier run that made it may already have issued its source delete.

Residuals, stated in the operation docs: a source object deleted out of band
(not by a move) is a plain 404, and a source with no metadata row cannot be
adopted or resumed (no row id to match), so it reports 404 / 409 with the keys
in the result.

**Checkpoint (one write per item).** At the start of each item, before step
1, one `save_checkpoint` records compact state: `moved` / `failed` counts,
the failed items' results with their request indices (normally few — every
earlier item's outcome is already final, so the list is complete; it is
seeded from the payload's enqueue-time rejections before the first item, so
a 403 at enqueue is never read as a success), and
`in_flight` = that item's request index, source path, and requested
destination path (S3 keys derive from them); `phase` = "Moving n of N". Size and ETag are not needed:
recovery is marker-based. Items run strictly in request order, so
after a failure the caller can derive the outcome of every item: indices
below `in_flight.index` that are not in the failed list succeeded, and
indices above it never ran. Successful
items are not repeated in every checkpoint — the full per-item results list is
built only in the terminal result — so each broadcast stays small instead of
growing with the batch. `checkpoint_platform_job` / `save_checkpoint` gain
optional `current` / `total` arguments so the same single write also updates
progress (today a checkpoint writes only `result` and `phase`, and the
notification would sit at 0% for the whole job). That is a shared-transport
extension, as `docs/architecture/platform-jobs.md` asks for, not a
feature-specific status path.

**Cleanup** (failure in steps 4–7 before the source delete is issued; never
after — see step 7): delete the destination copy with the raising delete while
the path locks are still held, then roll back (the rollback releases the locks)
— only if this item created it (a resumed copy is never cleaned up; see
*Resume*). Cleanup first heads the destination and deletes only when the
object there still carries this item's copy markers: a presigned upload can
replace our copy at any time, and a missing or foreign object means there is
nothing of ours to remove (cleanup counts as done). If that head raises,
ownership is unproven, nothing is deleted and cleanup has failed. If cleanup
itself fails, the item's result is 500 `cleanup_failed`, and the job
publishes an operator log entry (`context.log`) naming both S3 keys, because
an orphaned copy under a new prefix can be readable there. When cleanup ran
because a step raised (a head, the metadata write), the item reports that
error as `storage_unavailable` or `internal_error` instead, with the copy
kept and the operator log entry still published.

**Runner loss mid-item, including timeout** (the job is not retried). The job
fails; its stored result is the last checkpoint, whose `in_flight` names the
interrupted item. What a retry of that item does depends on where it stopped:
- During steps 1–6: the source and its row are untouched; at worst a copy
  sits at the destination. A retry recognizes it by its markers and resumes
  (above) rather than reporting 409 or renaming around it.
- Between steps 7 and 8: the retry adopts the destination copy (above),
  including a renamed candidate.
- After step 8 but before the next checkpoint: `in_flight` still names this
  item although it committed; a retry reports it as already moved (above).
- A multipart copy cut off mid-way leaves incomplete parts; deployments should
  have an `AbortIncompleteMultipartUpload` lifecycle rule on the bucket as the
  backstop (stated in the operation docs).

## Surfaces

| Layer | Change |
|---|---|
| Job | `api/src/jobs/platform/file_move.py` (payload model v1, handler, policy, definition); registered in `api/src/jobs/platform/registry.py` |
| Enqueue service | `sdk_enqueue_file_moves` in `api/shared/sdk_files.py`: active-job lookup by dedupe key first; then `resolve_effective_scope`, `require_declared_solution_file_location`, path validation, `authorize_file_policy` + `emit_file_policy_deny` with per-request memoization (install org, policy rows, pre-resolved claims); then `enqueue_platform_job` with dedupe key, requester, resource metadata, title, and notification per the platform-job contract |
| Platform jobs (shared) | `checkpoint_platform_job` / `PlatformJobContext.save_checkpoint` gain optional `current` / `total` progress arguments (`api/src/services/platform_jobs.py`, `api/src/jobs/platform/base.py`) |
| Shared helper | `_scope_to_org_id` and its `_SCOPE_SKIP` sentinel move together from `file_storage/service.py` to `shared/file_paths.py` (public names); the existing callers in `record_signed_upload_metadata` and `record_file_write_metadata` are updated, and both metadata paths import them |
| Per-item move | `api/src/services/file_storage/` (lock, head, rename, copy, verify, metadata, strict delete, cleanup) |
| Storage | `S3StorageClient`: `head_object`, `copy_object_if_match` (single + multipart part-copy), `delete_object_strict`, `list_keys_with_prefix` (delimiter + continuation). Assumes the store implements `CopyObject`, `UploadPartCopy` with `CopySourceRange`, and the `x-amz-copy-source-if-match` precondition: true for AWS S3; for SeaweedFS (`chrislusf/seaweedfs:latest` in dev/test) `CopyObject` is exercised by e2e, while `UploadPartCopy` and precondition enforcement are not proven by CI |
| Metadata | `FilePolicyService.move_metadata(...)` with write-path keying |
| HTTP | `FileMoveRequest`, `FileMoveBatchRequest`, `FileMoveResult` (job result model); `POST /api/files/move` and `POST /api/files/move-many` in `api/src/routers/files.py`, each returning `202 PlatformJobAccepted` + `Location` |
| Registries | `operation_catalog.py`: `workspace.files.move` (CLI `files move`) and `workspace.files.move_many` (CLI excluded), both with `async_policy=OperationAsyncPolicy.PLATFORM_JOB`. Both carry explicit `mcp` exclusions (`move` is not in `_MCP_VERBS`, and no file-content MCP tools exist — the `bifrost_file_delete` binding is dangling and is not copied), plus `native_builder=True` (as `workspace.files.delete`) and a `manifest` exclusion worded like delete's. `access_list.py` entries for both routes. Both enqueue paths added to `FILES_ROUTE_PATHS` (and its "All eight" comment updated). `api/tests/unit/services/test_operation_inventory.py` counts updated; both ops added to `CANONICAL_OPERATIONS` in `api/tests/unit/services/test_operation_catalog.py`. |
| Python SDK | `files.move`, `files.move_many` (enqueue + poll) |
| CLI | `bifrost files move` (enqueue + `poll_platform_job`) |
| Generated | `client/src/lib/v1.d.ts`; skill-truth appendices (`python api/scripts/skill-truth/generate.py`). The request DTOs stay router-local like the other file DTOs, but `FileMoveResult` — a response shape the CLI parses out of the job result — is defined in `api/src/models/contracts/cli.py` so `test_contract_version.py` fingerprints it; the fingerprint is refreshed as an additive change (no `MIN_CLI_VERSION` bump). No `CLIFile*`/`SDKFile*` sibling models are added for move (the existing ones for other file operations are unused). |
| Product Updates | One feature entry ("Move files in a location") with its disposition file under `product-updates/dispositions/` |

## Testing

- **Job handler (unit)**: per-item order — lock, head, conflict, copy with
  `CopySourceIfMatch`, size verify, metadata, strict source delete, commit,
  publish; the source is never deleted before verify; verify mismatch and
  copy failure each roll back and remove the destination copy (a failure
  after the source delete is issued never does — see *Source delete
  outcome*); cleanup failure reports `cleanup_failed` and logs; each
  item ends in commit or rollback (a 409 on item 2 does not break item 3);
  one compact checkpoint per item, written at the start of the item, carrying
  `in_flight` and progress (`current`/`total`), not the cumulative success
  list; a DB error inside an item (dropped connection) becomes a 500 result
  and later items still run; an outer failure raises `PlatformJobFailure`
  carrying the checkpoint state; metadata uses Core statements and succeeds on
  Solution-scoped rows with the write guard installed.
- **Source changed (unit)**: the pre-delete re-`HeadObject` returns a different
  ETag → 409 `source_changed`, destination copy removed, the new source
  object and its row untouched.
- **Adoption (unit + e2e)**: simulate a crash between source delete and
  commit (source object gone, row still at source path, destination copy with
  matching markers) → the retry reports success with `adopted: true` and the
  row moves. A destination of the same size but without markers, markers for
  a different source key, a marker row id that differs from the source path's
  current row (a later file at that path), or no row at the source → 404 and
  nothing changes.
- **Resume (unit)**: crash after the copy with the source intact → the retry
  finds the marked orphan with `bifrost-move-etag` equal to the source ETag,
  skips the copy, and completes; under `on_conflict="rename"` it completes
  at the orphan's location (requested destination or candidate) instead of
  creating a new candidate; a
  marked orphan whose ETag no longer matches the source is a plain 409.
- **Copy markers (unit)**: single-part `CopyObject` uses
  `MetadataDirective=REPLACE` and re-sends the source's system and user
  metadata plus the three markers; multipart sets the same on
  `create_multipart_upload` and passes `IfNoneMatch="*"` to
  `CompleteMultipartUpload`; a 412 from a copy (`CopySourceIfMatch`) maps to
  409 `source_changed`, a 412 from complete (`IfNoneMatch`) aborts the
  upload and maps to 409 `file_exists` with no cleanup.
- **Lock scope (unit)**: single-part holds the path locks across the copy;
  multipart releases them after step 3 and re-takes them for steps 5–8, and a
  destination whose markers changed after the copy → 409
  `destination_changed` without deleting it.
- **Source delete outcome (unit)**: the delete raises after succeeding
  server-side (source head → gone) → success, copy kept; delete raises and
  source still present, or the confirming head fails → row rolled back, copy
  kept (never cleaned up), `source_delete_unconfirmed`; a retry resumes it
  (source present) or adopts it (source gone).
- **Multipart re-check (unit)**: after the unlocked copy, destination
  missing or markers changed → 409 `destination_changed`, nothing deleted;
  source missing → our copy cleaned up, 404, no row inserted; `NoSuchKey` on
  `UploadPartCopy` → abort, 404; `ConditionalRequestConflict` on complete →
  409 `file_exists`.
- **Systemic stop (unit)**: three consecutive storage-level failures →
  `storage_unavailable` with the remaining items `not_attempted`; a
  non-storage failure resets the count.
- **Adopt guards (unit)**: row size differing from the destination's →
  404, not adopted; a row present at the source never takes the "already
  moved" branch; enqueue-time 403s appear in the first checkpoint's failed
  list.
- **Rename recovery (unit + e2e)**: a crash after copying to `report (1).pdf`
  (before or after the source delete) → the retry finds the candidate by its
  markers and resumes / adopts it rather than creating `report (2).pdf` or
  reporting 404.
- **Already moved (unit)**: re-submitting a completed item → no-op success,
  `adopted: true`.
- **HeadObject contract (unit)**: a `ClientError` with code `404` returns
  `None`; other errors raise.
- **Request size (unit)**: summed path length over 200,000 → 422; a request
  whose items are all rejected is enqueued without a `resource_lock_key`.
- **Job outcome (unit)**: three consecutive storage-level failures →
  `PlatformJobFailure("storage_unavailable")` with full results; a lost lease
  surfaces as `PlatformJobCancelled`, not `PlatformJobFailure`; the checkpoint
  carries `in_flight.index` and failed indices, is written at the start of
  each item (so items failing at steps 1–3 appear in the next item's failed
  list), and items run in request order.
- **Rename (unit)**: `report (1).pdf`, skips taken numbers, extensionless and
  dotfile names, `a.tar (1).gz`; ignores `report (draft).pdf` and keys under a
  `report (x)/` subfolder; finds the lowest free number when taken numbers
  span more than one listing page; a candidate that appears between listing
  and lock moves on to the next number; cap → `no_available_name`.
- **Storage (unit)**: single vs multipart selection at 5 GiB; part size keeps
  parts ≤ 10,000 and ≥ 5 MiB; content type + metadata passed to
  `create_multipart_upload`; `CopySourceIfMatch` is passed on single and
  part copies (asserted on the call — SeaweedFS may not enforce the
  precondition, so CI proves the parameter is sent and only S3 proves it is
  honored); abort on failure; strict delete raises.
- **Metadata (unit)**: solution-row and org-row keying (including a superuser
  `scope="global"` move); the org-row stale-destination delete leaves a
  Solution row at the same `(organization_id, location, path)` intact; stale
  destination row removed; missing source row inserts.
- **Enqueue (unit + e2e)**: request-level 400/404/422; per-item 400/403
  recorded (403 audited via `emit_file_policy_deny`) and excluded from the
  work list; all-rejected still enqueues and completes with only those
  results; 202 + `Location`; an identical retry reuses the active job without
  re-running policy or writing deny-audit rows; caller can read the job;
  cancel succeeds while queued and is refused while running; empty,
  trailing-slash, `//`, and `.`-segment paths and over-length destinations
  are 400 results; an invalid location name is one request-level 400, not
  per-item results; policy rows and install org are loaded once per distinct
  prefix through the cache passed into `is_allowed`, not per item; the job row
  is committed and published before the 202 is returned.
- **Enqueue budget (e2e)**: time a 500-item enqueue through the workflow
  engine-socket path (which adds `check_file`) with a realistic policy set;
  the item cap is set from this measurement.
- **Concurrency (unit)**: two move jobs for the same location and scope share
  a `resource_lock_key` and run one at a time; jobs for different scopes do
  not block each other.
- **E2E (`api/tests/e2e/platform/`)**: Solution-scoped `move_many` through the
  real scheduler job in a declared location with a path-prefix read policy —
  a principal with read on the old prefix loses access and one with read on
  the new prefix gains it; metadata row moved; events published. The multipart
  path gets live coverage by passing the storage helper a lowered multipart
  threshold and part size (keyword arguments with production defaults, not a
  setting) so a ~11 MiB object copies as three parts against SeaweedFS; this
  proves `UploadPartCopy` and that SeaweedFS accepts (rather than rejects)
  `IfNoneMatch` on `CompleteMultipartUpload`. Whether SeaweedFS *enforces*
  the conditionals is still not asserted — only AWS proves that.
- **SDK/CLI (unit)**: enqueue + poll to terminal, per-item results, `move`
  raising the item's error, `TimeoutError` wording with the job id; CLI uses
  `poll_platform_job`, exits non-zero on an item failure, re-reads the job
  and prints its checkpoint on job failure, and honors `--timeout`; the SDK's
  default wait equals the 4 h job timeout.
- **Platform jobs (unit)**: `checkpoint_platform_job` with `current`/`total`
  sets `progress_percent` (computed from them, which the notification reads)
  in the same write; existing
  callers that pass neither are unchanged.
- **Registry tripwires**: `test_operation_catalog.py` (both ops added to
  `CANONICAL_OPERATIONS`, `cli_path=None` for `move_many`, `mcp_name=None`
  for both),
  `api/tests/unit/services/test_operation_inventory.py`, access-list tests, `test_contract_version.py`,
  skill-truth freshness, `./test.sh quality api`.
- **Platform-job contract** items from `docs/architecture/platform-jobs.md`
  that this policy affects: payload validation and registry dispatch,
  structured failure, duplicate enqueue, runner loss without retry,
  cancellation, notification projection, and caller visibility.
