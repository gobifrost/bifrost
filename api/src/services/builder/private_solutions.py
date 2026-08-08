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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from src.models.contracts.solution_builder import (
    BuilderCollaboratorDTO,
    BuilderSessionDTO,
    BuilderTurnDTO,
    PrivateSolutionDTO,
    SourceRevisionDTO,
)
from src.models.orm.agents import Conversation
from src.models.orm.solution_builder import (
    SolutionBuilderCollaborator,
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionBuilderTurn,
    SolutionSourceRevision,
)
from src.models.orm.solutions import Solution
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.services.builder.agent_identity import ensure_builder_agent
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


class CollaboratorNotEligible(ValueError):
    """The requested user cannot be granted access to this Solution."""


class CollaboratorNotFound(LookupError):
    """No matching explicit collaborator grant exists."""


@dataclass(frozen=True)
class PrivateSolutionRecord:
    """A list row enriched for the multi-tenant Builder catalog."""

    solution: Solution
    project: SolutionBuilderProject
    owner_name: str | None
    owner_email: str | None
    organization_name: str | None
    collaborator_access: Literal["view", "edit"] | None


@dataclass(frozen=True)
class PrivateSolutionDTOContext:
    owner_name: str | None
    owner_email: str | None
    organization_name: str | None
    caller_access: Literal["owner", "collaborator", "support"]
    collaborator_access: Literal["view", "edit"] | None


def _collaborator_access(value: str | None) -> Literal["view", "edit"] | None:
    if value == "view":
        return "view"
    if value == "edit":
        return "edit"
    return None


def to_dto(
    solution: Solution,
    project: SolutionBuilderProject,
    *,
    app_origin: str | None = None,
    owner_name: str | None = None,
    owner_email: str | None = None,
    organization_name: str | None = None,
    caller_access: Literal["owner", "collaborator", "support"] = "owner",
    collaborator_access: Literal["view", "edit"] | None = None,
) -> PrivateSolutionDTO:
    """Flatten an install row plus its builder project into the read shape."""
    return PrivateSolutionDTO(
        id=solution.id,
        slug=solution.slug,
        name=solution.name,
        visibility=solution.visibility,
        owner_user_id=solution.owner_user_id,
        owner_name=owner_name,
        owner_email=owner_email,
        organization_id=solution.organization_id,
        organization_name=organization_name,
        caller_access=caller_access,
        collaborator_access=collaborator_access,
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
    actor_user_id: UUID,
    is_external: bool,
    view: Literal["mine", "all"] = "mine",
    can_support: bool = False,
    organization_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    search: str | None = None,
) -> list[PrivateSolutionRecord]:
    """List personal/shared work or the deliberate provider support catalog."""
    collaborator = SolutionBuilderCollaborator
    query = (
        select(
            Solution,
            SolutionBuilderProject,
            User.name,
            User.email,
            Organization.name,
            collaborator.access,
        )
        .join(
            SolutionBuilderProject,
            SolutionBuilderProject.solution_id == Solution.id,
        )
        .outerjoin(User, User.id == Solution.owner_user_id)
        .outerjoin(Organization, Organization.id == Solution.organization_id)
        .outerjoin(
            collaborator,
            (collaborator.solution_id == Solution.id)
            & (collaborator.user_id == actor_user_id),
        )
        .where(Solution.visibility == VISIBILITY_PRIVATE)
        .options(
            noload(Solution.connection_schema),
            noload(Solution.file_locations),
        )
    )
    if view == "all":
        if not can_support or is_external:
            return []
        if organization_id is not None:
            query = query.where(Solution.organization_id == organization_id)
        if owner_user_id is not None:
            query = query.where(Solution.owner_user_id == owner_user_id)
    else:
        query = query.where(
            visible_solutions_criterion(
                actor_user_id=actor_user_id,
                is_external=is_external,
            )
        )
    normalized_search = (search or "").strip().lower()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        query = query.where(
            or_(
                func.lower(Solution.name).like(pattern),
                func.lower(Solution.slug).like(pattern),
                func.lower(func.coalesce(User.name, "")).like(pattern),
                func.lower(func.coalesce(User.email, "")).like(pattern),
                func.lower(func.coalesce(Organization.name, "")).like(pattern),
            )
        )
    rows = await db.execute(query.order_by(Solution.updated_at.desc()))
    return [
        PrivateSolutionRecord(
            solution=solution,
            project=project,
            owner_name=owner_name,
            owner_email=owner_email,
            organization_name=organization_name,
            collaborator_access=_collaborator_access(collaborator_access),
        )
        for (
            solution,
            project,
            owner_name,
            owner_email,
            organization_name,
            collaborator_access,
        ) in rows.all()
    ]


