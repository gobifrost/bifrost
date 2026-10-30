"""Adding messages to a conversation in order."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.agents import Conversation, Message


async def add_conversation_message(session: AsyncSession, message: Message) -> Message:
    """Add ``message`` as its conversation's next message, in the caller's transaction.

    Every writer of conversation messages goes through here. The
    conversation row is locked before the next sequence is read, so
    concurrent writers take turns: each sees the message committed before
    it, and no two share or invert a sequence.

    Lock order: a transaction that also locks an agent run (a chat run's
    terminal write) locks the run's row first, then the conversation's.
    """
    conversation = (
        await session.execute(
            select(Conversation)
            .where(Conversation.id == message.conversation_id)
            .with_for_update(of=Conversation)
        )
    ).scalar_one()
    max_sequence = (
        await session.execute(
            select(func.coalesce(func.max(Message.sequence), 0)).where(
                Message.conversation_id == message.conversation_id
            )
        )
    ).scalar() or 0
    message.sequence = max_sequence + 1
    session.add(message)
    conversation.updated_at = datetime.now(timezone.utc)
    return message
