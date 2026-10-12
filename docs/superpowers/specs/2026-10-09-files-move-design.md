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
    timeout_seconds: float = 1800,
) -> list[{"source", "destination", "path", "renamed", "status_code", "error"}]

files.move(
    source: str,
    destination: str,
    location: str,                                       # required
    on_conflict: "error" | "rename" = "error",
    scope: str | None = None,
    timeout_seconds: float = 1800,
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

Request-level failures reject the whole call before enqueue: empty or > 500
items → 422; `location="workspace"` → 400; the Solution does not declare the
location → 404. The request models carry no `mode` field; moves are always
cloud mode.

The job result (in `PlatformJobPublic.result`) is
`{"results": [FileMoveResult, ...], "moved": n, "failed": m}`, in request
order, including the items rejected at enqueue. `FileMoveResult` = `source`,
`destination` (requested), `path` (final; set on success), `renamed`,
`adopted` (true when an interrupted earlier move was completed — see
*Adopting an interrupted move*), `status_code` (200, 400, 403, 404, 409, 500), `error`. A 403 result's `error`
is the literal `"forbidden"`, matching the `/signed-urls` precedent.

### CLI (`api/bifrost/commands/files.py`)

`bifrost files move SOURCE DESTINATION --location L [--rename] [--timeout S]`
enqueues and follows the job with the shared `poll_platform_job` helper
(`api/bifrost/platform_jobs.py`). `poll_platform_job` returns on job
`succeeded` regardless of item outcome and raises on `failed` with only the
error message, so the command itself:
- prints the final path and exits 0 only when `results[0].status_code == 200`;
  otherwise prints the item's status and error and exits non-zero;
- on job failure, prints the job's stored checkpoint (`in_flight` included)
  before exiting non-zero;
- passes `--timeout` (default 4 h, the job timeout) instead of the helper's
  20-minute default, with the helper's existing "may still be running"
  wording on expiry.

No CLI command for `move-many`; a batch is an SDK primitive.

## Authorization (at enqueue)

Enqueue runs in the request with the caller's `UserPrincipal`, in the same
order `sdk_delete_file` uses:

1. Resolve effective scope (Solution context wins) — `resolve_effective_scope`.
2. `require_declared_solution_file_location`.
3. Per item, before any existence probe: path validation, then file policy
   with denial audit. A path is a 400 result if `source == destination`, if
   `resolve_s3_key` rejects it, or if it is empty, ends in `/`, or contains
   an empty (`//`) or `.` segment — `resolve_s3_key` alone accepts those and
   would create a scope-root or folder-marker object. Then:
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
   install org once per request and memoizes policy rows and pre-resolved
   claims per distinct policy prefix for the request's duration, so a batch
   whose files share a few prefixes costs a few policy loads, not hundreds.
   The e2e suite times a 500-item enqueue against a realistic policy set; if
   it does not fit comfortably inside the timeout, the item cap is lowered to
   what does and the cap is stated with that measurement.
4. Items that fail step 3 are recorded as results and left out of the job's
   work list. A job is always enqueued — even when every item was rejected,
   in which case its work list is empty and it completes immediately with
   those results — so every caller always gets `202` and one result shape.

The payload stores, per authorized item, the source, the requested
destination, and the resolved S3 keys; plus the location, effective scope,
`solution_id`, install `organization_id`, `on_conflict`, the requester, and the
pre-recorded results of rejected items — enough to run after the request
context is gone. It holds no credentials.

Rename mode: candidates differ from the requested destination only in the file
name (same directory) and are authorized by the requested destination's
`write` grant. A file-level policy written for one exact candidate name is the
only case where a candidate's effective policy could differ; the operation
docs state this.

For a workflow caller, the principal at enqueue is the workflow engine's
principal, exactly as for `files.delete` today; the healthcare-user policy
semantics above are what direct API callers get. Platform-job visibility for
engine-created jobs (shared engine identity) is a known platform-wide issue
being fixed separately and is out of scope here.

Policy and claims are evaluated once, at enqueue. A grant revoked while a job
is queued does not stop its authorized items — the same semantics every other
platform job has.

## Job: `files.move` (`api/src/jobs/platform/file_move.py`)

### Policy

```
PlatformJobPolicy(
    timeout_seconds=4 * 60 * 60,
    max_attempts=1,
    max_concurrency=1,                # one move job at a time across replicas
    retry_on_runner_loss=False,       # a half-done item is not safely repeatable
    allow_running_cancellation=False, # cancel only while queued
    min_memory_headroom_mb=128,       # copies are server-side; no bytes in memory
)
```

Enqueued with `priority=50` (claims order by priority descending, default
100), so OAuth refresh, webhook renewal, and Solution deploys are claimed
ahead of a queued move. Capacity impact: a running move holds one scheduler
slot for its duration; `max_concurrency=1` caps that at one slot platform-wide.

Cancellation is only available while the job is queued. The scheduler stops a
running job by terminating its process (SIGTERM, then SIGKILL after 5 s), not
cooperatively, so a running move is only ever stopped by timeout or runner
loss — both handled as **runner loss mid-item** below. Revisit cooperative
cancellation if operators need it.

Enqueue metadata: `title` = "Move N files in {location}", `resource_type` =
`"file_location"`, `resource_id` = `"{location}:{effective scope}"`,
`action_url` = none, and the standard platform-job notification.

Dedupe key: sha256 of (requester, location, effective scope, `on_conflict`,
ordered items). The enqueue service computes it from the request and looks for
an active job with that key **before** running any policy checks, so an
identical retry reuses the job without re-evaluating policy or writing
duplicate deny-audit rows. `enqueue_platform_job` still applies the key under
its own advisory lock, which covers two first-time requests racing.

### Per-item execution

The handler runs items sequentially. Each item opens its own
`get_db_context()` (the runner passes no session; precedent
`workspace_bundle_import.py`), and ends with `commit` (success) or `rollback`
(anything else), so one failure cannot leave a session in a failed state for
later items.

**No exception escapes an item.** Every per-item failure — including DB
errors such as a dropped connection — becomes that item's result row (500 with
the error code), and the loop continues. A failure outside any item (for
example the checkpoint write itself) is raised as
`PlatformJobFailure(code, message, result=...)` carrying the compact
checkpoint state (below). The runner's generic `handler_error` path stores no
result, which would erase the record of completed moves, so it must never be
reached.