async def load_accessible_private_solution(
    db: AsyncSession,
    *,
    solution_id: UUID,
    action: SolutionAction,
    actor_user_id: UUID,
    is_platform_admin: bool,
    is_external: bool,
    can_support: bool = False,
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
    collaborator_access = await db.scalar(
        select(SolutionBuilderCollaborator.access).where(
            SolutionBuilderCollaborator.solution_id == solution_id,
            SolutionBuilderCollaborator.user_id == actor_user_id,
        )
    )
    if not can_access_solution(
        action=action,
        visibility=solution.visibility,
        owner_user_id=solution.owner_user_id,
        actor_user_id=actor_user_id,
        is_platform_admin=is_platform_admin,
        is_external=is_external,
        collaborator_access=collaborator_access,
        can_support=can_support,
    ):
        return None
    return solution, project


async def private_solution_dto_context(
    db: AsyncSession,
    *,
    solution: Solution,
    actor_user_id: UUID,
    can_support: bool,
) -> PrivateSolutionDTOContext:
    """Return display attribution and the caller's deliberate access mode."""
    row = (
        await db.execute(
            select(
                User.name,
                User.email,
                Organization.name,
                SolutionBuilderCollaborator.access,
            )
            .select_from(Solution)
            .outerjoin(User, User.id == Solution.owner_user_id)
            .outerjoin(Organization, Organization.id == Solution.organization_id)
            .outerjoin(
                SolutionBuilderCollaborator,
                (SolutionBuilderCollaborator.solution_id == Solution.id)
                & (SolutionBuilderCollaborator.user_id == actor_user_id),
            )
            .where(Solution.id == solution.id)
        )
    ).one()
    owner_name, owner_email, organization_name, raw_collaborator_access = row
    collaborator_access = _collaborator_access(raw_collaborator_access)
    caller_access: Literal["owner", "collaborator", "support"]
    if solution.owner_user_id == actor_user_id:
        caller_access = "owner"
    elif collaborator_access:
        caller_access = "collaborator"
    elif can_support:
        caller_access = "support"
    else:  # The central gate should make this state unreachable.
        caller_access = "collaborator"
    return PrivateSolutionDTOContext(
        owner_name=owner_name,
        owner_email=owner_email,
        organization_name=organization_name,
        caller_access=caller_access,
        collaborator_access=collaborator_access,
    )


def collaborator_to_dto(
    collaborator: SolutionBuilderCollaborator,
    user: User,
) -> BuilderCollaboratorDTO:
    access = _collaborator_access(collaborator.access)
    if access is None:
        raise ValueError("Invalid Builder collaborator access value")
    return BuilderCollaboratorDTO(
        id=collaborator.id,
        user_id=user.id,
        name=user.name,
        email=user.email,
        access=access,
        invited_by=collaborator.invited_by,
        created_at=collaborator.created_at,
        updated_at=collaborator.updated_at,
    )


async def list_collaborators(
    db: AsyncSession,
    *,
    solution_id: UUID,
) -> list[BuilderCollaboratorDTO]:
    rows = await db.execute(
        select(SolutionBuilderCollaborator, User)
        .join(User, User.id == SolutionBuilderCollaborator.user_id)
        .where(SolutionBuilderCollaborator.solution_id == solution_id)
        .order_by(func.lower(User.email))
    )
    return [collaborator_to_dto(grant, user) for grant, user in rows.all()]


async def upsert_collaborator(
    db: AsyncSession,
    *,
    solution: Solution,
    email: str,
    access: Literal["view", "edit"],
    invited_by: UUID,
) -> BuilderCollaboratorDTO:
    normalized_email = email.strip().lower()
    user = await db.scalar(
        select(User).where(func.lower(User.email) == normalized_email)
    )
    if user is None or not user.is_active:
        raise CollaboratorNotEligible("No active user has that email address")
    if user.is_external:
        raise CollaboratorNotEligible("External users cannot collaborate on Builder source")
    if user.organization_id != solution.organization_id:
        raise CollaboratorNotEligible(
            "Collaborators must belong to the Solution's organization"
        )
    if user.id == solution.owner_user_id:
        raise CollaboratorNotEligible("The Solution owner already has full access")

    grant = await db.scalar(
        select(SolutionBuilderCollaborator).where(
            SolutionBuilderCollaborator.solution_id == solution.id,
            SolutionBuilderCollaborator.user_id == user.id,
        )
    )
    if grant is None:
        grant = SolutionBuilderCollaborator(
            solution_id=solution.id,
            user_id=user.id,
            access=access,
            invited_by=invited_by,
        )
        db.add(grant)
    else:
        grant.access = access
        grant.invited_by = invited_by
        grant.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(grant)
    return collaborator_to_dto(grant, user)


async def remove_collaborator(
    db: AsyncSession,
    *,
    solution_id: UUID,
    collaborator_user_id: UUID,
) -> None:
    grant = await db.scalar(
        select(SolutionBuilderCollaborator).where(
            SolutionBuilderCollaborator.solution_id == solution_id,
            SolutionBuilderCollaborator.user_id == collaborator_user_id,
        )
    )
    if grant is None:
        raise CollaboratorNotFound(str(collaborator_user_id))
    await db.delete(grant)
    await db.commit()


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

    solution = await db.get(Solution, solution_id)
    if solution is None:
        raise ValueError(f"Solution {solution_id} does not exist")
    agent = await ensure_builder_agent(db, solution=solution)
    conversation.agent_id = agent.id

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
    db: AsyncSession, *, solution_id: UUID
) -> list[SolutionBuilderSession]:
    """Return the Solution team's sessions, newest first."""
    rows = await db.execute(
        select(SolutionBuilderSession)
        .where(SolutionBuilderSession.solution_id == solution_id)
        .order_by(SolutionBuilderSession.created_at.desc())
    )
    return list(rows.scalars().all())


def session_to_dto(session: SolutionBuilderSession) -> BuilderSessionDTO:
    from src.services.builder.scaffold import builder_agent_id

    return BuilderSessionDTO(
        id=session.id,
        solution_id=session.solution_id,
        conversation_id=session.conversation_id,
        user_id=session.user_id,
        builder_agent_id=builder_agent_id(session.solution_id),
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


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
