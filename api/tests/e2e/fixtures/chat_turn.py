"""Run one chat turn in the test process, where its model can be patched.

The API runs chat turns on workers; a test that substitutes the model or the
LLM client with ``unittest.mock.patch`` drives the agent executor directly,
authorized the way the chat routes authorize a turn.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.run_lineage import principal_lineage
from src.core.database import get_session_factory
from src.core.principal import UserPrincipal
from src.models.orm import Agent, Conversation
from src.services.agent_executor import AgentExecutor
from src.services.authorization.agent_runtime import authorize_agent_execution
from src.services.authorization.enforce import load_caller


async def chat_turn_in_process(
    db: AsyncSession, principal: UserPrincipal, conversation_id: UUID, message: str
) -> str:
    """Run one turn of an agent conversation and return its reply."""
    conversation = (
        await db.execute(
            select(Conversation)
            .options(
                selectinload(Conversation.agent).selectinload(Agent.tools),
                selectinload(Conversation.agent).selectinload(Agent.delegated_agents),
            )
            .where(Conversation.id == conversation_id)
        )
    ).scalar_one()
    assert conversation.agent is not None, "an agent conversation"
    caller, conversation.agent = await authorize_agent_execution(
        db, await load_caller(db, principal), conversation.agent.id
    )
    lineage = await principal_lineage(db, caller.principal)
    reply = ""
    async for chunk in AgentExecutor(get_session_factory()).chat(
        agent=conversation.agent,
        conversation=conversation,
        user_message=message,
        stream=False,
        caller=caller,
        run_user_id=lineage.run_user_id if lineage else None,
    ):
        if chunk.type == "done":
            reply = chunk.content or ""
        elif chunk.type == "error":
            raise AssertionError(f"Chat turn failed: {chunk.error}")
    return reply
