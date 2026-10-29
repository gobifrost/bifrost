"""FileMover: multipart copies run without path locks and re-check after."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
from sqlalchemy import text

from src.core.database import get_db_context
from src.services.file_storage.move import FileMover, MoveRequest
from src.services.file_storage.s3_client import ObjectMissing

from .support import allow_rename, holds_move_copy, key_for, rows_at, seed_file

DATA = b"x" * 64  # above the test threshold of 16 bytes


def mover(store, target, **kwargs) -> FileMover:
    return FileMover(store, target, authorize_rename=allow_rename, multipart_threshold=16, part_size=8, **kwargs)


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


# -- Task 7 rulings carried into the multipart path ---------------------------


async def test_recheck_cleanup_runs_under_reacquired_destination_lock(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/big.bin", data=DATA)
    source_key, dest_key = key_for(solution_target, "a/big.bin"), key_for(solution_target, "b/big.bin")
    lock_free: list[bool] = []

    async def presigned_upload(_key: str) -> None:
        store.put(source_key, b"y" * 64)

    async def probe(key: str) -> None:
        if key == dest_key:
            lock_free.append(await _path_lock_free(solution_target, "b/big.bin"))

    store.after["copy"] = presigned_upload
    store.before["delete"] = probe
    outcome = await mover(store, solution_target).move(MoveRequest("a/big.bin", "b/big.bin"), "error")
    assert (outcome.status_code, outcome.error) == (409, "source_changed")
    assert lock_free == [False]
    assert dest_key not in store.objects


async def test_recheck_source_head_raising_cleans_up_copy(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/big.bin", data=DATA)
    source_key, dest_key = key_for(solution_target, "a/big.bin"), key_for(solution_target, "b/big.bin")
    copied = False

    async def mark_copied(_key: str) -> None:
        nonlocal copied
        copied = True

    async def source_head_fails(key: str) -> None:
        if copied and key == source_key:
            raise httpx.ConnectError("s3 unavailable")

    store.after["copy"] = mark_copied
    store.before["head"] = source_head_fails
    outcome = await mover(store, solution_target).move(MoveRequest("a/big.bin", "b/big.bin"), "error")
    assert outcome.status_code == 500 and outcome.storage_error
    assert outcome.error.startswith("storage_unavailable")
    assert dest_key not in store.objects
    assert source_key in store.objects
    assert len(await rows_at(solution_target, "a/big.bin")) == 1
    assert await rows_at(solution_target, "b/big.bin") == []


async def test_destination_head_raising_after_copy_deletes_nothing(store, solution_target) -> None:
    """Ownership of the destination is unproven, so it must not be cleaned up."""
    await seed_file(store, solution_target, "a/big.bin", data=DATA)
    source_key, dest_key = key_for(solution_target, "a/big.bin"), key_for(solution_target, "b/big.bin")
    copied = False

    async def mark_copied(_key: str) -> None:
        nonlocal copied
        copied = True

    async def dest_head_fails(key: str) -> None:
        if copied and key == dest_key:
            raise httpx.ConnectError("s3 unavailable")

    store.after["copy"] = mark_copied
    store.before["head"] = dest_head_fails
    outcome = await mover(store, solution_target).move(MoveRequest("a/big.bin", "b/big.bin"), "error")
    assert outcome.status_code == 500 and outcome.storage_error
    assert ("delete", dest_key) not in store.calls
    assert ("delete", source_key) not in store.calls
    assert source_key in store.objects


async def test_recheck_cleanup_failure_is_cleanup_failed(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/big.bin", data=DATA)
    source_key, dest_key = key_for(solution_target, "a/big.bin"), key_for(solution_target, "b/big.bin")

    async def presigned_upload(_key: str) -> None:
        store.put(source_key, b"y" * 64)

    async def cleanup_fails(key: str) -> None:
        if key == dest_key:
            raise RuntimeError("delete failed")

    store.after["copy"] = presigned_upload
    store.before["delete"] = cleanup_fails
    notify = AsyncMock(side_effect=RuntimeError("log sink down"))
    outcome = await mover(store, solution_target, on_cleanup_failed=notify).move(
        MoveRequest("a/big.bin", "b/big.bin"), "error"
    )
    assert (outcome.status_code, outcome.error) == (500, "cleanup_failed")
    notify.assert_awaited_once_with(source_key, dest_key)
    assert source_key in store.objects


async def test_recheck_cleanup_keeps_a_foreign_object_that_replaced_our_copy(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/big.bin", data=DATA)
    source_key, dest_key = key_for(solution_target, "a/big.bin"), key_for(solution_target, "b/big.bin")
    copied = verified = False

    async def presigned_upload(_key: str) -> None:
        nonlocal copied
        store.put(source_key, b"y" * 64)
        copied = True

    async def destination_swapped_at_recheck(key: str) -> None:
        nonlocal verified
        if copied and key == dest_key:
            verified = True
        elif verified and key == source_key and holds_move_copy(store, dest_key):
            store.put(dest_key, b"someone else")

    store.after["copy"] = presigned_upload
    store.before["head"] = destination_swapped_at_recheck
    outcome = await mover(store, solution_target).move(MoveRequest("a/big.bin", "b/big.bin"), "error")
    assert (outcome.status_code, outcome.error) == (409, "source_changed")
    assert dest_key in store.objects and store.objects[dest_key].data == b"someone else"
    assert ("delete", dest_key) not in store.calls
    assert source_key in store.objects
