"""FileMover: a retry recognizes its interrupted copy by markers, never by size."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy import update

from src.core.database import get_db_context
from src.services.file_policy_service import FilePolicyService
from src.models.orm.file_metadata import FileMetadata
from src.services.file_storage.move import MARK_ROW, FileMover, MoveRequest

from .support import allow_rename, key_for, markers, rows_at, seed_file


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
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.adopted) == (200, False)
    assert not any(op == "copy" for op, _ in store.calls)
    assert key_for(solution_target, "a/r.pdf") not in store.objects
    assert [r.id for r in await rows_at(solution_target, "b/r.pdf")] == [row_id]


async def test_resume_with_stale_etag_marker_is_a_conflict(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r.pdf", row_id, delete_source=False)
    store.put(key_for(solution_target, "a/r.pdf"), b"replaced since")  # new ETag
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (409, "file_exists")


async def test_resume_under_rename_finishes_at_the_orphans_candidate(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"someone else")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r (1).pdf", row_id, delete_source=False)
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    assert (outcome.status_code, outcome.path, outcome.renamed) == (200, "b/r (1).pdf", True)
    assert key_for(solution_target, "b/r (2).pdf") not in store.objects
    assert not any(op == "copy" for op, _ in store.calls)
    assert key_for(solution_target, "a/r.pdf") not in store.objects
    assert [r.id for r in await rows_at(solution_target, "b/r (1).pdf")] == [row_id]


async def test_adopt_completes_a_move_whose_source_was_deleted(store, solution_target, published) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r.pdf", row_id, delete_source=True)
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.adopted) == (200, True)
    assert [r.id for r in await rows_at(solution_target, "b/r.pdf")] == [row_id]
    assert await rows_at(solution_target, "a/r.pdf") == []
    assert published.await_count == 2


async def test_adopt_under_rename_finds_the_candidate(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"someone else")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r (1).pdf", row_id, delete_source=True)
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
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
        outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", path), "error")
        assert (outcome.status_code, outcome.error) == (404, "not_found"), path
    assert len(await rows_at(solution_target, "a/r.pdf")) == 1


async def test_adopt_refuses_a_copy_of_an_older_version(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf", data=b"version two, longer")
    source_key = key_for(solution_target, "a/r.pdf")
    store.objects.pop(source_key)
    store.put(key_for(solution_target, "b/r.pdf"), b"v1", markers(source_key, row_id, '"old"'))
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 404


async def test_already_moved_is_a_noop_success(store, solution_target, published) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    mover = FileMover(store, solution_target, authorize_rename=allow_rename)
    assert (await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")).status_code == 200
    published.reset_mock()

    again = await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (again.status_code, again.adopted) == (200, True)
    assert [r.id for r in await rows_at(solution_target, "b/r.pdf")] == [row_id]
    published.assert_not_awaited()


async def test_already_moved_under_rename_reports_the_final_path(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"someone else")
    mover = FileMover(store, solution_target, authorize_rename=allow_rename)
    first = await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    assert (first.status_code, first.path) == (200, "b/r (1).pdf")
    again = await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    assert (again.status_code, again.path, again.adopted) == (200, first.path, True)


async def test_row_at_source_never_takes_the_already_moved_branch(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    mover = FileMover(store, solution_target, authorize_rename=allow_rename)
    assert (await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")).status_code == 200
    # A new file row appears at the source path, its object removed out of band.
    await seed_file(store, solution_target, "a/r.pdf")
    store.objects.pop(key_for(solution_target, "a/r.pdf"))
    outcome = await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (404, "not_found")


async def test_commit_failure_after_source_delete_is_adopted_on_retry(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")

    @asynccontextmanager
    async def commit_lost():
        async with get_db_context() as db:
            async def fail() -> None:
                raise RuntimeError("connection dropped during commit")

            db.commit = fail
            yield db

    first = await FileMover(store, solution_target, authorize_rename=allow_rename, open_session=commit_lost).move(
        MoveRequest("a/r.pdf", "b/r.pdf"), "error"
    )
    assert first.status_code == 500
    assert key_for(solution_target, "a/r.pdf") not in store.objects
    assert [r.id for r in await rows_at(solution_target, "a/r.pdf")] == [row_id]

    retry = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (retry.status_code, retry.adopted) == (200, True)
    assert [r.id for r in await rows_at(solution_target, "b/r.pdf")] == [row_id]
    assert await rows_at(solution_target, "a/r.pdf") == []


def _replace_on_second_head(store, key: str, data: bytes):
    """A writer replaces ``key`` between the engine's first look and its lock."""
    seen = 0

    async def hook(head_key: str) -> None:
        nonlocal seen
        if head_key == key:
            seen += 1
            if seen == 2:
                store.put(key, data)

    return hook


