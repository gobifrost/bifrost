"""Branching primitives — ORM and history loader."""
import pytest
from sqlalchemy import delete, select

from src.models.enums import MessageRole
from src.models.orm import Conversation, Message
from src.services.agent_executor import AgentExecutor


async def _cleanup_conversation(session_factory, conversation_id):
    """Hard-delete a conversation + its messages.

    Tests in this file commit so a fresh session inside ``_load_active_branch``
    can see the data; that defeats the ``db_session`` rollback fixture, so the
    rows must be torn down explicitly. Cascade on Conversation.messages takes
    care of the message rows.
    """
    async with session_factory() as cleanup:
        await cleanup.execute(
            delete(Message).where(Message.conversation_id == conversation_id)
        )
        await cleanup.execute(
            delete(Conversation).where(Conversation.id == conversation_id)
        )
        await cleanup.commit()


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
    conv_id = conv.id

    try:
        executor = AgentExecutor(async_session_factory)
        path = await executor._load_active_branch(conv)
        assert [m.content for m in path] == ["hi", "hey there"]
    finally:
        await _cleanup_conversation(async_session_factory, conv_id)


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
    conv_id = conv.id

    try:
        executor = AgentExecutor(async_session_factory)
        path = await executor._load_active_branch(conv)
        assert [m.content for m in path] == ["hi", "hello"]
    finally:
        await _cleanup_conversation(async_session_factory, conv_id)


@pytest.mark.asyncio
async def test_load_active_branch_empty_conversation(
    db_session, seed_user, async_session_factory
):
    """Empty conversation returns an empty path."""
    conv = Conversation(user_id=seed_user.id, channel="chat")
    db_session.add(conv)
    await db_session.commit()
    conv_id = conv.id

    try:
        executor = AgentExecutor(async_session_factory)
        path = await executor._load_active_branch(conv)
        assert path == []
    finally:
        await _cleanup_conversation(async_session_factory, conv_id)


@pytest.mark.asyncio
async def test_load_active_branch_breaks_cycles(
    db_session, seed_user, async_session_factory
):
    """A corrupt parent-chain cycle does not infinite-loop the loader."""
    conv = Conversation(user_id=seed_user.id, channel="chat")
    db_session.add(conv)
    await db_session.flush()
    m1 = Message(conversation_id=conv.id, role=MessageRole.USER, content="a", sequence=0)
    db_session.add(m1)
    await db_session.flush()
    m2 = Message(
        conversation_id=conv.id, role=MessageRole.ASSISTANT, content="b",
        sequence=1, parent_message_id=m1.id,
    )
    db_session.add(m2)
    await db_session.flush()
    # Inject a cycle: m1 → m2 → m1.
    m1.parent_message_id = m2.id
    conv.active_leaf_message_id = m2.id
    await db_session.commit()
    conv_id = conv.id

    try:
        executor = AgentExecutor(async_session_factory)
        path = await executor._load_active_branch(conv)
        # The walker terminates at the cycle. Two messages in either order is
        # acceptable; the contract is "doesn't hang or crash."
        assert len(path) == 2
        assert {m.content for m in path} == {"a", "b"}
    finally:
        # Break the cycle before deleting so the DELETE doesn't fight the FK.
        async with async_session_factory() as cleanup:
            await cleanup.execute(
                delete(Message).where(Message.conversation_id == conv_id)
            )
            await cleanup.execute(
                delete(Conversation).where(Conversation.id == conv_id)
            )
            await cleanup.commit()


@pytest.mark.asyncio
async def test_save_message_appends_to_active_branch(
    db_session, seed_user, async_session_factory
):
    """_save_message links the new row to the current leaf and advances it."""
    conv = Conversation(user_id=seed_user.id, channel="chat")
    db_session.add(conv)
    await db_session.commit()
    conv_id = conv.id

    try:
        executor = AgentExecutor(async_session_factory)

        m1 = await executor._save_message(
            conversation_id=conv_id,
            role=MessageRole.USER,
            content="hi",
        )
        async with async_session_factory() as session:
            fresh_conv = await session.get(Conversation, conv_id)
            assert fresh_conv is not None
            assert fresh_conv.active_leaf_message_id == m1.id
        assert m1.parent_message_id is None

        m2 = await executor._save_message(
            conversation_id=conv_id,
            role=MessageRole.ASSISTANT,
            content="hello",
        )
        async with async_session_factory() as session:
            fresh_conv = await session.get(Conversation, conv_id)
            assert fresh_conv is not None
            assert fresh_conv.active_leaf_message_id == m2.id
        assert m2.parent_message_id == m1.id
    finally:
        await _cleanup_conversation(async_session_factory, conv_id)


