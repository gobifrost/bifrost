"""Live authorization for isolated Solution application sessions.

Both launch-cookie renewal and bearer-token authentication call this module so
promotion, collaboration changes, application access changes, and user
deactivation revoke an existing runtime session immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.role_cache import get_user_roles
from src.core.exceptions import AccessDeniedError
from src.core.principal import UserPrincipal
from src.models.orm.applications import Application
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.repositories.applications import ApplicationRepository
from src.services.builder.private_solutions import load_accessible_private_solution
from src.services.solutions.access import VISIBILITY_PRIVATE, SolutionAction
from src.services.solutions.builder_authz import can_support_builds
from src.services.user_provisioning import get_user_scopes


@dataclass(frozen=True)
class RuntimeViewer:
    """The live viewer and exact application binding behind a runtime token."""

    user: User
    principal: UserPrincipal
    solution: Solution
    application: Application


async def load_runtime_viewer(
    db: AsyncSession,
    *,
    user_id: UUID,
    solution_id: UUID,
    app_id: UUID,
    organization_id: UUID,
) -> RuntimeViewer | None:
    """Load an authorized viewer for one exact isolated app deployment."""

    row = (
        await db.execute(
            select(Solution, Application, User)
            .join(Application, Application.solution_id == Solution.id)
            .join(User, User.id == user_id)
            .where(
                Solution.id == solution_id,
                Solution.status == "active",
                Application.id == app_id,
                Solution.organization_id.is_not_distinct_from(
                    Application.organization_id
                ),
                or_(
                    Application.organization_id == organization_id,
                    and_(
                        Application.organization_id.is_(None),
                        Solution.visibility != VISIBILITY_PRIVATE,
                    ),
                ),
                Application.runtime_mode == "isolated",
                User.is_active.is_(True),
            )
        )
    ).one_or_none()
    if row is None:
        return None

    solution, application, user = row
    role_ids, role_names = await get_user_roles(user.id, db)
    scopes = await get_user_scopes(db, user.id)
    organization = (
        await db.get(Organization, user.organization_id)
        if user.organization_id is not None
        else None
    )
    principal = UserPrincipal(
        user_id=user.id,
        email=user.email,
        name=user.name or user.email,
        organization_id=user.organization_id,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        is_external=user.is_external,
        is_provider_org=bool(organization and organization.is_provider),
        roles=role_names,
        scopes=scopes,
        role_ids=role_ids,
        role_names=role_names,
    )

    if solution.visibility == VISIBILITY_PRIVATE:
        accessible = await load_accessible_private_solution(
            db,
            solution_id=solution.id,
            action=SolutionAction.VIEW,
            actor_user_id=user.id,
            is_platform_admin=principal.is_platform_admin,
            is_external=principal.is_external,
            can_support=can_support_builds(principal),
        )
        if accessible is None:
            return None
    else:
        repository = ApplicationRepository(
            db,
            organization_id,
            user_id=user.id,
            is_superuser=user.is_superuser,
            is_external=user.is_external,
        )
        try:
            await repository.can_access(id=application.id)
        except AccessDeniedError:
            return None

    return RuntimeViewer(
        user=user,
        principal=principal,
        solution=solution,
        application=application,
    )
