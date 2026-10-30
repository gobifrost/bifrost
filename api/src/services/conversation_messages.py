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

    Lock order: a chat run's terminal write locks the run's row, then the
    conversation's. A cascading delete (of a user or a conversation) takes
    the conversation first and then sets its runs' ``conversation_id`` to
    NULL, the opposite order, so the two can deadlock; PostgreSQL then
    aborts one with deadlock_detected (40P01), which the terminal write's
    retry runs again. Raises ``NoResultFound`` if the conversation is gone.
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