1. **Lock.** `lock_file_mutation` (`pg_advisory_xact_lock`) on the source and
   destination paths in sorted order. These are transaction-scoped locks,
   compatible with transaction-mode PgBouncer. The transaction stays open
   through the copy, holding one connection in the job's own process.
   Consequence for other callers: an API `files.write` / `files.delete` on
   either path blocks on the same lock for the rest of the item; its SDK
   caller gives up at 30 s, but the API request keeps its pooled connection
   until the lock frees. Writes to a file that is mid-move are expected to be
   rare and this is accepted, not engineered around. This also depends on the
   deployment not setting PgBouncer `idle_transaction_timeout`/`query_timeout`
   or Postgres `idle_in_transaction_session_timeout` below the longest single
   copy; the repo's PgBouncer config sets none, but production values live
   outside the repo and the operation docs state the dependency.
2. **Source.** `HeadObject` the source. Record its size, ETag, content type,
   and user metadata. If the source object is missing, check for an
   **interrupted earlier move** (see *Adopting an interrupted move*); if that
   does not apply → 404.
3. **Destination.** `HeadObject` the destination.
   - `error` mode: exists → 409 `{"reason": "file_exists", "path": ...}`
     (no version fields, so no read of the destination).
   - `rename` mode: split with `os.path.splitext` (`report.pdf` →
     `report (1).pdf`, `.env` → `.env (1)`, `a.tar.gz` → `a.tar (1).gz`).
     List the destination directory with `ListObjectsV2`
     (`Prefix="{dir}/{stem} ("`, `Delimiter="/"`, following continuation
     tokens), keep only keys matching `^{stem} \((\d+)\){ext}$`, and take
     the lowest `n ≥ 1` not in that set. Lock that candidate and `HeadObject`
     it; if it now exists (a writer raced the listing), try the next `n`.
     Stop after 1000 attempted numbers with 409
     `{"reason": "no_available_name"}`.
4. **Copy** server-side with `CopySourceIfMatch=<source ETag>`, so the bytes
   copied are the bytes inspected in step 2 (a concurrent overwrite fails the
   copy instead of moving different content).
   - ≤ 5 GiB: `CopyObject` (default `MetadataDirective=COPY` keeps content
     type and metadata).
   - > 5 GiB: multipart upload created with the source's `ContentType` and
     `Metadata`; `UploadPartCopy` with `CopySourceRange` and part size
     `max(256 MiB, ceil(size / 10000))` (within S3's 5 MiB–5 GiB part limits
     and 10,000-part cap) and `CopySourceIfMatch` on every part;
     `AbortMultipartUpload` on any failure.
