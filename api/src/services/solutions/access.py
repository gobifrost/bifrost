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

from enum import Enum
from uuid import UUID

from sqlalchemy import ColumnElement, exists, or_, select

from src.models.orm.solutions import Solution

VISIBILITY_PRIVATE = "private"
VISIBILITY_SHARED = "shared"


class SolutionAction(str, Enum):
    VIEW = "view"
    EDIT = "edit"
    BUILD = "build"
    RUN = "run"
    PROMOTE = "promote"


def can_access_solution(
    *,
    action: SolutionAction,
    visibility: str,
    owner_user_id: UUID | None,
    actor_user_id: UUID | None,
    is_platform_admin: bool,
    is_external: bool,
) -> bool:
    """Decide whether the actor may perform ``action`` on a Solution.

    Rules (spec "Private access invariant"):
    - PROMOTE is a platform-admin-only action on a private Solution. The owner
      may *request* promotion (a separate surface), never perform it.
    - Shared Solutions defer to the existing downstream authorization
      (superuser routes today, role/access-level checks at the entity layer).
    - Private Solutions admit only their owner — including against platform
      admins, whose access is limited to the promotion-review and break-glass
      surfaces. External users are always denied.
    """
    if action is SolutionAction.PROMOTE:
        return (
            visibility == VISIBILITY_PRIVATE and is_platform_admin and not is_external
        )

    if visibility != VISIBILITY_PRIVATE:
        return True

    if is_external:
        return False
    return owner_user_id is not None and actor_user_id == owner_user_id


def visible_solutions_criterion(
    *,
    actor_user_id: UUID | None,
    is_external: bool,
) -> ColumnElement[bool]:
    """SQL predicate hiding other users' private Solutions from list queries.

    Applies to every principal, platform admins included: private installs do
    not appear in the ordinary administrator catalog (spec invariant). Shared
    rows remain subject to the caller's existing scope filters.
    """
    if is_external or actor_user_id is None:
        return Solution.visibility == VISIBILITY_SHARED
    return or_(
        Solution.visibility == VISIBILITY_SHARED,
        Solution.owner_user_id == actor_user_id,
    )


def visible_solution_child_criterion(
    *,
    child_solution_id,
    actor_user_id: UUID | None,
    is_external: bool,
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
