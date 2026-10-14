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

from .support import key_for, rows_at, seed_file


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
