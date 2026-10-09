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
  process's local filesystem is meaningless. `mode="local"` → 400.
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
raises the item's error with its status code. `location` is required on both,
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
`status_code` (200, 400, 403, 404, 409, 500), `error`. A 403 result's `error`
is the literal `"forbidden"`, matching the `/signed-urls` precedent.

### CLI (`api/bifrost/commands/files.py`)

`bifrost files move SOURCE DESTINATION --location L [--rename]` enqueues and
follows the job with the shared `poll_platform_job` helper
(`api/bifrost/platform_jobs.py`), then prints the final path. No CLI command
for `move-many`; a batch is an SDK primitive.

## Authorization (at enqueue)

Enqueue runs in the request with the caller's `UserPrincipal`, in the same
order `sdk_delete_file` uses:

1. Resolve effective scope (Solution context wins) — `resolve_effective_scope`.
2. `require_declared_solution_file_location`.
3. Per item, before any existence probe: path validation (`source ==
   destination`, or a path `resolve_s3_key` rejects → a 400 result), then
   file policy with denial audit via `require_file_policy` /
   `deny_file_policy`:
   - `read` on the source (without it, delete + write would let a caller move
     an unreadable file into a path they can read),
   - `delete` on the source,
   - `write` on the requested destination.
4. Items that fail step 3 are recorded as results and left out of the job's
   work list. If no item passes, nothing is enqueued and the endpoint returns
   the status shared by every item when they agree (403 or 400), otherwise
   400 with the per-item results in the error detail.

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

Policy and claims are evaluated once, at enqueue. A grant revoked while a job
is queued does not stop its authorized items — the same semantics every other
platform job has.

## Job: `files.move` (`api/src/jobs/platform/file_move.py`)

### Policy

```
PlatformJobPolicy(
    timeout_seconds=4 * 60 * 60,
    max_attempts=1,
    retry_on_runner_loss=False,      # a half-done item is not safely repeatable
    allow_running_cancellation=True, # honored between items only
    min_memory_headroom_mb=128,      # copies are server-side; no bytes in memory
)
```

Dedupe key: sha256 of (requester, location, effective scope, `on_conflict`,
ordered items). Retrying an identical enqueue while the job is active reuses
it; the requester can always read the reused job.

### Per-item execution

The handler runs items sequentially. Each item gets its own transaction in the
job's DB session, and every item ends with `commit` (success) or `rollback`
(anything else) before the next item starts, so one failure cannot leave the
session in a failed state for later items.

1. **Lock.** `lock_file_mutation` (`pg_advisory_xact_lock`) on the source and
   destination paths in sorted order. These are transaction-scoped locks,
   compatible with transaction-mode PgBouncer. The transaction stays open
   through the copy, holding one connection in the job's own process (not the
   API pool); concurrent `files.write` / `files.delete` on those two paths
   wait for this item.
2. **Source.** `HeadObject` the source: missing → 404. Record its size, ETag,
   content type, and user metadata.
3. **Destination.** `HeadObject` the destination.
   - `error` mode: exists → 409 `{"reason": "file_exists", "path": ...}`
     (no version fields, so no read of the destination).
   - `rename` mode: one `ListObjectsV2` on the destination directory with
     prefix `"{stem} ("` picks the lowest free `n` for `{stem} ({n}){ext}`
     (`os.path.splitext`, so `report.pdf` → `report (1).pdf`, `.env` →
     `.env (1)`, `a.tar.gz` → `a.tar (1).gz`); lock the chosen candidate and
     `HeadObject` it to confirm. More than 1000 taken → 409
     `{"reason": "no_available_name"}`.
4. **Copy** server-side with `CopySourceIfMatch=<source ETag>`, so the bytes
   copied are the bytes inspected in step 2 (a concurrent overwrite fails the
   copy instead of moving different content).
   - ≤ 5 GiB: `CopyObject` (default `MetadataDirective=COPY` keeps content
     type and metadata).
   - > 5 GiB: multipart upload created with the source's `ContentType` and
     `Metadata`; `UploadPartCopy` with part size
     `max(256 MiB, ceil(size / 10000))` (within S3's 5 MiB–5 GiB part limits
     and 10,000-part cap) and `CopySourceIfMatch` on every part;
     `AbortMultipartUpload` on any failure.
5. **Verify** with `HeadObject` on the destination: content length equals the
   source size. ETags are not compared — a copy of a multipart-uploaded object
   legitimately gets a different ETag. Mismatch → cleanup (below), 500
   `copy_verification_failed`.
6. **Metadata.** Delete any existing `FileMetadata` row at the destination
   path (a stale row whose object is gone would otherwise violate the unique
   index), then update the source row in place: new `path` and `s3_key`,
   `updated_by` = requester, `updated_at` = now; `id`, `created_by`,
   `created_at`, size, sha256, and content type are kept. Rows are located
   with exactly the keying `record_file_write_metadata` uses — `solution_id`
   rows by `(solution_id, location, path)`, others by
   `_scope_to_org_id(location, scope)` — not the caller-org keying of the
   delete path. If the source has no row, insert one for the destination from
   step 2's values (`sha256` NULL, `created_by` = requester). Flushed, not yet
   committed.
7. **Delete source** with a delete that raises on failure (the existing
   `delete_raw_from_s3` swallows all errors and cannot be used), then
   `HeadObject` to confirm the source is gone. Failure → cleanup, 500
   `source_delete_failed`. Only reached after steps 4–5 succeed.
