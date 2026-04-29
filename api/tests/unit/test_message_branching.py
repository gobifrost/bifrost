"""Branching primitives — ORM and history loader."""
import pytest
from sqlalchemy import select

from src.models.enums import MessageRole
from src.models.orm import Conversation, Message


@pytest.mark.asyncio
async def test_message_has_parent_message_id_field(db_session, seed_user):
    """Message rows can be linked into a parent chain."""
    conv = Conversation(user_id=seed_user.id, channel="chat")
    db_session.add(conv)
    await db_session.flush()

    root = Message(
        conversation_id=conv.id,
        role=MessageRole.USER,
        content="hello",
        sequence=0,
        parent_message_id=None,
    )
    db_session.add(root)
    await db_session.flush()

    child = Message(
        conversation_id=conv.id,
        role=MessageRole.ASSISTANT,
        content="hi",
        sequence=1,
        parent_message_id=root.id,
    )
    db_session.add(child)
    await db_session.flush()

    fetched = (
        await db_session.execute(
            select(Message).where(Message.id == child.id)
        )
    ).scalar_one()
    assert fetched.parent_message_id == root.id


@pytest.mark.asyncio
async def test_conversation_has_active_leaf_and_instructions(db_session, seed_user):
    """Conversation tracks an active leaf and per-conversation instructions."""
    conv = Conversation(
        user_id=seed_user.id,
        channel="chat",
        instructions="Speak only in haiku.",
    )
    db_session.add(conv)
    await db_session.flush()

    msg = Message(
        conversation_id=conv.id,
        role=MessageRole.USER,
        content="hi",
        sequence=0,
    )
    db_session.add(msg)
    await db_session.flush()

    conv.active_leaf_message_id = msg.id
    await db_session.flush()

    fetched = (
        await db_session.execute(
            select(Conversation).where(Conversation.id == conv.id)
        )
    ).scalar_one()
    assert fetched.active_leaf_message_id == msg.id
    assert fetched.instructions == "Speak only in haiku."