async def test_candidate_orphan_replaced_before_its_lock_is_not_resumed(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"someone else")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r (1).pdf", row_id, delete_source=False)
    candidate = key_for(solution_target, "b/r (1).pdf")
    store.before["head"] = _replace_on_second_head(store, candidate, b"a writer's file")
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    assert (outcome.status_code, outcome.path) == (200, "b/r (2).pdf")
    assert store.objects[candidate].data == b"a writer's file"


async def test_candidate_copy_replaced_before_its_lock_is_not_adopted(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"someone else")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r (1).pdf", row_id, delete_source=True)
    candidate = key_for(solution_target, "b/r (1).pdf")
    store.before["head"] = _replace_on_second_head(store, candidate, b"a writer's file")
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")
    assert (outcome.status_code, outcome.error) == (404, "not_found")
    assert store.objects[candidate].data == b"a writer's file"
    assert [r.id for r in await rows_at(solution_target, "a/r.pdf")] == [row_id]


def _on_second_source_head(store, source_key: str, action):
    """Run ``action`` just before the step-7 re-check heads the source."""
    seen = 0

    async def hook(key: str) -> None:
        nonlocal seen
        if key == source_key:
            seen += 1
            if seen == 2:
                await action()

    return hook


async def test_resume_whose_source_vanishes_before_recheck_is_adopted(store, solution_target, published) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r.pdf", row_id, delete_source=False)
    source_key, dest_key = key_for(solution_target, "a/r.pdf"), key_for(solution_target, "b/r.pdf")

    async def late_delete_lands() -> None:  # an earlier run's unconfirmed delete
        store.objects.pop(source_key)

    store.before["head"] = _on_second_source_head(store, source_key, late_delete_lands)
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.adopted) == (200, True)
    assert store.objects[dest_key].data == b"%PDF-1.7 test"
    assert [r.id for r in await rows_at(solution_target, "b/r.pdf")] == [row_id]
    assert await rows_at(solution_target, "a/r.pdf") == []
    assert published.await_count == 2