5. **Verify** with `HeadObject` on the destination: content length equals the
   source size. ETags are not compared — a copy of a multipart-uploaded object
   legitimately gets a different ETag. Mismatch → cleanup (below), 500
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
     `_scope_to_org_id` (private in `file_storage/service.py`) moves to
     `shared/file_paths.py` so both services import it.
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
   `HeadObject` to confirm the source is gone. Failure → cleanup, 500
   `source_delete_failed`. Residual: an upload landing between the re-check
   and the delete (milliseconds) is still lost; stated in the operation docs.
8. **Commit**, then publish `file_change` `delete` (source) and `write`
   (final destination).

**Adopting an interrupted move** (step 2, source object missing). Steps 7 and
8 are separate, so a timeout or runner loss can land between them: the source
object is deleted, the destination copy exists, and the transaction rolls
back, leaving the metadata row still at the source path. A later move of the
same item repairs this instead of failing: when the source object is missing,
a metadata row exists at the source path with a non-null `size_bytes`, and the
**requested** destination object exists with exactly that size, the item skips
steps 3–5 and 7, runs step 6 against the requested destination, commits,
publishes, and reports success with `"adopted": true`. Any other combination
is a plain 404. This needs the row's `size_bytes`; rows with no recorded size
are not adopted (404, and the checkpoint/operator log carries the keys). Under
`on_conflict="rename"` adoption still targets the requested destination, never
a renamed candidate.

**Checkpoint (one write per item).** Before an item's copy starts, one
`save_checkpoint` records compact state: `moved` / `failed` counts, the
failed items' results (normally few), and `in_flight` = that item's source
key, destination key, size, and ETag; `phase` = "Moving n of N". Successful
items are not repeated in every checkpoint — the full per-item results list is
built only in the terminal result — so each broadcast stays small instead of
growing with the batch. `checkpoint_platform_job` / `save_checkpoint` gain
optional `current` / `total` arguments so the same single write also updates
progress (today a checkpoint writes only `result` and `phase`, and the
notification would sit at 0% for the whole job). That is a shared-transport
extension, as `docs/architecture/platform-jobs.md` asks for, not a
feature-specific status path.

**Cleanup** (failure in steps 4–7): roll back, then delete the destination
copy with the raising delete — only if this item created it. If cleanup
itself fails, the item's result is 500 `cleanup_failed` with both S3 keys, and
the job publishes an operator log entry (`context.log`) naming them, because
an orphaned copy under a new prefix can be readable there.

**Runner loss mid-item, including timeout** (the job is not retried). The job
fails; its stored result is the last checkpoint, whose `in_flight` names the
interrupted item. What a retry of that item does depends on where it stopped:
- During steps 1–6: the source and its row are untouched; at worst an
  untracked copy sits at the destination. Retrying with `on_conflict="error"`
  reports 409 `file_exists` for that copy rather than overwriting it; with
  `"rename"` it moves the source to `dest (1)` and the orphan remains at
  `dest`. Both are reported, not auto-resolved, because the orphan's bytes
  cannot be proven to be the source's.
- Between steps 7 and 8: the retry adopts the destination copy (above).
- A multipart copy cut off mid-way leaves incomplete parts; deployments should
  have an `AbortIncompleteMultipartUpload` lifecycle rule on the bucket as the
  backstop (stated in the operation docs).

## Surfaces

