"""Shared authorization for native and external Builder runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.role_cache import get_user_roles
from src.core.principal import UserPrincipal
from src.models.orm import Organization, User
from src.models.orm.solution_builder import SolutionBuilderProject
from src.models.orm.solutions import Solution
from src.services.authorization import (
    AuthorizationBoundary,
    AuthorizationContext,
    resolve_authorization_context,
)
from src.services.builder.private_solutions import load_accessible_private_solution
from src.services.solutions.access import SolutionAction
from src.services.user_provisioning import get_user_capabilities


class BuilderRuntimeForbidden(RuntimeError):
    """The requester cannot use the selected Builder project."""


@dataclass(frozen=True, slots=True)
class AuthorizedBuilderProject:
    solution: Solution
    project: SolutionBuilderProject
    principal: UserPrincipal
    authorization: AuthorizationContext


async def load_builder_principal(
    db: AsyncSession,
    user_id: UUID,
) -> UserPrincipal:
    """Reload one requester from durable identity and Role assignments."""

    user = await db.get(User, user_id)
    if user is None or not user.is_active or user.is_external:
        raise BuilderRuntimeForbidden("The Builder requester is no longer authorized")
    role_ids, role_names = await get_user_roles(user.id, db)
    capabilities = await get_user_capabilities(db, user.id)
    is_provider_org = False
    if user.organization_id is not None:
        is_provider_org = bool(
            await db.scalar(
                select(Organization.is_provider).where(
                    Organization.id == user.organization_id
                )
            )
        )
    return UserPrincipal(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or user.email,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        is_external=user.is_external,
        is_provider_org=is_provider_org,
        roles=role_names,
        scopes=capabilities,
        role_ids=role_ids,
        role_names=role_names,
    )


async def authorize_builder_project(
    db: AsyncSession,
    *,
    solution_id: UUID,
    requester_user_id: UUID,
    action: SolutionAction,
    required_capabilities: tuple[str, ...],
) -> AuthorizedBuilderProject:
    """Require capability, boundary, and resource admission for one project."""

    solution = await db.get(Solution, solution_id)
    project = await db.get(SolutionBuilderProject, solution_id)
    if solution is None or project is None:
        raise BuilderRuntimeForbidden("The Builder project is not available")

    principal = await load_builder_principal(db, requester_user_id)
    boundary = (
        AuthorizationBoundary.organization(solution.organization_id)
        if solution.organization_id is not None
        else AuthorizationBoundary.platform()
    )
    authorization = await resolve_authorization_context(
        db,
        requester=principal,
        selected_boundary=boundary,
    )
    effective_required = list(required_capabilities)
    if project.target_kind == "global_repo":
        effective_required.append(
            "repository.read"
            if action is SolutionAction.VIEW
            else "repository.readwrite"
        )
    if not all(
        authorization.has_capability(capability)
        for capability in dict.fromkeys(effective_required)
    ):
        raise BuilderRuntimeForbidden(
            "The Builder requester is no longer authorized"
        )
    if project.target_kind in {"global_repo", "organization"}:
        return AuthorizedBuilderProject(
            solution=solution,
            project=project,
            principal=principal,
            authorization=authorization,
        )
    loaded = await load_accessible_private_solution(
        db,
        solution_id=solution.id,
        action=action,
        actor_user_id=principal.user_id,
        is_platform_admin=authorization.has_capability("platform.superuser"),
        is_external=principal.is_external,
        can_support=authorization.has_delegated_capability("builder.read"),
        effective_role_ids=frozenset(authorization.role_ids),
    )
    if loaded is None:
        raise BuilderRuntimeForbidden("The Builder project is not available")
    return AuthorizedBuilderProject(
        solution=solution,
        project=project,
        principal=principal,
        authorization=authorization,
    )


__all__ = [
    "AuthorizedBuilderProject",
    "BuilderRuntimeForbidden",
    "authorize_builder_project",
    "load_builder_principal",
]
