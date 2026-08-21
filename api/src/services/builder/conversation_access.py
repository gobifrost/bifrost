"""Centralize access to Chat conversations owned by Builder sessions."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.orm.agents import Conversation
from src.models.orm.solution_builder import (
    SolutionUserGrant,
    SolutionBuilderSession,
)
from src.models.orm.solution_role_grants import SolutionRoleGrant
from src.models.orm.solutions import Solution
from src.services.authorization import (
    AuthorizationBoundary,
    resolve_authorization_context,
)
from src.services.solutions.access import SolutionAction, can_access_solution

BUILDER_CONVERSATION_CHANNEL = "builder"
BuilderConversationAction = Literal["view", "edit", "manage"]


async def can_access_conversation(
    db: AsyncSession,
    *,
    conversation: Conversation,
    principal: UserPrincipal,
    action: BuilderConversationAction,
) -> bool:
    """Apply ordinary ownership or the linked Builder Solution policy."""
    if conversation.channel != BUILDER_CONVERSATION_CHANNEL:
        return conversation.user_id == principal.user_id

    session = await db.scalar(
        select(SolutionBuilderSession).where(
            SolutionBuilderSession.conversation_id == conversation.id
        )
    )
    if session is None:
        return False
    solution = await db.get(Solution, session.solution_id)
    if solution is None:
        return False
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
    if action == "view":
        required = ("builder.read", "solutions.read")
    else:
        required = ("builder.execute", "solutions.readwrite")
    if not all(authorization.has_capability(capability) for capability in required):
        return False
    if solution.organization_id is None:
        repository_capability = (
            "repository.read" if action == "view" else "repository.readwrite"
        )
        if not authorization.has_capability(repository_capability):
            return False
    collaborator_access = await db.scalar(
        select(SolutionUserGrant.access).where(
            SolutionUserGrant.solution_id == solution.id,
            SolutionUserGrant.user_id == principal.user_id,
        )
    )
    role_grant_access = None
    if authorization.role_ids:
        role_grant_accesses = (
            (
                await db.execute(
                    select(SolutionRoleGrant.access).where(
                        SolutionRoleGrant.solution_id == solution.id,
                        SolutionRoleGrant.role_id.in_(authorization.role_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        role_grant_access = (
            "edit"
            if "edit" in role_grant_accesses
            else "view"
            if "view" in role_grant_accesses
            else None
        )
    solution_action = {
        "view": SolutionAction.VIEW,
        "edit": SolutionAction.BUILD,
        "manage": SolutionAction.MANAGE,
    }[action]
    return can_access_solution(
        action=solution_action,
        visibility=solution.visibility,
        owner_user_id=solution.owner_user_id,
        actor_user_id=principal.user_id,
        is_platform_admin=authorization.has_capability("platform.superuser"),
        is_external=principal.is_external,
        collaborator_access=collaborator_access,
        role_grant_access=role_grant_access,
        can_support=authorization.has_delegated_capability("builder.read"),
    )


__all__ = ["BUILDER_CONVERSATION_CHANNEL", "can_access_conversation"]