@pytest.mark.asyncio
async def test_save_message_with_parent_override_creates_sibling(
    db_session, seed_user, async_session_factory
):
    """parent_message_id_override creates a sibling under the chosen parent."""
    conv = Conversation(user_id=seed_user.id, channel="chat")
    db_session.add(conv)
    await db_session.commit()
    conv_id = conv.id

    try:
        executor = AgentExecutor(async_session_factory)
        u1 = await executor._save_message(
            conversation_id=conv_id, role=MessageRole.USER, content="hi",
        )
        a1 = await executor._save_message(
            conversation_id=conv_id, role=MessageRole.ASSISTANT, content="hello",
        )

        # Edit-style: a sibling user message under u1's parent (NULL).
        u1_edit = await executor._save_message(
            conversation_id=conv_id,
            role=MessageRole.USER,
            content="hi (edited)",
            parent_message_id_override=u1.parent_message_id,
        )
        assert u1_edit.parent_message_id == u1.parent_message_id  # both NULL
        # The leaf advanced to the new sibling.
        async with async_session_factory() as session:
            fresh_conv = await session.get(Conversation, conv_id)
            assert fresh_conv is not None
            assert fresh_conv.active_leaf_message_id == u1_edit.id

        # Retry-style: a sibling assistant under u1 (a1's parent).
        a1_retry = await executor._save_message(
            conversation_id=conv_id,
            role=MessageRole.ASSISTANT,
            content="hello (retry)",
            parent_message_id_override=a1.parent_message_id,
        )
        assert a1_retry.parent_message_id == u1.id
        async with async_session_factory() as session:
            fresh_conv = await session.get(Conversation, conv_id)
            assert fresh_conv is not None
            assert fresh_conv.active_leaf_message_id == a1_retry.id
    finally:
        await _cleanup_conversation(async_session_factory, conv_id)


@pytest.mark.asyncio
async def test_save_message_first_message_has_null_parent(
    db_session, seed_user, async_session_factory
):
    """First message in an empty conversation has NULL parent."""
    conv = Conversation(user_id=seed_user.id, channel="chat")
    db_session.add(conv)
    await db_session.commit()
    conv_id = conv.id

    try:
        executor = AgentExecutor(async_session_factory)
        m = await executor._save_message(
            conversation_id=conv_id, role=MessageRole.USER, content="first",
        )
        assert m.parent_message_id is None
    finally:
        await _cleanup_conversation(async_session_factory, conv_id)


@pytest.mark.asyncio
async def test_system_prompt_includes_workspace_and_conversation_instructions(
    db_session, seed_user, seed_agent, async_session_factory
):
    """System prompt = agent prompt + workspace inst + conv inst."""
    from sqlalchemy import delete
    from src.models.enums import WorkspaceScope
    from src.models.orm import Workspace

    ws = Workspace(
        name="Test M3",
        scope=WorkspaceScope.PERSONAL,
        user_id=seed_user.id,
        organization_id=None,
        created_by=seed_user.email,
        instructions="Always respond in formal English.",
    )
    db_session.add(ws)
    await db_session.flush()

    conv = Conversation(
        user_id=seed_user.id,
        channel="chat",
        workspace_id=ws.id,
        agent_id=seed_agent.id,
        instructions="Cite the user's name in every reply.",
    )
    db_session.add(conv)
    await db_session.commit()
    conv_id = conv.id
    ws_id = ws.id

    try:
        executor = AgentExecutor(async_session_factory)
        async with async_session_factory() as s:
            fresh_conv = await s.get(Conversation, conv_id)
            fresh_agent = await s.get(type(seed_agent), seed_agent.id)
        messages = await executor._build_message_history(fresh_agent, fresh_conv)
        assert messages[0].role == "system"
        sysp = messages[0].content or ""
        ws_idx = sysp.index("Always respond in formal English.")
        conv_idx = sysp.index("Cite the user's name in every reply.")
        # Spec order: agent prompt → workspace inst → conversation inst.
        assert ws_idx < conv_idx
        # The agent's prompt must come first; both inst blocks come after it.
        agent_idx = sysp.index((seed_agent.system_prompt or "").strip())
        assert agent_idx < ws_idx
    finally:
        await _cleanup_conversation(async_session_factory, conv_id)
        async with async_session_factory() as s:
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


@pytest.mark.asyncio
async def test_system_prompt_appends_workspace_only_when_conv_inst_empty(
    db_session, seed_user, seed_agent, async_session_factory
):
    """Workspace inst alone (no conv inst) appends with one separator."""
    from sqlalchemy import delete
    from src.models.enums import WorkspaceScope
    from src.models.orm import Workspace

    ws = Workspace(
        name="Test M3 ws-only",
        scope=WorkspaceScope.PERSONAL,
        user_id=seed_user.id,
        organization_id=None,
        created_by=seed_user.email,
        instructions="Workspace-only instruction.",
    )
    db_session.add(ws)
    await db_session.flush()

    conv = Conversation(
        user_id=seed_user.id,
        channel="chat",
        workspace_id=ws.id,
        agent_id=seed_agent.id,
        # conversation.instructions intentionally None.
    )
    db_session.add(conv)
    await db_session.commit()
    conv_id = conv.id
    ws_id = ws.id

    try:
        executor = AgentExecutor(async_session_factory)
        async with async_session_factory() as s:
            fresh_conv = await s.get(Conversation, conv_id)
            fresh_agent = await s.get(type(seed_agent), seed_agent.id)
        messages = await executor._build_message_history(fresh_agent, fresh_conv)
        sysp = messages[0].content or ""
        assert "Workspace-only instruction." in sysp
        # No triple newline (would indicate an empty block sneaking in).
        assert "\n\n\n" not in sysp
        # Exactly one "\n\n" between the agent prompt and the workspace block.
        assert sysp.count("\n\n") == 1
    finally:
        await _cleanup_conversation(async_session_factory, conv_id)
        async with async_session_factory() as s:
            await s.execute(delete(Workspace).where(Workspace.id == ws_id))
            await s.commit()