async def test_resume_whose_source_vanishes_keeps_a_copy_that_fails_the_adopt_guards(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r.pdf", row_id, delete_source=False)
    source_key, dest_key = key_for(solution_target, "a/r.pdf"), key_for(solution_target, "b/r.pdf")

    async def source_gone_and_markers_lost() -> None:
        store.objects.pop(source_key)
        store.objects[dest_key].metadata[MARK_ROW] = str(uuid4())

    store.before["head"] = _on_second_source_head(store, source_key, source_gone_and_markers_lost)
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (500, "source_missing_during_resume")
    assert dest_key in store.objects
    assert ("delete", dest_key) not in store.calls
    assert [r.id for r in await rows_at(solution_target, "a/r.pdf")] == [row_id]
    assert await rows_at(solution_target, "b/r.pdf") == []


async def test_resume_whose_source_recheck_raises_keeps_the_copy(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r.pdf", row_id, delete_source=False)
    source_key, dest_key = key_for(solution_target, "a/r.pdf"), key_for(solution_target, "b/r.pdf")

    async def head_fails() -> None:
        raise RuntimeError("head failed")

    store.before["head"] = _on_second_source_head(store, source_key, head_fails)
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 500
    assert dest_key in store.objects
    assert ("delete", dest_key) not in store.calls
    assert [r.id for r in await rows_at(solution_target, "a/r.pdf")] == [row_id]
    assert await rows_at(solution_target, "b/r.pdf") == []


async def test_already_moved_refuses_a_row_marker_that_is_not_the_rows(store, solution_target) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    mover = FileMover(store, solution_target, authorize_rename=allow_rename)
    assert (await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")).status_code == 200
    store.objects[key_for(solution_target, "b/r.pdf")].metadata[MARK_ROW] = str(uuid4())
    outcome = await mover.move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (404, "not_found")


async def test_adopt_without_a_recorded_size_relies_on_markers(store, solution_target) -> None:
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    async with get_db_context() as db:
        await db.execute(update(FileMetadata).where(FileMetadata.id == row_id).values(size_bytes=None))
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r.pdf", row_id, delete_source=True)
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.adopted) == (200, True)
    assert [r.id for r in await rows_at(solution_target, "b/r.pdf")] == [row_id]


# A resumed copy is never cleaned up: an earlier run's source delete may land
# at any moment, so a failed resume keeps the copy for a later retry.


def _on_second_head(store, key: str, action):
    """Run ``action`` just before the engine's second head of ``key``."""
    seen = 0

    async def hook(head_key: str) -> None:
        nonlocal seen
        if head_key == key:
            seen += 1
            if seen == 2:
                await action()

    return hook


async def _resume_setup(store, target):
    row_id = await seed_file(store, target, "a/r.pdf")
    await _interrupted_copy(store, target, "a/r.pdf", "b/r.pdf", row_id, delete_source=False)
    return row_id, key_for(target, "a/r.pdf"), key_for(target, "b/r.pdf")


async def _assert_copy_kept(store, target, row_id, dest_key: str) -> None:
    assert dest_key in store.objects
    assert not any(op == "delete" for op, _ in store.calls)
    assert [r.id for r in await rows_at(target, "a/r.pdf")] == [row_id]
    assert await rows_at(target, "b/r.pdf") == []


async def test_failed_resume_keeps_the_copy_when_the_verify_head_raises(store, solution_target) -> None:
    row_id, _, dest_key = await _resume_setup(store, solution_target)

    async def head_fails() -> None:
        raise RuntimeError("head failed")

    store.before["head"] = _on_second_head(store, dest_key, head_fails)  # step 5
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 500 and outcome.error.startswith("internal_error")
    await _assert_copy_kept(store, solution_target, row_id, dest_key)


async def test_failed_resume_keeps_the_copy_when_the_metadata_write_raises(
    store, solution_target, monkeypatch
) -> None:
    row_id, _, dest_key = await _resume_setup(store, solution_target)
    monkeypatch.setattr(FilePolicyService, "move_metadata", AsyncMock(side_effect=RuntimeError("db")))
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert outcome.status_code == 500 and outcome.error.startswith("internal_error")
    await _assert_copy_kept(store, solution_target, row_id, dest_key)


async def test_failed_resume_keeps_the_copy_when_verification_fails(store, solution_target) -> None:
    row_id, _, dest_key = await _resume_setup(store, solution_target)

    async def copy_size_changes() -> None:
        store.objects[dest_key].data += b" truncated differently"

    store.before["head"] = _on_second_head(store, dest_key, copy_size_changes)  # step 5
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (500, "copy_verification_failed")
    await _assert_copy_kept(store, solution_target, row_id, dest_key)


async def test_failed_resume_keeps_the_copy_when_the_source_changes(store, solution_target) -> None:
    row_id, source_key, dest_key = await _resume_setup(store, solution_target)

    async def upload_lands() -> None:
        store.put(source_key, b"new upload")

    store.before["head"] = _on_second_head(store, source_key, upload_lands)  # step 7
    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "error")
    assert (outcome.status_code, outcome.error) == (409, "source_changed")
    assert store.objects[source_key].data == b"new upload"
    await _assert_copy_kept(store, solution_target, row_id, dest_key)


async def test_adopt_refuses_when_two_same_size_versions_match(store, solution_target) -> None:
    """Row id and size cannot tell an older version's copy from the newer one."""
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"someone else")
    source_key = key_for(solution_target, "a/r.pdf")
    version_a = store.objects[source_key].data
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r (1).pdf", row_id, delete_source=False)
    # Overwritten in place with a same-length version B; a later run copies B
    # to (2), deletes the source, then loses its metadata commit.
    store.put(source_key, bytes(reversed(version_a)))
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r (2).pdf", row_id, delete_source=True)

    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(MoveRequest("a/r.pdf", "b/r.pdf"), "rename")

    assert (outcome.status_code, outcome.error) == (500, "recovery_ambiguous")
    assert [r.id for r in await rows_at(solution_target, "a/r.pdf")] == [row_id]
    assert key_for(solution_target, "b/r (1).pdf") in store.objects
    assert key_for(solution_target, "b/r (2).pdf") in store.objects


