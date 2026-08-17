"""Centralize access to Chat conversations owned by Builder sessions."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.orm.agents import Conversation
from src.models.orm.solution_builder import (
    SolutionBuilderCollaborator,
    SolutionBuilderSession,
)
from src.models.orm.solutions import Solution
from src.services.solutions.access import SolutionAction, can_access_solution
from src.services.solutions.builder_authz import can_support_builds

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
    collaborator_access = await db.scalar(
        select(SolutionBuilderCollaborator.access).where(
            SolutionBuilderCollaborator.solution_id == solution.id,
            SolutionBuilderCollaborator.user_id == principal.user_id,
        )
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
        is_platform_admin=principal.is_platform_admin,
        is_external=principal.is_external,
        collaborator_access=collaborator_access,
        can_support=can_support_builds(principal),
    )


__all__ = ["BUILDER_CONVERSATION_CHANNEL", "can_access_conversation"]
