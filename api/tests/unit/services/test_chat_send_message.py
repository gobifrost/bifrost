"""The non-streaming chat route runs its turn as a worker chat run."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete

from src.core.principal import UserPrincipal
from src.models.contracts.agents import ChatRequest, ChatResponse, ToolCall
from src.models.contracts.artifacts import ArtifactRef
from src.models.enums import MessageRole
from src.models.orm import Artifact, Conversation, Message, MessageAttachment
from src.services import chat_runs
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from src.services.chat_errors import CHAT_FAILURE_MESSAGE

pytestmark = pytest.mark.asyncio


def _caller(user: Any) -> Caller:
    return Caller(
        UserPrincipal(
            user_id=user.id,
            email=user.email,
            organization_id=user.organization_id,
            name=user.name or "",
            is_superuser=True,
        ),
        AuthorizationContext(
            user_id=user.id,
            home_organization_id=user.organization_id,
            base_role_id=user.base_role_id,
            is_external=False,
            base_permissions=frozenset(),
        ),
    )


async def _conversation(db_session, user) -> Conversation:
    conversation = Conversation(id=uuid4(), user_id=user.id, title="Turn")
    db_session.add(conversation)
    await db_session.commit()
    return conversation


def _submit(db_session, submitted: dict[str, Any], run_id: UUID):
    """``create_chat_run``: persists the user message and queues the run."""

    async def create_chat_run(db, caller, request, *, sync: bool):
        submitted.update(request=request, sync=sync)
        db.add(Message(
            id=uuid4(), conversation_id=request.conversation_id, role=MessageRole.USER,
            content=request.content, sequence=1,
        ))
        await db.commit()
        return SimpleNamespace(run_id=run_id, user_message=SimpleNamespace(sequence=1))

    return create_chat_run


async def test_a_message_runs_as_a_sync_chat_run_and_answers_with_its_persisted_reply(
    db_session, async_session_factory, seed_user, monkeypatch,
) -> None:
    conversation = await _conversation(db_session, seed_user)
    run_id, reply_id, artifact_id = uuid4(), uuid4(), uuid4()
    submitted: dict[str, Any] = {}

    async def wait_for_agent_run_result(waited: str, timeout: int) -> dict[str, Any]:
        """The worker's chat run persists its turn, then pushes its result."""
        assert waited == str(run_id)
        async with async_session_factory() as worker:
            tool_call = Message(
                id=uuid4(), conversation_id=conversation.id, role=MessageRole.TOOL_CALL,
                tool_call_id="call-1", tool_name="make_report", tool_input={"title": "Q3"},
                tool_state="completed", sequence=2,
            )
            artifact = Artifact(
                id=artifact_id, created_by_user_id=seed_user.id,
                s3_key=f"_artifacts/{artifact_id}/report.pdf", filename="report.pdf",
                content_type="application/pdf", size_bytes=10,
            )
            worker.add_all([tool_call, artifact])
            await worker.flush()
            worker.add_all([
                MessageAttachment(
                    artifact_id=artifact_id, message_id=tool_call.id,
                    conversation_id=conversation.id, s3_key=artifact.s3_key,
                    filename="report.pdf", content_type="application/pdf", size_bytes=10,
                ),
                Message(
                    id=reply_id, conversation_id=conversation.id, role=MessageRole.ASSISTANT,
                    content="Here is the report.", token_count_input=12,
                    token_count_output=5, duration_ms=800, sequence=3,
                ),
            ])
            await worker.commit()
        return {"status": "completed", "output": {"text": "Here is the report."}, "error": None}

    monkeypatch.setattr(chat_runs, "create_chat_run", _submit(db_session, submitted, run_id))
    monkeypatch.setattr(chat_runs, "wait_for_agent_run_result", wait_for_agent_run_result)
    try:
        response = await chat_runs.send_chat_message(
            db_session, _caller(seed_user), conversation.id,
            ChatRequest(message="Make the Q3 report"),
        )
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(delete(Artifact).where(Artifact.id == artifact_id))
            await cleanup.execute(delete(Conversation).where(Conversation.id == conversation.id))
            await cleanup.commit()

    assert submitted["sync"] is True
    assert (submitted["request"].conversation_id, submitted["request"].content) == (
        conversation.id, "Make the Q3 report",
    )
    assert response == ChatResponse(
        message_id=reply_id,
        content="Here is the report.",
        tool_calls=[ToolCall(id="call-1", name="make_report", arguments={"title": "Q3"})],
        artifacts=[ArtifactRef(
            id=str(artifact_id), filename="report.pdf", content_type="application/pdf",
            size_bytes=10,
        )],
        token_count_input=12,
        token_count_output=5,
        duration_ms=800,
    )


async def test_a_failed_chat_run_answers_500_with_public_copy(
    db_session, async_session_factory, seed_user, monkeypatch,
) -> None:
    conversation = await _conversation(db_session, seed_user)
    monkeypatch.setattr(chat_runs, "create_chat_run", _submit(db_session, {}, uuid4()))
    monkeypatch.setattr(chat_runs, "wait_for_agent_run_result", AsyncMock(return_value={
        "status": "failed", "output": None, "error": "Traceback: internal detail",
    }))
    try:
        with pytest.raises(HTTPException) as raised:
            await chat_runs.send_chat_message(
                db_session, _caller(seed_user), conversation.id, ChatRequest(message="Hello"),
            )
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(delete(Conversation).where(Conversation.id == conversation.id))
            await cleanup.commit()

    assert (raised.value.status_code, raised.value.detail) == (500, CHAT_FAILURE_MESSAGE)


async def test_another_users_conversation_is_not_found_and_runs_nothing(
    db_session, async_session_factory, seed_user, monkeypatch,
) -> None:
    conversation = await _conversation(db_session, seed_user)
    create_chat_run = AsyncMock()
    monkeypatch.setattr(chat_runs, "create_chat_run", create_chat_run)
    stranger = SimpleNamespace(
        id=uuid4(), email="stranger@example.com", organization_id=None, name="Stranger",
        base_role_id=None,
    )
    try:
        with pytest.raises(HTTPException) as raised:
            await chat_runs.send_chat_message(
                db_session, _caller(stranger), conversation.id, ChatRequest(message="Hello"),
            )
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(delete(Conversation).where(Conversation.id == conversation.id))
            await cleanup.commit()

    assert raised.value.status_code == 404
    create_chat_run.assert_not_awaited()
