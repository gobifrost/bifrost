"""Central access gate for Solution ownership and visibility.

This is the ONLY place that interprets private-Solution ownership and
visibility (2026-07-25 private-solution-builder spec, "One central parent
gate"). Repositories and routers call it; they must not reimplement
private-owner logic inline.

The functions are pure over already-loaded values so every rule is unit
testable without a database. Routers map a False decision on detail reads to
404 (private Solutions are invisible, not forbidden).
"""

from __future__ import annotations

from collections.abc import Collection
from enum import Enum
from uuid import UUID

from sqlalchemy import ColumnElement, and_, exists, or_, select

from src.models.orm.solution_builder import SolutionUserGrant
from src.models.orm.solution_role_grants import SolutionRoleGrant
from src.models.orm.organization_groups import OrganizationGroupMembership
from src.models.orm.organizations import Organization
from src.models.orm.role_assignments import RoleAssignment, RoleAssignmentBoundary
from src.models.orm.solutions import Solution

VISIBILITY_PRIVATE = "private"
VISIBILITY_SHARED = "shared"


class SolutionAction(str, Enum):
    VIEW = "view"
    EDIT = "edit"
    BUILD = "build"
    RUN = "run"
    MANAGE = "manage"
    PROMOTE = "promote"


def can_access_solution(
    *,
    action: SolutionAction,
    visibility: str,
    owner_user_id: UUID | None,
    actor_user_id: UUID | None,
    is_platform_admin: bool,
    is_external: bool,
    collaborator_access: str | None = None,
    role_grant_access: str | None = None,
    can_support: bool = False,
) -> bool:
    """Decide whether the actor may perform ``action`` on a Solution.

    Rules (spec "Private access invariant"):
    - PROMOTE is a platform-admin-only action on a private Solution. The owner
      may *request* promotion (a separate surface), never perform it.
    - Shared Solutions defer to the existing downstream authorization
      (superuser routes today, role/access-level checks at the entity layer).
    - Private Solutions admit their owner, explicitly invited collaborators,
      and provider support principals acting deliberately through Builder.
      Support access never widens ordinary catalog queries. External users are
      always denied.
    """
    if action is SolutionAction.PROMOTE:
        return (
            visibility == VISIBILITY_PRIVATE and is_platform_admin and not is_external
        )

    if visibility != VISIBILITY_PRIVATE:
        return True

    if is_external:
        return False
    if owner_user_id is not None and actor_user_id == owner_user_id:
        return True
    if is_platform_admin or can_support:
        return True
    if action is SolutionAction.MANAGE:
        return False
    if collaborator_access == "edit" or role_grant_access == "edit":
        return True
    return (
        collaborator_access == "view" or role_grant_access == "view"
    ) and action is SolutionAction.VIEW


def visible_solutions_criterion(
    *,
    actor_user_id: UUID | None,
    is_external: bool,
    effective_role_ids: Collection[UUID] = (),
) -> ColumnElement[bool]:
    """SQL predicate hiding other users' private Solutions from list queries.

    Applies to every principal, platform admins included: support privilege is
    intentionally absent here, so foreign private installs do not clutter
    ordinary catalogs. The dedicated Builder ``All`` view is the explicit
    support surface. Shared rows retain their existing scope filters.
    """
    if is_external or actor_user_id is None:
        return Solution.visibility == VISIBILITY_SHARED
    grants: list[ColumnElement[bool]] = [
        Solution.visibility == VISIBILITY_SHARED,
        Solution.owner_user_id == actor_user_id,
        exists(
            select(SolutionUserGrant.id)
            .where(
                SolutionUserGrant.solution_id == Solution.id,
                SolutionUserGrant.user_id == actor_user_id,
            )
            .correlate(Solution)
        ),
    ]
    cross_boundary_role_grant = assigned_solution_role_grant_criterion(
        actor_user_id=actor_user_id,
    )

    role_ids = tuple(effective_role_ids)
    if role_ids:
        grants.append(
            exists(
                select(SolutionRoleGrant.id)
                .where(
                    SolutionRoleGrant.solution_id == Solution.id,
                    SolutionRoleGrant.role_id.in_(role_ids),
                )
                .correlate(Solution)
            )
        )
    else:
        grants.append(cross_boundary_role_grant)
    return or_(
        *grants,
    )