@pytest.mark.asyncio
async def test_system_prompt_no_extras_equals_agent_prompt(
    db_session, seed_user, seed_agent, async_session_factory
):
    """No workspace, no conv inst → system prompt is exactly the agent prompt."""
    conv = Conversation(
        user_id=seed_user.id,
        channel="chat",
        agent_id=seed_agent.id,
    )
    db_session.add(conv)
    await db_session.commit()
    conv_id = conv.id

    try:
        executor = AgentExecutor(async_session_factory)
        async with async_session_factory() as s:
            fresh_conv = await s.get(Conversation, conv_id)
            fresh_agent = await s.get(type(seed_agent), seed_agent.id)
        messages = await executor._build_message_history(fresh_agent, fresh_conv)
        sysp = messages[0].content or ""
        # Without extras, the assembly takes the no-op path and returns the
        # agent prompt unchanged. Exact equality, no trailing whitespace shifts.
        from src.services.execution.agent_helpers import build_agent_system_prompt
        expected = build_agent_system_prompt(
            fresh_agent, execution_context={"mode": "chat"}
        )
        assert sysp == expected
    finally:
        await _cleanup_conversation(async_session_factory, conv_id)


@pytest.mark.asyncio
async def test_edit_user_message_rejects_non_user_message(
    db_session, seed_user, seed_agent, async_session_factory
):
    """edit_user_message refuses to edit an assistant message."""
    conv = Conversation(
        user_id=seed_user.id, channel="chat", agent_id=seed_agent.id,
    )
    db_session.add(conv)
    await db_session.commit()
    conv_id = conv.id

    try:
        executor = AgentExecutor(async_session_factory)
        u1 = await executor._save_message(
            conversation_id=conv_id, role=MessageRole.USER, content="hi",
        )
        a1 = await executor._save_message(
            conversation_id=conv_id, role=MessageRole.ASSISTANT, content="hello",
        )

        with pytest.raises(ValueError, match="user messages"):
            async for _ in executor.edit_user_message(
                agent=None,
                conversation=await _refetch_conv(async_session_factory, conv_id),
                target_message_id=a1.id,
                new_text="should not work",
            ):
                pass
    finally:
        await _cleanup_conversation(async_session_factory, conv_id)


@pytest.mark.asyncio
async def test_retry_assistant_message_rejects_non_assistant(
    db_session, seed_user, seed_agent, async_session_factory
):
    """retry_assistant_message refuses to retry a user message."""
    conv = Conversation(
        user_id=seed_user.id, channel="chat", agent_id=seed_agent.id,
    )
    db_session.add(conv)
    await db_session.commit()
    conv_id = conv.id

    try:
        executor = AgentExecutor(async_session_factory)
        u1 = await executor._save_message(
            conversation_id=conv_id, role=MessageRole.USER, content="hi",
        )

        with pytest.raises(ValueError, match="assistant messages"):
            async for _ in executor.retry_assistant_message(
                agent=None,
                conversation=await _refetch_conv(async_session_factory, conv_id),
                target_message_id=u1.id,
            ):
                pass
    finally:
        await _cleanup_conversation(async_session_factory, conv_id)


@pytest.mark.asyncio
async def test_retry_walks_leaf_back_to_user_parent(
    db_session, seed_user, seed_agent, async_session_factory
):
    """retry_assistant_message moves the leaf to the assistant's parent.

    This is the prerequisite step before the new assistant turn runs. The
    walk back is what makes the next _save_message create a sibling under
    the same user message (because Task 4's _save_message reads the leaf as
    the parent for the new row).
    """
    conv = Conversation(
        user_id=seed_user.id, channel="chat", agent_id=seed_agent.id,
    )
    db_session.add(conv)
    await db_session.commit()
    conv_id = conv.id

    try:
        executor = AgentExecutor(async_session_factory)
        u1 = await executor._save_message(
            conversation_id=conv_id, role=MessageRole.USER, content="hi",
        )
        a1 = await executor._save_message(
            conversation_id=conv_id, role=MessageRole.ASSISTANT, content="hello",
        )

        # Directly invoke the leaf-walk-back portion via the test helper.
        await executor._walk_leaf_to_assistant_parent(
            await _refetch_conv(async_session_factory, conv_id),
            a1.id,
        )
        async with async_session_factory() as s:
            fresh = await s.get(Conversation, conv_id)
            assert fresh is not None
            assert fresh.active_leaf_message_id == u1.id
    finally:
        await _cleanup_conversation(async_session_factory, conv_id)


async def _refetch_conv(async_session_factory, conv_id):
    """Helper: refetch a Conversation in a fresh session."""
    async with async_session_factory() as s:
        return await s.get(Conversation, conv_id)
