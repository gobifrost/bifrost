"""Server-side move of one file inside one location and scope.

The ``files.move`` platform job (``src.jobs.platform.file_move``) creates one
:class:`FileMover` per job and calls :meth:`FileMover.move` once per item.
Each item:
1. Locks both paths.
2. Inspects the source.
3. Resolves the destination (conflict or rename).
4. Copies server-side with a source-ETag guard.
5. Verifies the destination is our copy (markers) and its size.
6. Re-keys metadata.
7. Re-checks the source, then deletes it.
8. Commits and publishes.

A retry recognizes an interrupted earlier run of the same item by the copy
markers, never by size: it resumes a marked copy (source present), adopts it
(source gone, row still at the source) or reports the item already moved.

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
# Whether the requesting user may write a numbered ``name (n)`` destination.
# Enqueue authorizes only the requested destination, and a copy's markers name
# its source and row, not whose authorization made it, so every numbered
# candidate is checked before it is copied to, resumed, or adopted.
AuthorizeRename = Callable[[str], Awaitable[bool]]


class ObjectStore(Protocol):
    async def head_object(self, key: str) -> ObjectHead | None:
        """The object's head, or None when no object is stored at ``key``."""
        raise NotImplementedError

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
        """Copy ``source`` server-side, guarded by its ETag, with ``metadata``."""
        raise NotImplementedError

    async def delete_object_strict(self, key: str) -> None:
        """Delete ``key``, raising on any storage error."""
        raise NotImplementedError

    async def list_keys(self, prefix: str, *, delimiter: str = "/") -> list[str]:
        """Keys directly under ``prefix``."""
        raise NotImplementedError


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


