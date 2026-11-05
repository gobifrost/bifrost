"""FileMover: the locked single-part move of one file."""

from __future__ import annotations

from unittest.mock import AsyncMock, call

import pytest
import httpx
from sqlalchemy import text

from src.core.database import get_db_context
from src.services.file_policy_service import FilePolicyService

from src.services.file_storage.move import (
    MARK_ETAG,
    MARK_ROW,
    MARK_SOURCE,
    FileMover,
    MoveRequest,
    source_marker,
)
from src.services.file_storage.s3_client import ObjectPreconditionFailed

from .support import allow_rename, holds_move_copy, key_for, rows_at, seed_file


async def test_moves_file_keeps_row_identity_and_publishes(store, solution_target, published) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    source_key, dest_key = key_for(solution_target, "a/r.pdf"), key_for(solution_target, "b/r.pdf")
    source_etag = store.objects[source_key].etag

    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")

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
    await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    [copy] = store.copies
    assert copy["metadata"]["owner"] == "x"
    assert copy["source"].etag.startswith('"')


async def test_org_scoped_rows_move(store, org_target) -> None:
    row_id = await seed_file(store, org_target, "a/r.pdf")
    outcome = await FileMover(store, org_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 200
    assert [row.id for row in await rows_at(org_target, "b/r.pdf")] == [row_id]


async def test_source_without_row_inserts_destination_row(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf", with_row=False)
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 200
    [row] = await rows_at(solution_target, "b/r.pdf")
    assert row.created_by == solution_target.requested_by
    assert store.objects[key_for(solution_target, "b/r.pdf")].metadata[MARK_ROW] == ""


async def test_case_only_rename_is_a_real_move(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/Report.pdf")
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/Report.pdf", "a/report.pdf"), "error")
    assert outcome.status_code == 200
    assert key_for(solution_target, "a/Report.pdf") not in store.objects
    assert key_for(solution_target, "a/report.pdf") in store.objects


async def test_zero_byte_file_moves(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/empty.txt", data=b"")
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/empty.txt", "b/empty.txt"), "error")
    assert outcome.status_code == 200
    assert store.objects[key_for(solution_target, "b/empty.txt")].data == b""


async def test_existing_destination_in_error_mode_is_409_and_nothing_changes(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    await seed_file(store, solution_target, "b/r.pdf", data=b"other")
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (409, "file_exists")
    assert store.objects[key_for(solution_target, "b/r.pdf")].data == b"other"
    assert key_for(solution_target, "a/r.pdf") in store.objects
    assert len(await rows_at(solution_target, "a/r.pdf")) == 1


async def test_rename_mode_takes_lowest_free_number(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    for path in ("b/r.pdf", "b/r (1).pdf", "b/r (3).pdf"):
        await seed_file(store, solution_target, path, data=b"other", with_row=False)
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
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
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    assert outcome.path == "b/r (2).pdf"
    assert store.objects[raced].data == b"raced"


async def test_rename_exhausted_is_no_available_name(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"other")
    for n in range(1, 1001):
        store.put(key_for(solution_target, f"b/r ({n}).pdf"), b"other")
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    assert (outcome.status_code, outcome.error) == (409, "no_available_name")


async def test_verification_mismatch_cleans_up_copy(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    dest_key = key_for(solution_target, "b/r.pdf")

    async def truncate(key: str) -> None:
        store.objects[key].data = b"short"

    store.after["copy"] = truncate
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
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
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
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
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 200
    assert len(await rows_at(solution_target, "b/r.pdf")) == 1


async def test_unconfirmed_delete_keeps_copy_and_row_at_source(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    source_key = key_for(solution_target, "a/r.pdf")

    async def delete_fails(key: str) -> None:
        if key == source_key:
            raise RuntimeError("delete failed")

    store.before["delete"] = delete_fails
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
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
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (500, "source_delete_unconfirmed")
    assert key_for(solution_target, "b/r.pdf") in store.objects
    assert len(await rows_at(solution_target, "a/r.pdf")) == 1


@pytest.mark.parametrize(("side", "code"), [("source", "source_changed"), ("destination", "file_exists")])
async def test_copy_precondition_failures(store, solution_target, side, code) -> None:
    await seed_file(store, solution_target, "a/r.pdf")

    async def refuse(_key: str) -> None:
        raise ObjectPreconditionFailed(side)

    store.before["copy"] = refuse
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
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
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename, on_cleanup_failed=notify).move(
        MoveRequest("a/r.pdf", "b/r.pdf"), "error"
    )
    assert (outcome.status_code, outcome.error) == (500, "cleanup_failed")
    notify.assert_awaited_once_with(key_for(solution_target, "a/r.pdf"), dest_key)


async def test_missing_source_is_404(store, solution_target) -> None:
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/none.pdf", "b/none.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (404, "not_found")


async def test_storage_errors_are_flagged(store, solution_target) -> None:
    async def unreachable(_key: str) -> None:
        raise httpx.ConnectError("s3 unavailable")

    store.before["head"] = unreachable
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 500 and outcome.storage_error
    assert outcome.error.startswith("storage_unavailable")


async def test_internal_errors_are_not_storage_errors(store, solution_target) -> None:
    async def boom(_key: str) -> None:
        raise ValueError("bad")

    store.before["head"] = boom
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 500 and not outcome.storage_error
    assert outcome.error.startswith("internal_error")


async def test_publish_failure_after_commit_still_reports_success(store, solution_target, published) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    published.side_effect = RuntimeError("redis down")
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 200
    assert len(await rows_at(solution_target, "b/r.pdf")) == 1


async def _lock_is_held(target, path: str) -> bool:
    """True when another session holds ``lock_file_mutation``'s lock on ``path``."""
    lock_key = f"{target.location}:{target.scope}:{path}"
    async with get_db_context() as db:
        acquired = (
            await db.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {"lock_key": lock_key},
            )
        ).scalar_one()
    return not acquired


async def test_path_locks_are_held_across_the_copy(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    held: list[bool] = []

    async def check_locks(_key: str) -> None:
        held.append(await _lock_is_held(solution_target, "a/r.pdf"))
        held.append(await _lock_is_held(solution_target, "b/r.pdf"))

    store.before["copy"] = check_locks
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 200
    assert held == [True, True]


async def test_destination_lock_is_held_during_cleanup(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    source_key, dest_key = key_for(solution_target, "a/r.pdf"), key_for(solution_target, "b/r.pdf")
    held: list[bool] = []

    async def presigned_upload_replaces_source(_key: str) -> None:
        store.put(source_key, b"new upload")

    async def check_lock(key: str) -> None:
        if key == dest_key:
            held.append(await _lock_is_held(solution_target, "b/r.pdf"))

    store.after["copy"] = presigned_upload_replaces_source
    store.before["delete"] = check_lock
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (409, "source_changed")
    assert held == [True]
    assert dest_key not in store.objects


async def test_verify_head_raising_cleans_up_copy(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    source_key, dest_key = key_for(solution_target, "a/r.pdf"), key_for(solution_target, "b/r.pdf")
    copied = failed = False

    async def mark_copied(_key: str) -> None:
        nonlocal copied
        copied = True

    async def verify_head_fails(key: str) -> None:
        # Only step 5's head fails; cleanup's ownership head then succeeds.
        nonlocal failed
        if copied and key == dest_key and not failed:
            failed = True
            raise httpx.ConnectError("s3 unavailable")

    store.after["copy"] = mark_copied
    store.before["head"] = verify_head_fails
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 500 and outcome.storage_error
    assert outcome.error.startswith("storage_unavailable")
    assert dest_key not in store.objects
    assert source_key in store.objects
    assert len(await rows_at(solution_target, "a/r.pdf")) == 1
    assert await rows_at(solution_target, "b/r.pdf") == []


async def test_metadata_write_raising_cleans_up_copy(store, solution_target, monkeypatch) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    source_key, dest_key = key_for(solution_target, "a/r.pdf"), key_for(solution_target, "b/r.pdf")
    monkeypatch.setattr(FilePolicyService, "move_metadata", AsyncMock(side_effect=RuntimeError("db")))
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 500 and not outcome.storage_error
    assert outcome.error.startswith("internal_error")
    assert dest_key not in store.objects
    assert source_key in store.objects
    assert len(await rows_at(solution_target, "a/r.pdf")) == 1


async def test_cleanup_failure_callback_raising_still_reports_cleanup_failed(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    dest_key = key_for(solution_target, "b/r.pdf")

    async def truncate(key: str) -> None:
        store.objects[key].data = b"short"

    async def cleanup_fails(key: str) -> None:
        if key == dest_key:
            raise RuntimeError("delete failed")

    store.after["copy"] = truncate
    store.before["delete"] = cleanup_fails
    notify = AsyncMock(side_effect=RuntimeError("log sink down"))
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename, on_cleanup_failed=notify).move(
        MoveRequest("a/r.pdf", "b/r.pdf"), "error"
    )
    assert (outcome.status_code, outcome.error) == (500, "cleanup_failed")
    notify.assert_awaited_once()


# -- Final review: the destination must still be our copy -------------------
# Presigned uploads take no path lock, so a PUT can replace the destination
# between the copy and any later step.


@pytest.mark.parametrize(
    ("foreign", "same_size"),
    [(b"someone else's upload", False), (b"%PDF-1.7 tes!", True)],
    ids=["other-size", "same-size"],
)
async def test_destination_replaced_after_single_part_copy_is_destination_changed(
    store, solution_target, foreign, same_size
) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    source_key, dest_key = key_for(solution_target, "a/r.pdf"), key_for(solution_target, "b/r.pdf")
    source_etag = store.objects[source_key].etag
    assert (len(foreign) == len(store.objects[source_key].data)) is same_size

    async def presigned_put_replaces_destination(key: str) -> None:
        store.put(key, foreign)

    store.after["copy"] = presigned_put_replaces_destination
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (409, "destination_changed")
    assert store.objects[dest_key].data == foreign
    assert store.objects[dest_key].metadata == {}
    assert ("delete", dest_key) not in store.calls
    assert store.objects[source_key].etag == source_etag
    assert ("delete", source_key) not in store.calls
    assert [r.id for r in await rows_at(solution_target, "a/r.pdf")] == [row_id]
    assert await rows_at(solution_target, "b/r.pdf") == []


async def test_cleanup_keeps_a_foreign_object_that_replaced_our_copy(store, solution_target) -> None:
    """Our copy passed the ownership check, then a PUT replaced it before cleanup."""
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    source_key, dest_key = key_for(solution_target, "a/r.pdf"), key_for(solution_target, "b/r.pdf")
    copied = verified = False

    async def presigned_upload_replaces_source(_key: str) -> None:
        nonlocal copied
        store.put(source_key, b"new upload")
        copied = True

    async def destination_swapped_at_step_7(key: str) -> None:
        nonlocal verified
        if copied and key == dest_key:
            verified = True
        elif verified and key == source_key and holds_move_copy(store, dest_key):
            store.put(dest_key, b"someone else's upload")

    store.after["copy"] = presigned_upload_replaces_source
    store.before["head"] = destination_swapped_at_step_7
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (409, "source_changed")
    assert dest_key in store.objects and store.objects[dest_key].data == b"someone else's upload"
    assert ("delete", dest_key) not in store.calls
    assert store.objects[source_key].data == b"new upload"
    assert [r.id for r in await rows_at(solution_target, "a/r.pdf")] == [row_id]
    assert await rows_at(solution_target, "b/r.pdf") == []


async def test_cleanup_head_raising_keeps_the_copy_and_reports_cleanup_failed(store, solution_target) -> None:
    """Ownership cannot be re-proven, so nothing is deleted; the copy is reported."""
    await seed_file(store, solution_target, "a/r.pdf")
    source_key, dest_key = key_for(solution_target, "a/r.pdf"), key_for(solution_target, "b/r.pdf")
    copied = False

    async def mark_copied(_key: str) -> None:
        nonlocal copied
        copied = True

    async def destination_head_fails(key: str) -> None:
        if copied and key == dest_key:
            raise httpx.ConnectError("s3 unavailable")

    store.after["copy"] = mark_copied
    store.before["head"] = destination_head_fails
    notify = AsyncMock()
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename, on_cleanup_failed=notify).move(
        MoveRequest("a/r.pdf", "b/r.pdf"), "error"
    )
    assert outcome.status_code == 500 and outcome.storage_error
    assert outcome.error.startswith("storage_unavailable")
    assert dest_key in store.objects
    assert ("delete", dest_key) not in store.calls
    assert source_key in store.objects
    notify.assert_awaited_once_with(source_key, dest_key)
    assert await rows_at(solution_target, "b/r.pdf") == []


async def test_renamed_destination_needs_its_own_write_authorization(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"someone else")
    asked: list[str] = []

    async def deny(path: str) -> bool:
        asked.append(path)
        return False

    outcome = await FileMover(store, solution_target, authorize_rename=deny).move(
        MoveRequest("a/r.pdf", "b/r.pdf"), "rename"
    )

    assert (outcome.status_code, outcome.error) == (403, "forbidden")
    assert asked == ["b/r (1).pdf"]
    assert key_for(solution_target, "b/r (1).pdf") not in store.objects
    assert key_for(solution_target, "a/r.pdf") in store.objects
    assert not any(op == "copy" for op, _ in store.calls)
