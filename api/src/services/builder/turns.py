"""Builder turn lifecycle: one mutating agent turn against a Solution's source.

The unit of change is a *turn*. A turn takes the project's current revision,
materializes it into a private temporary workspace, lets a caller-supplied
mutation run against that workspace through the safe tools, validates the
result, and — only if the content actually changed — stores a new immutable
revision and advances ``current_revision_id``.

Three invariants make this safe to run behind a chat UI:

- **One writer per Solution.** Turns take the same per-install write lock a
  deploy takes, so a turn can never interleave with a deploy or another turn.
  Contention is refused immediately (:class:`BuilderTurnConflict`), not queued.
- **Failure is inert.** Any error — mutation, validation, storage — fails the
  turn row and leaves ``current_revision_id`` pointing at the last good
  revision. Nothing partial is ever published.
- **History is append-only.** Undo does not rewind; it writes a *new* revision
  whose content came from an older one, recording ``restored_from_revision_id``
  so the lineage stays auditable.

The mutation is a callback rather than an agent call so the lifecycle can be
exercised and tested without a model in the loop; the agent hook plugs in here.

Build and deploy queueing (spec step 8) is not this service's job — it lands
with the job tables in WP3.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionBuilderTurn,
    SolutionSourceRevision,
)
from src.services.builder.fs_tools import WorkspaceLimits, WorkspaceRoot, safe_extract_zip
from src.services.builder.revision_storage import SolutionRevisionStorage
from src.services.builder.scaffold import (
    build_initial_workspace,
    validate_workspace,
    zip_workspace,
)
from src.services.solutions.write_lock import SolutionWriteLockHeld, solution_write_lock

# The mutation the turn applies. Receives the extracted workspace; every edit it
# makes goes through the safe tools on WorkspaceRoot.
WorkspaceMutation = Callable[[WorkspaceRoot], Awaitable[None]]

STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

_WORKSPACE_DIRNAME = "workspace"
_SOURCE_ZIP_NAME = "source.zip"


class BuilderTurnError(Exception):
    """A builder turn could not run or could not complete."""


class BuilderTurnConflict(BuilderTurnError):
    """Another writer holds this Solution's write lock (concurrent turn or deploy)."""


class BuilderProjectMissing(BuilderTurnError):
    """No builder project exists for this Solution, or it has no current revision."""