def assigned_solution_role_grant_criterion(
    *,
    actor_user_id: UUID,
    access: str | None = None,
) -> ColumnElement[bool]:
    """Match a Solution Role grant through a boundary covering that Solution."""

    grant_filters: list[ColumnElement[bool]] = [
        SolutionRoleGrant.solution_id == Solution.id,
        RoleAssignment.user_id == actor_user_id,
    ]
    if access is not None:
        grant_filters.append(SolutionRoleGrant.access == access)
    return (
        exists(
            select(SolutionRoleGrant.id)
            .join(
                RoleAssignment,
                RoleAssignment.role_id == SolutionRoleGrant.role_id,
            )
            .join(
                RoleAssignmentBoundary,
                RoleAssignmentBoundary.role_assignment_id == RoleAssignment.id,
            )
            .where(
                *grant_filters,
                or_(
                    and_(
                        RoleAssignmentBoundary.boundary_kind == "organization",
                        RoleAssignmentBoundary.organization_id
                        == Solution.organization_id,
                    ),
                    and_(
                        RoleAssignmentBoundary.boundary_kind
                        == "organization_group",
                        exists(
                            select(OrganizationGroupMembership.organization_id).where(
                                OrganizationGroupMembership.organization_group_id
                                == RoleAssignmentBoundary.organization_group_id,
                                OrganizationGroupMembership.organization_id
                                == Solution.organization_id,
                            )
                        ),
                    ),
                    and_(
                        RoleAssignmentBoundary.boundary_kind
                        == "managed_organizations",
                        exists(
                            select(Organization.id).where(
                                Organization.id == Solution.organization_id,
                                Organization.is_provider.is_(False),
                            )
                        ),
                    ),
                    and_(
                        RoleAssignmentBoundary.boundary_kind == "platform",
                        Solution.organization_id.is_(None),
                    ),
                ),
            )
            .correlate(Solution)
        )
    )


def visible_solution_child_criterion(
    *,
    child_solution_id,
    actor_user_id: UUID | None,
    is_external: bool,
    effective_role_ids: Collection[UUID] = (),
) -> ColumnElement[bool]:
    """SQL predicate for an entity optionally owned by a Solution.

    Loose entities remain visible to their existing repository policy. A
    Solution-owned entity is visible only when its parent is visible to this
    actor. Keeping this predicate beside :func:`can_access_solution` prevents
    repository-specific interpretations of the private-parent invariant.
    """
    return or_(
        child_solution_id.is_(None),
        exists(
            select(Solution.id).where(
                Solution.id == child_solution_id,
                visible_solutions_criterion(
                    actor_user_id=actor_user_id,
                    is_external=is_external,
                    effective_role_ids=effective_role_ids,
                ),
            )
        ),
    )


async def is_private_solution_owner(
    db,
    *,
    solution_id: UUID | None,
    actor_user_id: UUID | None,
    is_external: bool,
) -> bool:
    """Whether this actor owns this exact, still-private Solution.

    This is the server-derived policy-bypass fact used by table/file runtime
    checks. It is recomputed from the parent row so promotion stops the bypass
    immediately and callers cannot assert it in a token or request body.
    """
    if solution_id is None or actor_user_id is None or is_external:
        return False
    result = await db.execute(
        select(Solution.id).where(
            Solution.id == solution_id,
            Solution.visibility == VISIBILITY_PRIVATE,
            Solution.owner_user_id == actor_user_id,
        )
    )
    return result.scalar_one_or_none() is not None
