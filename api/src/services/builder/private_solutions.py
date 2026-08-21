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

from sqlalchemy import and_, case, exists, func, or_, select
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
    SolutionUserGrant,
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionBuilderTurn,
    SolutionSourceRevision,
)
from src.models.orm.solutions import Solution
from src.models.orm.solution_role_grants import SolutionRoleGrant
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, User
from src.services.builder.revision_storage import BUILDER_ROOT, REVISION_ARTIFACT_NAME
from src.services.builder.turns import BuilderTurnService
from src.services.file_storage import FileStorageService
from src.services.solutions.access import (
    VISIBILITY_PRIVATE,
    SolutionAction,
    can_access_solution,
    assigned_solution_role_grant_criterion,
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


class RoleGrantNotEligible(ValueError):
    """The requested Role cannot be used as a Solution resource grant."""


class RoleGrantNotFound(LookupError):
    """No matching Role grant exists on the Solution."""


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
class PrivateSolutionPage:
    """One bounded support-catalog page and its filtered total."""

    records: list[PrivateSolutionRecord]
    total: int


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


def _strongest_access(*values: str | None) -> Literal["view", "edit"] | None:
    if "edit" in values:
        return "edit"
    if "view" in values:
        return "view"
    return None


def to_dto(
    solution: Solution,
    project: SolutionBuilderProject,
    *,
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
        status=solution.status,
        target_kind=project.target_kind,
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
    target_kind: str = "solution",
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
        target_kind=target_kind,
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
    effective_role_ids: frozenset[UUID] = frozenset(),
    view: Literal["mine", "all"] = "mine",
    can_support: bool = False,
    organization_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    search: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    allowed_solution_organization_ids: frozenset[UUID] | None = None,
    allowed_workspace_organization_ids: frozenset[UUID] | None = None,
) -> PrivateSolutionPage:
    """List personal/shared work or the deliberate provider support catalog."""
    collaborator = SolutionUserGrant
    role_ids = tuple(effective_role_ids)
    role_access = (
        case(
            (
                exists(
                    select(SolutionRoleGrant.id).where(
                        SolutionRoleGrant.solution_id == Solution.id,
                        SolutionRoleGrant.role_id.in_(role_ids),
                        SolutionRoleGrant.access == "edit",
                    )
                ),
                "edit",
            ),
            (
                exists(
                    select(SolutionRoleGrant.id).where(
                        SolutionRoleGrant.solution_id == Solution.id,
                        SolutionRoleGrant.role_id.in_(role_ids),
                    )
                ),
                "view",
            ),
            else_=None,
        )
        if role_ids
        else case(
            (
                assigned_solution_role_grant_criterion(
                    actor_user_id=actor_user_id,
                    access="edit",
                ),
                "edit",
            ),
            (
                assigned_solution_role_grant_criterion(
                    actor_user_id=actor_user_id,
                ),
                "view",
            ),
            else_=None,
        )
    )
    query = (
        select(
            Solution,
            SolutionBuilderProject,
            User.name,
            User.email,
            Organization.name,
            collaborator.access,
            role_access,
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
        .where(SolutionBuilderProject.target_kind.in_(("solution", "organization")))
        .options(
            noload(Solution.connection_schema),
            noload(Solution.file_locations),
        )
    )
    if (
        allowed_solution_organization_ids is not None
        or allowed_workspace_organization_ids is not None
    ):
        solution_org_ids = tuple(allowed_solution_organization_ids or ())
        workspace_org_ids = tuple(allowed_workspace_organization_ids or ())
        target_filters = []
        if solution_org_ids:
            target_filters.append(
                and_(
                    SolutionBuilderProject.target_kind == "solution",
                    Solution.organization_id.in_(solution_org_ids),
                )
            )
        if workspace_org_ids:
            target_filters.append(
                and_(
                    SolutionBuilderProject.target_kind == "organization",
                    Solution.organization_id.in_(workspace_org_ids),
                )
            )
        if not target_filters:
            return PrivateSolutionPage(records=[], total=0)
        query = query.where(or_(*target_filters))
    if view == "all":
        if not can_support or is_external:
            return PrivateSolutionPage(records=[], total=0)
        if organization_id is not None:
            query = query.where(Solution.organization_id == organization_id)
        if owner_user_id is not None:
            query = query.where(Solution.owner_user_id == owner_user_id)
    else:
        query = query.where(
            visible_solutions_criterion(
                actor_user_id=actor_user_id,
                is_external=is_external,
                effective_role_ids=effective_role_ids,
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
    total = (
        await db.execute(query.with_only_columns(func.count()).order_by(None))
    ).scalar_one()
    page_query = query.order_by(Solution.updated_at.desc()).offset(offset)
    if limit is not None:
        page_query = page_query.limit(limit)
    rows = await db.execute(page_query)
    records = [
        PrivateSolutionRecord(
            solution=solution,
            project=project,
            owner_name=owner_name,
            owner_email=owner_email,
            organization_name=organization_name,
            collaborator_access=_strongest_access(
                collaborator_access,
                role_grant_access,
            ),
        )
        for (
            solution,
            project,
            owner_name,
            owner_email,
            organization_name,
            collaborator_access,
            role_grant_access,
        ) in rows.all()
    ]
    return PrivateSolutionPage(records=records, total=total)


async def load_accessible_private_solution(
    db: AsyncSession,
    *,
    solution_id: UUID,
    action: SolutionAction,
    actor_user_id: UUID,
    is_platform_admin: bool,
    is_external: bool,
    can_support: bool = False,
    effective_role_ids: frozenset[UUID] = frozenset(),
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
            .where(SolutionBuilderProject.target_kind.in_(("solution", "organization")))
            .options(
                noload(Solution.connection_schema), noload(Solution.file_locations)
            )
        )
    ).one_or_none()
    if row is None:
        return None

    solution, project = row
    if solution.visibility != VISIBILITY_PRIVATE:
        return None
    collaborator_access = await db.scalar(
        select(SolutionUserGrant.access).where(
            SolutionUserGrant.solution_id == solution_id,
            SolutionUserGrant.user_id == actor_user_id,
        )
    )
    role_grant_access: Literal["view", "edit"] | None = None
    if effective_role_ids:
        role_accesses = (
            (
                await db.execute(
                    select(SolutionRoleGrant.access).where(
                        SolutionRoleGrant.solution_id == solution_id,
                        SolutionRoleGrant.role_id.in_(effective_role_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        role_grant_access = _strongest_access(*role_accesses)
    if not can_access_solution(
        action=action,
        visibility=solution.visibility,
        owner_user_id=solution.owner_user_id,
        actor_user_id=actor_user_id,
        is_platform_admin=is_platform_admin,
        is_external=is_external,
        collaborator_access=collaborator_access,
        role_grant_access=role_grant_access,
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
    effective_role_ids: frozenset[UUID] = frozenset(),
) -> PrivateSolutionDTOContext:
    """Return display attribution and the caller's deliberate access mode."""
    row = (
        await db.execute(
            select(
                User.name,
                User.email,
                Organization.name,
                SolutionUserGrant.access,
            )
            .select_from(Solution)
            .outerjoin(User, User.id == Solution.owner_user_id)
            .outerjoin(Organization, Organization.id == Solution.organization_id)
            .outerjoin(
                SolutionUserGrant,
                (SolutionUserGrant.solution_id == Solution.id)
                & (SolutionUserGrant.user_id == actor_user_id),
            )
            .where(Solution.id == solution.id)
        )
    ).one()
    owner_name, owner_email, organization_name, raw_collaborator_access = row
    role_accesses: list[str] = []
    if effective_role_ids:
        role_accesses = list(
            (
                await db.execute(
                    select(SolutionRoleGrant.access).where(
                        SolutionRoleGrant.solution_id == solution.id,
                        SolutionRoleGrant.role_id.in_(effective_role_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
    collaborator_access = _strongest_access(
        raw_collaborator_access,
        *role_accesses,
    )
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
    collaborator: SolutionUserGrant,
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
        invited_by=collaborator.granted_by_user_id,
        created_at=collaborator.created_at,
        updated_at=collaborator.updated_at,
    )


async def list_collaborators(
    db: AsyncSession,
    *,
    solution_id: UUID,
) -> list[BuilderCollaboratorDTO]:
    rows = await db.execute(
        select(SolutionUserGrant, User)
        .join(User, User.id == SolutionUserGrant.user_id)
        .where(SolutionUserGrant.solution_id == solution_id)
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
        raise CollaboratorNotEligible(
            "External users cannot collaborate on Builder source"
        )
    if user.organization_id != solution.organization_id:
        raise CollaboratorNotEligible(
            "Collaborators must belong to the Solution's organization"
        )
    if user.id == solution.owner_user_id:
        raise CollaboratorNotEligible("The Solution owner already has full access")

    grant = await db.scalar(
        select(SolutionUserGrant).where(
            SolutionUserGrant.solution_id == solution.id,
            SolutionUserGrant.user_id == user.id,
        )
    )
    if grant is None:
        grant = SolutionUserGrant(
            solution_id=solution.id,
            user_id=user.id,
            access=access,
            granted_by_user_id=invited_by,
        )
        db.add(grant)
    else:
        grant.access = access
        grant.granted_by_user_id = invited_by
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
        select(SolutionUserGrant).where(
            SolutionUserGrant.solution_id == solution_id,
            SolutionUserGrant.user_id == collaborator_user_id,
        )
    )
    if grant is None:
        raise CollaboratorNotFound(str(collaborator_user_id))
    await db.delete(grant)
    await db.commit()


async def list_solution_role_grants(
    db: AsyncSession,
    *,
    solution_id: UUID,
) -> list[SolutionRoleGrant]:
    result = await db.execute(
        select(SolutionRoleGrant)
        .where(SolutionRoleGrant.solution_id == solution_id)
        .order_by(SolutionRoleGrant.created_at, SolutionRoleGrant.id)
    )
    return list(result.scalars().all())


async def upsert_solution_role_grant(
    db: AsyncSession,
    *,
    solution_id: UUID,
    role_id: UUID,
    access: Literal["view", "edit"],
    granted_by_user_id: UUID,
) -> SolutionRoleGrant:
    role = await db.get(Role, role_id)
    if role is None:
        raise RoleGrantNotEligible("Role not found")
    if not role.assignable_to_resources:
        raise RoleGrantNotEligible(
            "This platform capability Role cannot be assigned to a Solution"
        )
    grant = await db.scalar(
        select(SolutionRoleGrant).where(
            SolutionRoleGrant.solution_id == solution_id,
            SolutionRoleGrant.role_id == role_id,
        )
    )
    if grant is None:
        grant = SolutionRoleGrant(
            solution_id=solution_id,
            role_id=role_id,
            access=access,
            granted_by_user_id=granted_by_user_id,
        )
        db.add(grant)
    else:
        grant.access = access
        grant.granted_by_user_id = granted_by_user_id
        grant.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(grant)
    return grant


async def remove_solution_role_grant(
    db: AsyncSession,
    *,
    solution_id: UUID,
    role_id: UUID,
) -> None:
    grant = await db.scalar(
        select(SolutionRoleGrant).where(
            SolutionRoleGrant.solution_id == solution_id,
            SolutionRoleGrant.role_id == role_id,
        )
    )
    if grant is None:
        raise RoleGrantNotFound(str(role_id))
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
    if project.target_kind != "solution":
        raise ValueError("Organization targets cannot be published as apps")
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
    return BuilderSessionDTO(
        id=session.id,
        solution_id=session.solution_id,
        conversation_id=session.conversation_id,
        user_id=session.user_id,
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
    key = (
        f"{BUILDER_ROOT}/{solution_id}/revisions/{revision_id}/{REVISION_ARTIFACT_NAME}"
    )
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