8. **Commit**, then publish `file_change` `delete` (source) and `write`
   (final destination). Save a job checkpoint with the results so far and
   report progress (`current` = items done, `total` = items).

**Cleanup** (failure in steps 4–7): roll back, then delete the destination
copy with the raising delete — only if this item created it. If cleanup
itself fails, the item's result is 500 `cleanup_failed` with both S3 keys, and
the job publishes an operator log entry (`context.log`) naming them, because
an orphaned copy under a new prefix can be readable there.

**Runner loss mid-item** (the job is not retried): the source object and its
metadata are untouched until step 8's commit, so at worst an untracked copy
exists at the destination. The checkpoint lists every completed item; the job
fails with `runner_lost`, and the interrupted item's destination key is
recorded in the checkpoint before its copy starts so the caller can resolve
it. A retry of that item reports 409 `file_exists` for the orphan rather than
overwriting it.

## Surfaces

| Layer | Change |
|---|---|
| Job | `api/src/jobs/platform/file_move.py` (payload model v1, handler, policy, definition); registered in `api/src/jobs/platform/registry.py` |
| Enqueue service | `sdk_enqueue_file_moves` in `api/shared/sdk_files.py`, reusing `resolve_effective_scope`, `require_declared_solution_file_location`, `authorize_file_policy`/`deny_file_policy`, `install_org_id`; calls `enqueue_platform_job` with dedupe key, requester, resource metadata, title, and notification per the platform-job contract |
| Per-item move | `api/src/services/file_storage/` (lock, head, rename, copy, verify, metadata, strict delete, cleanup) |
| Storage | `S3StorageClient`: `head_object`, `copy_object_if_match` (single + multipart part-copy), `delete_object_strict`, `list_keys_with_prefix` |
| Metadata | `FilePolicyService.move_metadata(...)` with write-path keying |
| HTTP | `FileMoveRequest`, `FileMoveBatchRequest`, `FileMoveResult` (job result model); `POST /api/files/move` and `POST /api/files/move-many` in `api/src/routers/files.py`, each returning `202 PlatformJobAccepted` + `Location` |
| Registries | `operation_catalog.py`: `workspace.files.move` (CLI `files move`) and `workspace.files.move_many` (CLI excluded). Both carry explicit `mcp` exclusions (`move` is not in `_MCP_VERBS`, and no file-content MCP tools exist — the `bifrost_file_delete` binding is dangling and is not copied), plus `native_builder` and `manifest` dispositions. `access_list.py` entries for both routes. Both enqueue paths added to `FILES_ROUTE_PATHS` (and its "All eight" comment updated). `test_operation_inventory.py` counts updated. |
| Python SDK | `files.move`, `files.move_many` (enqueue + poll) |
| CLI | `bifrost files move` (enqueue + `poll_platform_job`) |
| Generated | `client/src/lib/v1.d.ts`; skill-truth appendices (`python api/scripts/skill-truth/generate.py`). Router-local request DTOs are not CLI-contract fingerprinted (`test_contract_version.py`), so no fingerprint change is expected; confirmed by running that test. |
| Product Updates | One feature entry ("Move files in a location") with its disposition file under `product-updates/dispositions/` |

## Testing

- **Job handler (unit)**: per-item order — lock, head, conflict, copy with
  `CopySourceIfMatch`, size verify, metadata, strict source delete, commit,
  publish; the source is never deleted before verify; verify mismatch,
  source-delete failure, and copy failure each roll back and remove the
  destination copy; cleanup failure reports `cleanup_failed` and logs; each
  item ends in commit or rollback (a 409 on item 2 does not break item 3);
  checkpoint after each item, with the in-flight destination recorded before
  its copy; cancellation honored between items, never mid-item.
- **Rename (unit)**: `report (1).pdf`, skips taken numbers, extensionless and
  dotfile names, `a.tar (1).gz`, cap → `no_available_name`.
- **Storage (unit)**: single vs multipart selection at 5 GiB; part size keeps
  parts ≤ 10,000 and ≥ 5 MiB; content type + metadata passed to
  `create_multipart_upload`; `CopySourceIfMatch` on single and part copies;
  abort on failure; strict delete raises.
- **Metadata (unit)**: solution-row and org-row keying (including a superuser
  `scope="global"` move); stale destination row removed; missing source row
  inserts.
- **Enqueue (unit + e2e)**: request-level 400/404/422; per-item 400/403
  recorded (403 audited) and excluded from the work list; all-rejected →
  nothing enqueued; 202 + `Location`; dedupe reuse; caller can read the job.
- **E2E (`tests/e2e/platform/`)**: Solution-scoped `move_many` through the
  real scheduler job in a declared location with a path-prefix read policy —
  a principal with read on the old prefix loses access and one with read on
  the new prefix gains it; metadata row moved; events published. The > 5 GiB
  multipart path has no live coverage (fixture size) and is covered only by
  the storage unit tests — stated deliberately.
- **SDK/CLI (unit)**: enqueue + poll to terminal, per-item results, `move`
  raising the item's error, `TimeoutError` wording with the job id; CLI uses
  `poll_platform_job`.
- **Registry tripwires**: `test_operation_catalog.py`,
  `test_operation_inventory.py`, access-list tests, `test_contract_version.py`,
  skill-truth freshness, `./test.sh quality api`.
- **Platform-job contract** items from `docs/architecture/platform-jobs.md`
  that this policy affects: payload validation and registry dispatch,
  structured failure, duplicate enqueue, runner loss without retry,
  cancellation, notification projection, and caller visibility.