class WorkspaceInvalid(BuilderTurnError):
    """The workspace the turn produced is not a valid Solution workspace."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def _private_tempdir() -> Iterator[Path]:
    """A 0700 scratch directory, removed on the way out however the block ends.

    ``TemporaryDirectory`` already creates at 0700, but chmod is explicit here
    because the mode is a security property of the turn (revision content is
    private to the Solution's owner), not an implementation detail to inherit.
    """
    with tempfile.TemporaryDirectory(prefix="bifrost-builder-") as name:
        path = Path(name)
        os.chmod(path, 0o700)
        yield path


def _clear_directory(root: Path) -> None:
    """Remove every entry under ``root``, leaving the directory itself in place."""
    for entry in root.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


class BuilderTurnService:
    """Runs the create/turn/undo lifecycle for one deployment's builder projects."""

    def __init__(self, db: AsyncSession, limits: WorkspaceLimits | None = None) -> None:
        self.db = db
        self.limits = limits or WorkspaceLimits()

    def _storage(self, solution_id: UUID) -> SolutionRevisionStorage:
        return SolutionRevisionStorage(solution_id)

    async def create_project(
        self,
        solution_id: UUID,
        *,
        slug: str,
        name: str,
        conversation_id: UUID | None,
        user_id: UUID | None,
    ) -> SolutionSourceRevision:
        """Scaffold revision 0 and open the builder project pointing at it.

        No write lock: the project does not exist yet, so there is nothing
        another writer could be holding.
        """
        revision_id = uuid4()
        with _private_tempdir() as scratch:
            workspace = scratch / _WORKSPACE_DIRNAME
            build_initial_workspace(
                workspace,
                slug=slug,
                name=name,
                solution_id=solution_id,
            )

            errors = validate_workspace(workspace)
            if errors:
                raise WorkspaceInvalid(errors)

            zip_path = scratch / _SOURCE_ZIP_NAME
            sha256 = zip_workspace(workspace, zip_path)
            size_bytes = zip_path.stat().st_size
            await self._storage(solution_id).write_from_path(revision_id, zip_path)

        revision = SolutionSourceRevision(
            id=revision_id,
            solution_id=solution_id,
            conversation_id=conversation_id,
            created_by=user_id,
            source_sha256=sha256,
            size_bytes=size_bytes,
            summary="initial scaffold",
        )
        self.db.add(revision)
        self.db.add(
            SolutionBuilderProject(
                solution_id=solution_id,
                current_revision_id=revision_id,
            )
        )
        await self.db.flush()
        return revision

    async def run_turn(
        self,
        solution_id: UUID,
        *,
        session_id: UUID,
        requested_by: UUID | None,
        mutate: WorkspaceMutation,
        summary: str | None = None,
    ) -> SolutionBuilderTurn:
        """Apply ``mutate`` to the current revision and publish the result.

        Raises :class:`BuilderTurnConflict` when another writer holds the lock,
        and :class:`BuilderProjectMissing` when there is nothing to build from.
        Any other failure is recorded on the turn row (``status='failed'``) and
        re-raised; ``current_revision_id`` is left untouched.
        """
        return await self._run_exclusive(
            solution_id,
            session_id=session_id,
            requested_by=requested_by,
            mutate=mutate,
            summary=summary,
            restored_from_revision_id=None,
        )

    async def undo(
        self,
        solution_id: UUID,
        *,
        session_id: UUID,
        requested_by: UUID | None,
        to_revision_id: UUID,
    ) -> SolutionBuilderTurn:
        """Restore an earlier revision's content as a new revision.

        History is never rewritten: this runs the normal turn machinery with a
        mutation that replaces the workspace wholesale, and stamps
        ``restored_from_revision_id`` on the revision it writes.
        """
        target = await self.db.get(SolutionSourceRevision, to_revision_id)
        if target is None or target.solution_id != solution_id:
            raise BuilderProjectMissing(
                f"revision {to_revision_id} does not belong to Solution {solution_id}"
            )

        storage = self._storage(solution_id)

        async def restore(workspace: WorkspaceRoot) -> None:
            with _private_tempdir() as scratch:
                zip_path = scratch / _SOURCE_ZIP_NAME
                if not await storage.copy_to_path(to_revision_id, zip_path):
                    raise BuilderTurnError(f"revision {to_revision_id} content is missing")
                _clear_directory(workspace.root)
                safe_extract_zip(zip_path, workspace.root, self.limits)

        return await self._run_exclusive(
            solution_id,
            session_id=session_id,
            requested_by=requested_by,
            mutate=restore,
            summary=f"restored from revision {to_revision_id}",
            restored_from_revision_id=to_revision_id,
        )

    async def _run_exclusive(
        self,
        solution_id: UUID,
        *,
        session_id: UUID,
        requested_by: UUID | None,
        mutate: WorkspaceMutation,
        summary: str | None,
        restored_from_revision_id: UUID | None,
    ) -> SolutionBuilderTurn:
        """Take the write lock and run the turn, mapping contention to a conflict."""
        try:
            async with solution_write_lock(solution_id):
                return await self._run_locked(
                    solution_id,
                    session_id=session_id,
                    requested_by=requested_by,
                    mutate=mutate,
                    summary=summary,
                    restored_from_revision_id=restored_from_revision_id,
                )
        except SolutionWriteLockHeld as exc:
            raise BuilderTurnConflict(
                f"another turn or deploy is already writing Solution {solution_id}"
            ) from exc

    async def _run_locked(
        self,
        solution_id: UUID,
        *,
        session_id: UUID,
        requested_by: UUID | None,
        mutate: WorkspaceMutation,
        summary: str | None,
        restored_from_revision_id: UUID | None,
    ) -> SolutionBuilderTurn:
        """The turn body, run with the Solution's write lock already held."""
        project, conversation_id = await self._load_project(solution_id, session_id)
        base_revision_id = project.current_revision_id
        if base_revision_id is None:
            raise BuilderProjectMissing(f"Solution {solution_id} has no current revision")
        base = await self.db.get(SolutionSourceRevision, base_revision_id)
        if base is None:
            raise BuilderProjectMissing(f"revision {base_revision_id} is missing")

        turn = SolutionBuilderTurn(
            id=uuid4(),
            session_id=session_id,
            requested_by=requested_by,
            base_revision_id=base_revision_id,
            status=STATUS_RUNNING,
            started_at=_now(),
        )
        self.db.add(turn)
        await self.db.flush()

        try:
            output_revision_id = await self._materialize(
                solution_id,
                project=project,
                conversation_id=conversation_id,
                base=base,
                mutate=mutate,
                summary=summary,
                restored_from_revision_id=restored_from_revision_id,
                requested_by=requested_by,
            )
        except Exception as exc:
            turn.status = STATUS_FAILED
            turn.error = str(exc)
            turn.completed_at = _now()
            await self.db.flush()
            raise

        turn.output_revision_id = output_revision_id
        turn.status = STATUS_SUCCEEDED
        turn.completed_at = _now()
        await self.db.flush()
        return turn

    async def _materialize(
        self,
        solution_id: UUID,
        *,
        project: SolutionBuilderProject,
        conversation_id: UUID,
        base: SolutionSourceRevision,
        mutate: WorkspaceMutation,
        summary: str | None,
        restored_from_revision_id: UUID | None,
        requested_by: UUID | None,
    ) -> UUID:
        """Run the mutation and publish its result; return the output revision id.

        Returns the base revision's id unchanged when the mutation produced
        byte-identical content — a no-op turn adds no revision to history.
        """
        storage = self._storage(solution_id)
        revision_id = uuid4()

        with _private_tempdir() as scratch:
            base_zip = scratch / "base.zip"
            if not await storage.copy_to_path(base.id, base_zip):
                raise BuilderTurnError(f"revision {base.id} content is missing")

            workspace_dir = scratch / _WORKSPACE_DIRNAME
            workspace_dir.mkdir(mode=0o700)
            safe_extract_zip(base_zip, workspace_dir, self.limits)

            await mutate(WorkspaceRoot(workspace_dir, self.limits))

            errors = validate_workspace(workspace_dir)
            if errors:
                raise WorkspaceInvalid(errors)

            output_zip = scratch / _SOURCE_ZIP_NAME
            sha256 = zip_workspace(workspace_dir, output_zip)
            if sha256 == base.source_sha256:
                return base.id

            size_bytes = output_zip.stat().st_size
            await storage.write_from_path(revision_id, output_zip)

        self.db.add(
            SolutionSourceRevision(
                id=revision_id,
                solution_id=solution_id,
                parent_revision_id=base.id,
                restored_from_revision_id=restored_from_revision_id,
                conversation_id=conversation_id,
                created_by=requested_by,
                source_sha256=sha256,
                size_bytes=size_bytes,
                summary=summary,
            )
        )
        project.current_revision_id = revision_id
        await self.db.flush()
        return revision_id

    async def _load_project(
        self, solution_id: UUID, session_id: UUID
    ) -> tuple[SolutionBuilderProject, UUID]:
        """Load the project plus the conversation the turn's session belongs to."""
        project = await self.db.get(SolutionBuilderProject, solution_id)
        if project is None:
            raise BuilderProjectMissing(f"Solution {solution_id} has no builder project")

        session = await self.db.get(SolutionBuilderSession, session_id)
        if session is None or session.solution_id != solution_id:
            raise BuilderProjectMissing(
                f"builder session {session_id} does not belong to Solution {solution_id}"
            )
        return project, session.conversation_id
