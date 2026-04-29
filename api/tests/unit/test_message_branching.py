"""Branching primitives — ORM and history loader."""
import pytest
from sqlalchemy import select

from src.models.enums import MessageRole
from src.models.orm import Conversation, Message
from src.services.agent_executor import AgentExecutor


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


@pytest.mark.asyncio
async def test_load_active_branch_returns_path_root_to_leaf(
    db_session, seed_user, async_session_factory
):
    """Active-branch loader returns messages from root to active leaf in order."""
    conv = Conversation(user_id=seed_user.id, channel="chat")
    db_session.add(conv)
    await db_session.flush()

    # Tree:
    #   m1 (user "hi")
    #   ├── m2 (assistant "hello")
    #   │     └── m3 (user "more")
    #   │           └── m4 (assistant "old reply")     <- old branch
    #   └── m2b (assistant "hey there")                <- new branch (retried)
    m1 = Message(conversation_id=conv.id, role=MessageRole.USER, content="hi", sequence=0)
    db_session.add(m1)
    await db_session.flush()
    m2 = Message(conversation_id=conv.id, role=MessageRole.ASSISTANT, content="hello",
                 sequence=1, parent_message_id=m1.id)
    db_session.add(m2)
    await db_session.flush()
    m3 = Message(conversation_id=conv.id, role=MessageRole.USER, content="more",
                 sequence=2, parent_message_id=m2.id)
    db_session.add(m3)
    await db_session.flush()
    m4 = Message(conversation_id=conv.id, role=MessageRole.ASSISTANT, content="old reply",
                 sequence=3, parent_message_id=m3.id)
    db_session.add(m4)
    await db_session.flush()
    m2b = Message(conversation_id=conv.id, role=MessageRole.ASSISTANT, content="hey there",
                  sequence=4, parent_message_id=m1.id)
    db_session.add(m2b)
    await db_session.flush()

    # Active leaf points at the new branch.
    conv.active_leaf_message_id = m2b.id
    await db_session.commit()

    executor = AgentExecutor(async_session_factory)
    path = await executor._load_active_branch(conv)

    assert [m.content for m in path] == ["hi", "hey there"]


@pytest.mark.asyncio
async def test_load_active_branch_falls_back_to_max_sequence(
    db_session, seed_user, async_session_factory
):
    """If active_leaf is NULL, fall back to MAX(sequence) for legacy rows."""
    conv = Conversation(user_id=seed_user.id, channel="chat")
    db_session.add(conv)
    await db_session.flush()
    m1 = Message(conversation_id=conv.id, role=MessageRole.USER, content="hi", sequence=0)
    db_session.add(m1)
    await db_session.flush()
    m2 = Message(conversation_id=conv.id, role=MessageRole.ASSISTANT, content="hello",
                 sequence=1, parent_message_id=m1.id)
    db_session.add(m2)
    # active_leaf_message_id intentionally left NULL to simulate legacy data.
    await db_session.commit()

    executor = AgentExecutor(async_session_factory)
    path = await executor._load_active_branch(conv)

    assert [m.content for m in path] == ["hi", "hello"]


@pytest.mark.asyncio
async def test_load_active_branch_empty_conversation(
    db_session, seed_user, async_session_factory
):
    """Empty conversation returns an empty path."""
    conv = Conversation(user_id=seed_user.id, channel="chat")
    db_session.add(conv)
    await db_session.commit()

    executor = AgentExecutor(async_session_factory)
    path = await executor._load_active_branch(conv)

    assert path == []
