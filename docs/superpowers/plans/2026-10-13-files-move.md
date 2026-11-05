# `files.move` / `files.move_many` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `files.move` (one file) and `files.move_many` (a batch) for non-workspace, cloud-mode file locations. Both run as one durable `files.move` PlatformJob that copies server-side, re-keys metadata, and deletes the source only after the copy is verified.

**Architecture:**
- **Enqueue.** A shared enqueue service (`shared/sdk_files.py`) validates, authorizes and enqueues at request time with the caller's principal. It's reached by both HTTP routes and the worker engine socket.
- **Job.** The scheduler runs the `files.move` job (`src/jobs/platform/file_move.py`), which drives a per-item move engine (`src/services/file_storage/move.py`). The engine uses new S3 primitives on `S3StorageClient`, plus a Core-statement metadata re-key on `FilePolicyService`.
- **Callers.** SDK and CLI callers enqueue, then poll `/api/platform-jobs/{id}`.

**Tech Stack:** Python 3, FastAPI, SQLAlchemy async (Core statements for metadata), aiobotocore S3, pytest via `./test.sh` (Docker stack: PostgreSQL, Redis, RabbitMQ, SeaweedFS), Click CLI.

**Spec:** `docs/superpowers/specs/2026-10-09-files-move-design.md`. Read it before starting any task. Every behavior in this plan is argued there.

## Global Constraints

**Where and how to work**
- Work only in the worktree `/home/michael/Github/bifrost/.claude/worktrees/files-move` (branch `feat/files-move`). Never edit the primary checkout.
- Run Python tests only through `./test.sh` from the worktree root, e.g. `./test.sh tests/unit/... -v`. Host pytest fails for anything touching DB, queue or storage. Paths given to `./test.sh` are relative to `api/`.
- Tests touch only the isolated `./test.sh` stack: its own Postgres (`bifrost_test`), Redis and SeaweedFS (`bifrost-test` bucket), with the API at `http://api:8000` on the compose network. Never point `TEST_API_URL`, `BIFROST_DATABASE_URL*` or `BIFROST_S3_*` at a real environment. Never run the new SDK, CLI or routes against a real Bifrost instance or real bucket during implementation; unit tests for the SDK and CLI use mocked HTTP clients only.
- Every commit message ends with:
  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  ```

**Scope**
- Moves are cloud-only. Request models and SDK signatures have no `mode` field.
- `location="workspace"` → 400.
- One effective scope per request; no cross-location or cross-scope moves.

**Limits**
- Items per request: 1–500. Summed source + destination path length ≤ 200,000 characters. Violating either → 422 at the request model.
- Destination path length + 7 ≤ 2000, and its S3 key length + 7 ≤ 2500. Violating either → a per-item 400.
- Rename candidates: `stem (n)ext` for `n` in 1–1000. None free → 409 `no_available_name`.

**Job**
- Job type `"files.move"`.
- Policy: `timeout_seconds=14400`, `max_attempts=1`, `retry_on_runner_loss=False`, `allow_running_cancellation=False`, `min_memory_headroom_mb=128`.
- Priority 50 with work. Default priority (100) and no `resource_lock_key` when every item was rejected.
- `resource_lock_key = "files.move:" + sha256(f"{location}:{scope}").hexdigest()`.
- After 3 consecutive storage-level item failures → `PlatformJobFailure("storage_unavailable")`. Remaining items get 500 `not_attempted`.

**S3**
- Single-part `CopyObject` up to 5 GiB (`5 * 1024**3`). Above that, multipart part copy with part size `max(256 MiB, ceil(size / 10000))`.
- Copy markers (S3 user metadata keys): `bifrost-move-row`, `bifrost-move-source` (sha256 hex of the source S3 key) and `bifrost-move-etag`.
- ETags are stored and compared verbatim as `HeadObject` returns them, quotes included.
- `aiobotocore>=2.15.0` (its botocore supports `IfNoneMatch` on `CompleteMultipartUpload`).

**Results**
- `FileMoveResult` fields: `source`, `destination`, `path` (set only when renamed), `renamed`, `adopted`, `status_code`, `error`.
- `error` is ≤ 500 characters, is `code` or `code: message`, and never contains S3 keys. A 403 is the literal `"forbidden"`.
- SDK default `timeout_seconds=14400`. CLI default `--timeout 14400`.

## Review Focus

These are inputs the spec implies but doesn't name. A reasonable user would expect each to just work, and the tasks named below pin each one with a test:

1. **File names with spaces, parentheses or non-ASCII characters** (`Floor Plan – Rev 2 (final).pdf`) move and rename correctly, and markers still match on retry. Tests: Task 6 (names), Task 15 (live SeaweedFS).
2. **A case-only rename** (`Report.pdf` → `report.pdf`) is a real move, not a "same path" rejection or a self-conflict. Test: Task 7.
3. **The same source listed twice in one batch.** The second item is a no-op success (`adopted: true`) when the destination matches, and a 404 when it differs. Never a 500. Test: Task 10.
4. **A swap in one batch** (`A→B`, `B→A` while both exist) fails cleanly with 409s and never loses either file. Test: Task 10.
5. **A zero-byte file** moves (size verification accepts 0). Test: Task 7.

## File Structure

| File | Responsibility |
|---|---|
| `api/shared/file_paths.py` (modify) | Gains public `scope_to_org_id` / `SCOPE_SKIP` (moved from `file_storage/service.py`). |
| `api/src/services/file_storage/service.py` (modify) | Imports the moved helper. |
| `pyproject.toml` (modify, repo root) | `aiobotocore>=2.15.0`. |
| `api/src/services/file_storage/s3_client.py` (modify) | `ObjectHead`, `ObjectMissing`, `ObjectPreconditionFailed`, `is_storage_unavailable`, `head_object`, `copy_object`, `delete_object_strict`, `list_keys`. |
| `api/src/services/file_policy_service.py` (modify) | `FilePolicyEvalCache`; cache-aware `load_policy` / `is_allowed`; `get_scoped_metadata`; `move_metadata`. |
| `api/src/services/access_check_policies.py` (modify) | `check_file(..., policy_cache=None)`. |
| `api/shared/file_access.py` (modify) | `authorize_file_policy(..., policy_cache=None)`. |
| `api/src/services/platform_jobs.py` (modify) | `find_active_platform_job`, `finalize_enqueued_platform_job`, checkpoint progress. |
| `api/src/jobs/platform/base.py` (modify) | `save_checkpoint(..., current=, total=)`. |
| `api/shared/sdk_video.py` (modify) | `finalize_sdk_video_job` delegates to the shared finalize helper. |
| `api/src/services/file_storage/move_names.py` (create) | Pure rename-candidate helpers. |
| `api/src/services/file_storage/move.py` (create) | `FileMover` per-item engine. |
| `api/src/models/contracts/cli.py` (modify) | `FileMoveResult`. |
| `api/src/jobs/platform/file_move.py` (create) | Payload models, handler, `FILE_MOVE_DEFINITION`. |
| `api/src/jobs/platform/registry.py` (modify) | Registers the definition. |
| `api/shared/sdk_files.py` (modify) | `sdk_enqueue_file_moves` + validation helpers. |
| `api/src/routers/files.py` (modify) | `FileMoveItem`, `FileMoveRequest`, `FileMoveBatchRequest`, `POST /move`, `POST /move-many`. |
| `api/src/services/execution/worker_sdk_http.py` (modify) | The two paths in `FILES_ROUTE_PATHS`. |
| `api/src/services/operation_catalog.py` (modify) | Two operation definitions. |
| `api/src/services/access_list.py` (modify) | Two access entries. |
| `api/bifrost/files.py` (modify) | `FileMoveError`, `files.move`, `files.move_many`. |
| `api/bifrost/commands/files.py` (modify) | `bifrost files move`. |
| `api/tests/unit/services/file_move/` (create) | `__init__.py`, `support.py`, `conftest.py`, engine + job tests. |
| `api/tests/e2e/platform/test_files_move.py` (create) | Live HTTP, recovery, multipart and workflow tests. |
| Generated / docs | `client/src/lib/v1.d.ts`, `docs/generated/access-list.json`, skill-truth appendices, `.claude/skills/bifrost-build/references/files.md` + mirrors, product-updates entry. |

---

### Task 1: Shared scope helper and S3 client floor

**Files:**
- Modify: `api/shared/file_paths.py` (append after `resolve_s3_key`)
- Modify: `api/src/services/file_storage/service.py:37-61` (remove the helper), `:257-258` and `:303-304` (use the import)
- Modify: `pyproject.toml:127`
- Test: `api/tests/unit/test_file_paths_scope.py` (create)

**Interfaces:**
- Produces:
  - `shared.file_paths.SCOPE_SKIP: object`
  - `shared.file_paths.scope_to_org_id(location: str, scope: str | None) -> UUID | None | object`

  Behavior is identical to the old private `_scope_to_org_id`.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/unit/test_file_paths_scope.py
"""Storage scope → metadata org mapping shared by write and move paths."""

from uuid import UUID

import botocore.session

from shared.file_paths import SCOPE_SKIP, scope_to_org_id

ORG = "22222222-2222-2222-2222-222222222222"


def test_scope_to_org_id_maps_storage_scopes() -> None:
    assert scope_to_org_id("docs", ORG) == UUID(ORG)
    assert scope_to_org_id("docs", "global") is None
    assert scope_to_org_id("workspace", None) is None
    assert scope_to_org_id("docs", None) is SCOPE_SKIP
    assert scope_to_org_id("docs", "not-a-uuid") is SCOPE_SKIP


def test_botocore_supports_conditional_multipart_complete() -> None:
    """files.move relies on IfNoneMatch on CompleteMultipartUpload."""
    model = botocore.session.get_session().get_service_model("s3")
    members = model.operation_model("CompleteMultipartUpload").input_shape.members
    assert "IfNoneMatch" in members
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./test.sh tests/unit/test_file_paths_scope.py -v`
Expected: FAIL with `ImportError: cannot import name 'SCOPE_SKIP'`. The botocore test may already pass; if it fails, the installed botocore is too old and the Step 4 pin fixes it once the image is rebuilt (`./test.sh stack down && ./test.sh stack up`).

- [ ] **Step 3: Move the helper**

Append to `api/shared/file_paths.py`. Add `from uuid import UUID` to its imports.

```python
# Sentinel — returned by scope_to_org_id to signal "no metadata to write".
SCOPE_SKIP = object()


def scope_to_org_id(location: str, scope: str | None) -> "UUID | None | object":
    """Coerce a storage-scope string to the org UUID for FileMetadata rows.

    Returns:
        ``None``        — global (org IS NULL), and the unscoped workspace
        ``UUID(scope)`` — org-scoped row
        ``SCOPE_SKIP``  — no metadata row applies (missing or non-UUID scope)

    This is the NON-solution path. Solution rows use the install's org so
    the install UUID never ends up in organization_id (C2).
    """
    if location == "workspace":
        return None
    if scope is None:
        return SCOPE_SKIP
    if scope == "global":
        return None
    try:
        return UUID(scope)
    except ValueError:
        return SCOPE_SKIP
```

In `api/src/services/file_storage/service.py`:
- Delete lines 37–61 (the `_SCOPE_SKIP` sentinel and the `_scope_to_org_id` function).
- Add `from shared.file_paths import SCOPE_SKIP, scope_to_org_id` beside the other imports.
- Replace the two call sites in `record_signed_upload_metadata` and `record_file_write_metadata`. Change:

```python
            organization_id = _scope_to_org_id(location, scope)
            if organization_id is _SCOPE_SKIP:
```

to:

```python
            organization_id = scope_to_org_id(location, scope)
            if organization_id is SCOPE_SKIP:
```

Then run `grep -rn "_scope_to_org_id\|_SCOPE_SKIP" api/` and fix any remaining reference, including tests. The expected result is no matches.

- [ ] **Step 4: Pin the S3 client floor**

In the repo-root `pyproject.toml`, change line 127 from `"aiobotocore",  # Async S3 client` to:

```toml
    "aiobotocore>=2.15.0",  # Async S3 client; >=2.15 bundles botocore with IfNoneMatch on CompleteMultipartUpload (files.move)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./test.sh tests/unit/test_file_paths_scope.py tests/unit/sdk/test_sdk_files.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/shared/file_paths.py api/src/services/file_storage/service.py pyproject.toml api/tests/unit/test_file_paths_scope.py
git commit -m "refactor(files): share scope_to_org_id; pin aiobotocore for conditional multipart complete

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: S3 move primitives

**Files:**
- Modify: `api/src/services/file_storage/s3_client.py`
- Test: `api/tests/unit/test_s3_move_primitives.py` (create)

**Interfaces:**
- Produces, in `src.services.file_storage.s3_client`:
  - `COPY_OBJECT_LIMIT = 5 * 1024**3`, `MIN_COPY_PART = 256 * 1024**2`
  - `@dataclass(frozen=True) class ObjectHead(size: int, etag: str, metadata: dict[str, str], headers: dict[str, Any])`. `headers` holds the system metadata to re-send (`ContentType`, `ContentDisposition`, `ContentEncoding`, `ContentLanguage`, `CacheControl`, `Expires`), only the keys present.
  - `class ObjectMissing(Exception)`: the source vanished during a copy.
  - `class ObjectPreconditionFailed(Exception)`, with `.side: Literal["source", "destination"]`.
  - `def is_storage_unavailable(exc: BaseException) -> bool`
  - `S3StorageClient.head_object(key) -> ObjectHead | None`
  - `S3StorageClient.copy_object(source_key, destination_key, *, source: ObjectHead, metadata: dict[str, str], multipart_threshold: int = COPY_OBJECT_LIMIT, part_size: int | None = None) -> None`
  - `S3StorageClient.delete_object_strict(key) -> None`
  - `S3StorageClient.list_keys(prefix, *, delimiter="/") -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/unit/test_s3_move_primitives.py
"""S3 primitives used by files.move: head, conditional copy, strict delete, listing."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from src.services.file_storage.s3_client import (
    COPY_OBJECT_LIMIT,
    ObjectHead,
    ObjectMissing,
    ObjectPreconditionFailed,
    S3StorageClient,
    is_storage_unavailable,
)


def client_error(code: str, status: int, operation: str = "Op") -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


def storage_with(monkeypatch, **methods):
    client = SimpleNamespace(**methods)
    storage = S3StorageClient(SimpleNamespace(s3_bucket="bucket"))

    @asynccontextmanager
    async def get_client():
        yield client

    monkeypatch.setattr(storage, "get_client", get_client)
    return storage, client


SOURCE = ObjectHead(
    size=10,
    etag='"abc"',
    metadata={"owner": "x"},
    headers={"ContentType": "application/pdf", "ContentDisposition": "inline"},
)


async def test_head_object_returns_none_only_for_not_found(monkeypatch) -> None:
    for code in ("404", "NoSuchKey", "NotFound"):
        storage, _ = storage_with(
            monkeypatch, head_object=AsyncMock(side_effect=client_error(code, 404, "HeadObject"))
        )
        assert await storage.head_object("k") is None
    storage, _ = storage_with(
        monkeypatch, head_object=AsyncMock(side_effect=client_error("403", 403, "HeadObject"))
    )
    with pytest.raises(ClientError):
        await storage.head_object("k")


async def test_head_object_maps_response(monkeypatch) -> None:
    storage, _ = storage_with(
        monkeypatch,
        head_object=AsyncMock(
            return_value={
                "ContentLength": 10,
                "ETag": '"abc"',
                "Metadata": {"owner": "x"},
                "ContentType": "application/pdf",
                "CacheControl": None,
            }
        ),
    )
    head = await storage.head_object("k")
    assert head == ObjectHead(
        size=10, etag='"abc"', metadata={"owner": "x"}, headers={"ContentType": "application/pdf"}
    )


async def test_single_part_copy_replaces_metadata_and_guards_source(monkeypatch) -> None:
    storage, client = storage_with(monkeypatch, copy_object=AsyncMock())
    await storage.copy_object("src", "dst", source=SOURCE, metadata={"m": "1"})
    client.copy_object.assert_awaited_once_with(
        Bucket="bucket",
        Key="dst",
        CopySource={"Bucket": "bucket", "Key": "src"},
        CopySourceIfMatch='"abc"',
        MetadataDirective="REPLACE",
        Metadata={"m": "1"},
        ContentType="application/pdf",
        ContentDisposition="inline",
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (client_error("PreconditionFailed", 412), ObjectPreconditionFailed),
        (client_error("NoSuchKey", 404), ObjectMissing),
    ],
)
async def test_single_part_copy_maps_source_errors(monkeypatch, error, expected) -> None:
    storage, _ = storage_with(monkeypatch, copy_object=AsyncMock(side_effect=error))
    with pytest.raises(expected) as caught:
        await storage.copy_object("src", "dst", source=SOURCE, metadata={})
    if expected is ObjectPreconditionFailed:
        assert caught.value.side == "source"


def multipart_client(monkeypatch, *, complete_error=None, part_error=None):
    return storage_with(
        monkeypatch,
        create_multipart_upload=AsyncMock(return_value={"UploadId": "u1"}),
        upload_part_copy=AsyncMock(
            side_effect=part_error,
            return_value={"CopyPartResult": {"ETag": '"p"'}},
        ),
        complete_multipart_upload=AsyncMock(side_effect=complete_error),
        abort_multipart_upload=AsyncMock(),
    )


async def test_multipart_copy_ranges_parts_and_refuses_existing_destination(monkeypatch) -> None:
    storage, client = multipart_client(monkeypatch)
    await storage.copy_object(
        "src", "dst", source=SOURCE, metadata={"m": "1"}, multipart_threshold=4, part_size=4
    )
    client.create_multipart_upload.assert_awaited_once_with(
        Bucket="bucket", Key="dst", Metadata={"m": "1"},
        ContentType="application/pdf", ContentDisposition="inline",
    )
    ranges = [c.kwargs["CopySourceRange"] for c in client.upload_part_copy.await_args_list]
    assert ranges == ["bytes=0-3", "bytes=4-7", "bytes=8-9"]
    assert all(c.kwargs["CopySourceIfMatch"] == '"abc"' for c in client.upload_part_copy.await_args_list)
    complete = client.complete_multipart_upload.await_args.kwargs
    assert complete["IfNoneMatch"] == "*"
    assert [p["PartNumber"] for p in complete["MultipartUpload"]["Parts"]] == [1, 2, 3]
    client.abort_multipart_upload.assert_not_awaited()


@pytest.mark.parametrize(
    "error",
    [client_error("PreconditionFailed", 412), client_error("ConditionalRequestConflict", 409)],
)
async def test_multipart_complete_conflict_is_destination_precondition(monkeypatch, error) -> None:
    storage, client = multipart_client(monkeypatch, complete_error=error)
    with pytest.raises(ObjectPreconditionFailed) as caught:
        await storage.copy_object("src", "dst", source=SOURCE, metadata={}, multipart_threshold=4, part_size=4)
    assert caught.value.side == "destination"
    client.abort_multipart_upload.assert_awaited_once()


@pytest.mark.parametrize(("size", "multipart"), [(COPY_OBJECT_LIMIT, False), (COPY_OBJECT_LIMIT + 1, True)])
async def test_default_threshold_is_the_5_gib_copy_limit(monkeypatch, size, multipart) -> None:
    storage, client = multipart_client(monkeypatch)
    client.copy_object = AsyncMock()
    head = ObjectHead(size=size, etag='"abc"', metadata={}, headers={})
    await storage.copy_object("src", "dst", source=head, metadata={})
    assert client.create_multipart_upload.await_count == int(multipart)
    assert client.copy_object.await_count == int(not multipart)


async def test_multipart_part_failure_aborts(monkeypatch) -> None:
    storage, client = multipart_client(monkeypatch, part_error=client_error("NoSuchKey", 404))
    with pytest.raises(ObjectMissing):
        await storage.copy_object("src", "dst", source=SOURCE, metadata={}, multipart_threshold=4, part_size=4)
    client.abort_multipart_upload.assert_awaited_once()
    client.complete_multipart_upload.assert_not_awaited()


async def test_default_part_size_respects_part_limits(monkeypatch) -> None:
    storage, client = multipart_client(monkeypatch)
    huge = ObjectHead(size=6 * 1024**4, etag='"e"', metadata={}, headers={})  # 6 TiB
    await storage.copy_object("src", "dst", source=huge, metadata={})
    assert client.upload_part_copy.await_count <= 10_000
    first = client.upload_part_copy.await_args_list[0].kwargs["CopySourceRange"]
    start, end = (int(x) for x in first.removeprefix("bytes=").split("-"))
    assert end - start + 1 >= 256 * 1024**2


async def test_delete_object_strict_raises(monkeypatch) -> None:
    storage, _ = storage_with(monkeypatch, delete_object=AsyncMock(side_effect=client_error("InternalError", 500)))
    with pytest.raises(ClientError):
        await storage.delete_object_strict("k")


async def test_list_keys_follows_continuation(monkeypatch) -> None:
    pages = [
        {"Contents": [{"Key": "a"}], "IsTruncated": True, "NextContinuationToken": "t"},
        {"Contents": [{"Key": "b"}], "IsTruncated": False},
    ]
    storage, client = storage_with(monkeypatch, list_objects_v2=AsyncMock(side_effect=pages))
    assert await storage.list_keys("p/") == ["a", "b"]
    second = client.list_objects_v2.await_args_list[1].kwargs
    assert second["ContinuationToken"] == "t" and second["Delimiter"] == "/"


def test_storage_unavailable_classification() -> None:
    assert is_storage_unavailable(EndpointConnectionError(endpoint_url="http://s3"))
    assert is_storage_unavailable(client_error("AccessDenied", 403))
    assert is_storage_unavailable(client_error("InternalError", 503))
    assert not is_storage_unavailable(client_error("NoSuchKey", 404))
    assert not is_storage_unavailable(ValueError("x"))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./test.sh tests/unit/test_s3_move_primitives.py -v`
Expected: FAIL with `ImportError: cannot import name 'ObjectHead'`.

- [ ] **Step 3: Implement the primitives**

In `api/src/services/file_storage/s3_client.py`, extend the imports:

```python
import hashlib
import logging
import math
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from src.config import Settings

logger = logging.getLogger(__name__)
```

Add these module-level definitions above `class S3StorageClient`:

```python
COPY_OBJECT_LIMIT = 5 * 1024**3
MIN_COPY_PART = 256 * 1024**2
_MAX_PARTS = 10_000
# System metadata a copy must carry over (MetadataDirective=REPLACE drops it).
_COPIED_HEADERS = (
    "ContentType",
    "ContentDisposition",
    "ContentEncoding",
    "ContentLanguage",
    "CacheControl",
    "Expires",
)
_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NotFound"})
_UNAVAILABLE_CODES = frozenset({"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"})


@dataclass(frozen=True)
class ObjectHead:
    """The parts of a HeadObject response a server-side move relies on."""

    size: int
    etag: str
    metadata: dict[str, str]
    headers: dict[str, Any]


class ObjectMissing(Exception):
    """The copy source no longer exists."""


class ObjectPreconditionFailed(Exception):
    """A conditional copy was refused: the source changed or the destination exists."""

    def __init__(self, side: Literal["source", "destination"]) -> None:
        super().__init__(f"{side} precondition failed")
        self.side = side


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _http_status(exc: ClientError) -> int | None:
    return exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")


def is_storage_unavailable(exc: BaseException) -> bool:
    """True when ``exc`` means object storage is unreachable or refusing the bucket."""
    if isinstance(exc, ClientError):
        status = _http_status(exc)
        return _error_code(exc) in _UNAVAILABLE_CODES or (status is not None and status >= 500)
    return isinstance(exc, (BotoCoreError, ConnectionError, TimeoutError))


def _copy_error(exc: ClientError) -> Exception:
    if _error_code(exc) == "PreconditionFailed" or _http_status(exc) == 412:
        return ObjectPreconditionFailed("source")
    if _error_code(exc) in _NOT_FOUND_CODES:
        return ObjectMissing(str(exc))
    return exc
```

Add these methods to `S3StorageClient`:

```python
    async def head_object(self, key: str) -> ObjectHead | None:
        """HeadObject; ``None`` only when the key does not exist, anything else raises."""
        async with self.get_client() as s3:
            try:
                response = await s3.head_object(Bucket=self.settings.s3_bucket, Key=key)
            except ClientError as exc:
                if _error_code(exc) in _NOT_FOUND_CODES:
                    return None
                raise
        return ObjectHead(
            size=int(response["ContentLength"]),
            etag=response["ETag"],
            metadata=dict(response.get("Metadata") or {}),
            headers={
                name: response[name]
                for name in _COPIED_HEADERS
                if response.get(name) is not None
            },
        )

    async def copy_object(
        self,
        source_key: str,
        destination_key: str,
        *,
        source: ObjectHead,
        metadata: dict[str, str],
        multipart_threshold: int = COPY_OBJECT_LIMIT,
        part_size: int | None = None,
    ) -> None:
        """Server-side copy of exactly the bytes ``source`` describes.

        Every copy request carries ``CopySourceIfMatch=source.etag``. Objects
        up to ``multipart_threshold`` use one ``CopyObject``; larger ones use
        a multipart part copy whose completion refuses to overwrite an object
        that appeared at the destination (``IfNoneMatch="*"``) and which is
        aborted on any failure.
        """
        bucket = self.settings.s3_bucket
        copy_source = {"Bucket": bucket, "Key": source_key}
        async with self.get_client() as s3:
            if source.size <= multipart_threshold:
                try:
                    await s3.copy_object(
                        Bucket=bucket,
                        Key=destination_key,
                        CopySource=copy_source,
                        CopySourceIfMatch=source.etag,
                        MetadataDirective="REPLACE",
                        Metadata=metadata,
                        **source.headers,
                    )
                except ClientError as exc:
                    mapped = _copy_error(exc)
                    if mapped is exc:
                        raise
                    raise mapped from exc
                return

            size_per_part = part_size or max(MIN_COPY_PART, math.ceil(source.size / _MAX_PARTS))
            created = await s3.create_multipart_upload(
                Bucket=bucket, Key=destination_key, Metadata=metadata, **source.headers
            )
            upload_id = created["UploadId"]
            try:
                parts: list[dict[str, Any]] = []
                for number, start in enumerate(range(0, source.size, size_per_part), start=1):
                    end = min(start + size_per_part, source.size) - 1
                    try:
                        response = await s3.upload_part_copy(
                            Bucket=bucket,
                            Key=destination_key,
                            UploadId=upload_id,
                            PartNumber=number,
                            CopySource=copy_source,
                            CopySourceRange=f"bytes={start}-{end}",
                            CopySourceIfMatch=source.etag,
                        )
                    except ClientError as exc:
                        mapped = _copy_error(exc)
                        if mapped is exc:
                            raise
                        raise mapped from exc
                    parts.append({"PartNumber": number, "ETag": response["CopyPartResult"]["ETag"]})
                try:
                    await s3.complete_multipart_upload(
                        Bucket=bucket,
                        Key=destination_key,
                        UploadId=upload_id,
                        MultipartUpload={"Parts": parts},
                        IfNoneMatch="*",
                    )
                except ClientError as exc:
                    if _error_code(exc) in {"PreconditionFailed", "ConditionalRequestConflict"} or _http_status(exc) == 412:
                        raise ObjectPreconditionFailed("destination") from exc
                    raise
            except BaseException:
                try:
                    await s3.abort_multipart_upload(
                        Bucket=bucket, Key=destination_key, UploadId=upload_id
                    )
                except Exception:
                    logger.warning("Could not abort multipart copy", exc_info=True)
                raise

    async def delete_object_strict(self, key: str) -> None:
        """DeleteObject that raises on any failure (unlike ``delete_raw_from_s3``)."""
        async with self.get_client() as s3:
            await s3.delete_object(Bucket=self.settings.s3_bucket, Key=key)

    async def list_keys(self, prefix: str, *, delimiter: str = "/") -> list[str]:
        """Keys directly under ``prefix`` (no deeper ``delimiter``), all pages."""
        keys: list[str] = []
        kwargs: dict[str, Any] = {
            "Bucket": self.settings.s3_bucket,
            "Prefix": prefix,
            "Delimiter": delimiter,
        }
        async with self.get_client() as s3:
            while True:
                response = await s3.list_objects_v2(**kwargs)
                keys.extend(item["Key"] for item in response.get("Contents", []))
                if not response.get("IsTruncated"):
                    return keys
                kwargs["ContinuationToken"] = response["NextContinuationToken"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./test.sh tests/unit/test_s3_move_primitives.py tests/unit/test_s3_stream_upload.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/file_storage/s3_client.py api/tests/unit/test_s3_move_primitives.py
git commit -m "feat(files): S3 head, conditional copy, strict delete, and listing primitives

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Metadata lookup and Core-statement re-key

**Files:**
- Modify: `api/src/services/file_policy_service.py`
- Test: `api/tests/unit/services/test_file_move_metadata.py` (create)

**Interfaces:**
- Produces, on `FilePolicyService`:
  - `async get_scoped_metadata(*, organization_id: UUID | None, solution_id: UUID | None, location: str, path: str) -> FileMetadata | None`. Solution rows match on `(solution_id, location, path)`; other rows match on `(organization_id, location, path)` with `solution_id IS NULL`.
  - `async move_metadata(*, organization_id: UUID | None, solution_id: UUID | None, location: str, source_row_id: UUID | None, destination_path: str, s3_key: str, content_type: str | None, size_bytes: int, updated_by: UUID) -> None`. Core statements only, no commit.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/unit/services/test_file_move_metadata.py
"""files.move metadata re-key: write-path keying, Core statements, write guard."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import insert, select