| Layer | Change |
|---|---|
| Job | `api/src/jobs/platform/file_move.py` (payload model v1, handler, policy, definition); registered in `api/src/jobs/platform/registry.py` |
| Enqueue service | `sdk_enqueue_file_moves` in `api/shared/sdk_files.py`: active-job lookup by dedupe key first; then `resolve_effective_scope`, `require_declared_solution_file_location`, path validation, `authorize_file_policy` + `emit_file_policy_deny` with per-request memoization (install org, policy rows, pre-resolved claims); then `enqueue_platform_job` with dedupe key, requester, resource metadata, title, and notification per the platform-job contract |
| Platform jobs (shared) | `checkpoint_platform_job` / `PlatformJobContext.save_checkpoint` gain optional `current` / `total` progress arguments (`api/src/services/platform_jobs.py`, `api/src/jobs/platform/base.py`) |
| Shared helper | `_scope_to_org_id` moves from `file_storage/service.py` to `shared/file_paths.py` (public name) for use by both the write-metadata and move-metadata paths |
| Per-item move | `api/src/services/file_storage/` (lock, head, rename, copy, verify, metadata, strict delete, cleanup) |
| Storage | `S3StorageClient`: `head_object`, `copy_object_if_match` (single + multipart part-copy), `delete_object_strict`, `list_keys_with_prefix` (delimiter + continuation). Assumes the store implements `CopyObject`, `UploadPartCopy` with `CopySourceRange`, and the `x-amz-copy-source-if-match` precondition: true for AWS S3; for SeaweedFS (`chrislusf/seaweedfs:latest` in dev/test) `CopyObject` is exercised by e2e, while `UploadPartCopy` and precondition enforcement are not proven by CI |
| Metadata | `FilePolicyService.move_metadata(...)` with write-path keying |
| HTTP | `FileMoveRequest`, `FileMoveBatchRequest`, `FileMoveResult` (job result model); `POST /api/files/move` and `POST /api/files/move-many` in `api/src/routers/files.py`, each returning `202 PlatformJobAccepted` + `Location` |
| Registries | `operation_catalog.py`: `workspace.files.move` (CLI `files move`) and `workspace.files.move_many` (CLI excluded), both with `async_policy=OperationAsyncPolicy.PLATFORM_JOB`. Both carry explicit `mcp` exclusions (`move` is not in `_MCP_VERBS`, and no file-content MCP tools exist — the `bifrost_file_delete` binding is dangling and is not copied), plus `native_builder` and `manifest` dispositions. `access_list.py` entries for both routes. Both enqueue paths added to `FILES_ROUTE_PATHS` (and its "All eight" comment updated). `api/tests/unit/services/test_operation_inventory.py` counts updated. |
| Python SDK | `files.move`, `files.move_many` (enqueue + poll) |
| CLI | `bifrost files move` (enqueue + `poll_platform_job`) |
| Generated | `client/src/lib/v1.d.ts`; skill-truth appendices (`python api/scripts/skill-truth/generate.py`). Router-local request DTOs are not CLI-contract fingerprinted (`test_contract_version.py`), so no fingerprint change is expected; confirmed by running that test. No `CLIFile*`/`SDKFile*` sibling models are added for move (the existing ones for other file operations are unused). |
| Product Updates | One feature entry ("Move files in a location") with its disposition file under `product-updates/dispositions/` |

## Testing

- **Job handler (unit)**: per-item order — lock, head, conflict, copy with
  `CopySourceIfMatch`, size verify, metadata, strict source delete, commit,
  publish; the source is never deleted before verify; verify mismatch,
  source-delete failure, and copy failure each roll back and remove the
  destination copy; cleanup failure reports `cleanup_failed` and logs; each
  item ends in commit or rollback (a 409 on item 2 does not break item 3);
  one compact checkpoint per item, written before the copy, carrying
  `in_flight` and progress (`current`/`total`), not the cumulative success
  list; a DB error inside an item (dropped connection) becomes a 500 result
  and later items still run; an outer failure raises `PlatformJobFailure`
  carrying the checkpoint state; metadata uses Core statements and succeeds on
  Solution-scoped rows with the write guard installed.
- **Source changed (unit)**: the pre-delete re-`HeadObject` returns a different
  ETag → 409 `source_changed`, destination copy removed, the new source
  object and its row untouched.
- **Adoption (unit + e2e)**: simulate a crash between source delete and
  commit (source object gone, row still at source path, destination object of
  the row's size) → the retry reports success with `adopted: true` and the row
  moves; the same with a size mismatch, a null `size_bytes`, or a missing
  destination → 404 and nothing changes.
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
  trailing-slash, `//`, and `.`-segment paths are 400 results; policy rows and
  install org are loaded once per distinct prefix, not per item.
- **Enqueue budget (e2e)**: time a 500-item enqueue with a realistic policy
  set; the item cap is set from this measurement.
- **E2E (`tests/e2e/platform/`)**: Solution-scoped `move_many` through the
  real scheduler job in a declared location with a path-prefix read policy —
  a principal with read on the old prefix loses access and one with read on
  the new prefix gains it; metadata row moved; events published. The > 5 GiB
  multipart path has no live coverage (fixture size) and is covered only by
  the storage unit tests — stated deliberately.
- **SDK/CLI (unit)**: enqueue + poll to terminal, per-item results, `move`
  raising the item's error, `TimeoutError` wording with the job id; CLI uses
  `poll_platform_job`, exits non-zero on an item failure, prints the
  checkpoint on job failure, and honors `--timeout`.
- **Platform jobs (unit)**: `checkpoint_platform_job` with `current`/`total`
  updates progress and the notification percent in the same write; existing
  callers that pass neither are unchanged.
- **Registry tripwires**: `test_operation_catalog.py`,
  `api/tests/unit/services/test_operation_inventory.py`, access-list tests, `test_contract_version.py`,
  skill-truth freshness, `./test.sh quality api`.
- **Platform-job contract** items from `docs/architecture/platform-jobs.md`
  that this policy affects: payload validation and registry dispatch,
  structured failure, duplicate enqueue, runner loss without retry,
  cancellation, notification projection, and caller visibility.
