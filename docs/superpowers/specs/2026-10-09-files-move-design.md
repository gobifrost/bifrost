# `files.move` for Solution and org file locations

## Problem

The file SDK can read, write, list, stat, sign, and delete files in custom
locations (for example a Solution's `docs` location), but it cannot move one.
The only way to relocate a file today is read → write → delete, which buffers
the whole object in the API process and fails for large objects. The editor's
`/api/files/editor/rename` already does a server-side S3 copy + delete, but it
is superuser-only and hard-wired to the `_repo/` workspace.

Driving case: the RTM DMS Solution stores document files under
`documents/campuses/{facility}/types/{doctype}/{pdf|download}/{doc_id}/{file}`
in its `docs` location, and file policies authorize healthcare users by that
path prefix. Moving a document or a whole building to another campus must
move its files to the new campus prefix — some of them multi-GB drawings.

## Goal

A `files.move(source, destination)` operation for non-workspace locations
that behaves exactly like the existing guarded file operations (same scope
resolution, Solution location gate, file-policy checks with denial audit,
advisory locking, `expected_version` guard, metadata maintenance, commit
before publish), moves bytes server-side with no buffering and no size
ceiling, and never deletes the source until the destination is written and
verified.

## Non-goals

- **Workspace (`_repo/`) moves.** Workspace files carry entity, module-cache,
  and file-index side effects that `FileStorageService.move_file` (editor
  rename) already owns. `files.move` rejects `location="workspace"` with 400
  rather than duplicate that logic.
- **Cross-location or cross-scope moves.** Source and destination are paths in
  the same location under the same effective scope (one Solution install or
  one org).
- **Folder (prefix) moves.** One file per call. Callers that move many files
  loop; RTM's relocation workflow does exactly that.
- **Background execution.** A move is one server-side copy; it stays a
  synchronous request (no `PlatformJob`).

## Behavior

### Signature

```
files.move(
    source: str,
    destination: str,
    location: str,                 # required; any non-workspace location
    mode: "cloud" | "local" = "cloud",
    expected_version: str | None = None,   # guards the source, as files.delete
    on_conflict: "error" | "rename" = "error",
    scope: str | None = None,
) -> str   # the final destination path
```

HTTP: `POST /api/files/move` with `FileMoveRequest`
(`source`, `destination`, `location`, `scope`, `mode`, `expected_version`,
`on_conflict`) returning `FileMoveResponse` (`path`, `renamed`).

### Authorization

All checks use the caller's effective scope and Solution context, exactly as
`sdk_delete_file` does:

1. Resolve effective scope (Solution context wins). `location == "workspace"`
   → 400 `"files.move does not support the workspace location"`.
2. `source == destination` → 400.
3. `require_declared_solution_file_location` (404 when the Solution does not
   declare the location).
4. File policy, each denial audited through `deny_file_policy`:
   - `read` on the source — without it, a caller holding delete + write could
     move an unreadable file into a path they can read.
   - `delete` on the source.
   - `write` on the destination (the final destination, re-checked if
     `on_conflict="rename"` picks a different path).

### Conflicts

- Source missing → 404 `File not found: {source}`.
- `expected_version` set and the source's current version differs → 409
  `version_conflict` (same payload shape as delete).
- Destination exists and `on_conflict="error"` → 409
  `{"reason": "file_exists", "path": destination, ...}` (same shape as
  `create_only` on write). Nothing is overwritten, ever.
- Destination exists and `on_conflict="rename"` → pick the first free name
  using the Windows download convention: `report.pdf` → `report (1).pdf` →
  `report (2).pdf` …; the suffix goes before the last extension, or at the end
  of a name with no extension. Give up after 1000 candidates with 409
  `{"reason": "no_available_name"}`. The response returns the chosen path and
  `renamed: true`.

### Locking

`lock_file_mutation` on the source and the requested destination, acquired in
sorted order so two opposite moves cannot deadlock. With
`on_conflict="rename"`, the chosen candidate is locked too before its
existence is re-checked; any competing move that targets the same requested
destination already serializes on the shared lock.

### Order of operations (cloud mode)

1. Authorization, locks, source existence, `expected_version`, destination
   resolution (above).
2. **Copy** source → destination server-side, preserving content type and
   object metadata. Objects ≤ 5 GiB use `CopyObject`; larger objects use a
   multipart upload with `UploadPartCopy` ranges. No bytes pass through the
   API.
3. **Verify** the destination: `HeadObject` content length equals the
   source's; for single-request copies the ETag must also match. Mismatch →
   delete the destination copy, 500 `copy_verification_failed`.
4. **Move metadata** in place: the source's `FileMetadata` row gets the new
   `path` and `s3_key` (keeping `id`, `created_by`, `created_at`, size, hash,
   content type). If the source has no metadata row, insert one for the
   destination from the verified `HeadObject` values. Flushed, not committed.
5. **Delete** the source object — only now, after the destination is written
   and verified.
6. **Commit**, then publish `file_change` `delete` for the source and `write`
   for the destination (existing consumers need no change).

Failure handling: any failure in steps 2–5 rolls back the transaction and
deletes the destination copy if this call created it, so the caller sees the
pre-move state and can retry. The only unrecoverable window is a commit
failure after step 5 (source object gone, metadata still at the old path);
it is logged with both paths.

### Local mode

Same scope, location, and policy checks as cloud mode (as `sdk_delete_file`
does, the checks run before the mode branch); then `os.replace` after the
same conflict/rename resolution. No metadata, commit, or publish side
effects, matching local `write`/`delete`.

## Surfaces

Mirrors `files.delete` end to end:

| Layer | Change |
|---|---|
| Shared service | `sdk_move_file` in `api/shared/sdk_files.py` (exported in `__all__`) |
| Backends | `FileBackend.move`; `LocalBackend.move` (`os.replace`); `S3Backend.move` delegates copy/verify/delete to `FileStorageService` |
| Storage | `copy_raw_in_s3(src_key, dst_key)` with single/multipart copy + `head_raw_s3` for verification (`api/src/services/file_storage/`) |
| Metadata | `FilePolicyService.move_metadata(...)` keyed like `upsert_metadata` / `delete_metadata` (solution rows by `solution_id`, org rows by `organization_id`) |
| HTTP | `FileMoveRequest`, `FileMoveResponse`, `POST /api/files/move` in `api/src/routers/files.py` |
| Registries | `workspace.files.move` in `operation_catalog.py` (CLI `files move`, MCP binding matching delete); `access_list.py` entry; `/api/files/move` in `FILES_ROUTE_PATHS` (`worker_sdk_http.py`) so workflows reach it over the engine socket |
| Python SDK | `files.move(...)` in `api/bifrost/files.py` (`location` is required, unlike the other methods, because their `workspace` default is the one value move rejects) |
| CLI | `bifrost files move SOURCE DESTINATION --location … [--expected-version …] [--rename]`, prints the final path (`api/bifrost/commands/files.py`) |
| Web SDK | `files.move(source, destination, options)` → `{ path, renamed }` in `client/src/lib/app-sdk/files.ts` |
| Generated | `client/src/lib/v1.d.ts`; CLI contract fingerprint (additive — fingerprint only, no `MIN_CLI_VERSION` bump); skill-truth appendices |
| Product Updates | One feature entry ("Move files in a location") with a disposition file |

## Testing

- **Unit (`tests/unit/sdk/test_sdk_files.py`)**: policy checks for read,
  delete, and write each deny independently and audit; workspace rejected;
  same-path rejected; 404 missing source; 409 `version_conflict`; 409
  `file_exists`; rename picks `name (1).ext`, skips taken candidates, handles
  extensionless names, stops at the cap; lock order is sorted; the source is
  deleted only after copy + verify succeed; verification failure removes the
  destination copy and keeps the source; source-delete failure rolls back and
  removes the destination copy; commit happens before publish.
- **Unit (router)**: commit-before-response contract alongside
  `test_files_mutation_commit.py`.
- **Unit (storage)**: single vs multipart copy selection at the 5 GiB
  threshold (multipart part ranges computed correctly).
- **E2E (`tests/e2e/platform/`)**: Solution-scoped move in a declared
  location with a path-prefix read policy — a healthcare-style principal loses
  read on the old prefix and gains it on the new one; denial when the caller
  lacks delete on the source; local-mode move.
- **Client vitest (`files.test.ts`)**: request shape and response parsing.
- Focused verification only (per CLAUDE.md): the tests above, `test_dto_flags`,
  `test_contract_version`, `test_operation_catalog`, access-list tests,
  `./test.sh quality api`, `npm run tsc` / `npm run lint`.