from src.models.orm.file_metadata import FileMetadata
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.file_policy_service import FilePolicyService
from src.services.solutions.guard import install_solution_write_guard


@pytest.fixture(autouse=True)
def _guard() -> None:
    install_solution_write_guard()


async def _org_and_solution(db_session):
    org = Organization(id=uuid4(), name=f"Move-{uuid4().hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    solution = Solution(id=uuid4(), slug=f"move-{uuid4().hex[:8]}", name="Move", organization_id=org.id)
    db_session.add(solution)
    await db_session.flush()
    return org, solution


async def _row(db_session, **values) -> FileMetadata:
    row_id = uuid4()
    await db_session.execute(
        insert(FileMetadata).values(
            id=row_id,
            location="docs",
            s3_key=f"docs/x/{values['path']}",
            content_type="application/pdf",
            size_bytes=5,
            created_by=uuid4(),
            **values,
        )
    )
    return (await db_session.execute(select(FileMetadata).where(FileMetadata.id == row_id))).scalar_one()


async def test_solution_row_moves_in_place(db_session) -> None:
    org, solution = await _org_and_solution(db_session)
    source = await _row(db_session, organization_id=org.id, solution_id=solution.id, path="a/r.pdf")
    created_by, created_at = source.created_by, source.created_at
    mover = uuid4()
    service = FilePolicyService(db_session)

    await service.move_metadata(
        organization_id=org.id, solution_id=solution.id, location="docs",
        source_row_id=source.id, destination_path="b/r.pdf", s3_key="docs/s/b/r.pdf",
        content_type="application/pdf", size_bytes=5, updated_by=mover,
    )

    db_session.expire_all()
    moved = await service.get_scoped_metadata(
        organization_id=org.id, solution_id=solution.id, location="docs", path="b/r.pdf"
    )
    assert moved is not None and moved.id == source.id
    assert (moved.created_by, moved.created_at) == (created_by, created_at)
    assert moved.updated_by == mover and moved.s3_key == "docs/s/b/r.pdf"
    assert await service.get_scoped_metadata(
        organization_id=org.id, solution_id=solution.id, location="docs", path="a/r.pdf"
    ) is None


async def test_stale_org_destination_row_removed_and_solution_row_untouched(db_session) -> None:
    org, solution = await _org_and_solution(db_session)
    source = await _row(db_session, organization_id=org.id, solution_id=None, path="a/r.pdf")
    stale = await _row(db_session, organization_id=org.id, solution_id=None, path="b/r.pdf")
    sibling = await _row(db_session, organization_id=org.id, solution_id=solution.id, path="b/r.pdf")
    service = FilePolicyService(db_session)

    await service.move_metadata(
        organization_id=org.id, solution_id=None, location="docs",
        source_row_id=source.id, destination_path="b/r.pdf", s3_key="docs/o/b/r.pdf",
        content_type="application/pdf", size_bytes=5, updated_by=uuid4(),
    )

    db_session.expire_all()
    ids = set((await db_session.execute(
        select(FileMetadata.id).where(FileMetadata.location == "docs", FileMetadata.path == "b/r.pdf")
    )).scalars())
    assert ids == {source.id, sibling.id}
    assert stale.id not in ids


async def test_global_rows_key_on_null_org(db_session) -> None:
    source = await _row(db_session, organization_id=None, solution_id=None, path=f"g-{uuid4().hex}.pdf")
    service = FilePolicyService(db_session)
    destination = f"g-{uuid4().hex}.pdf"
    await service.move_metadata(
        organization_id=None, solution_id=None, location="docs",
        source_row_id=source.id, destination_path=destination, s3_key=f"docs/global/{destination}",
        content_type="application/pdf", size_bytes=5, updated_by=uuid4(),
    )
    db_session.expire_all()
    moved = await service.get_scoped_metadata(
        organization_id=None, solution_id=None, location="docs", path=destination
    )
    assert moved is not None and moved.id == source.id


async def test_missing_source_row_inserts_destination_row(db_session) -> None:
    org, solution = await _org_and_solution(db_session)
    mover = uuid4()
    service = FilePolicyService(db_session)
    await service.move_metadata(
        organization_id=org.id, solution_id=solution.id, location="docs",
        source_row_id=None, destination_path="b/new.pdf", s3_key="docs/s/b/new.pdf",
        content_type="application/pdf", size_bytes=7, updated_by=mover,
    )
    row = await service.get_scoped_metadata(
        organization_id=org.id, solution_id=solution.id, location="docs", path="b/new.pdf"
    )
    assert row is not None
    assert (row.created_by, row.updated_by, row.size_bytes, row.sha256) == (mover, mover, 7, None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./test.sh tests/unit/services/test_file_move_metadata.py -v`
Expected: FAIL with `AttributeError: 'FilePolicyService' object has no attribute 'move_metadata'`.

- [ ] **Step 3: Implement**

In `api/src/services/file_policy_service.py`:
- Change `from sqlalchemy import delete, select` to `from sqlalchemy import delete, func, insert, select, update`.
- Change `from uuid import UUID` to `from uuid import UUID, uuid4`.

Then add these methods after `get_metadata`:

```python
    def _scoped_row_filter(
        self,
        *,
        organization_id: UUID | None,
        solution_id: UUID | None,
        location: str,
        path: str,
    ) -> list[Any]:
        """Row key used by the write path (``record_file_write_metadata``)."""
        if solution_id is not None:
            return [
                FileMetadata.solution_id == solution_id,
                FileMetadata.location == location,
                FileMetadata.path == path,
            ]
        return [
            FileMetadata.organization_id == organization_id,
            FileMetadata.location == location,
            FileMetadata.path == path,
            FileMetadata.solution_id.is_(None),
        ]

    async def get_scoped_metadata(
        self,
        *,
        organization_id: UUID | None,
        solution_id: UUID | None,
        location: str,
        path: str,
    ) -> FileMetadata | None:
        stmt = select(FileMetadata).where(
            *self._scoped_row_filter(
                organization_id=organization_id,
                solution_id=solution_id,
                location=location,
                path=path,
            )
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def move_metadata(
        self,
        *,
        organization_id: UUID | None,
        solution_id: UUID | None,
        location: str,
        source_row_id: UUID | None,
        destination_path: str,
        s3_key: str,
        content_type: str | None,
        size_bytes: int,
        updated_by: UUID,
    ) -> None:
        """Re-key a moved file's row to its destination; executed, not committed.

        Core statements only: the Solution write guard rejects ORM flushes of
        Solution-owned rows, and every Solution file row is one. A stale row
        already at the destination (its object is gone) is removed first so
        the partial unique indexes hold. The source row keeps its id,
        creator, creation time, size, sha256, and content type.
        """
        await self.db.execute(
            delete(FileMetadata).where(
                *self._scoped_row_filter(
                    organization_id=organization_id,
                    solution_id=solution_id,
                    location=location,
                    path=destination_path,
                )
            )
        )
        if source_row_id is not None:
            await self.db.execute(
                update(FileMetadata)
                .where(FileMetadata.id == source_row_id)
                .values(
                    path=destination_path,
                    s3_key=s3_key,
                    updated_by=updated_by,
                    updated_at=func.now(),
                )
            )
            return
        await self.db.execute(
            insert(FileMetadata).values(
                id=uuid4(),
                organization_id=organization_id,
                solution_id=solution_id,
                location=location,
                path=destination_path,
                s3_key=s3_key,
                content_type=content_type,
                size_bytes=size_bytes,
                sha256=None,
                created_by=updated_by,
                updated_by=updated_by,
            )
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./test.sh tests/unit/services/test_file_move_metadata.py tests/unit/services/test_file_policy_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/file_policy_service.py api/tests/unit/services/test_file_move_metadata.py
git commit -m "feat(files): scoped metadata lookup and Core-statement move re-key

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Request-scoped file policy evaluation cache

**Files:**
- Modify: `api/src/services/file_policy_service.py` (`load_policy`, `is_allowed`, new `FilePolicyEvalCache`)
- Modify: `api/src/services/access_check_policies.py` (`check_file`)
- Modify: `api/shared/file_access.py` (`authorize_file_policy`, `require_file_policy`)
- Test: `api/tests/unit/services/test_file_policy_eval_cache.py` (create)

**Interfaces:**
- Produces:
  - `src.services.file_policy_service.FilePolicyEvalCache`, a dataclass with no required arguments.
  - The `cache: FilePolicyEvalCache | None = None` keyword on `FilePolicyService.load_policy` and `FilePolicyService.is_allowed`.
  - The `policy_cache: FilePolicyEvalCache | None = None` keyword on `check_file` and `authorize_file_policy`.

  Omitting the cache keeps today's behavior exactly.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/unit/services/test_file_policy_eval_cache.py
"""A request-scoped cache removes repeated policy loading without changing decisions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event

from src.models.contracts.policies import FilePolicies
from src.models.orm.organizations import Organization
from src.services.file_policy_service import FilePolicyEvalCache, FilePolicyService


def _user(org_id):
    return SimpleNamespace(
        user_id=str(uuid4()), email="u@example.com", organization_id=str(org_id),
        is_platform_admin=False, role_ids=[], role_names=[], claims={},
    )


async def _org_with_policy(db_session):
    org = Organization(id=uuid4(), name=f"Cache-{uuid4().hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    await FilePolicyService(db_session).upsert_policy(
        organization_id=org.id, location="docs", path="campus-a",
        policies=FilePolicies.model_validate(
            {"policies": [{"name": "r", "actions": ["read"], "when": None}]}
        ),
        created_by=uuid4(),
    )
    return org


@pytest.fixture
def policy_selects(db_session):
    """Statements against file_policies issued through the test session."""
    seen: list[str] = []
    engine = db_session.bind.sync_engine

    def before(_conn, _cursor, statement, *_args):
        if "FROM file_policies" in statement:
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    yield seen
    event.remove(engine, "before_cursor_execute", before)


async def test_cache_loads_policy_rows_once_and_keeps_decisions(db_session, policy_selects) -> None:
    org = await _org_with_policy(db_session)
    service = FilePolicyService(db_session)
    user = _user(org.id)
    seen = policy_selects
    seen.clear()

    cache = FilePolicyEvalCache()
    decisions = [
        await service.is_allowed("read", organization_id=org.id, location="docs", path=p, user=user, cache=cache)
        for p in ("campus-a/1.pdf", "campus-a/2.pdf", "campus-b/3.pdf")
    ]
    cached_queries = len(seen)

    seen.clear()
    uncached = [
        await service.is_allowed("read", organization_id=org.id, location="docs", path=p, user=user)
        for p in ("campus-a/1.pdf", "campus-a/2.pdf", "campus-b/3.pdf")
    ]

    assert decisions == uncached == [True, True, False]
    assert cached_queries < len(seen)
    assert cached_queries <= 2  # org arm + global arm, once each


async def test_authorize_forwards_cache_to_evaluator_and_check(monkeypatch) -> None:
    from shared import file_access

    is_allowed = AsyncMock(return_value=True)
    check = AsyncMock()
    monkeypatch.setattr(FilePolicyService, "is_allowed", is_allowed)
    monkeypatch.setattr(file_access, "check_file", check)
    cache = FilePolicyEvalCache()
    caller = SimpleNamespace(user=SimpleNamespace(is_superuser=False), db=object())

    assert await file_access.authorize_file_policy(
        caller, action="read", location="docs", scope=str(uuid4()), path="a",
        organization_id=None, policy_cache=cache,
    )
    assert is_allowed.await_args.kwargs["cache"] is cache
    assert check.await_args.kwargs["policy_cache"] is cache
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./test.sh tests/unit/services/test_file_policy_eval_cache.py -v`
Expected: FAIL with `ImportError: cannot import name 'FilePolicyEvalCache'`.

- [ ] **Step 3: Implement the cache in `FilePolicyService`**

Add `from dataclasses import dataclass, field` to `api/src/services/file_policy_service.py` and define the cache above the class:

```python
@dataclass
class FilePolicyEvalCache:
    """Request-scoped memo for evaluating many paths in one location.

    Used by the ``files.move`` enqueue, which runs up to three policy checks
    per item. It memoizes the policy rows each cascade arm loads, each policy
    row's validated and ref-resolved policies, claim pre-resolution per
    (principal, policy row), and metadata rows per path. Decisions are
    identical to uncached evaluation: the canonical cascade and evaluator
    still run.
    """

    policy_rows: dict[tuple[Any, ...], list[FilePolicy]] = field(default_factory=dict)
    resolved: dict[UUID, FilePolicies | None] = field(default_factory=dict)
    preresolved: set[tuple[int, UUID]] = field(default_factory=set)
    metadata: dict[tuple[Any, ...], FileMetadata | None] = field(default_factory=dict)
```

Rewrite `load_policy` so each arm's query goes through one helper. Add a `cache` keyword; the body keeps today's cascade:

```python
    async def _policy_rows(
        self,
        key: tuple[Any, ...],
        stmt: Any,
        cache: "FilePolicyEvalCache | None",
    ) -> list[FilePolicy]:
        if cache is not None and key in cache.policy_rows:
            return cache.policy_rows[key]
        rows = list((await self.db.execute(stmt)).scalars().all())
        if cache is not None:
            cache.policy_rows[key] = rows
        return rows

    async def load_policy(
        self,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
        solution_id: UUID | None = None,
        cache: "FilePolicyEvalCache | None" = None,
    ) -> FilePolicy | None:
        """Resolve the governing policy with own-solution → org → global cascade.

        When ``solution_id`` is provided (solution context), the solution's own
        prefix policies are consulted first (longest-prefix within the solution
        tier).  A solution-scoped policy that covers the path wins; if none
        matches, resolution falls through to the existing org → global arm
        unchanged.

        For non-solution callers (``solution_id=None``) the original org →
        global cascade applies with no change. ``cache`` memoizes each arm's
        rows for the rest of one request.
        """
        if solution_id is not None:
            solution_rows = await self._policy_rows(
                ("solution", solution_id, location),
                select(FilePolicy).where(
                    FilePolicy.solution_id == solution_id,
                    FilePolicy.location == location,
                ),
                cache,
            )
            solution_match = select_longest_prefix(solution_rows, location, path)  # type: ignore[arg-type]
            if solution_match is not None:
                return cast(FilePolicy, solution_match)

        if organization_id is not None:
            org_rows = await self._policy_rows(
                ("org", organization_id, location),
                select(FilePolicy).where(
                    FilePolicy.organization_id == organization_id,
                    FilePolicy.location == location,
                    FilePolicy.solution_id.is_(None),
                ),
                cache,
            )
            org_match = select_longest_prefix(org_rows, location, path)  # type: ignore[arg-type]
            if org_match is not None:
                return cast(FilePolicy, org_match)

        global_rows = await self._policy_rows(
            ("global", location),
            select(FilePolicy).where(
                FilePolicy.organization_id.is_(None),
                FilePolicy.location == location,
                FilePolicy.solution_id.is_(None),
            ),
            cache,
        )
        global_match = select_longest_prefix(global_rows, location, path)  # type: ignore[arg-type]
        return cast("FilePolicy | None", global_match)
```

Rewrite `is_allowed` to take `cache` and memoize the per-row work:

```python
    async def is_allowed(
        self,
        action: FileAction,
        *,
        organization_id: UUID | None,
        location: str,
        path: str,
        user: Any,
        solution_id: UUID | None = None,
        cache: "FilePolicyEvalCache | None" = None,
    ) -> bool:
        if not self._principal_matches_org(user, organization_id):
            return False

        policy_row = await self.load_policy(
            organization_id=organization_id,
            location=location,
            path=path,
            solution_id=solution_id,
            cache=cache,
        )
        if policy_row is None:
            return False

        if cache is not None and policy_row.id in cache.resolved:
            policies = cache.resolved[policy_row.id]
        else:
            policies = await self._resolved_policies(policy_row, organization_id)
            if cache is not None:
                cache.resolved[policy_row.id] = policies
        if policies is None:
            return False

        preresolve_key = (id(user), policy_row.id)
        if cache is None or preresolve_key not in cache.preresolved:
            await preresolve_for_policies(
                user,
                policies,  # type: ignore[arg-type]
                self.db,
                organization_id,
                solution_id,
            )
            if cache is not None:
                cache.preresolved.add(preresolve_key)

        metadata_key = (solution_id, organization_id, location, path)
        if cache is not None and metadata_key in cache.metadata:
            metadata = cache.metadata[metadata_key]
        else:
            if solution_id is not None:
                metadata = await self._get_solution_metadata(
                    solution_id=solution_id,
                    location=location,
                    path=path,
                )
            else:
                metadata = await self.get_metadata(
                    organization_id=organization_id,
                    location=location,
                    path=path,
                )
            if cache is not None:
                cache.metadata[metadata_key] = metadata
        context = FilePolicyContext(
            location=location,
            path=path,
            created_by=metadata.created_by if metadata is not None else None,
            created_at=metadata.created_at if metadata is not None else None,
        )
        return evaluate_file_action(action, policies, context, user)

    async def _resolved_policies(
        self, policy_row: FilePolicy, organization_id: UUID | None
    ) -> FilePolicies | None:
        """Validated, ref-resolved policies for a row, or ``None`` to deny."""
        try:
            policies = FilePolicies.model_validate(policy_row.policies)
        except ValidationError as exc:
            logger.warning(
                "malformed file policies; denying file access: %s",
                exc.__class__.__name__,
            )
            return None

        rule_repo = PolicyRuleRepository(self.db, org_id=organization_id, is_superuser=True)
        try:
            await resolve_policy_refs(policies, repo=rule_repo, action_domain="file")
        except (PolicyRuleNotFound, PolicyRuleDomainMismatch) as exc:
            logger.warning(
                "unresolvable file policy ref; denying file access: %s",
                exc.__class__.__name__,
            )
            return None
        return policies
```

- [ ] **Step 4: Thread the cache through `check_file` and `authorize_file_policy`**

In `api/src/services/access_check_policies.py`, add a keyword to `check_file`:

```python
async def check_file(
    db: AsyncSession,
    action: str,
    *,
    organization_id: UUID | None,
    location: str,
    path: str,
    solution_id: UUID | None,
    allowed_today: bool,
    policy_cache: Any = None,
) -> None:
```

Pass it into the inner call as `cache=policy_cache` (after `solution_id=solution_id,` in the `FilePolicyService(db).is_allowed(...)` call). Add `Any` to the module's `typing` import if it isn't there.

In `api/shared/file_access.py`:
- Add the keyword `policy_cache: Any = None` (after `organization_id`) to both `authorize_file_policy` and `require_file_policy`.
- In `authorize_file_policy`, pass `cache=policy_cache` to `service.is_allowed(...)` and `policy_cache=policy_cache` to `check_file(...)`.
- In `require_file_policy`, forward `policy_cache=policy_cache` to `authorize_file_policy`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./test.sh tests/unit/services/test_file_policy_eval_cache.py tests/unit/services/test_file_policy_service.py tests/unit/sdk/test_sdk_files.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add api/src/services/file_policy_service.py api/src/services/access_check_policies.py api/shared/file_access.py api/tests/unit/services/test_file_policy_eval_cache.py
git commit -m "feat(files): request-scoped file policy evaluation cache

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Shared platform-job helpers (active lookup, finalize, checkpoint progress)

**Files:**
- Modify: `api/src/services/platform_jobs.py` (`enqueue_platform_job` lookup, `checkpoint_platform_job`, two new functions)
- Modify: `api/src/jobs/platform/base.py` (`PlatformJobContext.save_checkpoint`)
- Modify: `api/shared/sdk_video.py` (`finalize_sdk_video_job`)
- Test: `api/tests/unit/services/test_platform_jobs.py` (append)

**Interfaces:**
- Produces:
  - `src.services.platform_jobs.find_active_platform_job(db, job_type: str, dedupe_key: str) -> PlatformJob | None`
  - `src.services.platform_jobs.finalize_enqueued_platform_job(db, job: PlatformJob, *, log_label: str) -> None`. It creates the notification on a best-effort basis (warning on failure), commits, refreshes, and publishes.
  - `checkpoint_platform_job(job_id, lease_token, *, result, phase, current: int | None = None, total: int | None = None) -> bool`. When `current` is given it also sets `progress_current`, `progress_total` and `progress_percent` (`current / total * 100`, or `None` without a total).
  - `PlatformJobContext.save_checkpoint(result, *, phase, current: int | None = None, total: int | None = None)`

- [ ] **Step 1: Write the failing tests** (append to `api/tests/unit/services/test_platform_jobs.py`)

```python
@pytest.mark.asyncio
async def test_checkpoint_with_progress_sets_percent_in_same_write(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await _enqueue(db_session)
    token = uuid4()
    job.status = "running"
    job.lease_token = token
    await db_session.commit()
    monkeypatch.setattr(service, "publish_platform_job_update", AsyncMock())

    assert await service.checkpoint_platform_job(
        job.id, token, result={"moved": 1}, phase="Moving 2 of 4", current=1, total=4
    )
    await db_session.refresh(job)
    assert (job.result, job.phase) == ({"moved": 1}, "Moving 2 of 4")
    assert (job.progress_current, job.progress_total, job.progress_percent) == (1, 4, 25.0)

    assert await service.checkpoint_platform_job(job.id, token, result={"moved": 2}, phase="Later")
    await db_session.refresh(job)
    assert (job.progress_current, job.progress_total, job.progress_percent) == (1, 4, 25.0)


@pytest.mark.asyncio
async def test_find_active_platform_job_ignores_terminal_jobs(db_session: AsyncSession) -> None:
    job = await _enqueue(db_session)
    assert (await service.find_active_platform_job(db_session, job.job_type, job.dedupe_key)).id == job.id
    job.status = "succeeded"
    await db_session.flush()
    assert await service.find_active_platform_job(db_session, job.job_type, job.dedupe_key) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./test.sh tests/unit/services/test_platform_jobs.py -k "checkpoint_with_progress or find_active" -v`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'current'` and `AttributeError: ... find_active_platform_job`.

- [ ] **Step 3: Implement**

In `api/src/services/platform_jobs.py`, add `import logging` and a module `logger = logging.getLogger(__name__)` if they are absent. Add these functions above `enqueue_platform_job`:

```python
async def find_active_platform_job(
    db: AsyncSession, job_type: str, dedupe_key: str
) -> PlatformJob | None:
    """The oldest active job for a deduplication key, if any."""
    return (
        await db.execute(
            select(PlatformJob)
            .where(
                PlatformJob.job_type == job_type,
                PlatformJob.dedupe_key == dedupe_key,
                PlatformJob.status.in_(ACTIVE_PLATFORM_JOB_STATUSES),
            )
            .order_by(PlatformJob.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def finalize_enqueued_platform_job(
    db: AsyncSession, job: PlatformJob, *, log_label: str
) -> None:
    """Attach the notification, commit, refresh, and publish an enqueued job.

    Notification creation is best effort (a failure only warns), and the
    commit precedes the publish so a caller polling right after the 202
    always finds the durable row.
    """
    try:
        await ensure_platform_job_notification(db, job)
    except Exception:
        logger.warning(
            f"{log_label} queued without a progress notification",
            extra={"platform_job_id": str(job.id)},
            exc_info=True,
        )
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)
```

In `enqueue_platform_job`, replace the inline `existing = (await db.execute(select(PlatformJob)...)).scalar_one_or_none()` block, which runs after the advisory lock, with:

```python
        existing = await find_active_platform_job(db, definition.job_type, dedupe_key)
```

Replace `checkpoint_platform_job` with:

```python
async def checkpoint_platform_job(
    job_id: UUID,
    lease_token: UUID,
    *,
    result: dict[str, Any],
    phase: str,
    current: int | None = None,
    total: int | None = None,
) -> bool:
    """Durably save resumable handler state on the fenced shared job row.

    With ``current`` the same write also records progress, so a job that
    checkpoints per unit of work does not need a second progress write.
    """
    async with get_db_context() as db:
        job = (await db.execute(
            select(PlatformJob).where(
                PlatformJob.id == job_id, PlatformJob.lease_token == lease_token,
                PlatformJob.status == "running",
            ).with_for_update()
        )).scalar_one_or_none()
        if job is None:
            return False
        job.result = result
        job.phase = phase[:200]
        if current is not None:
            job.progress_current = max(0, current)
            job.progress_total = total
            job.progress_percent = (current / total * 100) if total else None
        job.revision += 1
        await db.commit()
    await publish_platform_job_update(job)
    return True
```

In `api/src/jobs/platform/base.py`, replace `save_checkpoint` with:

```python
    async def save_checkpoint(
        self,
        result: dict[str, Any],
        *,
        phase: str,
        current: int | None = None,
        total: int | None = None,
    ) -> None:
        from src.services.platform_jobs import checkpoint_platform_job

        if not await checkpoint_platform_job(
            self.job_id,
            self.lease_token,
            result=result,
            phase=phase,
            current=current,
            total=total,
        ):
            raise PlatformJobCancelled
```

In `api/shared/sdk_video.py`, replace the body of `finalize_sdk_video_job`, keeping its docstring, with:

```python
    from src.services.platform_jobs import finalize_enqueued_platform_job

    await finalize_enqueued_platform_job(db, job, log_label="SDK video generation")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./test.sh tests/unit/services/test_platform_jobs.py tests/unit/jobs/test_platform_job_runner.py tests/unit/jobs/test_video_generation_platform_job.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/platform_jobs.py api/src/jobs/platform/base.py api/shared/sdk_video.py api/tests/unit/services/test_platform_jobs.py
git commit -m "feat(platform-jobs): active-job lookup, shared enqueue finalize, checkpoint progress

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Rename-candidate names

**Files:**
- Create: `api/src/services/file_storage/move_names.py`
- Test: `api/tests/unit/services/test_file_move_names.py` (create)

**Interfaces:**
- Produces:
  - `numbered_name(path: str, n: int) -> str`
  - `candidate_prefix(path: str) -> str`: the relative path prefix shared by all of `path`'s candidates (`"dir/stem ("`).
  - `matching_candidates(path: str, names: Iterable[str]) -> dict[int, str]`: maps `n` → candidate base name, for the names that are exactly `stem (n)ext` with `n ≥ 1`.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/unit/services/test_file_move_names.py
"""Windows-style rename candidates for files.move on_conflict='rename'."""

import pytest

from src.services.file_storage.move_names import (
    candidate_prefix,
    matching_candidates,
    numbered_name,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("a/report.pdf", "a/report (2).pdf"),
        ("report", "report (2)"),
        ("a/.env", "a/.env (2)"),
        ("a/a.tar.gz", "a/a.tar (2).gz"),
        ("a/Floor Plan – Rev 2 (final).pdf", "a/Floor Plan – Rev 2 (final) (2).pdf"),
    ],
)
def test_numbered_name(path: str, expected: str) -> None:
    assert numbered_name(path, 2) == expected


def test_candidate_prefix() -> None:
    assert candidate_prefix("a/b/report.pdf") == "a/b/report ("
    assert candidate_prefix("report.pdf") == "report ("


def test_matching_candidates_keeps_only_exact_numbered_siblings() -> None:
    names = [
        "report (1).pdf",
        "report (3).pdf",
        "report (draft).pdf",
        "report (0).pdf",
        "report (01).pdf",
        "report (2).txt",
        "report (4).pdf.bak",
    ]
    assert matching_candidates("x/report.pdf", names) == {1: "report (1).pdf", 3: "report (3).pdf"}


def test_matching_candidates_escapes_regex_characters() -> None:
    names = ["Floor Plan – Rev 2 (final) (1).pdf", "Floor Plan – Rev 2 (final)X(1).pdf"]
    assert matching_candidates("Floor Plan – Rev 2 (final).pdf", names) == {
        1: "Floor Plan – Rev 2 (final) (1).pdf"
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./test.sh tests/unit/services/test_file_move_names.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.file_storage.move_names'`.

- [ ] **Step 3: Implement**

```python
# api/src/services/file_storage/move_names.py
"""Windows-style ``name (n).ext`` candidates for files.move rename mode."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable


def _split(path: str) -> tuple[str, str, str]:
    directory, _, name = path.rpartition("/")
    stem, ext = os.path.splitext(name)
    return directory, stem, ext


def _join(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def numbered_name(path: str, n: int) -> str:
    """``a/report.pdf`` → ``a/report (n).pdf``; ``.env`` → ``.env (n)``."""
    directory, stem, ext = _split(path)
    return _join(directory, f"{stem} ({n}){ext}")


def candidate_prefix(path: str) -> str:
    """Relative path prefix every candidate of ``path`` starts with."""
    directory, stem, _ = _split(path)
    return _join(directory, f"{stem} (")


def matching_candidates(path: str, names: Iterable[str]) -> dict[int, str]:
    """Map ``n`` to each base name in ``names`` that is exactly ``stem (n)ext``."""
    _, stem, ext = _split(path)
    pattern = re.compile(rf"^{re.escape(stem)} \(([1-9]\d*)\){re.escape(ext)}$")
    found: dict[int, str] = {}
    for name in names:
        match = pattern.match(name)
        if match is not None:
            found[int(match.group(1))] = name
    return found
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./test.sh tests/unit/services/test_file_move_names.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/file_storage/move_names.py api/tests/unit/services/test_file_move_names.py
git commit -m "feat(files): rename candidate naming for files.move

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 7: Move engine — locked single-part flow

The engine is built in three tasks. This one moves a file under the path locks: conflicts, rename, verify, metadata, the source delete and its "never clean up after the delete is issued" rule, and cleanup. Task 8 releases the locks for multipart copies. Task 9 adds recovery (resume, adopt, already moved).

**Files:**
- Create: `api/src/services/file_storage/move.py`
- Create: `api/tests/unit/services/file_move/__init__.py` (empty), `api/tests/unit/services/file_move/support.py`, `api/tests/unit/services/file_move/conftest.py`
- Test: `api/tests/unit/services/file_move/test_engine.py` (create)

**Interfaces:**
- Consumes:
  - Task 2: `ObjectHead`, `ObjectMissing`, `ObjectPreconditionFailed`, `COPY_OBJECT_LIMIT`, `is_storage_unavailable`.
  - Task 3: `FilePolicyService.get_scoped_metadata` and `move_metadata`.
  - Task 6: `numbered_name`, `candidate_prefix`, `matching_candidates`.
  - Existing: `lock_file_mutation`, `resolve_s3_key`, `src.core.pubsub.publish_file_change`.
- Produces, in `src.services.file_storage.move`:
  - Markers: `MARK_ROW`, `MARK_SOURCE`, `MARK_ETAG`, and `source_marker(source_key) -> str`.
  - `MoveTarget(location, scope, solution_id, organization_id, requested_by)`
  - `MoveRequest(source, destination)`
  - `MoveOutcome(status_code, error=None, path=None, renamed=False, adopted=False, storage_error=False)`
  - `FileMover(store, target, *, open_session=None, on_cleanup_failed=None, multipart_threshold=COPY_OBJECT_LIMIT, part_size=None)`, with `.move(request, on_conflict) -> MoveOutcome`. The `on_cleanup_failed(source_key, destination_key)` callback is async.
  - Error codes: `file_exists`, `no_available_name`, `source_changed`, `not_found`, `copy_verification_failed`, `source_delete_unconfirmed`, `cleanup_failed`, `storage_unavailable`, `internal_error` (and, from Task 8, `destination_changed`).

- [ ] **Step 1: Create the test support**

```python
# api/tests/unit/services/file_move/support.py
"""Fakes and helpers for the files.move engine and job tests."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select

from shared.file_paths import resolve_s3_key
from src.core.database import get_db_context
from src.models.orm.file_metadata import FileMetadata
from src.services.file_storage.move import (
    MARK_ETAG,
    MARK_ROW,
    MARK_SOURCE,
    MoveTarget,
    source_marker,
)
from src.services.file_storage.s3_client import (
    COPY_OBJECT_LIMIT,
    ObjectHead,
    ObjectMissing,
    ObjectPreconditionFailed,
)

Hook = Callable[[str], Awaitable[None]]


@dataclass
class StoredObject:
    data: bytes
    metadata: dict[str, str]
    etag: str


class FakeStore:
    """In-memory stand-in for the S3StorageClient move primitives.

    ``before[op]`` / ``after[op]`` hooks receive the key and run around the
    operation, so tests can inject failures and concurrent writers.
    """

    def __init__(self) -> None:
        self.objects: dict[str, StoredObject] = {}
        self.before: dict[str, Hook] = {}
        self.after: dict[str, Hook] = {}
        self.calls: list[tuple[str, str]] = []
        self.copies: list[dict[str, Any]] = []
        self._version = 0

    def put(self, key: str, data: bytes = b"%PDF-1.7 test", metadata: dict[str, str] | None = None) -> str:
        self._version += 1
        etag = '"' + hashlib.md5(data + str(self._version).encode()).hexdigest() + '"'
        self.objects[key] = StoredObject(data, dict(metadata or {}), etag)
        return etag

    async def _run(self, hooks: dict[str, Hook], op: str, key: str) -> None:
        hook = hooks.get(op)
        if hook is not None:
            await hook(key)

    async def head_object(self, key: str) -> ObjectHead | None:
        self.calls.append(("head", key))
        await self._run(self.before, "head", key)
        obj = self.objects.get(key)
        if obj is None:
            return None
        return ObjectHead(
            size=len(obj.data),
            etag=obj.etag,
            metadata=dict(obj.metadata),
            headers={"ContentType": "application/pdf"},
        )

    async def copy_object(
        self,
        source_key: str,
        destination_key: str,
        *,
        source: ObjectHead,
        metadata: dict[str, str],
        multipart_threshold: int = COPY_OBJECT_LIMIT,
        part_size: int | None = None,
    ) -> None:
        self.calls.append(("copy", destination_key))
        self.copies.append(
            {
                "source_key": source_key,
                "destination_key": destination_key,
                "source": source,
                "metadata": dict(metadata),
                "multipart_threshold": multipart_threshold,
                "part_size": part_size,
            }
        )
        await self._run(self.before, "copy", destination_key)
        current = self.objects.get(source_key)
        if current is None:
            raise ObjectMissing(source_key)
        if current.etag != source.etag:
            raise ObjectPreconditionFailed("source")
        self.put(destination_key, current.data, metadata)
        await self._run(self.after, "copy", destination_key)

    async def delete_object_strict(self, key: str) -> None:
        self.calls.append(("delete", key))
        await self._run(self.before, "delete", key)
        self.objects.pop(key, None)
        await self._run(self.after, "delete", key)

    async def list_keys(self, prefix: str, *, delimiter: str = "/") -> list[str]:
        self.calls.append(("list", prefix))
        return sorted(
            key
            for key in self.objects
            if key.startswith(prefix) and delimiter not in key[len(prefix):]
        )


def key_for(target: MoveTarget, path: str) -> str:
    return resolve_s3_key(target.location, target.scope, path)


def markers(source_key: str, row_id: UUID | None, etag: str) -> dict[str, str]:
    """The metadata an interrupted move's copy carries."""
    return {
        MARK_SOURCE: source_marker(source_key),
        MARK_ROW: str(row_id) if row_id is not None else "",
        MARK_ETAG: etag,
    }


async def seed_file(
    store: FakeStore,
    target: MoveTarget,
    path: str,
    data: bytes = b"%PDF-1.7 test",
    *,
    with_row: bool = True,
    metadata: dict[str, str] | None = None,
) -> UUID | None:
    """Put an object and (by default) its metadata row; returns the row id."""
    key = key_for(target, path)
    store.put(key, data, metadata)
    if not with_row:
        return None
    row_id = uuid4()
    async with get_db_context() as db:
        await db.execute(
            insert(FileMetadata).values(
                id=row_id,
                organization_id=target.organization_id,
                solution_id=target.solution_id,
                location=target.location,
                path=path,
                s3_key=key,
                content_type="application/pdf",
                size_bytes=len(data),
                created_by=uuid4(),
            )
        )
    return row_id


async def rows_at(target: MoveTarget, path: str) -> list[FileMetadata]:
    filters: list[Any] = [FileMetadata.location == target.location, FileMetadata.path == path]
    if target.solution_id is not None:
        filters.append(FileMetadata.solution_id == target.solution_id)
    else:
        filters += [
            FileMetadata.organization_id == target.organization_id,
            FileMetadata.solution_id.is_(None),
        ]
    async with get_db_context() as db:
        return list((await db.execute(select(FileMetadata).where(*filters))).scalars().all())
```

```python
# api/tests/unit/services/file_move/conftest.py
"""Fixtures for files.move engine and job tests (real Postgres, fake S3)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete

from src.core.database import get_db_context
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.file_storage.move import MoveTarget

from .support import FakeStore


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture(autouse=True)
def published(monkeypatch) -> AsyncMock:
    mock = AsyncMock()
    monkeypatch.setattr("src.core.pubsub.publish_file_change", mock)
    return mock


@pytest_asyncio.fixture
async def solution_target():
    org_id, solution_id = uuid4(), uuid4()
    async with get_db_context() as db:
        db.add(Organization(id=org_id, name=f"Move-{org_id.hex[:8]}", created_by="test"))
        await db.flush()
        db.add(Solution(id=solution_id, slug=f"move-{solution_id.hex[:8]}", name="Move", organization_id=org_id))
    yield MoveTarget(
        location="docs",
        scope=str(solution_id),
        solution_id=solution_id,
        organization_id=org_id,
        requested_by=uuid4(),
    )
    async with get_db_context() as db:
        await db.execute(delete(Solution).where(Solution.id == solution_id))
        await db.execute(delete(Organization).where(Organization.id == org_id))


@pytest_asyncio.fixture
async def org_target():
    org_id = uuid4()
    async with get_db_context() as db:
        db.add(Organization(id=org_id, name=f"Move-{org_id.hex[:8]}", created_by="test"))
    yield MoveTarget(
        location="docs",
        scope=str(org_id),
        solution_id=None,
        organization_id=org_id,
        requested_by=uuid4(),
    )
    async with get_db_context() as db:
        await db.execute(delete(Organization).where(Organization.id == org_id))
```

- [ ] **Step 2: Write the failing engine tests**

```python
# api/tests/unit/services/file_move/test_engine.py
"""FileMover: the locked single-part move of one file."""

from __future__ import annotations

from unittest.mock import AsyncMock, call

import pytest
from botocore.exceptions import EndpointConnectionError

from src.services.file_storage.move import (
    MARK_ETAG,
    MARK_ROW,
    MARK_SOURCE,
    FileMover,
    MoveRequest,
    source_marker,
)
from src.services.file_storage.s3_client import ObjectPreconditionFailed

from .support import FakeStore, key_for, rows_at, seed_file


async def test_moves_file_keeps_row_identity_and_publishes(store, solution_target, published) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    source_key, dest_key = key_for(solution_target, "a/r.pdf"), key_for(solution_target, "b/r.pdf")
    source_etag = store.objects[source_key].etag

    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")

    assert (outcome.status_code, outcome.error, outcome.path, outcome.renamed, outcome.adopted) == (
        200, None, None, False, False,
    )
    assert source_key not in store.objects
    assert store.objects[dest_key].data == b"%PDF-1.7 test"
    assert store.objects[dest_key].metadata == {
        MARK_SOURCE: source_marker(source_key),
        MARK_ROW: str(row_id),
        MARK_ETAG: source_etag,
    }
    [moved] = await rows_at(solution_target, "b/r.pdf")
    assert moved.id == row_id and moved.updated_by == solution_target.requested_by
    assert await rows_at(solution_target, "a/r.pdf") == []
    assert published.await_args_list == [
        call(location="docs", scope=solution_target.scope, path="a/r.pdf", action="delete"),
        call(location="docs", scope=solution_target.scope, path="b/r.pdf", action="write"),
    ]


async def test_copy_carries_source_metadata_and_guards_with_source_etag(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf", metadata={"owner": "x"})
    await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    [copy] = store.copies
    assert copy["metadata"]["owner"] == "x"
    assert copy["source"].etag.startswith('"')


async def test_org_scoped_rows_move(store, org_target) -> None:
    row_id = await seed_file(store, org_target, "a/r.pdf")
    outcome = await FileMover(store, org_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 200
    assert [row.id for row in await rows_at(org_target, "b/r.pdf")] == [row_id]


async def test_source_without_row_inserts_destination_row(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf", with_row=False)
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 200
    [row] = await rows_at(solution_target, "b/r.pdf")
    assert row.created_by == solution_target.requested_by
    assert store.objects[key_for(solution_target, "b/r.pdf")].metadata[MARK_ROW] == ""


async def test_case_only_rename_is_a_real_move(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/Report.pdf")
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/Report.pdf", "a/report.pdf"), "error")
    assert outcome.status_code == 200
    assert key_for(solution_target, "a/Report.pdf") not in store.objects
    assert key_for(solution_target, "a/report.pdf") in store.objects


async def test_zero_byte_file_moves(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/empty.txt", data=b"")
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/empty.txt", "b/empty.txt"), "error")
    assert outcome.status_code == 200
    assert store.objects[key_for(solution_target, "b/empty.txt")].data == b""


async def test_existing_destination_in_error_mode_is_409_and_nothing_changes(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    await seed_file(store, solution_target, "b/r.pdf", data=b"other")
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (409, "file_exists")
    assert store.objects[key_for(solution_target, "b/r.pdf")].data == b"other"
    assert key_for(solution_target, "a/r.pdf") in store.objects
    assert len(await rows_at(solution_target, "a/r.pdf")) == 1


async def test_rename_mode_takes_lowest_free_number(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    for path in ("b/r.pdf", "b/r (1).pdf", "b/r (3).pdf"):
        await seed_file(store, solution_target, path, data=b"other", with_row=False)
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    assert (outcome.status_code, outcome.path, outcome.renamed) == (200, "b/r (2).pdf", True)
    assert len(await rows_at(solution_target, "b/r (2).pdf")) == 1


async def test_rename_candidate_taken_after_listing_moves_on(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    await seed_file(store, solution_target, "b/r.pdf", data=b"other", with_row=False)
    raced = key_for(solution_target, "b/r (1).pdf")

    async def writer_races_listing(key: str) -> None:
        if key == raced and raced not in store.objects:
            store.put(raced, b"raced")

    store.before["head"] = writer_races_listing
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    assert outcome.path == "b/r (2).pdf"
    assert store.objects[raced].data == b"raced"


async def test_rename_exhausted_is_no_available_name(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"other")
    for n in range(1, 1001):
        store.put(key_for(solution_target, f"b/r ({n}).pdf"), b"other")
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    assert (outcome.status_code, outcome.error) == (409, "no_available_name")


async def test_verification_mismatch_cleans_up_copy(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    dest_key = key_for(solution_target, "b/r.pdf")

    async def truncate(key: str) -> None:
        store.objects[key].data = b"short"

    store.after["copy"] = truncate
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (500, "copy_verification_failed")
    assert dest_key not in store.objects
    assert key_for(solution_target, "a/r.pdf") in store.objects
    assert len(await rows_at(solution_target, "a/r.pdf")) == 1


async def test_source_replaced_before_delete_keeps_new_upload_and_removes_copy(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    source_key = key_for(solution_target, "a/r.pdf")

    async def presigned_upload_replaces_source(_key: str) -> None:
        store.put(source_key, b"new upload")

    store.after["copy"] = presigned_upload_replaces_source
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (409, "source_changed")
    assert store.objects[source_key].data == b"new upload"
    assert key_for(solution_target, "b/r.pdf") not in store.objects
    assert len(await rows_at(solution_target, "a/r.pdf")) == 1


async def test_delete_error_after_server_side_success_is_success(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    source_key = key_for(solution_target, "a/r.pdf")

    async def delete_lands_then_client_times_out(key: str) -> None:
        if key == source_key:
            store.objects.pop(key, None)
            raise TimeoutError("read timeout")

    store.before["delete"] = delete_lands_then_client_times_out
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 200
    assert len(await rows_at(solution_target, "b/r.pdf")) == 1


async def test_unconfirmed_delete_keeps_copy_and_row_at_source(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    source_key = key_for(solution_target, "a/r.pdf")

    async def delete_fails(key: str) -> None:
        if key == source_key:
            raise RuntimeError("delete failed")

    store.before["delete"] = delete_fails
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (500, "source_delete_unconfirmed")
    assert key_for(solution_target, "b/r.pdf") in store.objects  # never cleaned up
    assert len(await rows_at(solution_target, "a/r.pdf")) == 1
    assert await rows_at(solution_target, "b/r.pdf") == []


async def test_failed_confirmation_head_keeps_copy(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    source_key = key_for(solution_target, "a/r.pdf")
    deleted = False

    async def mark_deleted(key: str) -> None:
        nonlocal deleted
        deleted = deleted or key == source_key

    async def head_fails_after_delete(key: str) -> None:
        if deleted and key == source_key:
            raise RuntimeError("head failed")

    store.after["delete"] = mark_deleted
    store.before["head"] = head_fails_after_delete
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (500, "source_delete_unconfirmed")
    assert key_for(solution_target, "b/r.pdf") in store.objects
    assert len(await rows_at(solution_target, "a/r.pdf")) == 1


@pytest.mark.parametrize(("side", "code"), [("source", "source_changed"), ("destination", "file_exists")])
async def test_copy_precondition_failures(store, solution_target, side, code) -> None:
    await seed_file(store, solution_target, "a/r.pdf")

    async def refuse(_key: str) -> None:
        raise ObjectPreconditionFailed(side)

    store.before["copy"] = refuse
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (409, code)
    assert key_for(solution_target, "b/r.pdf") not in store.objects


async def test_cleanup_failure_is_reported_and_logged(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    dest_key = key_for(solution_target, "b/r.pdf")

    async def truncate(key: str) -> None:
        store.objects[key].data = b"short"

    async def cleanup_fails(key: str) -> None:
        if key == dest_key:
            raise RuntimeError("delete failed")

    store.after["copy"] = truncate
    store.before["delete"] = cleanup_fails
    notify = AsyncMock()
    outcome = await FileMover(store, solution_target, on_cleanup_failed=notify).move(
        MoveRequest("a/r.pdf", "b/r.pdf"), "error"
    )
    assert (outcome.status_code, outcome.error) == (500, "cleanup_failed")
    notify.assert_awaited_once_with(key_for(solution_target, "a/r.pdf"), dest_key)


async def test_missing_source_is_404(store, solution_target) -> None:
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/none.pdf", "b/none.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (404, "not_found")


async def test_storage_errors_are_flagged(store, solution_target) -> None:
    async def unreachable(_key: str) -> None:
        raise EndpointConnectionError(endpoint_url="http://s3")

    store.before["head"] = unreachable
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 500 and outcome.storage_error
    assert outcome.error.startswith("storage_unavailable")


async def test_internal_errors_are_not_storage_errors(store, solution_target) -> None:
    async def boom(_key: str) -> None:
        raise ValueError("bad")

    store.before["head"] = boom
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 500 and not outcome.storage_error
    assert outcome.error.startswith("internal_error")


async def test_publish_failure_after_commit_still_reports_success(store, solution_target, published) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    published.side_effect = RuntimeError("redis down")
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 200
    assert len(await rows_at(solution_target, "b/r.pdf")) == 1
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `./test.sh tests/unit/services/file_move/test_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.file_storage.move'`.

- [ ] **Step 4: Implement the engine (locked flow)**

```python
# api/src/services/file_storage/move.py
"""Server-side move of one file inside one location and scope.

The ``files.move`` platform job (``src.jobs.platform.file_move``) creates one
:class:`FileMover` per job and calls :meth:`FileMover.move` once per item.
Each item:
1. Locks both paths.
2. Inspects the source.
3. Resolves the destination (conflict or rename).
4. Copies server-side with a source-ETag guard.
5. Verifies the copy's size.
6. Re-keys metadata.
7. Re-checks the source, then deletes it.
8. Commits and publishes.

The full contract is ``docs/superpowers/specs/2026-10-09-files-move-design.md``.

A move never raises. Every outcome, including storage and database errors,
is returned as a :class:`MoveOutcome`, so one item cannot stop the rest of a
batch.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from shared.file_access import lock_file_mutation
from shared.file_paths import resolve_s3_key
from src.services.file_policy_service import FilePolicyService

from .move_names import candidate_prefix, matching_candidates, numbered_name
from .s3_client import (
    COPY_OBJECT_LIMIT,
    ObjectHead,
    ObjectMissing,
    ObjectPreconditionFailed,
    is_storage_unavailable,
)

logger = logging.getLogger(__name__)

# S3 user-metadata markers that make a copy self-identifying, so a retry
# can recognize its own interrupted copy.
MARK_ROW = "bifrost-move-row"
MARK_SOURCE = "bifrost-move-source"
MARK_ETAG = "bifrost-move-etag"
MAX_RENAME_NUMBER = 1000

OnConflict = Literal["error", "rename"]
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
CleanupFailed = Callable[[str, str], Awaitable[None]]


class ObjectStore(Protocol):
    async def head_object(self, key: str) -> ObjectHead | None: ...

    async def copy_object(
        self,
        source_key: str,
        destination_key: str,
        *,
        source: ObjectHead,
        metadata: dict[str, str],
        multipart_threshold: int = ...,
        part_size: int | None = ...,
    ) -> None: ...

    async def delete_object_strict(self, key: str) -> None: ...

    async def list_keys(self, prefix: str, *, delimiter: str = "/") -> list[str]: ...


@dataclass(frozen=True)
class MoveTarget:
    """The one location and scope a job moves files within."""

    location: str
    scope: str
    solution_id: UUID | None
    organization_id: UUID | None
    requested_by: UUID


@dataclass(frozen=True)
class MoveRequest:
    source: str
    destination: str


@dataclass(frozen=True)
class MoveOutcome:
    status_code: int
    error: str | None = None
    path: str | None = None
    renamed: bool = False
    adopted: bool = False
    storage_error: bool = False


@dataclass(frozen=True)
class _Row:
    """Snapshot of a metadata row; ORM rows expire when a phase rolls back."""

    id: UUID
    size_bytes: int | None


@dataclass(frozen=True)
class _Destination:
    path: str
    key: str
    renamed: bool
    # True when an interrupted earlier run's copy already sits here.
    resume: bool


def source_marker(source_key: str) -> str:
    """Value of the ``bifrost-move-source`` marker for a source S3 key."""
    return hashlib.sha256(source_key.encode("utf-8")).hexdigest()


def _row_marker(row: _Row | None) -> str:
    return str(row.id) if row is not None else ""


def _fail(status_code: int, code: str, message: str | None = None) -> MoveOutcome:
    error = code if message is None else f"{code}: {message}"
    return MoveOutcome(status_code=status_code, error=error[:500])


class FileMover:
    """Moves single files within one :class:`MoveTarget`."""

    def __init__(
        self,
        store: ObjectStore,
        target: MoveTarget,
        *,
        open_session: SessionFactory | None = None,
        on_cleanup_failed: CleanupFailed | None = None,
        multipart_threshold: int = COPY_OBJECT_LIMIT,
        part_size: int | None = None,
    ) -> None:
        if open_session is None:
            from src.core.database import get_db_context

            open_session = get_db_context
        self._store = store
        self._target = target
        self._open_session = open_session
        self._on_cleanup_failed = on_cleanup_failed
        self._multipart_threshold = multipart_threshold
        self._part_size = part_size

    async def move(self, request: MoveRequest, on_conflict: OnConflict) -> MoveOutcome:
        """Move one file; every failure is returned, never raised."""
        try:
            return await self._move(request, on_conflict)
        except Exception as exc:
            storage = is_storage_unavailable(exc)
            logger.warning(
                "File move item failed",
                extra={"location": self._target.location},
                exc_info=True,
            )
            code = "storage_unavailable" if storage else "internal_error"
            return MoveOutcome(
                status_code=500,
                error=f"{code}: {type(exc).__name__}",
                storage_error=storage,
            )

    async def _move(self, request: MoveRequest, on_conflict: OnConflict) -> MoveOutcome:
        source_key = self._key(request.source)
        async with self._open_session() as db:
            await self._lock(db, request.source, request.destination)
            row = await self._row(db, request.source)
            source = await self._store.head_object(source_key)
            if source is None:
                return await self._recover(db, request, source_key, row, on_conflict)
            chosen = await self._choose_destination(db, request, source_key, source, row, on_conflict)
            if isinstance(chosen, MoveOutcome):
                await db.rollback()
                return chosen
            if not chosen.resume:
                failure = await self._copy(source_key, chosen, source, row)
                if failure is not None:
                    await db.rollback()
                    return failure
            return await self._complete(db, request, source_key, source, row, chosen)

    # -- destination ------------------------------------------------------

    async def _choose_destination(
        self,
        db: AsyncSession,
        request: MoveRequest,
        source_key: str,
        source: ObjectHead,
        row: _Row | None,
        on_conflict: OnConflict,
    ) -> _Destination | MoveOutcome:
        destination_key = self._key(request.destination)
        if await self._store.head_object(destination_key) is None:
            return _Destination(request.destination, destination_key, renamed=False, resume=False)
        if on_conflict == "error":
            return _fail(409, "file_exists")
        taken = await self._candidates(request.destination)
        return await self._first_free_candidate(db, request.destination, taken)

    async def _candidates(self, destination: str) -> dict[int, tuple[str, str]]:
        """Existing ``stem (n)ext`` siblings of ``destination``: n → (path, key)."""
        keys = await self._store.list_keys(self._key(candidate_prefix(destination)))
        directory = destination.rpartition("/")[0]
        found: dict[int, tuple[str, str]] = {}
        names = (key.rsplit("/", 1)[-1] for key in keys)
        for number, name in matching_candidates(destination, names).items():
            path = f"{directory}/{name}" if directory else name
            found[number] = (path, self._key(path))
        return found

    async def _first_free_candidate(
        self,
        db: AsyncSession,
        destination: str,
        taken: dict[int, tuple[str, str]],
    ) -> _Destination | MoveOutcome:
        for number in range(1, MAX_RENAME_NUMBER + 1):
            if number in taken:
                continue
            path = numbered_name(destination, number)
            key = self._key(path)
            await self._lock(db, path)
            # A writer can take a free number between the listing and the lock.
            if await self._store.head_object(key) is None:
                return _Destination(path, key, renamed=True, resume=False)
        return _fail(409, "no_available_name")

    # -- copy, verify, commit -----------------------------------------------

    async def _copy(
        self,
        source_key: str,
        destination: _Destination,
        source: ObjectHead,
        row: _Row | None,
    ) -> MoveOutcome | None:
        metadata = {
            **source.metadata,
            MARK_ROW: _row_marker(row),
            MARK_SOURCE: source_marker(source_key),
            MARK_ETAG: source.etag,
        }
        try:
            await self._store.copy_object(
                source_key,
                destination.key,
                source=source,
                metadata=metadata,
                multipart_threshold=self._multipart_threshold,
                part_size=self._part_size,
            )
        except ObjectPreconditionFailed as exc:
            return _fail(409, "source_changed" if exc.side == "source" else "file_exists")
        except ObjectMissing:
            return _fail(404, "not_found", "the source was deleted during the move")
        return None

    async def _complete(
        self,
        db: AsyncSession,
        request: MoveRequest,
        source_key: str,
        source: ObjectHead,
        row: _Row | None,
        destination: _Destination,
    ) -> MoveOutcome:
        landed = await self._store.head_object(destination.key)
        if landed is None or landed.size != source.size:
            await db.rollback()
            return await self._cleanup(source_key, destination, _fail(500, "copy_verification_failed"))
        await self._write_metadata(db, destination, landed, row)

        current = await self._store.head_object(source_key)
        if current is None:
            await db.rollback()
            return await self._cleanup(
                source_key, destination, _fail(404, "not_found", "the source was deleted during the move")
            )
        if current.etag != source.etag:
            # A presigned upload replaced the source mid-move; keep it.
            await db.rollback()
            return await self._cleanup(source_key, destination, _fail(409, "source_changed"))

        # Point of no return: once the delete is issued, the copy is never
        # cleaned up — a delete can land server-side after a client error.
        try:
            await self._store.delete_object_strict(source_key)
        except Exception:
            logger.warning("Source delete raised during a move; confirming", exc_info=True)
        if not await self._confirmed_gone(source_key):
            await db.rollback()
            return _fail(500, "source_delete_unconfirmed")

        await db.commit()
        await self._publish(request.source, destination.path)
        return MoveOutcome(
            status_code=200,
            path=destination.path if destination.renamed else None,
            renamed=destination.renamed,
        )

    async def _write_metadata(
        self,
        db: AsyncSession,
        destination: _Destination,
        head: ObjectHead,
        row: _Row | None,
    ) -> None:
        await FilePolicyService(db).move_metadata(
            organization_id=self._target.organization_id,
            solution_id=self._target.solution_id,
            location=self._target.location,
            source_row_id=row.id if row is not None else None,
            destination_path=destination.path,
            s3_key=destination.key,
            content_type=head.headers.get("ContentType"),
            size_bytes=head.size,
            updated_by=self._target.requested_by,
        )

    async def _confirmed_gone(self, key: str) -> bool:
        try:
            return await self._store.head_object(key) is None
        except Exception:
            logger.warning("Could not confirm a source delete", exc_info=True)
            return False

    async def _cleanup(
        self, source_key: str, destination: _Destination, outcome: MoveOutcome
    ) -> MoveOutcome:
        """Remove this item's copy before the source delete was issued."""
        try:
            await self._store.delete_object_strict(destination.key)
        except Exception:
            logger.warning("Could not remove a failed move's copy", exc_info=True)
            if self._on_cleanup_failed is not None:
                await self._on_cleanup_failed(source_key, destination.key)
            return _fail(500, "cleanup_failed")
        return outcome

    async def _publish(self, source_path: str, destination_path: str) -> None:
        from src.core import pubsub

        try:
            await pubsub.publish_file_change(
                location=self._target.location,
                scope=self._target.scope,
                path=source_path,
                action="delete",
            )
            await pubsub.publish_file_change(
                location=self._target.location,
                scope=self._target.scope,
                path=destination_path,
                action="write",
            )
        except Exception:
            # The move is committed; a missed notification must not report it failed.
            logger.warning("Could not publish file-change events for a move", exc_info=True)

    # -- recovery -----------------------------------------------------------

    async def _recover(
        self,
        db: AsyncSession,
        request: MoveRequest,
        source_key: str,
        row: _Row | None,
        on_conflict: OnConflict,
    ) -> MoveOutcome:
        await db.rollback()
        return _fail(404, "not_found")

    # -- helpers ------------------------------------------------------------

    def _key(self, path: str) -> str:
        return resolve_s3_key(self._target.location, self._target.scope, path)

    async def _lock(self, db: AsyncSession, *paths: str) -> None:
        for path in sorted(set(paths)):
            await lock_file_mutation(
                db, location=self._target.location, scope=self._target.scope, path=path
            )

    async def _row(self, db: AsyncSession, path: str) -> _Row | None:
        row = await FilePolicyService(db).get_scoped_metadata(
            organization_id=self._target.organization_id,
            solution_id=self._target.solution_id,
            location=self._target.location,
            path=path,
        )
        return _Row(id=row.id, size_bytes=row.size_bytes) if row is not None else None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./test.sh tests/unit/services/file_move/test_engine.py -v`
Expected: PASS (22 tests).

- [ ] **Step 6: Commit**

```bash
git add api/src/services/file_storage/move.py api/tests/unit/services/file_move/
git commit -m "feat(files): files.move engine — locked single-part move with safe delete

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Move engine — unlocked multipart window

**Files:**
- Modify: `api/src/services/file_storage/move.py` (`_move`, plus new `_is_copy_of` and `_row_id`)
- Test: `api/tests/unit/services/file_move/test_engine_multipart.py` (create)

**Interfaces:**
- Produces:
  - Objects larger than `multipart_threshold` are copied with the path locks released. Steps 5–8 run in a new transaction that re-locks both paths and re-checks first.
  - New error code `destination_changed` (409).
  - `FileMover._is_copy_of(head, source_key, row) -> bool`: markers match the source and the row (including the "no row" marker `""`). Task 9 reuses it.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/unit/services/file_move/test_engine_multipart.py
"""FileMover: multipart copies run without path locks and re-check after."""

from __future__ import annotations

from sqlalchemy import text

from src.core.database import get_db_context
from src.services.file_storage.move import FileMover, MoveRequest
from src.services.file_storage.s3_client import ObjectMissing

from .support import key_for, rows_at, seed_file

DATA = b"x" * 64  # above the test threshold of 16 bytes


def mover(store, target) -> FileMover:
    return FileMover(store, target, multipart_threshold=16, part_size=8)


async def _path_lock_free(target, path: str) -> bool:
    async with get_db_context() as db:
        return bool(
            (
                await db.execute(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:k, 0))"),
                    {"k": f"{target.location}:{target.scope}:{path}"},
                )
            ).scalar()
        )


async def test_multipart_copy_runs_without_path_locks(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/big.bin", data=DATA)
    observed: list[bool] = []

    async def probe(_key: str) -> None:
        observed.append(await _path_lock_free(solution_target, "a/big.bin"))

    store.before["copy"] = probe
    outcome = await mover(store, solution_target).move(MoveRequest("a/big.bin", "b/big.bin"), "error")
    assert outcome.status_code == 200
    assert observed == [True]
    assert store.copies[0]["multipart_threshold"] == 16 and store.copies[0]["part_size"] == 8


async def test_single_part_copy_holds_path_locks(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/small.bin", data=b"tiny")
    observed: list[bool] = []

    async def probe(_key: str) -> None:
        observed.append(await _path_lock_free(solution_target, "a/small.bin"))

    store.before["copy"] = probe
    await mover(store, solution_target).move(MoveRequest("a/small.bin", "b/small.bin"), "error")
    assert observed == [False]


async def test_destination_replaced_after_copy_is_destination_changed(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/big.bin", data=DATA)
    dest_key = key_for(solution_target, "b/big.bin")

    async def writer_replaces_destination(key: str) -> None:
        store.put(key, b"someone else")

    store.after["copy"] = writer_replaces_destination
    outcome = await mover(store, solution_target).move(MoveRequest("a/big.bin", "b/big.bin"), "error")
    assert (outcome.status_code, outcome.error) == (409, "destination_changed")
    assert store.objects[dest_key].data == b"someone else"
    assert key_for(solution_target, "a/big.bin") in store.objects


async def test_destination_deleted_after_copy_is_destination_changed(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/big.bin", data=DATA)

    async def writer_deletes_destination(key: str) -> None:
        store.objects.pop(key)

    store.after["copy"] = writer_deletes_destination
    outcome = await mover(store, solution_target).move(MoveRequest("a/big.bin", "b/big.bin"), "error")
    assert (outcome.status_code, outcome.error) == (409, "destination_changed")


async def test_source_deleted_during_copy_is_404_and_never_resurrects(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/big.bin", data=DATA)
    source_key = key_for(solution_target, "a/big.bin")

    async def files_delete_lands(_key: str) -> None:
        from sqlalchemy import delete

        from src.models.orm.file_metadata import FileMetadata

        store.objects.pop(source_key)
        async with get_db_context() as db:
            await db.execute(delete(FileMetadata).where(FileMetadata.s3_key == source_key))

    store.after["copy"] = files_delete_lands
    outcome = await mover(store, solution_target).move(MoveRequest("a/big.bin", "b/big.bin"), "error")
    assert (outcome.status_code, outcome.error.split(":")[0]) == (404, "not_found")
    assert key_for(solution_target, "b/big.bin") not in store.objects
    assert await rows_at(solution_target, "b/big.bin") == []


async def test_source_replaced_during_copy_removes_copy(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/big.bin", data=DATA)
    source_key = key_for(solution_target, "a/big.bin")

    async def presigned_upload(_key: str) -> None:
        store.put(source_key, b"y" * 64)

    store.after["copy"] = presigned_upload
    outcome = await mover(store, solution_target).move(MoveRequest("a/big.bin", "b/big.bin"), "error")
    assert (outcome.status_code, outcome.error) == (409, "source_changed")
    assert key_for(solution_target, "b/big.bin") not in store.objects


async def test_source_missing_on_part_copy_is_404(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/big.bin", data=DATA)

    async def vanished(_key: str) -> None:
        raise ObjectMissing("gone")

    store.before["copy"] = vanished
    outcome = await mover(store, solution_target).move(MoveRequest("a/big.bin", "b/big.bin"), "error")
    assert outcome.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./test.sh tests/unit/services/file_move/test_engine_multipart.py -v`
Expected: FAIL. `test_multipart_copy_runs_without_path_locks` observes `[False]`, and the destination-change tests get 200 or `copy_verification_failed`.

- [ ] **Step 3: Implement**

In `move.py`, add a module helper after `_row_marker`:

```python
def _row_id(row: _Row | None) -> UUID | None:
    return row.id if row is not None else None
```

Replace `_move` with:

```python
    async def _move(self, request: MoveRequest, on_conflict: OnConflict) -> MoveOutcome:
        source_key = self._key(request.source)
        async with self._open_session() as db:
            await self._lock(db, request.source, request.destination)
            row = await self._row(db, request.source)
            source = await self._store.head_object(source_key)
            if source is None:
                return await self._recover(db, request, source_key, row, on_conflict)
            chosen = await self._choose_destination(db, request, source_key, source, row, on_conflict)
            if isinstance(chosen, MoveOutcome):
                await db.rollback()
                return chosen
            if chosen.resume or source.size <= self._multipart_threshold:
                if not chosen.resume:
                    failure = await self._copy(source_key, chosen, source, row)
                    if failure is not None:
                        await db.rollback()
                        return failure
                return await self._complete(db, request, source_key, source, row, chosen)
            # Multipart: end the locked transaction (nothing is written yet) so
            # no API writer waits on these paths for the length of the copy.
            # The S3 preconditions guard the copy instead.
            await db.rollback()

        failure = await self._copy(source_key, chosen, source, row)
        if failure is not None:
            return failure
        async with self._open_session() as db:
            await self._lock(db, request.source, chosen.path)
            landed = await self._store.head_object(chosen.key)
            if landed is None or not self._is_copy_of(landed, source_key, row):
                await db.rollback()
                return _fail(409, "destination_changed")
            current = await self._store.head_object(source_key)
            if current is None:
                await db.rollback()
                return await self._cleanup(
                    source_key, chosen, _fail(404, "not_found", "the source was deleted during the move")
                )
            current_row = await self._row(db, request.source)
            if current.etag != source.etag or _row_id(current_row) != _row_id(row):
                await db.rollback()
                return await self._cleanup(source_key, chosen, _fail(409, "source_changed"))
            return await self._complete(db, request, source_key, source, current_row, chosen)
```

Add to the helpers section:

```python
    def _is_copy_of(self, head: ObjectHead, source_key: str, row: _Row | None) -> bool:
        """The object carries this item's markers (source key and source row)."""
        return (
            head.metadata.get(MARK_SOURCE) == source_marker(source_key)
            and head.metadata.get(MARK_ROW) == _row_marker(row)
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./test.sh tests/unit/services/file_move/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/file_storage/move.py api/tests/unit/services/file_move/test_engine_multipart.py
git commit -m "feat(files): release path locks during multipart move copies

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Move engine — resume, adopt, already moved

**Files:**
- Modify: `api/src/services/file_storage/move.py` (`_choose_destination`, `_recover`, new `_is_orphan` and `_recovery_targets`)
- Test: `api/tests/unit/services/file_move/test_engine_recovery.py` (create)

**Interfaces:**
- Consumes: `FileMover._is_copy_of` (Task 8), `FileMover._candidates` (Task 7).
- Produces:
  - `MoveOutcome.adopted=True` for adopt and already-moved outcomes.
  - Resume reports normally (`adopted=False`) but skips the copy.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/unit/services/file_move/test_engine_recovery.py
"""FileMover: a retry recognizes its interrupted copy by markers, never by size."""

from __future__ import annotations

from src.services.file_storage.move import FileMover, MoveRequest

from .support import key_for, markers, rows_at, seed_file


async def _interrupted_copy(store, target, source: str, destination: str, row_id, *, delete_source: bool):
    """State after a run died: a marked copy at ``destination``, source maybe deleted."""
    source_key = key_for(target, source)
    obj = store.objects[source_key]
    store.put(key_for(target, destination), obj.data, markers(source_key, row_id, obj.etag))
    if delete_source:
        store.objects.pop(source_key)


async def test_resume_skips_copy_and_completes(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r.pdf", row_id, delete_source=False)
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.adopted) == (200, False)
    assert not any(op == "copy" for op, _ in store.calls)
    assert key_for(solution_target, "a/r.pdf") not in store.objects
    assert [r.id for r in await rows_at(solution_target, "b/r.pdf")] == [row_id]


async def test_resume_with_stale_etag_marker_is_a_conflict(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r.pdf", row_id, delete_source=False)
    store.put(key_for(solution_target, "a/r.pdf"), b"replaced since")  # new ETag
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (409, "file_exists")


async def test_resume_under_rename_finishes_at_the_orphans_candidate(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"someone else")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r (1).pdf", row_id, delete_source=False)
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    assert (outcome.status_code, outcome.path, outcome.renamed) == (200, "b/r (1).pdf", True)
    assert key_for(solution_target, "b/r (2).pdf") not in store.objects
    assert not any(op == "copy" for op, _ in store.calls)


async def test_adopt_completes_a_move_whose_source_was_deleted(store, solution_target, published) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r.pdf", row_id, delete_source=True)
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.adopted) == (200, True)
    assert [r.id for r in await rows_at(solution_target, "b/r.pdf")] == [row_id]
    assert await rows_at(solution_target, "a/r.pdf") == []
    assert published.await_count == 2


async def test_adopt_under_rename_finds_the_candidate(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"someone else")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r (1).pdf", row_id, delete_source=True)
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    assert (outcome.status_code, outcome.path, outcome.adopted) == (200, "b/r (1).pdf", True)


async def test_adopt_refuses_objects_that_are_not_this_moves_copy(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    source_key = key_for(solution_target, "a/r.pdf")
    data, etag = store.objects[source_key].data, store.objects[source_key].etag
    store.objects.pop(source_key)
    cases = {
        "b/no-markers.pdf": {},
        "b/other-source.pdf": markers(key_for(solution_target, "a/other.pdf"), row_id, etag),
        "b/other-row.pdf": markers(source_key, "not-the-row", etag),
    }
    for path, meta in cases.items():
        store.put(key_for(solution_target, path), data, meta)
        outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", path), "error")
        assert (outcome.status_code, outcome.error) == (404, "not_found"), path
    assert len(await rows_at(solution_target, "a/r.pdf")) == 1


async def test_adopt_refuses_a_copy_of_an_older_version(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf", data=b"version two, longer")
    source_key = key_for(solution_target, "a/r.pdf")
    store.objects.pop(source_key)
    store.put(key_for(solution_target, "b/r.pdf"), b"v1", markers(source_key, row_id, '"old"'))
    outcome = await FileMover(store, solution_target).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 404


async def test_already_moved_is_a_noop_success(store, solution_target, published) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    mover = FileMover(store, solution_target)
    assert (await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")).status_code == 200
    published.reset_mock()

    again = await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (again.status_code, again.adopted) == (200, True)
    assert [r.id for r in await rows_at(solution_target, "b/r.pdf")] == [row_id]
    published.assert_not_awaited()


async def test_already_moved_under_rename_reports_the_final_path(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"someone else")
    mover = FileMover(store, solution_target)
    first = await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    again = await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    assert (again.status_code, again.path, again.adopted) == (200, first.path, True)


async def test_row_at_source_never_takes_the_already_moved_branch(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    mover = FileMover(store, solution_target)
    assert (await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")).status_code == 200
    # A new file row appears at the source path, its object removed out of band.
    await seed_file(store, solution_target, "a/r.pdf")
    store.objects.pop(key_for(solution_target, "a/r.pdf"))
    outcome = await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (404, "not_found")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./test.sh tests/unit/services/file_move/test_engine_recovery.py -v`
Expected: FAIL. The resume tests get 409 `file_exists` or a `(2)` candidate, and the adopt and already-moved tests get 404.

- [ ] **Step 3: Implement**

Replace `_choose_destination` with:

```python
    async def _choose_destination(
        self,
        db: AsyncSession,
        request: MoveRequest,
        source_key: str,
        source: ObjectHead,
        row: _Row | None,
        on_conflict: OnConflict,
    ) -> _Destination | MoveOutcome:
        destination_key = self._key(request.destination)
        existing = await self._store.head_object(destination_key)
        if existing is None:
            return _Destination(request.destination, destination_key, renamed=False, resume=False)
        if self._is_orphan(existing, source_key, source, row):
            return _Destination(request.destination, destination_key, renamed=False, resume=True)
        if on_conflict == "error":
            return _fail(409, "file_exists")
        taken = await self._candidates(request.destination)
        for number in sorted(taken):
            path, key = taken[number]
            head = await self._store.head_object(key)
            if head is not None and self._is_orphan(head, source_key, source, row):
                await self._lock(db, path)
                return _Destination(path, key, renamed=True, resume=True)
        return await self._first_free_candidate(db, request.destination, taken)
```

Replace `_recover` with:

```python
    async def _recover(
        self,
        db: AsyncSession,
        request: MoveRequest,
        source_key: str,
        row: _Row | None,
        on_conflict: OnConflict,
    ) -> MoveOutcome:
        """The source object is gone: adopt our copy, report already moved, or 404."""
        targets = await self._recovery_targets(request.destination, on_conflict)
        if row is not None:
            # Adopt: an earlier run deleted the source but died before its commit.
            for path, key in targets:
                head = await self._store.head_object(key)
                if head is None or not self._is_copy_of(head, source_key, row):
                    continue
                if row.size_bytes is not None and row.size_bytes != head.size:
                    continue  # a copy of an older version of the file
                await self._lock(db, path)
                destination = _Destination(path, key, renamed=path != request.destination, resume=True)
                await self._write_metadata(db, destination, head, row)
                await db.commit()
                await self._publish(request.source, path)
                return MoveOutcome(
                    status_code=200,
                    path=path if destination.renamed else None,
                    renamed=destination.renamed,
                    adopted=True,
                )
            await db.rollback()
            return _fail(404, "not_found")

        # Already moved: the source row now lives at a destination we copied to.
        for path, key in targets:
            head = await self._store.head_object(key)
            if head is None or head.metadata.get(MARK_SOURCE) != source_marker(source_key):
                continue
            moved = await self._row(db, path)
            if moved is not None and head.metadata.get(MARK_ROW) == str(moved.id):
                await db.rollback()
                renamed = path != request.destination
                return MoveOutcome(
                    status_code=200,
                    path=path if renamed else None,
                    renamed=renamed,
                    adopted=True,
                )
        await db.rollback()
        return _fail(404, "not_found")

    async def _recovery_targets(
        self, destination: str, on_conflict: OnConflict
    ) -> list[tuple[str, str]]:
        """Where an earlier run of this item could have put its copy."""
        targets = [(destination, self._key(destination))]
        if on_conflict == "rename":
            candidates = await self._candidates(destination)
            targets += [candidates[number] for number in sorted(candidates)]
        return targets
```

Add to the helpers section:

```python
    def _is_orphan(
        self,
        head: ObjectHead,
        source_key: str,
        source: ObjectHead,
        row: _Row | None,
    ) -> bool:
        """An interrupted run's copy of exactly the current source bytes."""
        return (
            row is not None
            and self._is_copy_of(head, source_key, row)
            and head.metadata.get(MARK_ETAG) == source.etag
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./test.sh tests/unit/services/file_move/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/src/services/file_storage/move.py api/tests/unit/services/file_move/test_engine_recovery.py
git commit -m "feat(files): marker-based resume, adopt, and already-moved recovery

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: `files.move` platform job and result contract

**Files:**
- Modify: `api/src/models/contracts/cli.py` (append `FileMoveResult`)
- Create: `api/src/jobs/platform/file_move.py`
- Modify: `api/src/jobs/platform/registry.py`
- Test: `api/tests/unit/services/file_move/test_job.py` (create)

**Interfaces:**
- Consumes:
  - Task 7: `FileMover`, `MoveRequest`, `MoveTarget`.
  - Task 5: `PlatformJobContext.save_checkpoint(..., current=, total=)`.
- Produces:
  - `src.models.contracts.cli.FileMoveResult`
  - In `src.jobs.platform.file_move`:
    - `FILE_MOVE_JOB_TYPE = "files.move"`
    - `FileMoveWorkItem(index, source, destination)`
    - `RejectedFileMove(index, result: FileMoveResult)`
    - `FileMovePayload(location, scope, solution_id, organization_id, on_conflict, requested_by_user_id, total, items, rejected)`
    - `run_file_move(context, payload) -> dict`
    - `FILE_MOVE_DEFINITION`
    - `_object_store()`, a seam the tests patch.
  - Terminal result shape: `{"results": [FileMoveResult dict, ...], "moved": int, "failed": int}`, in request order.
  - Checkpoint shape: `{"moved": int, "failed": [{"index": int, **FileMoveResult}], "in_flight": {"index", "source", "destination"}}`.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/unit/services/file_move/test_job.py
"""The files.move job: request-order items, compact checkpoints, systemic stop."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from botocore.exceptions import EndpointConnectionError
from pydantic import ValidationError

from src.jobs.platform import file_move
from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure
from src.services import platform_jobs
from src.jobs.platform.file_move import (
    FILE_MOVE_DEFINITION,
    FileMovePayload,
    FileMoveWorkItem,
    RejectedFileMove,
    run_file_move,
)
from src.jobs.platform.registry import get_platform_job_definition
from src.models.contracts.cli import FileMoveResult

from .support import key_for, seed_file


@pytest.fixture
def context():
    return SimpleNamespace(save_checkpoint=AsyncMock(), log=AsyncMock())


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch, store):
    monkeypatch.setattr(file_move, "_object_store", lambda: store)


def payload(target, items, rejected=(), on_conflict="error") -> FileMovePayload:
    return FileMovePayload(
        location=target.location,
        scope=target.scope,
        solution_id=target.solution_id,
        organization_id=target.organization_id,
        on_conflict=on_conflict,
        requested_by_user_id=target.requested_by,
        total=len(items) + len(rejected),
        items=[FileMoveWorkItem(index=i, source=s, destination=d) for i, s, d in items],
        rejected=list(rejected),
    )


def test_definition_is_registered_with_the_spec_policy() -> None:
    assert get_platform_job_definition("files.move") is FILE_MOVE_DEFINITION
    policy = FILE_MOVE_DEFINITION.policy
    assert (policy.timeout_seconds, policy.max_attempts, policy.retry_on_runner_loss) == (14400, 1, False)
    assert (policy.allow_running_cancellation, policy.min_memory_headroom_mb) == (False, 128)


def test_payload_requires_every_index_exactly_once() -> None:
    with pytest.raises(ValidationError):
        FileMovePayload(
            location="docs", scope=str(uuid4()), on_conflict="error",
            requested_by_user_id=uuid4(), total=2,
            items=[FileMoveWorkItem(index=0, source="a", destination="b")],
        )


async def _enqueue_move(db_session):
    job, reused = await platform_jobs.enqueue_platform_job(
        db_session,
        FILE_MOVE_DEFINITION,
        FileMovePayload(
            location="docs", scope=str(uuid4()), on_conflict="error",
            requested_by_user_id=uuid4(), total=1,
            items=[FileMoveWorkItem(index=0, source="a.pdf", destination="b.pdf")],
        ),
        dedupe_key=None,
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type=None,
        resource_id=None,
        title="Moving 1 file",
        action_url=None,
    )
    assert reused is False
    return job


async def test_cancel_succeeds_while_queued(db_session) -> None:
    job = await _enqueue_move(db_session)
    assert job.status == "queued"

    job, accepted = await platform_jobs.request_platform_job_cancel(db_session, job)

    assert accepted is True
    assert job.status == "cancelled"


async def test_cancel_is_refused_while_running(db_session) -> None:
    job = await _enqueue_move(db_session)
    job.status = "running"
    job.lease_token = uuid4()
    job.lease_owner = "scheduler-a"
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()

    job, accepted = await platform_jobs.request_platform_job_cancel(db_session, job)

    assert accepted is False
    assert job.status == "running"
    assert job.cancel_requested_at is None


async def test_results_in_request_order_with_rejections_and_checkpoints(store, solution_target, context) -> None:
    await seed_file(store, solution_target, "a/1.pdf")
    await seed_file(store, solution_target, "a/3.pdf")
    forbidden = RejectedFileMove(
        index=1,
        result=FileMoveResult(source="a/2.pdf", destination="b/2.pdf", status_code=403, error="forbidden"),
    )
    result = await run_file_move(
        context,
        payload(solution_target, [(0, "a/1.pdf", "b/1.pdf"), (2, "a/3.pdf", "b/3.pdf")], [forbidden]),
    )

    assert [r["status_code"] for r in result["results"]] == [200, 403, 200]
    assert (result["moved"], result["failed"]) == (2, 1)
    first, second = context.save_checkpoint.await_args_list
    assert first.args[0]["failed"] == [{"index": 1, **forbidden.result.model_dump()}]
    assert first.args[0]["in_flight"] == {"index": 0, "source": "a/1.pdf", "destination": "b/1.pdf"}
    assert (first.kwargs["current"], first.kwargs["total"], first.kwargs["phase"]) == (0, 2, "Moving 1 of 2")
    assert second.args[0]["moved"] == 1 and second.args[0]["in_flight"]["index"] == 2


async def test_item_failure_is_recorded_in_the_next_checkpoint(store, solution_target, context) -> None:
    await seed_file(store, solution_target, "a/2.pdf")
    await run_file_move(
        context,
        payload(solution_target, [(0, "a/missing.pdf", "b/1.pdf"), (1, "a/2.pdf", "b/2.pdf")]),
    )
    first, second = (c.args[0] for c in context.save_checkpoint.await_args_list)
    assert first["failed"] == []
    assert [f["index"] for f in second["failed"]] == [0]
    assert second["failed"][0]["status_code"] == 404


async def test_same_source_twice_is_already_moved_then_404(store, solution_target, context) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    result = await run_file_move(
        context,
        payload(solution_target, [
            (0, "a/r.pdf", "b/r.pdf"),
            (1, "a/r.pdf", "b/r.pdf"),
            (2, "a/r.pdf", "c/r.pdf"),
        ]),
    )
    statuses = [(r["status_code"], r["adopted"]) for r in result["results"]]
    assert statuses == [(200, False), (200, True), (404, False)]


async def test_swap_fails_cleanly_and_loses_nothing(store, solution_target, context) -> None:
    await seed_file(store, solution_target, "a.pdf", data=b"A")
    await seed_file(store, solution_target, "b.pdf", data=b"B")
    result = await run_file_move(
        context, payload(solution_target, [(0, "a.pdf", "b.pdf"), (1, "b.pdf", "a.pdf")])
    )
    assert [r["status_code"] for r in result["results"]] == [409, 409]
    assert store.objects[key_for(solution_target, "a.pdf")].data == b"A"
    assert store.objects[key_for(solution_target, "b.pdf")].data == b"B"


async def test_three_consecutive_storage_failures_stop_the_job(store, solution_target, context) -> None:
    async def unreachable(_key: str) -> None:
        raise EndpointConnectionError(endpoint_url="http://s3")

    store.before["head"] = unreachable
    items = [(i, f"a/{i}.pdf", f"b/{i}.pdf") for i in range(5)]
    with pytest.raises(PlatformJobFailure) as caught:
        await run_file_move(context, payload(solution_target, items))
    assert caught.value.code == "storage_unavailable"
    results = caught.value.result["results"]
    assert [r["error"] for r in results[3:]] == ["not_attempted", "not_attempted"]
    assert all(r["status_code"] == 500 for r in results)
    assert context.save_checkpoint.await_count == 3


async def test_non_storage_failure_resets_the_storage_run(store, solution_target, context) -> None:
    calls = 0

    async def flaky(_key: str) -> None:
        nonlocal calls
        calls += 1
        # Items 0 and 1 hit storage errors; item 2 (two clean heads) is a plain
        # 404 that resets the run; item 3 fails on storage again — no stop.
        if calls in (1, 2, 5):
            raise EndpointConnectionError(endpoint_url="http://s3")

    store.before["head"] = flaky
    items = [(i, f"a/{i}.pdf", f"b/{i}.pdf") for i in range(4)]
    result = await run_file_move(context, payload(solution_target, items))
    assert [r["status_code"] for r in result["results"]] == [500, 500, 404, 500]


async def test_lost_lease_propagates(store, solution_target, context) -> None:
    context.save_checkpoint.side_effect = PlatformJobCancelled
    with pytest.raises(PlatformJobCancelled):
        await run_file_move(context, payload(solution_target, [(0, "a/r.pdf", "b/r.pdf")]))


async def test_checkpoint_failure_becomes_structured_failure(store, solution_target, context) -> None:
    context.save_checkpoint.side_effect = RuntimeError("db down")
    with pytest.raises(PlatformJobFailure) as caught:
        await run_file_move(context, payload(solution_target, [(0, "a/r.pdf", "b/r.pdf")]))
    assert caught.value.code == "checkpoint_failed"
    assert caught.value.result["in_flight"]["index"] == 0


async def test_all_rejected_completes_without_moving(store, solution_target, context) -> None:
    rejected = RejectedFileMove(
        index=0, result=FileMoveResult(source="a", destination="a", status_code=400, error="same_path")
    )
    result = await run_file_move(context, payload(solution_target, [], [rejected]))
    assert result == {"results": [rejected.result.model_dump()], "moved": 0, "failed": 1}
    context.save_checkpoint.assert_not_awaited()


async def test_cleanup_failure_is_logged_for_operators(store, solution_target, context) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    dest_key = key_for(solution_target, "b/r.pdf")

    async def truncate(key: str) -> None:
        store.objects[key].data = b"x"

    async def cleanup_fails(key: str) -> None:
        if key == dest_key:
            raise RuntimeError("nope")

    store.after["copy"] = truncate
    store.before["delete"] = cleanup_fails
    await run_file_move(context, payload(solution_target, [(0, "a/r.pdf", "b/r.pdf")]))
    level, code, message = context.log.await_args.args
    assert (level, code) == ("warning", "file_move_cleanup_failed")
    assert dest_key in message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./test.sh tests/unit/services/file_move/test_job.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.jobs.platform.file_move'`.

- [ ] **Step 3: Add the result contract**

Append to `api/src/models/contracts/cli.py`, in the CLI FILE OPERATIONS area after the last `CLIFile*` model:

```python
class FileMoveResult(BaseModel):
    """One item's outcome in a ``files.move`` platform-job result.

    ``path`` is set only when the file landed somewhere other than
    ``destination`` (rename mode). ``error`` is ``code`` or ``code: message``
    (≤ 500 characters), and the literal ``"forbidden"`` for a policy denial.
    """

    source: str
    destination: str
    path: str | None = None
    renamed: bool = False
    adopted: bool = False
    status_code: int
    error: str | None = None
```

- [ ] **Step 4: Implement the job**

```python
# api/src/jobs/platform/file_move.py
"""Durable server-side file moves (``files.move`` / ``files.move_many``).

Authorization happened at enqueue (``shared.sdk_files.sdk_enqueue_file_moves``).
This job only moves the authorized items, one at a time in request order,
through :class:`src.services.file_storage.move.FileMover`. Contract:
``docs/superpowers/specs/2026-10-09-files-move-design.md``.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from src.jobs.platform.base import (
    PlatformJobCancelled,
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.contracts.cli import FileMoveResult

FILE_MOVE_JOB_TYPE = "files.move"
MAX_FILE_MOVE_ITEMS = 500
# Consecutive storage-level item failures that mean storage itself is down.
SYSTEMIC_FAILURE_RUN = 3


class FileMoveWorkItem(BaseModel):
    index: int = Field(ge=0)
    source: str
    destination: str


class RejectedFileMove(BaseModel):
    index: int = Field(ge=0)
    result: FileMoveResult


class FileMovePayload(BaseModel):
    location: str
    scope: str
    solution_id: UUID | None = None
    # Metadata org: the install's org for Solution rows, scope_to_org_id otherwise.
    organization_id: UUID | None = None
    on_conflict: Literal["error", "rename"] = "error"
    requested_by_user_id: UUID
    total: int = Field(ge=1, le=MAX_FILE_MOVE_ITEMS)
    items: list[FileMoveWorkItem] = Field(default_factory=list)
    rejected: list[RejectedFileMove] = Field(default_factory=list)

    @model_validator(mode="after")
    def every_index_once(self) -> "FileMovePayload":
        indices = sorted([i.index for i in self.items] + [r.index for r in self.rejected])
        if indices != list(range(self.total)):
            raise ValueError("items and rejected must cover each request index exactly once")
        return self


def _object_store() -> Any:
    from src.config import get_settings
    from src.services.file_storage.s3_client import S3StorageClient

    return S3StorageClient(get_settings())


def _failed_entry(index: int, result: FileMoveResult) -> dict[str, Any]:
    return {"index": index, **result.model_dump()}


def _terminal(results: dict[int, FileMoveResult], moved: int, total: int) -> dict[str, Any]:
    return {
        "results": [results[index].model_dump() for index in range(total)],
        "moved": moved,
        "failed": total - moved,
    }


async def run_file_move(context: PlatformJobContext, raw_payload: BaseModel) -> dict[str, Any]:
    from src.services.file_storage.move import FileMover, MoveRequest, MoveTarget

    payload = FileMovePayload.model_validate(raw_payload)
    results: dict[int, FileMoveResult] = {r.index: r.result for r in payload.rejected}
    failed = [_failed_entry(r.index, r.result) for r in sorted(payload.rejected, key=lambda r: r.index)]
    moved = 0
    storage_failures = 0

    async def log_cleanup_failure(source_key: str, destination_key: str) -> None:
        await context.log(
            "warning",
            "file_move_cleanup_failed",
            f"A failed move of {source_key} left its copy at {destination_key}; "
            "remove it, because it can be readable under its new prefix.",
        )

    mover = FileMover(
        _object_store(),
        MoveTarget(
            location=payload.location,
            scope=payload.scope,
            solution_id=payload.solution_id,
            organization_id=payload.organization_id,
            requested_by=payload.requested_by_user_id,
        ),
        on_cleanup_failed=log_cleanup_failure,
    )
    work = sorted(payload.items, key=lambda item: item.index)
    for position, item in enumerate(work):
        checkpoint = {
            "moved": moved,
            "failed": list(failed),
            "in_flight": {"index": item.index, "source": item.source, "destination": item.destination},
        }
        try:
            await context.save_checkpoint(
                checkpoint,
                phase=f"Moving {position + 1} of {len(work)}",
                current=position,
                total=len(work),
            )
        except PlatformJobCancelled:
            raise
        except Exception as exc:
            raise PlatformJobFailure(
                "checkpoint_failed", "Could not record move progress.", result=checkpoint
            ) from exc

        outcome = await mover.move(MoveRequest(item.source, item.destination), payload.on_conflict)
        result = FileMoveResult(
            source=item.source,
            destination=item.destination,
            path=outcome.path,
            renamed=outcome.renamed,
            adopted=outcome.adopted,
            status_code=outcome.status_code,
            error=outcome.error,
        )
        results[item.index] = result
        if outcome.status_code == 200:
            moved += 1
            storage_failures = 0
            continue
        failed.append(_failed_entry(item.index, result))
        storage_failures = storage_failures + 1 if outcome.storage_error else 0
        if storage_failures >= SYSTEMIC_FAILURE_RUN:
            for later in work[position + 1:]:
                results[later.index] = FileMoveResult(
                    source=later.source,
                    destination=later.destination,
                    status_code=500,
                    error="not_attempted",
                )
            raise PlatformJobFailure(
                "storage_unavailable",
                "Object storage is unavailable; the remaining moves were not attempted.",
                result=_terminal(results, moved, payload.total),
            )
    return _terminal(results, moved, payload.total)


FILE_MOVE_DEFINITION = PlatformJobDefinition(
    job_type=FILE_MOVE_JOB_TYPE,
    payload_version=1,
    payload_model=FileMovePayload,
    handler=run_file_move,
    policy=PlatformJobPolicy(
        timeout_seconds=4 * 60 * 60,
        max_attempts=1,
        retry_on_runner_loss=False,  # a half-done item is not safely repeatable
        allow_running_cancellation=False,  # cancel only while queued
        min_memory_headroom_mb=128,  # copies are server-side; no bytes in memory
    ),
)
```

In `api/src/jobs/platform/registry.py`, import `from src.jobs.platform.file_move import FILE_MOVE_DEFINITION` (alphabetically with the other imports) and add `FILE_MOVE_DEFINITION.job_type: FILE_MOVE_DEFINITION,` to `_DEFINITIONS`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./test.sh tests/unit/services/file_move/ tests/unit/services/test_platform_jobs.py tests/unit/services/test_platform_job_memory_profiles.py tests/unit/jobs/test_platform_job_runner.py -v`
Expected: PASS. If a registry tripwire elsewhere enumerates job types (for example memory profiles or a Kubernetes allowlist test), add `files.move` there as a default-class job, then re-run.

- [ ] **Step 6: Commit**

```bash
git add api/src/models/contracts/cli.py api/src/jobs/platform/file_move.py api/src/jobs/platform/registry.py api/tests/unit/services/file_move/test_job.py
git commit -m "feat(files): files.move platform job with checkpoints and systemic stop

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---
### Task 11: Enqueue service (`sdk_enqueue_file_moves`)

**Files:**
- Modify: `api/shared/sdk_files.py`
- Modify: `api/src/jobs/platform/file_move.py` (add `MAX_FILE_MOVE_PATH_CHARS`)
- Test: `api/tests/unit/sdk/test_sdk_file_moves.py` (create)

**Interfaces:**
- Consumes:
  - Task 1: `scope_to_org_id`, `SCOPE_SKIP`.
  - Task 4: `FilePolicyEvalCache` and `authorize_file_policy(..., policy_cache=)`.
  - Task 5: `find_active_platform_job`.
  - Task 10: `FILE_MOVE_DEFINITION`, `FILE_MOVE_JOB_TYPE`, `FileMovePayload`, `FileMoveWorkItem`, `RejectedFileMove`, `FileMoveResult`.
- Produces, in `shared.sdk_files`:
  - `sdk_enqueue_file_moves(caller: FileCaller, *, items: list[tuple[str, str]], location: str, scope: str | None, on_conflict: str = "error") -> tuple[PlatformJob, bool]`. It flushes the job without committing; the route commits.
  - `file_move_dedupe_key(user_id, location, scope, on_conflict, items) -> str`
  - `file_move_lock_key(location, scope) -> str`
  - `MAX_FILE_MOVE_PATH_CHARS = 200_000` in `src.jobs.platform.file_move`.

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/unit/sdk/test_sdk_file_moves.py
"""Enqueue-time validation, authorization, and job metadata for files.move."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from shared.file_access import FileCaller, FileServiceError
from shared.sdk_files import file_move_dedupe_key, sdk_enqueue_file_moves
from src.core.principal import UserPrincipal
from src.services.file_policy_service import FilePolicyEvalCache

SOLUTION_ID = UUID("11111111-1111-1111-1111-111111111111")
ORG_ID = UUID("22222222-2222-2222-2222-222222222222")


def _caller(*, solution_id: UUID | None = SOLUTION_ID, is_superuser: bool = False) -> FileCaller:
    user = UserPrincipal(
        user_id=uuid4(), email="mover@example.com", organization_id=ORG_ID, name="Mover",
        is_active=True, is_superuser=is_superuser, is_verified=True,
    )
    db = MagicMock()
    db.commit = AsyncMock()
    return FileCaller(
        user=user, db=db, org_id=ORG_ID,
        solution_id=str(solution_id) if solution_id else None,
    )


@pytest.fixture
def env(monkeypatch):
    denied: set[tuple[str, str]] = set()
    checks: list[dict] = []

    async def authorize(_caller, *, action, location, scope, path, solution_id, organization_id, policy_cache):
        checks.append({"action": action, "path": path, "organization_id": organization_id, "cache": policy_cache})
        return (action, path) not in denied

    job = SimpleNamespace(id=uuid4(), notification_id=None, status="queued")
    state = SimpleNamespace(
        denied=denied,
        checks=checks,
        emit=AsyncMock(),
        enqueue=AsyncMock(return_value=(job, False)),
        find=AsyncMock(return_value=None),
        declared=AsyncMock(),
        job=job,
    )
    monkeypatch.setattr("shared.sdk_files.authorize_file_policy", authorize)
    monkeypatch.setattr("shared.sdk_files.emit_file_policy_deny", state.emit)
    monkeypatch.setattr("shared.sdk_files.require_declared_solution_file_location", state.declared)
    monkeypatch.setattr("shared.sdk_files.install_org_id", AsyncMock(return_value=ORG_ID))
    monkeypatch.setattr("src.services.platform_jobs.enqueue_platform_job", state.enqueue)
    monkeypatch.setattr("src.services.platform_jobs.find_active_platform_job", state.find)
    return state


def _payload(env):
    return env.enqueue.await_args.args[2]


async def test_valid_items_enqueue_a_locked_priority_job(env) -> None:
    caller = _caller()
    job, reused = await sdk_enqueue_file_moves(
        caller, items=[("a/1.pdf", "b/1.pdf"), ("a/2.pdf", "b/2.pdf")], location="docs", scope=None
    )
    assert (job, reused) == (env.job, False)
    payload = _payload(env)
    assert [(i.index, i.source, i.destination) for i in payload.items] == [
        (0, "a/1.pdf", "b/1.pdf"), (1, "a/2.pdf", "b/2.pdf"),
    ]
    assert (payload.scope, payload.solution_id, payload.organization_id, payload.total) == (
        str(SOLUTION_ID), SOLUTION_ID, ORG_ID, 2,
    )
    kwargs = env.enqueue.await_args.kwargs
    assert kwargs["resource_lock_key"] == "files.move:" + hashlib.sha256(
        f"docs:{SOLUTION_ID}".encode()
    ).hexdigest()
    assert kwargs["priority"] == 50
    assert kwargs["title"] == "Move 2 files in docs"
    assert kwargs["resource_type"] == "file_location"
    assert kwargs["resource_id"] == f"docs:{SOLUTION_ID}"
    assert kwargs["organization_id"] == ORG_ID
    assert kwargs["requested_by_user_id"] == caller.user.user_id


@pytest.mark.parametrize(
    ("source", "destination", "code"),
    [
        ("a/r.pdf", "a/r.pdf", "same_path"),
        ("", "b/r.pdf", "invalid_path"),
        ("a/r.pdf", "b/", "invalid_path"),
        ("a//r.pdf", "b/r.pdf", "invalid_path"),
        ("a/./r.pdf", "b/r.pdf", "invalid_path"),
        ("a/../r.pdf", "b/r.pdf", "invalid_path"),
        ("/a/r.pdf", "b/r.pdf", "invalid_path"),
        ("a/r.pdf", "b/" + "x" * 1995, "path_too_long"),
    ],
)
async def test_invalid_paths_are_per_item_400s(env, source, destination, code) -> None:
    await sdk_enqueue_file_moves(
        _caller(), items=[(source, destination), ("a/ok.pdf", "b/ok.pdf")], location="docs", scope=None
    )
    payload = _payload(env)
    [rejected] = payload.rejected
    assert (rejected.index, rejected.result.status_code) == (0, 400)
    assert rejected.result.error.split(":")[0] == code
    assert [i.index for i in payload.items] == [1]


async def test_read_denial_is_an_audited_403_and_stops_further_checks(env) -> None:
    env.denied.add(("read", "a/secret.pdf"))
    await sdk_enqueue_file_moves(_caller(), items=[("a/secret.pdf", "b/s.pdf")], location="docs", scope=None)
    [rejected] = _payload(env).rejected
    assert (rejected.result.status_code, rejected.result.error) == (403, "forbidden")
    assert env.emit.await_args.kwargs["policy_action"] == "read"
    assert [c["action"] for c in env.checks] == ["read"]


async def test_destination_write_denial_is_403(env) -> None:
    env.denied.add(("write", "b/r.pdf"))
    await sdk_enqueue_file_moves(_caller(), items=[("a/r.pdf", "b/r.pdf")], location="docs", scope=None)
    assert [c["action"] for c in env.checks] == ["read", "delete", "write"]
    assert env.emit.await_args.kwargs["path"] == "b/r.pdf"


async def test_checks_share_one_cache_and_the_resolved_org(env) -> None:
    await sdk_enqueue_file_moves(
        _caller(), items=[("a/1.pdf", "b/1.pdf"), ("a/2.pdf", "b/2.pdf")], location="docs", scope=None
    )
    caches = {id(c["cache"]) for c in env.checks}
    assert len(caches) == 1 and isinstance(env.checks[0]["cache"], FilePolicyEvalCache)
    assert {c["organization_id"] for c in env.checks} == {ORG_ID}


async def test_all_rejected_still_enqueues_without_lock_or_priority(env) -> None:
    await sdk_enqueue_file_moves(_caller(), items=[("a/r.pdf", "a/r.pdf")], location="docs", scope=None)
    kwargs = env.enqueue.await_args.kwargs
    assert (kwargs["resource_lock_key"], kwargs["priority"]) == (None, 100)
    assert kwargs["title"] == "Move 1 file in docs"
    assert _payload(env).items == []


async def test_identical_retry_reuses_active_job_before_policy(env) -> None:
    active = SimpleNamespace(id=uuid4())
    env.find.return_value = active
    job, reused = await sdk_enqueue_file_moves(
        _caller(), items=[("a/r.pdf", "b/r.pdf")], location="docs", scope=None
    )
    assert (job, reused) == (active, True)
    assert env.checks == [] and env.emit.await_count == 0
    env.enqueue.assert_not_awaited()


def test_dedupe_key_is_stable_and_order_sensitive() -> None:
    user = uuid4()
    one = file_move_dedupe_key(user, "docs", "s", "error", [("a", "b"), ("c", "d")])
    assert one == file_move_dedupe_key(user, "docs", "s", "error", [("a", "b"), ("c", "d")])
    assert one != file_move_dedupe_key(user, "docs", "s", "error", [("c", "d"), ("a", "b")])
    assert one != file_move_dedupe_key(user, "docs", "s", "rename", [("a", "b"), ("c", "d")])


@pytest.mark.parametrize("location", ["workspace", "_repo", "Bad Name"])
async def test_request_level_location_errors(env, location) -> None:
    with pytest.raises(FileServiceError) as caught:
        await sdk_enqueue_file_moves(_caller(), items=[("a", "b")], location=location, scope=None)
    assert caught.value.status_code == 400
    env.enqueue.assert_not_awaited()


async def test_undeclared_solution_location_is_404(env) -> None:
    env.declared.side_effect = FileServiceError(404, "File location 'docs' not found")
    with pytest.raises(FileServiceError) as caught:
        await sdk_enqueue_file_moves(_caller(), items=[("a", "b")], location="docs", scope=None)
    assert caught.value.status_code == 404


async def test_invalid_superuser_scope_is_400(env) -> None:
    with pytest.raises(FileServiceError) as caught:
        await sdk_enqueue_file_moves(
            _caller(solution_id=None, is_superuser=True),
            items=[("a", "b")], location="docs", scope="not-a-uuid",
        )
    assert caught.value.status_code == 400


async def test_org_scope_keys_metadata_by_scope(env) -> None:
    await sdk_enqueue_file_moves(
        _caller(solution_id=None), items=[("a/r.pdf", "b/r.pdf")], location="docs", scope=None
    )
    payload = _payload(env)
    assert (payload.scope, payload.solution_id, payload.organization_id) == (str(ORG_ID), None, ORG_ID)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./test.sh tests/unit/sdk/test_sdk_file_moves.py -v`
Expected: FAIL with `ImportError: cannot import name 'file_move_dedupe_key'`.

- [ ] **Step 3: Implement**

In `api/src/jobs/platform/file_move.py`, add below `MAX_FILE_MOVE_ITEMS`:

```python
# Bounds the terminal result, which is broadcast on notification channels.
MAX_FILE_MOVE_PATH_CHARS = 200_000
```

In `api/shared/sdk_files.py`:
- Add `import json` to the imports. Every `shared.file_access` helper this task uses (`authorize_file_policy`, `ctx_solution_id`, `install_org_id`, `require_declared_solution_file_location`, `resolve_effective_scope`) is already imported there.
- Add `from src.services.audit import emit_file_policy_deny` after the `shared.file_access` import block.

Then append:

```python
# ``name (1000).ext`` is the longest a rename can make a destination.
_RENAME_HEADROOM = len(" (1000)")
_MAX_METADATA_PATH = 2000
_MAX_METADATA_KEY = 2500


def file_move_dedupe_key(
    user_id: object,
    location: str,
    scope: str,
    on_conflict: str,
    items: list[tuple[str, str]],
) -> str:
    """Identical retries of one request map to one active move job."""
    material = json.dumps(
        [str(user_id), location, scope, on_conflict, [list(item) for item in items]],
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def file_move_lock_key(location: str, scope: str) -> str:
    """Scheduler lock: one running move job per location and scope."""
    return "files.move:" + hashlib.sha256(f"{location}:{scope}".encode("utf-8")).hexdigest()


def _move_path_error(location: str, scope: str, path: str) -> str | None:
    from shared.file_paths import resolve_s3_key

    if not path or path.endswith("/"):
        return "invalid_path: a file path is required"
    if any(segment in ("", ".") for segment in path.split("/")):
        return "invalid_path: empty or '.' path segment"
    try:
        resolve_s3_key(location, scope, path)
    except ValueError as exc:
        return f"invalid_path: {exc}"[:500]
    return None


def _move_item_error(location: str, scope: str, source: str, destination: str) -> str | None:
    """Why an item cannot be moved, checked before any policy or storage probe."""
    from shared.file_paths import resolve_s3_key

    if source == destination:
        return "same_path: source and destination are the same file"
    for path in (source, destination):
        error = _move_path_error(location, scope, path)
        if error is not None:
            return error
    destination_key = resolve_s3_key(location, scope, destination)
    if (
        len(destination) + _RENAME_HEADROOM > _MAX_METADATA_PATH
        or len(destination_key) + _RENAME_HEADROOM > _MAX_METADATA_KEY
    ):
        return "path_too_long: the destination path is too long"
    return None


async def _move_item_denied(
    caller: FileCaller,
    *,
    location: str,
    scope: str,
    solution_id: object,
    organization_id: object,
    source: str,
    destination: str,
    policy_cache: object,
) -> bool:
    """Read + delete on the source and write on the destination; audit a denial."""
    for action, path in (("read", source), ("delete", source), ("write", destination)):
        allowed = await authorize_file_policy(
            caller,
            action=action,
            location=location,
            scope=scope,
            path=path,
            solution_id=solution_id,
            organization_id=organization_id,
            policy_cache=policy_cache,
        )
        if not allowed:
            await emit_file_policy_deny(
                caller.db,
                policy_action=action,
                location=location,
                path=path,
                scope=scope,
                solution_id=solution_id,
            )
            return True
    return False


async def sdk_enqueue_file_moves(
    caller: FileCaller,
    *,
    items: list[tuple[str, str]],
    location: str,
    scope: str | None,
    on_conflict: str = "error",
):
    """Validate, authorize, and enqueue one ``files.move`` job.

    Request-level problems raise :class:`FileServiceError`:
    - ``workspace`` or an invalid location name → 400
    - an undeclared Solution location → 404
    - an invalid superuser scope → 400

    Per-item problems become results instead, and are left out of the work
    list:
    - path validation failures → 400
    - policy denials → 403 ``"forbidden"``, each one audited

    A job is always enqueued, even when every item was rejected. An identical
    request whose job is still active returns that job before any policy
    check runs. The row is flushed, not committed; the caller commits and
    publishes before acknowledging.
    """
    from shared.file_paths import SCOPE_SKIP, scope_to_org_id, validate_location_name
    from src.jobs.platform.file_move import (
        FILE_MOVE_DEFINITION,
        FILE_MOVE_JOB_TYPE,
        FileMovePayload,
        FileMoveWorkItem,
        RejectedFileMove,
    )
    from src.models.contracts.cli import FileMoveResult
    from src.services import platform_jobs
    from src.services.file_policy_service import FilePolicyEvalCache

    if location == "workspace":
        raise FileServiceError(400, "files.move does not support the workspace location")
    try:
        validate_location_name(location)
        effective_scope = resolve_effective_scope(caller, location, scope)
    except ValueError as exc:
        raise FileServiceError(400, str(exc)) from exc
    if effective_scope is None:
        raise FileServiceError(400, f"A scope is required for location '{location}'")
    solution_id = ctx_solution_id(caller, location)

    dedupe_key = file_move_dedupe_key(
        caller.user.user_id, location, effective_scope, on_conflict, items
    )
    existing = await platform_jobs.find_active_platform_job(
        caller.db, FILE_MOVE_JOB_TYPE, dedupe_key
    )
    if existing is not None:
        return existing, True

    await require_declared_solution_file_location(
        caller, solution_id=solution_id, location=location
    )
    if solution_id is not None:
        organization_id = await install_org_id(caller, solution_id)
    else:
        mapped = scope_to_org_id(location, effective_scope)
        if mapped is SCOPE_SKIP:
            raise FileServiceError(400, f"Invalid scope: {effective_scope}")
        organization_id = mapped

    policy_cache = FilePolicyEvalCache()
    work: list[FileMoveWorkItem] = []
    rejected: list[RejectedFileMove] = []
    for index, (source, destination) in enumerate(items):
        error = _move_item_error(location, effective_scope, source, destination)
        status_code = 400
        if error is None and await _move_item_denied(
            caller,
            location=location,
            scope=effective_scope,
            solution_id=solution_id,
            organization_id=organization_id,
            source=source,
            destination=destination,
            policy_cache=policy_cache,
        ):
            error, status_code = "forbidden", 403
        if error is not None:
            rejected.append(
                RejectedFileMove(
                    index=index,
                    result=FileMoveResult(
                        source=source,
                        destination=destination,
                        status_code=status_code,
                        error=error,
                    ),
                )
            )
            continue
        work.append(FileMoveWorkItem(index=index, source=source, destination=destination))

    total = len(items)
    return await platform_jobs.enqueue_platform_job(
        caller.db,
        FILE_MOVE_DEFINITION,
        FileMovePayload(
            location=location,
            scope=effective_scope,
            solution_id=solution_id,
            organization_id=organization_id,
            on_conflict=on_conflict,
            requested_by_user_id=caller.user.user_id,
            total=total,
            items=work,
            rejected=rejected,
        ),
        dedupe_key=dedupe_key,
        # A request with nothing to move must not queue behind a running move.
        resource_lock_key=file_move_lock_key(location, effective_scope) if work else None,
        priority=50 if work else 100,
        organization_id=organization_id,
        requested_by_user_id=caller.user.user_id,
        requested_by_email=caller.user.email,
        requested_by_name=caller.user.name or caller.user.email,
        resource_type="file_location",
        resource_id=f"{location}:{effective_scope}"[:255],
        title=f"Move {total} file{'' if total == 1 else 's'} in {location}",
        action_url=None,
    )
```

Note: `src.services.platform_jobs` is imported as a module (`platform_jobs.find_active_platform_job`), so the test's `monkeypatch.setattr("src.services.platform_jobs....")` takes effect.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./test.sh tests/unit/sdk/test_sdk_file_moves.py tests/unit/sdk/test_sdk_files.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/shared/sdk_files.py api/src/jobs/platform/file_move.py api/tests/unit/sdk/test_sdk_file_moves.py
git commit -m "feat(files): files.move enqueue service with per-item validation and policy

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: HTTP routes and registry surfaces

**Files:**
- Modify: `api/src/routers/files.py` (request models, two routes)
- Modify: `api/src/services/execution/worker_sdk_http.py:104-120`
- Modify: `api/src/services/operation_catalog.py` (two definitions after `workspace.files.delete`, around line 3435)
- Modify: `api/src/services/access_list.py` (two entries in path order, near line 309)
- Modify: `api/tests/unit/services/test_operation_catalog.py` (`WORKSPACE_FILE_OPERATIONS`)
- Modify: `api/tests/unit/services/test_operation_inventory.py:20-26` (counts)
- Modify: `api/tests/unit/services/test_worker_sdk_http.py:546-559` (`FILES_ROUTE_PATHS` expectation)
- Modify: `api/tests/unit/test_contract_version.py` (`EXPECTED_CONTRACT_FINGERPRINT`)
- Regenerate: `docs/generated/access-list.json`
- Test: `api/tests/unit/routers/test_files_move_routes.py` (create)

**Interfaces:**
- Consumes:
  - Task 11: `sdk_enqueue_file_moves`.
  - Task 5: `finalize_enqueued_platform_job`.
  - Task 10 and 11: `MAX_FILE_MOVE_ITEMS`, `MAX_FILE_MOVE_PATH_CHARS`.
- Produces:
  - `POST /api/files/move` (`FileMoveRequest`) and `POST /api/files/move-many` (`FileMoveBatchRequest`), each returning `202 PlatformJobAccepted` with a `Location: /api/platform-jobs/{job_id}` header.
  - Operation ids `workspace.files.move` and `workspace.files.move_many`.

- [ ] **Step 1: Write the failing route tests**

```python
# api/tests/unit/routers/test_files_move_routes.py
"""files.move routes: request bounds, commit-before-202, Location, error mapping."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

from shared.file_access import FileServiceError
from src.routers import files


def test_batch_request_bounds() -> None:
    item = {"source": "a", "destination": "b"}
    with pytest.raises(ValidationError):
        files.FileMoveBatchRequest(items=[], location="docs")
    with pytest.raises(ValidationError):
        files.FileMoveBatchRequest(items=[item] * 501, location="docs")
    with pytest.raises(ValidationError):
        files.FileMoveBatchRequest(
            items=[{"source": "a" * 100_000, "destination": "b" * 100_001}], location="docs"
        )
    assert files.FileMoveBatchRequest(items=[item] * 500, location="docs").on_conflict == "error"
    assert "mode" not in files.FileMoveBatchRequest.model_fields
    assert "mode" not in files.FileMoveRequest.model_fields


@pytest.fixture
def ctx():
    context = MagicMock()
    context.db = AsyncMock()
    return context


async def test_move_many_finalizes_before_returning_202(monkeypatch, ctx) -> None:
    events: list[str] = []
    job = SimpleNamespace(id=uuid4(), notification_id=uuid4(), status="queued")
    enqueue = AsyncMock(side_effect=lambda *_a, **_k: events.append("enqueue") or (job, False))
    finalize = AsyncMock(side_effect=lambda *_a, **_k: events.append("finalize"))
    monkeypatch.setattr("shared.sdk_files.sdk_enqueue_file_moves", enqueue)
    monkeypatch.setattr("src.services.platform_jobs.finalize_enqueued_platform_job", finalize)
    response = Response()

    accepted = await files.move_files(
        files.FileMoveBatchRequest(
            items=[{"source": "a/1.pdf", "destination": "b/1.pdf"}], location="docs", on_conflict="rename"
        ),
        ctx, MagicMock(), response, ctx.db,
    )

    assert events == ["enqueue", "finalize"]
    assert response.headers["Location"] == f"/api/platform-jobs/{job.id}"
    assert (accepted.job_id, accepted.status, accepted.reused) == (job.id, "queued", False)
    assert enqueue.await_args.kwargs == {
        "items": [("a/1.pdf", "b/1.pdf")], "location": "docs", "scope": None, "on_conflict": "rename",
    }


async def test_single_move_enqueues_one_item(monkeypatch, ctx) -> None:
    job = SimpleNamespace(id=uuid4(), notification_id=None, status="queued")
    enqueue = AsyncMock(return_value=(job, True))
    monkeypatch.setattr("shared.sdk_files.sdk_enqueue_file_moves", enqueue)
    monkeypatch.setattr("src.services.platform_jobs.finalize_enqueued_platform_job", AsyncMock())
    accepted = await files.move_file(
        files.FileMoveRequest(source="a", destination="b", location="docs"),
        ctx, MagicMock(), Response(), ctx.db,
    )
    assert enqueue.await_args.kwargs["items"] == [("a", "b")]
    assert accepted.reused is True


async def test_service_errors_map_to_http(monkeypatch, ctx) -> None:
    monkeypatch.setattr(
        "shared.sdk_files.sdk_enqueue_file_moves",
        AsyncMock(side_effect=FileServiceError(404, "File location 'docs' not found")),
    )
    with pytest.raises(HTTPException) as caught:
        await files.move_file(
            files.FileMoveRequest(source="a", destination="b", location="docs"),
            ctx, MagicMock(), Response(), ctx.db,
        )
    assert caught.value.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./test.sh tests/unit/routers/test_files_move_routes.py -v`
Expected: FAIL with `AttributeError: module 'src.routers.files' has no attribute 'FileMoveBatchRequest'`.

- [ ] **Step 3: Add the request models and routes**

In `api/src/routers/files.py`:
- Change `from fastapi import APIRouter, Depends, HTTPException, Query, status` to include `Response`.
- Change `from pydantic import BaseModel, Field` to include `model_validator`.
- Add `from src.models.contracts.platform_jobs import PlatformJobAccepted`.
- Add `from src.jobs.platform.file_move import MAX_FILE_MOVE_ITEMS, MAX_FILE_MOVE_PATH_CHARS`.

After `FileDeleteRequest`, add:

```python
class FileMoveItem(BaseModel):
    """One file to move within the request's location."""
    source: str = Field(..., description="Current path relative to the location root")
    destination: str = Field(..., description="New path relative to the location root")


def _check_move_path_budget(paths: list[str]) -> None:
    if sum(len(path) for path in paths) > MAX_FILE_MOVE_PATH_CHARS:
        raise ValueError(
            f"source and destination paths total more than {MAX_FILE_MOVE_PATH_CHARS} characters"
        )


_ON_CONFLICT_DESCRIPTION = (
    "error: fail an item whose destination exists. rename: use the first free "
    "'name (n).ext' instead."
)


class FileMoveRequest(BaseModel):
    """Move one file within a non-workspace location (a durable platform job)."""
    source: str = Field(..., description="Current path relative to the location root")
    destination: str = Field(..., description="New path relative to the location root")
    location: str = Field(..., description=FILE_LOCATION_DESCRIPTION)
    scope: str | None = Field(default=None, description="Org scope; Solution context wins.")
    on_conflict: Literal["error", "rename"] = Field(default="error", description=_ON_CONFLICT_DESCRIPTION)

    @model_validator(mode="after")
    def _bounded_paths(self) -> "FileMoveRequest":
        _check_move_path_budget([self.source, self.destination])
        return self


class FileMoveBatchRequest(BaseModel):
    """Move up to 500 files within one non-workspace location (one platform job)."""
    items: list[FileMoveItem] = Field(..., min_length=1, max_length=MAX_FILE_MOVE_ITEMS)
    location: str = Field(..., description=FILE_LOCATION_DESCRIPTION)
    scope: str | None = Field(default=None, description="Org scope; Solution context wins.")
    on_conflict: Literal["error", "rename"] = Field(default="error", description=_ON_CONFLICT_DESCRIPTION)

    @model_validator(mode="after")
    def _bounded_paths(self) -> "FileMoveBatchRequest":
        _check_move_path_budget([p for item in self.items for p in (item.source, item.destination)])
        return self
```

After the `delete_file` route, add:

```python
async def _enqueue_file_moves(
    ctx: Context,
    response: Response,
    *,
    items: list[tuple[str, str]],
    location: str,
    scope: str | None,
    on_conflict: str,
) -> PlatformJobAccepted:
    from shared import sdk_files
    from src.services import platform_jobs

    caller = FileCaller.from_context(ctx)
    try:
        job, reused = await sdk_files.sdk_enqueue_file_moves(
            caller, items=items, location=location, scope=scope, on_conflict=on_conflict
        )
    except FileServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    # Commit and publish before the 202: the SDK polls the job immediately.
    await platform_jobs.finalize_enqueued_platform_job(caller.db, job, log_label="File move")
    response.headers["Location"] = f"/api/platform-jobs/{job.id}"
    return PlatformJobAccepted(
        job_id=job.id,
        notification_id=job.notification_id,
        status=job.status,  # type: ignore[arg-type]
        reused=reused,
    )


@router.post(
    "/move",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    **operation_route("workspace.files.move"),
)
async def move_file(
    request: FileMoveRequest,
    ctx: Context,
    user: CurrentActiveUser,
    response: Response,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PlatformJobAccepted:
    """Move one file within a location as a durable platform job."""
    return await _enqueue_file_moves(
        ctx,
        response,
        items=[(request.source, request.destination)],
        location=request.location,
        scope=request.scope,
        on_conflict=request.on_conflict,
    )


@router.post(
    "/move-many",
    response_model=PlatformJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    **operation_route("workspace.files.move_many"),
)
async def move_files(
    request: FileMoveBatchRequest,
    ctx: Context,
    user: CurrentActiveUser,
    response: Response,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PlatformJobAccepted:
    """Move up to 500 files within a location as one durable platform job."""
    return await _enqueue_file_moves(
        ctx,
        response,
        items=[(item.source, item.destination) for item in request.items],
        location=request.location,
        scope=request.scope,
        on_conflict=request.on_conflict,
    )
```

The route test patches `shared.sdk_files.sdk_enqueue_file_moves` and `src.services.platform_jobs.finalize_enqueued_platform_job`. That is why both modules are imported as modules inside `_enqueue_file_moves`.

- [ ] **Step 4: Add the catalog definitions**

In `api/src/services/operation_catalog.py`, add a constant beside `_PLATFORM_JOB_SDK_EXCLUSION` (line 76):

```python
_FILE_MOVE_MCP_EXCLUSION = "File-content operations have no MCP tools; moves are an SDK and CLI surface."
```

Insert after the `workspace.files.delete` definition:

```python
    OperationDefinition(
        operation_id="workspace.files.move",
        summary="Move one file within a location",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/move",
            request_model="FileMoveRequest",
            response_model="PlatformJobAccepted",
        ),
        cli=CliOperationBinding(path=("files", "move")),
        native_builder=True,
        action_scopes=("repository.readwrite",),
        authorization_resolver="File policy read and delete on the source and write on the destination, evaluated at enqueue",
        side_effects=(
            "enqueue a durable files.move Platform Job",
            "copy server-side, re-key file metadata, and delete the source object",
            "publish file activity",
        ),
        async_policy=OperationAsyncPolicy.PLATFORM_JOB,
        exclusions={
            "mcp": _FILE_MOVE_MCP_EXCLUSION,
            "manifest": "Moving runtime files does not change portable manifest state.",
        },
    ),
    OperationDefinition(
        operation_id="workspace.files.move_many",
        summary="Move up to 500 files within a location",
        target_kind=OperationTargetKind.WORKSPACE,
        rest=RestOperationBinding(
            method="POST",
            path="/api/files/move-many",
            request_model="FileMoveBatchRequest",
            response_model="PlatformJobAccepted",
        ),
        native_builder=True,
        action_scopes=("repository.readwrite",),
        authorization_resolver="File policy read and delete on each source and write on each destination, evaluated at enqueue",
        side_effects=(
            "enqueue a durable files.move Platform Job",
            "copy server-side, re-key file metadata, and delete source objects",
            "publish file activity",
        ),
        async_policy=OperationAsyncPolicy.PLATFORM_JOB,
        exclusions={
            "cli": "A batch is an SDK primitive; the CLI moves one file with `files move`.",
            "mcp": _FILE_MOVE_MCP_EXCLUSION,
            "manifest": "Moving runtime files does not change portable manifest state.",
        },
    ),
```

In `api/tests/unit/services/test_operation_catalog.py`, add to `WORKSPACE_FILE_OPERATIONS` after the `workspace.files.delete` entry:

```python
    "workspace.files.move": (
        "POST",
        "/api/files/move",
        ("files", "move"),
        None,
        True,
    ),
    "workspace.files.move_many": (
        "POST",
        "/api/files/move-many",
        None,
        None,
        True,
    ),
```

- [ ] **Step 5: Allowlist, access list, and tripwire expectations**

In `api/src/services/execution/worker_sdk_http.py`, add `"/api/files/move"` and `"/api/files/move-many"` to `FILES_ROUTE_PATHS`, and change the comment's "All eight" to "All ten". Make the same two additions to the expected set in `api/tests/unit/services/test_worker_sdk_http.py::test_files_route_selection_is_exact`, and change its docstring to "the ten files facade routes".

In `api/src/services/access_list.py`, insert these two entries in path order (after `'/api/files/list'` or wherever path ordering puts them):

```python
    AccessEntry(method='POST', path='/api/files/move', access_class=AccessClass.TABLE_POLICY, current_gate=CurrentGate.AUTHENTICATED, permission=None, boundary=None, operation_id='workspace.files.move', reason='Repository file content governed by file policies.', intended_change=None),
    AccessEntry(method='POST', path='/api/files/move-many', access_class=AccessClass.TABLE_POLICY, current_gate=CurrentGate.AUTHENTICATED, permission=None, boundary=None, operation_id='workspace.files.move_many', reason='Repository file content governed by file policies.', intended_change=None),
```

Regenerate the JSON projection: `python api/scripts/generate_access_list.py`. If the host lacks the API dependencies, run it in the test-runner container, using the project name from `./test.sh stack status`.

- [ ] **Step 6: Run the tripwires and update counts and fingerprint**

Regenerate the skill-truth appendices so `test_skill_appendix_fresh.py` stays green on this commit:
```bash
python api/scripts/skill-truth/generate.py
scripts/sync-codex-skills.sh
```
If the host lacks the API dependencies for `generate.py`, run it in the test-runner container.

Run: `./test.sh tests/unit/routers/test_files_move_routes.py tests/unit/services/test_operation_catalog.py tests/unit/services/test_worker_sdk_http.py tests/unit/test_access_list.py tests/unit/services/test_operation_inventory.py tests/unit/test_contract_version.py tests/unit/test_dto_flags.py tests/unit/test_skill_appendix_fresh.py -v`

Expected:
- Only `test_operation_inventory.py::test_every_observed_surface_is_classified_with_a_reason` and `test_contract_version.py` fail, both with count or fingerprint diffs.
- The inventory diff should be `rest` 677→679 only. `cli` stays at 171: the inventory reads the live Click tree, and `bifrost files move` does not exist until Task 14. `mcp` and `manifest` are unchanged. An `sdk` change may appear if the inventory maps SDK methods to the new routes. Accept only these deltas; investigate anything else.
- Update the counts dict to the observed values.
- `FileMoveResult` is an additive contract change. Update `EXPECTED_CONTRACT_FINGERPRINT` to the value the failing test prints, and leave `MIN_CLI_VERSION` unchanged.

Re-run the same command. Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add api/src/routers/files.py api/src/services/execution/worker_sdk_http.py api/src/services/operation_catalog.py api/src/services/access_list.py docs/generated/access-list.json api/tests/unit/routers/test_files_move_routes.py api/tests/unit/services/test_operation_catalog.py api/tests/unit/services/test_operation_inventory.py api/tests/unit/services/test_worker_sdk_http.py api/tests/unit/test_contract_version.py .claude/skills plugins/bifrost/skills .codex/skills
git commit -m "feat(files): POST /api/files/move and /move-many with catalog, access, and socket surfaces

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Python SDK `files.move` / `files.move_many`

**Files:**
- Modify: `api/bifrost/files.py`
- Test: `api/tests/unit/sdk/test_sdk_files_move.py` (create)

**Interfaces:**
- Consumes: Task 12's routes and the job result shape from Task 10.
- Produces:
  - `bifrost.files.FileMoveError(RuntimeError)`, with `.status_code`, `.error` and `.job_id`.
  - `files.move_many(items, location, on_conflict="error", scope=None, timeout_seconds=14400, poll_interval_seconds=2) -> list[dict]`
  - `files.move(source, destination, location, on_conflict="error", scope=None, timeout_seconds=14400, poll_interval_seconds=2) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# api/tests/unit/sdk/test_sdk_files_move.py
"""SDK files.move / move_many: enqueue over the engine transport, then poll the job."""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest


def _response(body: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = body
    return response


def _module(monkeypatch, *responses):
    module = importlib.import_module("bifrost.files")
    client = MagicMock()
    client.engine_request = AsyncMock(side_effect=[_response(r) for r in responses])
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())
    monkeypatch.setattr(module, "resolve_scope", lambda scope: scope)
    monkeypatch.setattr(module, "_solution_query", lambda *_a: "")
    return module, client


ITEM = {"source": "a/r.pdf", "destination": "b/r.pdf", "path": None, "renamed": False,
        "adopted": False, "status_code": 200, "error": None}


async def test_move_many_enqueues_then_polls_to_results(monkeypatch) -> None:
    module, client = _module(
        monkeypatch,
        {"job_id": "job-1", "status": "queued"},
        {"status": "running"},
        {"status": "succeeded", "result": {"results": [ITEM], "moved": 1, "failed": 0}},
    )
    results = await module.files.move_many(
        [{"source": "a/r.pdf", "destination": "b/r.pdf"}],
        location="docs",
        on_conflict="rename",
        poll_interval_seconds=0.001,
    )
    assert results == [ITEM]
    calls = client.engine_request.await_args_list
    assert (calls[0].args, calls[0].kwargs["json"]) == (
        ("POST", "/api/files/move-many"),
        {"items": [{"source": "a/r.pdf", "destination": "b/r.pdf"}], "location": "docs",
         "scope": None, "on_conflict": "rename"},
    )
    assert calls[1].args == ("GET", "/api/platform-jobs/job-1")


async def test_move_returns_final_path_or_raises_item_error(monkeypatch) -> None:
    renamed = {**ITEM, "path": "b/r (1).pdf", "renamed": True}
    module, client = _module(
        monkeypatch,
        {"job_id": "job-1"},
        {"status": "succeeded", "result": {"results": [renamed], "moved": 1, "failed": 0}},
    )
    assert await module.files.move("a/r.pdf", "b/r.pdf", location="docs", poll_interval_seconds=0.001) == "b/r (1).pdf"
    assert client.engine_request.await_args_list[0].args == ("POST", "/api/files/move")

    conflict = {**ITEM, "status_code": 409, "error": "file_exists"}
    module, _ = _module(
        monkeypatch,
        {"job_id": "job-2"},
        {"status": "succeeded", "result": {"results": [conflict], "moved": 0, "failed": 1}},
    )
    with pytest.raises(module.FileMoveError) as caught:
        await module.files.move("a/r.pdf", "b/r.pdf", location="docs", poll_interval_seconds=0.001)
    assert (caught.value.status_code, caught.value.error, caught.value.job_id) == (409, "file_exists", "job-2")


async def test_failed_job_raises_with_job_id(monkeypatch) -> None:
    module, _ = _module(
        monkeypatch,
        {"job_id": "job-3"},
        {"status": "failed", "error": {"message": "Object storage is unavailable"}},
    )
    with pytest.raises(RuntimeError, match="job-3.*Object storage is unavailable"):
        await module.files.move_many(
            [{"source": "a", "destination": "b"}], location="docs", poll_interval_seconds=0.001
        )


async def test_deadline_raises_timeout_naming_the_job(monkeypatch) -> None:
    module, _ = _module(monkeypatch, {"job_id": "job-4"}, *([{"status": "running"}] * 50))
    with pytest.raises(TimeoutError, match="job-4.*may still be running"):
        await module.files.move_many(
            [{"source": "a", "destination": "b"}], location="docs",
            timeout_seconds=0.01, poll_interval_seconds=0.005,
        )


def test_default_wait_matches_the_job_timeout() -> None:
    import inspect

    module = importlib.import_module("bifrost.files")
    for method in (module.files.move, module.files.move_many):
        assert inspect.signature(method).parameters["timeout_seconds"].default == 14400
        assert "mode" not in inspect.signature(method).parameters
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./test.sh tests/unit/sdk/test_sdk_files_move.py -v`
Expected: FAIL with `AttributeError: type object 'files' has no attribute 'move_many'`.

- [ ] **Step 3: Implement**

In `api/bifrost/files.py`, add `import asyncio` and `import time` to the imports. After `Mode = ...`, add:

```python
# Matches the files.move platform job's timeout, so the SDK never gives up on
# a job that is still allowed to run.
MOVE_TIMEOUT_SECONDS = 4 * 60 * 60


class FileMoveError(RuntimeError):
    """``files.move`` could not move the file; ``status_code`` follows HTTP semantics."""

    def __init__(self, status_code: int, error: str | None, *, job_id: str) -> None:
        super().__init__(
            f"File move failed ({status_code}): {error or 'unknown error'} (platform job {job_id})"
        )
        self.status_code = status_code
        self.error = error
        self.job_id = job_id


async def _run_move_job(
    path: str,
    body: dict,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> tuple[str, list[dict]]:
    """Enqueue a files.move job, then poll it with short requests until terminal."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero.")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be greater than zero.")
    client = get_client()
    response = await client.engine_request("POST", f"{path}{_solution_query()}", json=body)
    raise_for_status_with_detail(response)
    job_id = str(response.json()["job_id"])
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status_response = await client.engine_request("GET", f"/api/platform-jobs/{job_id}")
        raise_for_status_with_detail(status_response)
        job = status_response.json()
        if job["status"] == "succeeded":
            return job_id, list(job["result"]["results"])
        if job["status"] in ("failed", "cancelled"):
            error = job.get("error") or {}
            raise RuntimeError(
                f"File move platform job {job_id} {job['status']}: "
                f"{error.get('message') or job['status']}"
            )
        await asyncio.sleep(poll_interval_seconds)
    raise TimeoutError(f"File move is still running as platform job {job_id}; the move may still be running.")
```

Add these static methods to `class files`, after `delete`:

```python
    @staticmethod
    async def move_many(
        items: list[dict[str, str]],
        location: str,
        on_conflict: Literal["error", "rename"] = "error",
        scope: str | None = None,
        timeout_seconds: float = MOVE_TIMEOUT_SECONDS,
        poll_interval_seconds: float = 2,
    ) -> list[dict]:
        """
        Move up to 500 files within one location as a single durable job.

        Each file is copied server-side, its metadata (and so its file-policy
        access) follows it, and the original is deleted only after the copy
        is verified. Items succeed or fail independently; nothing is undone
        when another item fails. Workspace files cannot be moved.

        Args:
            items: ``[{"source": ..., "destination": ...}]``, 1–500 entries.
            location: A non-workspace location (required).
            on_conflict: ``"error"`` fails an item whose destination exists;
                ``"rename"`` uses the first free ``name (n).ext`` instead.
            scope: Org scope; provider-org override allowed. Solution context wins.
            timeout_seconds: How long to wait for the job (default: the job's
                own 4-hour limit). The calling workflow's ``timeout_seconds``
                must be at least this long, including time queued behind other
                moves in the same location.

        Returns:
            One result per item, in request order: ``source``, ``destination``,
            ``path`` (set when renamed), ``renamed``, ``adopted``,
            ``status_code`` (200, 400, 403, 404, 409, 500), and ``error``.
        """
        _, results = await _run_move_job(
            "/api/files/move-many",
            {
                "items": [
                    {"source": item["source"], "destination": item["destination"]}
                    for item in items
                ],
                "location": location,
                "scope": resolve_scope(scope),
                "on_conflict": on_conflict,
            },
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        return results

    @staticmethod
    async def move(
        source: str,
        destination: str,
        location: str,
        on_conflict: Literal["error", "rename"] = "error",
        scope: str | None = None,
        timeout_seconds: float = MOVE_TIMEOUT_SECONDS,
        poll_interval_seconds: float = 2,
    ) -> str:
        """
        Move one file within a location; returns its final path.

        Each call is its own durable job, so move more than one file with
        ``files.move_many``. Raises :class:`FileMoveError` with the item's
        status code when the move fails (for example 409 ``file_exists``).
        See ``files.move_many`` for the arguments.
        """
        job_id, (result,) = await _run_move_job(
            "/api/files/move",
            {
                "source": source,
                "destination": destination,
                "location": location,
                "scope": resolve_scope(scope),
                "on_conflict": on_conflict,
            },
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        if result["status_code"] != 200:
            raise FileMoveError(result["status_code"], result.get("error"), job_id=job_id)
        return result.get("path") or result["destination"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Regenerate the skill-truth appendices so `test_skill_appendix_fresh.py` stays green on this commit:
```bash
python api/scripts/skill-truth/generate.py
scripts/sync-codex-skills.sh
```
If the host lacks the API dependencies for `generate.py`, run it in the test-runner container.

Run: `./test.sh tests/unit/sdk/test_sdk_files_move.py tests/unit/sdk/ tests/unit/test_skill_appendix_fresh.py -v`
Expected: PASS. If an SDK surface tripwire (for example a public-method or signature snapshot test) fails because of the two new methods, regenerate or extend it as that test's message instructs, then re-run.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/files.py api/tests/unit/sdk/test_sdk_files_move.py .claude/skills plugins/bifrost/skills .codex/skills
git commit -m "feat(sdk): files.move and files.move_many over the durable move job

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: CLI `bifrost files move`

**Files:**
- Modify: `api/bifrost/commands/files.py`
- Test: `api/tests/unit/test_cli_files.py` (append a `TestMove` class)

**Interfaces:**
- Consumes: `bifrost.platform_jobs.poll_platform_job`, Task 12's `POST /api/files/move`, and the existing `_resolve_solution_install_id`.
- Produces: `bifrost files move SOURCE DESTINATION --location L [--solution S] [--rename] [--timeout SECONDS]`. It prints the final path. It exits non-zero on an item failure, and prints the job's stored result to stderr when the job fails.

- [ ] **Step 1: Write the failing tests** (append to `api/tests/unit/test_cli_files.py`)

```python
class TestMove:
    def _run(self, args, accepted, job_bodies):
        client = mock.AsyncMock()
        client.post = mock.AsyncMock(return_value=_fake_response(accepted, status=202))
        client.get = mock.AsyncMock(side_effect=[_fake_response(b) for b in job_bodies])
        with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client):
            result = CliRunner().invoke(files_group, args)
        return result, client

    def _item(self, **overrides) -> dict:
        return {"source": "a/r.pdf", "destination": "b/r.pdf", "path": None, "renamed": False,
                "adopted": False, "status_code": 200, "error": None, **overrides}

    def test_moves_and_prints_final_path(self) -> None:
        result, client = self._run(
            ["move", "a/r.pdf", "b/r.pdf", "--location", "docs", "--rename"],
            {"job_id": "job-1", "status": "queued"},
            [{"status": "succeeded", "result": {"results": [self._item(path="b/r (1).pdf", renamed=True)]}}],
        )
        assert result.exit_code == 0, result.output
        assert result.output.strip().endswith("b/r (1).pdf")
        path, body = client.post.await_args.args[0], client.post.await_args.kwargs["json"]
        assert path == "/api/files/move"
        assert body == {"source": "a/r.pdf", "destination": "b/r.pdf", "location": "docs", "on_conflict": "rename"}

    def test_item_failure_exits_non_zero(self) -> None:
        result, _ = self._run(
            ["move", "a/r.pdf", "b/r.pdf", "--location", "docs"],
            {"job_id": "job-1"},
            [{"status": "succeeded", "result": {"results": [self._item(status_code=409, error="file_exists")]}}],
        )
        assert result.exit_code != 0
        assert "409" in result.output and "file_exists" in result.output

    def test_job_failure_prints_checkpoint(self) -> None:
        checkpoint = {"moved": 0, "failed": [], "in_flight": {"index": 0, "source": "a/r.pdf", "destination": "b/r.pdf"}}
        failed = {"status": "failed", "error": {"message": "timeout"}, "result": checkpoint}
        result, client = self._run(
            ["move", "a/r.pdf", "b/r.pdf", "--location", "docs"],
            {"job_id": "job-1"},
            [failed, failed],
        )
        assert result.exit_code != 0
        assert '"in_flight"' in result.output
        assert client.get.await_count == 2

    def test_solution_flag_targets_the_install(self) -> None:
        install = "11111111-1111-1111-1111-111111111111"
        result, client = self._run(
            ["move", "a", "b", "--location", "docs", "--solution", install],
            {"job_id": "job-1"},
            [{"status": "succeeded", "result": {"results": [self._item()]}}],
        )
        assert result.exit_code == 0, result.output
        assert client.post.await_args.args[0] == f"/api/files/move?solution={install}"

    def test_location_is_required(self) -> None:
        result, _ = self._run(["move", "a", "b"], {"job_id": "x"}, [])
        assert result.exit_code != 0 and "--location" in result.output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./test.sh tests/unit/test_cli_files.py -k TestMove -v`
Expected: FAIL with `No such command 'move'`.

- [ ] **Step 3: Implement**

In `api/bifrost/commands/files.py`, add `import json` to the imports, and `from bifrost.platform_jobs import poll_platform_job` after `from bifrost.files import files as files_sdk`. Insert after `delete_cmd`:

```python
@files_group.command("move")
@click.argument("source")
@click.argument("destination")
@click.option("--location", required=True, help=_LOCATION_HELP)
@click.option("--solution", "solution_ref", default=None, help=_SOLUTION_HELP)
@click.option(
    "--rename",
    is_flag=True,
    default=False,
    help="If the destination exists, use the next free 'name (n).ext' instead of failing.",
)
@click.option(
    "--timeout",
    "timeout_seconds",
    type=float,
    default=4 * 60 * 60,
    show_default=True,
    help="Seconds to follow the move job; the job keeps running after the CLI stops waiting.",
)
@click.pass_context
@pass_resolver
@run_async
async def move_cmd(
    ctx: click.Context,
    source: str,
    destination: str,
    location: str,
    solution_ref: str | None,
    rename: bool,
    timeout_seconds: float,
    *,
    client: BifrostClient,
    resolver,  # noqa: ARG001
) -> None:
    """Move one file within a location as a durable server-side job.

    The file is copied server-side, its metadata follows it, and the original
    is deleted only after the copy is verified. Workspace files cannot be
    moved. Prints the final path.
    """
    query = ""
    if solution_ref is not None:
        install_id = await _resolve_solution_install_id(client, solution_ref)
        query = f"?solution={install_id}"
    response = await client.post(
        f"/api/files/move{query}",
        json={
            "source": source,
            "destination": destination,
            "location": location,
            "on_conflict": "rename" if rename else "error",
        },
    )
    response.raise_for_status()
    job_id = str(response.json()["job_id"])
    try:
        job = await poll_platform_job(
            client,
            job_id,
            label="Moving file",
            timeout_seconds=timeout_seconds,
            timeout_operation="file move",
        )
    except click.ClickException:
        # poll_platform_job keeps only the message; show what the job recorded.
        try:
            detail = await client.get(f"/api/platform-jobs/{job_id}")
            stored = detail.json().get("result") if detail.status_code == 200 else None
        except Exception:
            stored = None
        if stored is not None:
            click.echo(json.dumps(stored, indent=2), err=True)
        raise
    (result,) = job["result"]["results"]
    if result["status_code"] != 200:
        raise click.ClickException(
            f"Move failed ({result['status_code']}): {result.get('error') or 'unknown error'}"
        )
    click.echo(result.get("path") or result["destination"])
```

- [ ] **Step 4: Run the tests to verify they pass**

Regenerate the skill-truth appendices so `test_skill_appendix_fresh.py` stays green on this commit:
```bash
python api/scripts/skill-truth/generate.py
scripts/sync-codex-skills.sh
```
If the host lacks the API dependencies for `generate.py`, run it in the test-runner container.

Run: `./test.sh tests/unit/test_cli_files.py tests/unit/test_dto_flags.py tests/unit/services/test_operation_inventory.py tests/unit/test_skill_appendix_fresh.py -v`
Expected: only `test_operation_inventory.py::test_every_observed_surface_is_classified_with_a_reason` fails, with `cli` 171→172 (the new `files move` leaf now exists in the Click tree). Update `"cli"` to 172 in its counts dict and re-run. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/bifrost/commands/files.py api/tests/unit/test_cli_files.py api/tests/unit/services/test_operation_inventory.py .claude/skills plugins/bifrost/skills .codex/skills
git commit -m "feat(cli): bifrost files move

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: Live end-to-end coverage

**Files:**
- Create: `api/tests/e2e/platform/test_files_move.py`

**Interfaces:**
- Consumes everything above, against the real scheduler, Postgres and SeaweedFS.
- Fixtures: `e2e_client`, `platform_admin`, `org1`, `org1_user`, `db_session`, `async_session_factory`. The autouse `isolate_file_policies` wipes policies before each test, so every test grants its own.

- [ ] **Step 1: Write the tests**

```python
# api/tests/e2e/platform/test_files_move.py
"""files.move end to end: scheduler job, real object storage, policy-following access."""

from __future__ import annotations

import time
import uuid
from urllib.parse import quote
from uuid import UUID

import pytest
from sqlalchemy import select

from shared.file_paths import resolve_s3_key
from src.config import get_settings
from src.models.orm.file_metadata import FileMetadata
from src.models.orm.solution_file_location import SolutionFileLocation
from src.services.file_storage.move import (
    MARK_ETAG,
    MARK_ROW,
    MARK_SOURCE,
    FileMover,
    MoveRequest,
    MoveTarget,
    source_marker,
)
from src.services.file_storage.s3_client import S3StorageClient
from tests.e2e.conftest import execute_workflow_sync, write_and_register
from tests.e2e.file_policy_helpers import grant_file_policy

pytestmark = pytest.mark.e2e

LOCATION = "docs"
ADMIN_RULE = {"name": "admin", "actions": ["read", "write", "delete", "list"], "when": {"user": "is_platform_admin"}}
READ_ALL_RULE = {"name": "read_all", "actions": ["read"], "when": None}


def _put_policy(e2e_client, headers, *, scope: str, prefix: str, rules: list[dict]) -> None:
    response = e2e_client.put(
        f"/api/files/policies/{quote(prefix or '/', safe='')}",
        headers=headers,
        params={"location": LOCATION, "scope": scope},
        json={"policies": {"policies": rules}},
    )
    assert response.status_code in (200, 201, 204), response.text


@pytest.fixture
async def solution(e2e_client, platform_admin, org1, db_session):
    slug = f"move-{uuid.uuid4().hex[:8]}"
    created = e2e_client.post(
        "/api/solutions", headers=platform_admin.headers,
        json={"slug": slug, "name": slug.upper(), "organization_id": org1["id"]},
    )
    assert created.status_code in (200, 201), created.text
    body = created.json()
    db_session.add(SolutionFileLocation(solution_id=UUID(body["id"]), location=LOCATION))
    await db_session.commit()
    _put_policy(e2e_client, platform_admin.headers, scope=body["organization_id"], prefix="", rules=[ADMIN_RULE])
    _put_policy(
        e2e_client, platform_admin.headers, scope=body["organization_id"],
        prefix="campus-b", rules=[ADMIN_RULE, READ_ALL_RULE],
    )
    return body


def _write(e2e_client, headers, sid: str, path: str, content: str) -> None:
    response = e2e_client.post(
        f"/api/files/write?solution={sid}", headers=headers,
        json={"location": LOCATION, "path": path, "content": content, "mode": "cloud"},
    )
    assert response.status_code == 204, response.text


def _read(e2e_client, headers, sid: str, path: str):
    return e2e_client.post(
        f"/api/files/read?solution={sid}", headers=headers,
        json={"location": LOCATION, "path": path, "mode": "cloud", "binary": False},
    )


def _poll(e2e_client, headers, job_id: str) -> dict:
    for _ in range(240):
        response = e2e_client.get(f"/api/platform-jobs/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.25)
    raise AssertionError(f"move job {job_id} did not finish")


def _move_many(e2e_client, headers, sid: str, items: list[tuple[str, str]], on_conflict: str = "error") -> dict:
    response = e2e_client.post(
        f"/api/files/move-many?solution={sid}", headers=headers,
        json={"items": [{"source": s, "destination": d} for s, d in items],
              "location": LOCATION, "on_conflict": on_conflict},
    )
    assert response.status_code == 202, response.text
    assert response.headers["Location"] == f"/api/platform-jobs/{response.json()['job_id']}"
    return _poll(e2e_client, headers, response.json()["job_id"])


async def _row(db_session, sid: str, path: str) -> FileMetadata | None:
    db_session.expire_all()
    return (
        await db_session.execute(
            select(FileMetadata).where(
                FileMetadata.solution_id == UUID(sid),
                FileMetadata.location == LOCATION,
                FileMetadata.path == path,
            )
        )
    ).scalar_one_or_none()


async def test_move_many_moves_files_and_access_follows(
    e2e_client, platform_admin, org1_user, solution, db_session
) -> None:
    sid, admin = solution["id"], platform_admin.headers
    _write(e2e_client, admin, sid, "campus-a/b1/plan.pdf", "plan")
    _write(e2e_client, admin, sid, "campus-a/b1/spec.pdf", "spec")
    before = await _row(db_session, sid, "campus-a/b1/plan.pdf")
    assert _read(e2e_client, org1_user.headers, sid, "campus-a/b1/plan.pdf").status_code == 403

    job = _move_many(
        e2e_client, admin, sid,
        [("campus-a/b1/plan.pdf", "campus-b/b1/plan.pdf"), ("campus-a/b1/spec.pdf", "campus-b/b1/spec.pdf")],
    )

    assert job["status"] == "succeeded", job
    assert [r["status_code"] for r in job["result"]["results"]] == [200, 200]
    assert _read(e2e_client, org1_user.headers, sid, "campus-b/b1/plan.pdf").json()["content"] == "plan"
    assert _read(e2e_client, admin, sid, "campus-a/b1/plan.pdf").status_code == 404
    after = await _row(db_session, sid, "campus-b/b1/plan.pdf")
    assert after is not None and after.id == before.id
    assert await _row(db_session, sid, "campus-a/b1/plan.pdf") is None


@pytest.mark.parametrize("rename", [False, True])
async def test_retry_adopts_an_interrupted_move(
    e2e_client, platform_admin, solution, db_session, rename
) -> None:
    sid, admin = solution["id"], platform_admin.headers
    _write(e2e_client, admin, sid, "campus-a/r.pdf", "report")
    destination = "campus-b/r.pdf"
    landed_at = destination
    if rename:
        _write(e2e_client, admin, sid, destination, "someone else")
        landed_at = "campus-b/r (1).pdf"
    row = await _row(db_session, sid, "campus-a/r.pdf")
    storage = S3StorageClient(get_settings())
    source_key = resolve_s3_key(LOCATION, sid, "campus-a/r.pdf")
    head = await storage.head_object(source_key)
    # Simulate a run that copied and deleted the source, then died before commit.
    await storage.copy_object(
        source_key, resolve_s3_key(LOCATION, sid, landed_at), source=head,
        metadata={MARK_SOURCE: source_marker(source_key), MARK_ROW: str(row.id), MARK_ETAG: head.etag},
    )
    await storage.delete_object_strict(source_key)

    job = _move_many(e2e_client, admin, sid, [("campus-a/r.pdf", destination)], "rename" if rename else "error")

    [result] = job["result"]["results"]
    assert (result["status_code"], result["adopted"]) == (200, True)
    assert (result["path"] or destination) == landed_at
    assert (await _row(db_session, sid, landed_at)).id == row.id


async def test_multipart_copy_against_object_storage(
    platform_admin, solution, async_session_factory
) -> None:
    sid = solution["id"]
    storage = S3StorageClient(get_settings())
    source = "campus-a/Floor Plan – Rev 2 (final).bin"
    payload = bytes((i * 31) % 256 for i in range(11 * 1024 * 1024))

    async def chunks():
        yield payload

    await storage.put_object_from_chunks(resolve_s3_key(LOCATION, sid, source), chunks())
    mover = FileMover(
        storage,
        MoveTarget(
            location=LOCATION, scope=sid, solution_id=UUID(sid),
            organization_id=UUID(solution["organization_id"]), requested_by=platform_admin.user_id,
        ),
        open_session=async_session_factory,
        multipart_threshold=6 * 1024 * 1024,
        part_size=5 * 1024 * 1024,
    )

    outcome = await mover.move(MoveRequest(source, "campus-b/Floor Plan – Rev 2 (final).bin"), "error")

    assert outcome.status_code == 200, outcome
    moved = await storage.read_uploaded_file(resolve_s3_key(LOCATION, sid, "campus-b/Floor Plan – Rev 2 (final).bin"))
    assert moved == payload
    assert await storage.head_object(resolve_s3_key(LOCATION, sid, source)) is None


@pytest.fixture
def _org_location_policy(e2e_client, platform_admin, org1):
    grant_file_policy(e2e_client, platform_admin.headers, location="move-e2e", scope=org1["id"], allow_all=True)


@pytest.mark.timeout(300)
async def test_workflow_moves_and_enqueue_fits_the_sdk_budget(
    e2e_client, platform_admin, org1, org1_user, _org_location_policy
) -> None:
    tag = uuid.uuid4().hex[:8]
    prefix = f"e2e-move-{tag}"
    write = e2e_client.post(
        "/api/files/write", headers=platform_admin.headers,
        json={"location": "move-e2e", "path": f"{prefix}/one.txt", "content": "one", "mode": "cloud", "scope": org1["id"]},
    )
    assert write.status_code == 204, write.text
    name = f"e2e_files_move_{tag}"
    content = f'''"""files.move e2e workflow."""
import time
from bifrost import workflow, files
from bifrost.client import get_client

@workflow(name="{name}", description="files.move e2e")
async def {name}():
    # The single move runs first: both jobs share the location/scope lock, so the reverse
    # order would queue it behind 500 items. The budget batch is only timed at enqueue;
    # its job finishes on its own with 404s after the workflow returns.
    single = await files.move_many(
        [{{"source": "{prefix}/one.txt", "destination": "{prefix}/two.txt"}}], location="move-e2e",
    )
    items = [{{"source": f"{prefix}/missing-{{i}}.pdf", "destination": f"{prefix}/moved-{{i}}.pdf"}} for i in range(500)]
    started = time.monotonic()
    response = await get_client().engine_request(
        "POST", "/api/files/move-many",
        json={{"items": items, "location": "move-e2e", "on_conflict": "error"}},
    )
    elapsed = time.monotonic() - started
    return {{"status": response.status_code, "elapsed": elapsed, "single": single}}
'''
    registered = write_and_register(
        e2e_client, platform_admin.headers, f"{name}.py", content, name, organization_id=org1["id"]
    )
    patched = e2e_client.patch(
        f"/api/workflows/{registered['id']}", headers=platform_admin.headers,
        json={"organization_id": org1["id"], "access_level": "authenticated"},
    )
    assert patched.status_code == 200, patched.text

    result = execute_workflow_sync(e2e_client, org1_user.headers, registered["id"], max_wait=240.0)

    assert result["status"] == "Success", result
    out = result["result"]
    assert out["status"] == 202
    # 500 items × three policy checks, through the engine socket, well inside the SDK's 30 s.
    assert out["elapsed"] < 10, out["elapsed"]
    assert [r["status_code"] for r in out["single"]] == [200]
```

- [ ] **Step 2: Run the e2e tests**

Run: `./test.sh e2e tests/e2e/platform/test_files_move.py -v`. If `./test.sh e2e` does not take a path, use `./test.sh tests/e2e/platform/test_files_move.py -v`; check AGENTS.md for the passthrough form.
Expected: PASS.

If `test_workflow_moves_and_enqueue_fits_the_sdk_budget` exceeds 10 s, lower `MAX_FILE_MOVE_ITEMS` (Task 10) to a measured size that fits, update the spec's cap with that measurement, and re-run. Do not raise the assertion.

If the multipart test shows SeaweedFS rejecting `IfNoneMatch` on `CompleteMultipartUpload`, stop and report it rather than removing the header; the spec requires the header.

- [ ] **Step 3: Commit**

```bash
git add api/tests/e2e/platform/test_files_move.py
git commit -m "test(files): live files.move coverage — scheduler, recovery, multipart, workflow budget

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Docs, generated artifacts, and PR readiness

**Files:**
- Modify: `.claude/skills/bifrost-build/references/files.md` (add a "Move files" section)
- Regenerate:
  - mirrors `plugins/bifrost/skills/`, `.codex/skills/` (`scripts/sync-codex-skills.sh`)
  - skill-truth appendices (`python api/scripts/skill-truth/generate.py`)
  - `client/src/lib/v1.d.ts`
- Create at PR time: `product-updates/dispositions/pr-<number>.json` and an entry, per `docs/product-updates-authoring.md`

- [ ] **Step 1: Document the operation**

In `.claude/skills/bifrost-build/references/files.md`, add after "## Access from a workflow":

````markdown
## Move files

`files.move` and `files.move_many` relocate files inside one non-workspace location (for example a Solution's `docs` location) without downloading them. Each call runs as one durable `files.move` platform job:
- Bytes are copied server-side.
- The file's metadata row moves with it, so path-prefix file policies grant access at the new path.
- The original is deleted only after the copy is verified.

```python
from bifrost import files

final_path = await files.move("campus-a/plan.pdf", "campus-b/plan.pdf", location="docs")
results = await files.move_many(
    [{"source": "campus-a/a.pdf", "destination": "campus-b/a.pdf"},
     {"source": "campus-a/b.pdf", "destination": "campus-b/b.pdf"}],
    location="docs",
    on_conflict="rename",  # "error" (default) fails an item whose destination exists
)
```

**Requests and authorization**
- Up to 500 items per call. Use `move_many` for more than one file, since every `move` is its own job.
- Each item needs `read` and `delete` on the source and `write` on the destination. These are checked when the job is queued.
- Items succeed or fail independently. Results come back in request order, with `status_code` and `error` per item.
- A retry of the same request is safe: interrupted moves are completed (`adopted: true`), and finished ones report success.

**Timing**
- Moves in one location and scope run one job at a time, so a single move can wait behind a large batch in the same location.
- A calling workflow must set its own `timeout_seconds` high enough. The SDK waits up to 4 hours by default, and the job itself is limited to 4 hours.
- A batch of very large files that would exceed 4 hours should be split.

**Limits and residual risks**
- `on_conflict="rename"` names are authorized by the requested destination's `write` grant. A file-level policy on one exact candidate name is not consulted.
- Browser uploads through signed URLs take no lock:
  - An upload that lands on the **destination** while a copy of 5 GiB or less runs is overwritten. Larger copies refuse instead.
  - An upload that lands on the **source** within milliseconds of its delete is lost.
- A source deleted outside a move is a plain 404. A source without a metadata row cannot be resumed or adopted after an interruption.
- Retrying an identical request while its job is still queued returns that job, including any 403s it recorded, even if the policy has since changed.

**Deployment requirements**
- The bucket credentials need `s3:ListBucket`. Without it, AWS reports a missing file as 403, and moves report 500 instead of 404.
- Configure an `AbortIncompleteMultipartUpload` lifecycle rule on the bucket.
- Keep PgBouncer and Postgres idle-transaction timeouts above the longest single-part copy.

The CLI moves one file: `bifrost files move SOURCE DESTINATION --location docs [--solution INSTALL] [--rename]`.
````

- [ ] **Step 2: Regenerate mirrors and appendices**

Run:
```bash
scripts/sync-codex-skills.sh
python api/scripts/skill-truth/generate.py
python3 scripts/check_skill_mirrors.py
```
Expected: the mirrors pick up the new `files.md` section; `generated/*` changes only if the docs edit feeds it (Tasks 12–14 already regenerated the code-driven appendices); the check passes. If the host lacks the API dependencies for `generate.py`, run it in the test-runner container.

- [ ] **Step 3: Regenerate client types**

Start this worktree's API (`./debug.sh up`, then `./debug.sh status` for the URL), and run:
```bash
cd client && OPENAPI_URL=<api-url>/openapi.json npm run generate:types && cd ..
```
Expected: `client/src/lib/v1.d.ts` gains `/api/files/move`, `/api/files/move-many`, `FileMoveRequest`, `FileMoveBatchRequest` and `FileMoveItem`.

- [ ] **Step 4: Focused verification**

Run:
```bash
./test.sh tests/unit/test_file_paths_scope.py tests/unit/test_s3_move_primitives.py tests/unit/services/test_file_move_metadata.py tests/unit/services/test_file_policy_eval_cache.py tests/unit/services/test_file_move_names.py tests/unit/services/file_move/ tests/unit/sdk/test_sdk_file_moves.py tests/unit/sdk/test_sdk_files_move.py tests/unit/routers/test_files_move_routes.py tests/unit/test_cli_files.py -v
./test.sh tests/unit/services/test_platform_jobs.py tests/unit/jobs/ tests/unit/sdk/ tests/unit/services/test_file_policy_service.py tests/unit/services/test_operation_catalog.py tests/unit/services/test_operation_inventory.py tests/unit/services/test_worker_sdk_http.py tests/unit/test_access_list.py tests/unit/test_contract_version.py tests/unit/test_dto_flags.py tests/unit/test_skill_appendix_fresh.py -v
./test.sh tests/e2e/platform/test_files_move.py tests/e2e/platform/test_sdk_files_local.py tests/e2e/platform/test_solution_file_scope.py -v
./test.sh quality api
```
Expected: all PASS. Record the exact commands and results for the PR description, and list the broader suites that were not run.

- [ ] **Step 5: Commit the docs and generated files**

```bash
git add .claude/skills plugins/bifrost/skills .codex/skills client/src/lib/v1.d.ts
git commit -m "docs(files): document files.move; regenerate skill appendices, mirrors, and client types

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Sync with main, open the PR, and add the product update**

```bash
git fetch origin && git rebase origin/main
```
Re-run Step 4's first two commands if the rebase touched files in this change surface. Push to the fork (`git push fork feat/files-move`) and open the PR against the upstream repository with `gh pr create`. The description must:
- summarize the feature and link the spec;
- list the verification commands and results, and the suites not run;
- end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

Then, using the PR number, follow `docs/product-updates-authoring.md` to add `product-updates/dispositions/pr-<number>.json` (classification: feature, category "feature") and an approved-draft entry titled "Move files in a location". Commit both with the attribution line, and push.
