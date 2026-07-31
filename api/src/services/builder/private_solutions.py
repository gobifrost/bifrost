"""Private-Solution lifecycle for the builder surface.

Owns the create/list/load/delete/promotion-request operations behind
``/api/builder/solutions``. The router stays a thin HTTP handler; every rule
about what a private Solution *is* (private visibility, owner stamp, sealed from
``_repo``) lives here, and every visibility decision defers to
``src.services.solutions.access`` (2026-07-25 private-solution-builder spec).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from src.models.contracts.solution_builder import (
    BuilderSessionDTO,
    BuilderTurnDTO,
    PrivateSolutionDTO,
    SourceRevisionDTO,
)
from src.models.orm.agents import Conversation
from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionBuilderTurn,
    SolutionSourceRevision,
)
from src.models.orm.solutions import Solution
from src.services.builder.revision_storage import BUILDER_ROOT, REVISION_ARTIFACT_NAME
from src.services.builder.turns import BuilderTurnService
from src.services.file_storage import FileStorageService
from src.services.solutions.access import (
    VISIBILITY_PRIVATE,
    SolutionAction,
    can_access_solution,
    visible_solutions_criterion,
)

PROMOTION_STATUS_REQUESTED = "requested"

BUILDER_CONVERSATION_CHANNEL = "builder"

_FILENAME_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class PrivateSolutionSlugTaken(Exception):
    """The caller already owns a private Solution at this slug."""


def to_dto(
    solution: Solution,
    project: SolutionBuilderProject,
    *,
    app_origin: str | None = None,
) -> PrivateSolutionDTO:
    """Flatten an install row plus its builder project into the read shape."""
    return PrivateSolutionDTO(
        id=solution.id,
        slug=solution.slug,
        name=solution.name,
        visibility=solution.visibility,
        owner_user_id=solution.owner_user_id,
        organization_id=solution.organization_id,
        app_origin=app_origin,
        status=solution.status,
        promotion_status=project.promotion_status,
        created_at=solution.created_at,
        updated_at=solution.updated_at,
    )


async def create_private_solution(
    db: AsyncSession,
    *,
    slug: str,
    name: str,
    owner_user_id: UUID,
    organization_id: UUID | None,
) -> tuple[Solution, SolutionBuilderProject]:
    """Create a private Solution owned by ``owner_user_id`` and its project row.

    ``global_repo_access`` is forced off: a private Solution is a self-contained
    world and must never import shared ``_repo/`` modules (spec, "Product
    model"). Slug collision is detected by the partial unique index rather than a
    pre-check, so two concurrent creates cannot both win.

    The builder project is opened by :meth:`BuilderTurnService.create_project`,
    which scaffolds revision 0 and points ``current_revision_id`` at it. A
    Solution therefore always has a current revision from the moment it exists,
    which is what lets the very first turn (or undo) run against real content
    instead of an empty project.

    Order matters: the Solution row is flushed *before* ``create_project`` runs,
    so a duplicate slug raises here and answers 409 without having written a
    scaffold zip to object storage. Everything after that flush shares one
    transaction — if scaffolding fails the whole create rolls back, rather than
    leaving a Solution that has no revision.
    """
    solution = Solution(
        slug=slug,
        name=name,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        visibility=VISIBILITY_PRIVATE,
        global_repo_access=False,
        status="active",
    )
    db.add(solution)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise PrivateSolutionSlugTaken(slug) from exc

    # Scaffolds revision 0 and adds the project row pointing at it. The
    # conversation is null at create time; sessions (and their conversations)
    # are opened later via POST /{solution_id}/sessions.
    await BuilderTurnService(db).create_project(
        solution.id,
        slug=slug,
        name=name,
        conversation_id=None,
        user_id=owner_user_id,
    )
    project = (
        await db.execute(
            select(SolutionBuilderProject).where(
                SolutionBuilderProject.solution_id == solution.id
            )
        )
    ).scalar_one()
    await db.commit()
    # Server defaults (timestamps, promotion_status) are only populated on the
    # instances after a round-trip; the session keeps objects alive past commit
    # (expire_on_commit=False), so an explicit refresh is what fills them in.
    await db.refresh(solution)
    await db.refresh(project)
    return solution, project


async def list_private_solutions(
    db: AsyncSession,
    *,
    owner_user_id: UUID,
    is_external: bool,
) -> list[tuple[Solution, SolutionBuilderProject]]:
    """Return only the caller's own private Solutions, newest first.

    The owner filter alone is sufficient; ``visible_solutions_criterion`` is
    applied as well so this list can never widen if the ownership rule changes
    in one place only.
    """
    rows = await db.execute(
        select(Solution, SolutionBuilderProject)
        .join(SolutionBuilderProject, SolutionBuilderProject.solution_id == Solution.id)
        .where(
            Solution.owner_user_id == owner_user_id,
            Solution.visibility == VISIBILITY_PRIVATE,
            visible_solutions_criterion(
                actor_user_id=owner_user_id, is_external=is_external
            ),
        )
        .options(noload(Solution.connection_schema), noload(Solution.file_locations))
        .order_by(Solution.created_at.desc())
    )
    return [(solution, project) for solution, project in rows.all()]


async def load_accessible_private_solution(
    db: AsyncSession,
    *,
    solution_id: UUID,
    action: SolutionAction,
    actor_user_id: UUID,
    is_platform_admin: bool,
    is_external: bool,
) -> tuple[Solution, SolutionBuilderProject] | None:
    """Load a private Solution the actor may act on, or ``None``.

    ``None`` covers "does not exist", "is not a private builder Solution", and
    "belongs to somebody else" alike — the router maps all three to 404, because
    a private Solution must be invisible rather than forbidden (spec, "Private
    access invariant").
    """
    row = (
        await db.execute(
            select(Solution, SolutionBuilderProject)
            .join(
                SolutionBuilderProject,
                SolutionBuilderProject.solution_id == Solution.id,
            )
            .where(Solution.id == solution_id)
            .options(noload(Solution.connection_schema), noload(Solution.file_locations))
        )
    ).one_or_none()
    if row is None:
        return None

    solution, project = row
    if solution.visibility != VISIBILITY_PRIVATE:
        return None
    if not can_access_solution(
        action=action,
        visibility=solution.visibility,
        owner_user_id=solution.owner_user_id,
        actor_user_id=actor_user_id,
        is_platform_admin=is_platform_admin,
        is_external=is_external,
    ):
        return None
    return solution, project


async def delete_private_solution(db: AsyncSession, solution: Solution) -> None:
    """Hard-delete the install row; owned rows go via ``ondelete=CASCADE`` FKs.

    The row was loaded with ``noload`` on its child relationships so the ORM
    ``delete-orphan`` cascade has nothing to mark deleted — the Solutions
    read-only backstop rejects ORM deletes of solution-managed children, and the
    DB-level cascade removes them correctly instead.
    """
    await db.delete(solution)
    await db.commit()


async def request_promotion(
    db: AsyncSession,
    project: SolutionBuilderProject,
    *,
    requested_by: UUID,
) -> SolutionBuilderProject:
    """Flag the project as awaiting administrator promotion review.

    The owner may only *request*; performing the promotion is a platform-admin
    action on a separate surface (spec, "Private access invariant").
    """
    if (
        project.deployed_revision_id is None
        or project.current_revision_id != project.deployed_revision_id
    ):
        raise ValueError(
            "The current revision must have a successful preview deploy before promotion"
        )
    project.promotion_status = PROMOTION_STATUS_REQUESTED
    project.promotion_revision_id = project.deployed_revision_id
    project.promotion_requested_by = requested_by
    project.promotion_requested_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(project)
    return project


async def create_builder_session(
    db: AsyncSession,
    *,
    solution_id: UUID,
    user_id: UUID,
    title: str | None,
) -> SolutionBuilderSession:
    """Open a new chat session against a Solution.

    A session is a Conversation plus the typed link that says which Solution the
    conversation builds. The Conversation is stamped with the ``builder``
    channel so ordinary chat surfaces, which filter on channel, never list it
    (spec, "Multiple chats" — builder state stays separate from ordinary
    conversations).
    """
    conversation = Conversation(
        user_id=user_id,
        channel=BUILDER_CONVERSATION_CHANNEL,
        title=title,
    )
    db.add(conversation)
    await db.flush()

    session = SolutionBuilderSession(
        solution_id=solution_id,
        conversation_id=conversation.id,
        user_id=user_id,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def list_builder_sessions(
    db: AsyncSession, *, solution_id: UUID, user_id: UUID
) -> list[SolutionBuilderSession]:
    """Return the caller's own sessions on this Solution, newest first.

    Filtering on ``user_id`` as well as ``solution_id`` is redundant today
    (a private Solution has exactly one possible session owner) but keeps the
    query honest if a Solution ever gains collaborators.
    """
    rows = await db.execute(
        select(SolutionBuilderSession)
        .where(
            SolutionBuilderSession.solution_id == solution_id,
            SolutionBuilderSession.user_id == user_id,
        )
        .order_by(SolutionBuilderSession.created_at.desc())
    )
    return list(rows.scalars().all())


def session_to_dto(session: SolutionBuilderSession) -> BuilderSessionDTO:
    return BuilderSessionDTO.model_validate(session)


async def list_source_revisions(
    db: AsyncSession, *, solution_id: UUID, project: SolutionBuilderProject
) -> list[SourceRevisionDTO]:
    """Return the Solution's revision history, newest first.

    ``is_current`` / ``is_deployed`` are computed against the project pointers
    passed in rather than re-read here, so the flags are consistent with the
    project row the caller already authorized against.
    """
    rows = await db.execute(
        select(SolutionSourceRevision)
        .where(SolutionSourceRevision.solution_id == solution_id)
        .order_by(SolutionSourceRevision.created_at.desc())
    )
    return [
        SourceRevisionDTO(
            id=revision.id,
            parent_revision_id=revision.parent_revision_id,
            restored_from_revision_id=revision.restored_from_revision_id,
            source_sha256=revision.source_sha256,
            size_bytes=revision.size_bytes,
            summary=revision.summary,
            created_at=revision.created_at,
            created_by=revision.created_by,
            is_current=revision.id == project.current_revision_id,
            is_deployed=revision.id == project.deployed_revision_id,
        )
        for revision in rows.scalars().all()
    ]


async def load_revision_for_solution(
    db: AsyncSession, *, solution_id: UUID, revision_id: UUID
) -> SolutionSourceRevision | None:
    """Load a revision only if it belongs to ``solution_id``.

    Returning ``None`` for a revision that exists under a *different* Solution
    is the point: the router maps it to 404, so a revision id guessed from
    another Solution never confirms its own existence.
    """
    revision = await db.get(SolutionSourceRevision, revision_id)
    if revision is None or revision.solution_id != solution_id:
        return None
    return revision


def revision_download_filename(slug: str, revision_id: UUID) -> str:
    """``{slug}-{revision_short}.zip``, safe to put in a Content-Disposition."""
    safe_slug = _FILENAME_UNSAFE_RE.sub("-", slug).strip(".-_") or "solution"
    return f"{safe_slug}-{revision_id.hex[:8]}.zip"


def iter_revision_chunks(
    db: AsyncSession, *, solution_id: UUID, revision_id: UUID
) -> AsyncIterator[bytes]:
    """Stream a revision's zip in bounded chunks.

    Revision zips carry a whole Solution workspace and are unbounded in
    principle, so the download path never materializes one as a single bytes
    blob (spec security invariant 14: artifact APIs stream).
    """
    key = f"{BUILDER_ROOT}/{solution_id}/revisions/{revision_id}/{REVISION_ARTIFACT_NAME}"
    return FileStorageService(db).iter_raw_s3_chunks(key)


async def list_builder_turns(
    db: AsyncSession, *, solution_id: UUID
) -> list[BuilderTurnDTO]:
    """Return every turn across the Solution's sessions, newest first.

    Turns hang off sessions rather than the Solution, so this joins through
    ``solution_builder_sessions``; a Solution's history is the union of its
    chats, not one chat's private view (spec, "Multiple chats").
    """
    rows = await db.execute(
        select(SolutionBuilderTurn)
        .join(
            SolutionBuilderSession,
            SolutionBuilderSession.id == SolutionBuilderTurn.session_id,
        )
        .where(SolutionBuilderSession.solution_id == solution_id)
        .order_by(SolutionBuilderTurn.created_at.desc())
    )
    return [BuilderTurnDTO.model_validate(turn) for turn in rows.scalars().all()]