async def _deny(path: str) -> bool:
    _deny.asked.append(path)
    return False


async def test_resume_at_a_renamed_candidate_needs_the_callers_write(store, solution_target) -> None:
    """Markers name the source and row, not whose authorization made the copy."""
    _deny.asked = []
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"someone else")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r (1).pdf", row_id, delete_source=False)

    outcome = await FileMover(store, solution_target, authorize_rename=_deny).move(
        MoveRequest("a/r.pdf", "b/r.pdf"), "rename"
    )

    assert (outcome.status_code, outcome.error) == (403, "forbidden")
    assert _deny.asked == ["b/r (1).pdf"]
    assert key_for(solution_target, "a/r.pdf") in store.objects
    assert key_for(solution_target, "b/r (1).pdf") in store.objects
    assert [r.id for r in await rows_at(solution_target, "a/r.pdf")] == [row_id]


async def test_adopt_at_a_renamed_candidate_needs_the_callers_write(store, solution_target, published) -> None:
    _deny.asked = []
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    store.put(key_for(solution_target, "b/r.pdf"), b"someone else")
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r (1).pdf", row_id, delete_source=True)

    outcome = await FileMover(store, solution_target, authorize_rename=_deny).move(
        MoveRequest("a/r.pdf", "b/r.pdf"), "rename"
    )

    assert (outcome.status_code, outcome.error) == (403, "forbidden")
    assert _deny.asked == ["b/r (1).pdf"]
    assert key_for(solution_target, "b/r (1).pdf") in store.objects
    assert [r.id for r in await rows_at(solution_target, "a/r.pdf")] == [row_id]
    assert await rows_at(solution_target, "b/r (1).pdf") == []
    published.assert_not_awaited()


async def test_adopt_scans_numbered_copies_whatever_mode_the_retry_uses(store, solution_target) -> None:
    """The interrupted run's mode is not recorded, so an error-mode retry still
    sees a rename run's copy and refuses to pick between two versions."""
    row_id = await seed_file(store, solution_target, "a/r.pdf")
    source_key = key_for(solution_target, "a/r.pdf")
    version_a = store.objects[source_key].data
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r.pdf", row_id, delete_source=False)
    store.put(source_key, bytes(reversed(version_a)))  # same row, same size
    await _interrupted_copy(store, solution_target, "a/r.pdf", "b/r (1).pdf", row_id, delete_source=True)

    outcome = await FileMover(store, solution_target, authorize_rename=allow_rename).move(
        MoveRequest("a/r.pdf", "b/r.pdf"), "error"
    )

    assert (outcome.status_code, outcome.error) == (500, "recovery_ambiguous")
    assert [r.id for r in await rows_at(solution_target, "a/r.pdf")] == [row_id]