def _row_id(row: _Row | None) -> UUID | None:
    return row.id if row is not None else None


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
        authorize_rename: AuthorizeRename,
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
        self._authorize_rename = authorize_rename
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
                return await self._recover(db, request, source_key, row)
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
                # No copy of ours is left, or the object there is someone
                # else's: nothing to clean up.
                await db.rollback()
                return _fail(409, "destination_changed")
            # The destination is our copy from here on; every failure before
            # the source delete removes it while the re-taken locks are held.
            try:
                failure, current_row = await self._recheck_source(db, request, source_key, source, row)
            except Exception:
                await self._cleanup(request, source_key, row, chosen)
                raise
            if failure is not None:
                # The copy carries the step-2 row's marker, not current_row's.
                return await self._discard(db, request, source_key, row, chosen, failure)
            return await self._complete(db, request, source_key, source, current_row, chosen)

    async def _recheck_source(
        self,
        db: AsyncSession,
        request: MoveRequest,
        source_key: str,
        source: ObjectHead,
        row: _Row | None,
    ) -> tuple[MoveOutcome | None, _Row | None]:
        """After an unlocked copy: the source must be the object that was copied."""
        current = await self._store.head_object(source_key)
        if current is None:
            # Never fall through to step 6's "no row, insert one": the
            # deleted file would reappear at the destination.
            return _fail(404, "not_found", "the source was deleted during the move"), None
        current_row = await self._row(db, request.source)
        if current.etag != source.etag or _row_id(current_row) != _row_id(row):
            return _fail(409, "source_changed"), current_row
        return None, current_row

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
        existing = await self._store.head_object(destination_key)
        if existing is None:
            return _Destination(request.destination, destination_key, renamed=False, resume=False)
        if self._is_orphan(existing, source_key, source, row):
            return _Destination(request.destination, destination_key, renamed=False, resume=True)
        if on_conflict == "error":
            return _fail(409, "file_exists")
        taken = await self._candidates(request.destination)
        found = await self._own_copies(
            db,
            [taken[number] for number in sorted(taken)],
            lambda head: self._is_orphan(head, source_key, source, row),
            limit=1,
        )
        if found:
            path, key, _ = found[0]
            if not await self._authorize_rename(path):
                # The copy is never removed (see _cleanup on resumed copies).
                return _fail(403, "forbidden")
            return _Destination(path, key, renamed=True, resume=True)
        return await self._first_free_candidate(db, request.destination, taken)

    async def _own_copies(
        self,
        db: AsyncSession,
        targets: list[tuple[str, str]],
        is_ours: Callable[[ObjectHead], bool],
        *,
        locked: str | None = None,
        limit: int | None = None,
    ) -> list[tuple[str, str, ObjectHead]]:
        """Targets holding this item's copy, each confirmed under its path lock.

        ``locked`` names a path whose lock is already held. Any other match is
        locked and looked at again: a writer can replace the object between
        the first look and the lock, and its file must never be resumed (and
        then cleaned up) or adopted. ``limit`` stops after that many matches.
        """
        found: list[tuple[str, str, ObjectHead]] = []
        for path, key in targets:
            head = await self._store.head_object(key)
            if head is None or not is_ours(head):
                continue
            if path != locked:
                await self._lock(db, path)
                head = await self._store.head_object(key)
                if head is None or not is_ours(head):
                    continue
            found.append((path, key, head))
            if limit is not None and len(found) >= limit:
                break
        return found

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
                if not await self._authorize_rename(path):
                    return _fail(403, "forbidden")
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
        # Cleanup runs before the rollback: the rollback releases the path
        # locks, and a writer waiting on the destination lock could otherwise
        # PUT its own file there for our cleanup to delete.
        try:
            landed = await self._store.head_object(destination.key)
        except Exception:
            # Steps 5–6 raised before the source delete was issued: remove our
            # copy, then let move() map the error (storage vs internal).
            await self._cleanup(request, source_key, row, destination)
            raise
        if landed is None or not self._is_copy_of(landed, source_key, row):
            # A presigned upload (which takes no path lock) replaced or removed
            # our copy: the object there is someone else's, so delete nothing.
            await db.rollback()
            return _fail(409, "destination_changed")
        try:
            failure = await self._verify_and_stage(db, source, row, destination, landed)
        except Exception:
            await self._cleanup(request, source_key, row, destination)
            raise
        if failure is not None:
            return await self._discard(db, request, source_key, row, destination, failure)

        # Step 7's re-check.
        try:
            current = await self._store.head_object(source_key)
        except Exception:
            await self._cleanup(request, source_key, row, destination)
            raise
        if current is None and destination.resume:
            return await self._adopt_resumed(db, request, source_key, row, destination)
        failure = self._recheck_before_delete(source, current)
        if failure is not None:
            return await self._discard(db, request, source_key, row, destination, failure)

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

    async def _verify_and_stage(
        self,
        db: AsyncSession,
        source: ObjectHead,
        row: _Row | None,
        destination: _Destination,
        landed: ObjectHead,
    ) -> MoveOutcome | None:
        """Steps 5–6 on our copy (``landed``); a returned outcome needs cleanup."""
        if landed.size != source.size:
            return _fail(500, "copy_verification_failed")
        await self._write_metadata(db, destination, landed, row)
        return None

    @staticmethod
    def _recheck_before_delete(source: ObjectHead, current: ObjectHead | None) -> MoveOutcome | None:
        """Step 7's source re-check; a returned outcome needs cleanup."""
        if current is None:
            return _fail(404, "not_found", "the source was deleted during the move")
        if current.etag != source.etag:
            # A presigned upload replaced the source mid-move; keep it.
            return _fail(409, "source_changed")
        return None

    async def _adopt_resumed(
        self,
        db: AsyncSession,
        request: MoveRequest,
        source_key: str,
        row: _Row | None,
        destination: _Destination,
    ) -> MoveOutcome:
        """A resume whose source vanished before step 7: never clean up.

        An earlier run's unconfirmed source delete can land after this run's
        step-2 head, leaving the resumed copy as the only one. Step 6 is
        already staged against it, so it is committed as an adopt when the
        adopt guards hold, and otherwise kept for a later retry.
        """
        head = await self._store.head_object(destination.key)
        if head is None or not self._is_adoptable(head, source_key, row):
            await db.rollback()
            return _fail(500, "source_missing_during_resume")
        await db.commit()
        await self._publish(request.source, destination.path)
        return MoveOutcome(
            status_code=200,
            path=destination.path if destination.renamed else None,
            renamed=destination.renamed,
            adopted=True,
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

    async def _discard(
        self,
        db: AsyncSession,
        request: MoveRequest,
        source_key: str,
        row: _Row | None,
        destination: _Destination,
        failure: MoveOutcome,
    ) -> MoveOutcome:
        """Remove our copy under the path locks, then roll back and fail."""
        if not await self._cleanup(request, source_key, row, destination):
            failure = _fail(500, "cleanup_failed")
        await db.rollback()
        return failure

    async def _confirmed_gone(self, key: str) -> bool:
        try:
            return await self._store.head_object(key) is None
        except Exception:
            logger.warning("Could not confirm a source delete", exc_info=True)
            return False

    async def _cleanup(
        self,
        request: MoveRequest,
        source_key: str,
        row: _Row | None,
        destination: _Destination,
    ) -> bool:
        """Remove this item's copy before the source delete was issued.

        Call it while the path locks are still held (before any rollback).
        Returns False when the copy could not be removed; never raises.

        A resumed copy is never removed: an earlier run created it and may
        already have issued its source delete, which can land at any moment
        and leave the copy as the only one. A failed resume leaves it for a
        later retry (which resumes or adopts it) or an operator.

        Only our copy is deleted. Presigned uploads take no path lock, so the
        destination is looked at first: a missing or foreign object means
        there is nothing of ours to remove. A look that raises leaves
        ownership unproven, so nothing is deleted and the cleanup fails.
        """
        if destination.resume:
            return True
        paths = {
            "location": self._target.location,
            "source_path": request.source,
            "destination_path": destination.path,
        }
        try:
            head = await self._store.head_object(destination.key)
            if head is None or not self._is_copy_of(head, source_key, row):
                return True
            await self._store.delete_object_strict(destination.key)
            return True
        except Exception:
            logger.warning("Could not remove a failed move's copy", extra=paths, exc_info=True)
        if self._on_cleanup_failed is not None:
            try:
                await self._on_cleanup_failed(source_key, destination.key)
            except Exception:
                logger.warning(
                    "Could not report a failed move cleanup", extra=paths, exc_info=True
                )
        return False

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
    ) -> MoveOutcome:
        """The source object is gone: adopt our copy, report already moved, or 404."""
        targets = await self._recovery_targets(request.destination)
        if row is not None:
            # Adopt: an earlier run deleted the source but died before its
            # commit (or its commit failed). The source is gone, so the copy
            # is never cleaned up here; any error leaves it for another retry.
            found = await self._own_copies(
                db,
                targets,
                lambda head: self._is_adoptable(head, source_key, row),
                locked=request.destination,
            )
            if len(found) > 1:
                # Copies of two versions of the row (overwritten in place at
                # the same size) both pass the markers and the size guard;
                # nothing durable says which is newest, so keep them all.
                await db.rollback()
                return _fail(500, "recovery_ambiguous")
            if found:
                path, key, head = found[0]
                renamed = path != request.destination
                if renamed and not await self._authorize_rename(path):
                    await db.rollback()
                    return _fail(403, "forbidden")
                destination = _Destination(path, key, renamed=renamed, resume=True)
                await self._write_metadata(db, destination, head, row)
                await db.commit()
                await self._publish(request.source, path)
                return MoveOutcome(
                    status_code=200,
                    path=path if renamed else None,
                    renamed=renamed,
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

    async def _recovery_targets(self, destination: str) -> list[tuple[str, str]]:
        """Where an earlier run of this item could have put its copy.

        Numbered candidates are scanned whatever this retry's ``on_conflict``:
        the interrupted run's mode is not recorded, and ownership is decided
        by the markers, never by the location.
        """
        candidates = await self._candidates(destination)
        return [(destination, self._key(destination))] + [
            candidates[number] for number in sorted(candidates)
        ]

    # -- helpers ------------------------------------------------------------

    def _key(self, path: str) -> str:
        return resolve_s3_key(self._target.location, self._target.scope, path)

    def _is_copy_of(self, head: ObjectHead, source_key: str, row: _Row | None) -> bool:
        """The object carries this item's markers (source key and source row)."""
        return (
            head.metadata.get(MARK_SOURCE) == source_marker(source_key)
            and head.metadata.get(MARK_ROW) == _row_marker(row)
        )

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

    def _is_adoptable(self, head: ObjectHead, source_key: str, row: _Row | None) -> bool:
        """This item's copy, finishable although the source object is gone.

        The size guard rejects a copy of an older version of a row that was
        since overwritten in place (same row id). It is never the identity.
        """
        return (
            row is not None
            and self._is_copy_of(head, source_key, row)
            and row.size_bytes in (None, head.size)
        )

    async def _lock(self, db: AsyncSession, *paths: str) -> None:
        # Paths passed together are locked in sorted order. Rename candidates
        # (and recovery targets) are locked by later calls, after the sorted
        # source/destination pair, so they are not globally ordered. That is
        # deadlock-free only because every other locker takes a single path
        # and move jobs are serialized per scope (one resource_lock_key).
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
