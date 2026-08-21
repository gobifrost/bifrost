from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.core.principal import UserPrincipal
from src.models.contracts.agents import ChatRequest, ChatStreamChunk
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _principal(*, organization_id=None, is_superuser: bool = False) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="user@example.com",
        organization_id=organization_id or uuid4(),
        name="User",
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
    )


def _authorization(
    user: UserPrincipal,
    boundary: AuthorizationBoundary,
    *capabilities: str,
) -> AuthorizationContext:
    return AuthorizationContext(
        requester=user,
        effective_actor=user,
        selected_boundary=boundary,
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def _conversation(*, agent=None):
    conversation = MagicMock()
    conversation.id = uuid4()
    conversation.user_id = uuid4()
    conversation.agent = agent
    conversation.agent_id = getattr(agent, "id", None) if agent else None
    conversation.channel = "chat"
    conversation.title = "Existing"
    return conversation


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _SessionFactory:
    def __init__(self, session):
        self._session = session

    @asynccontextmanager
    async def __call__(self):
        yield self._session


@pytest.mark.asyncio
async def test_rest_chat_passes_authorization_context_to_executor():
    from src.routers import chat

    user = _principal()
    authorization = _authorization(
        user,
        AuthorizationBoundary.organization(uuid4()),
    )
    conversation = _conversation()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_ScalarResult(conversation))
    captured = {}

    class Executor:
        def __init__(self, session_factory):
            del session_factory

        async def chat(self, **kwargs):
            captured.update(kwargs)
            yield ChatStreamChunk(
                type="done",
                content="ok",
                message_id=str(uuid4()),
            )

    with (
        patch(
            "src.routers.chat.can_access_conversation",
            new=AsyncMock(return_value=True),
        ),
        patch("src.routers.chat.AgentExecutor", Executor),
    ):
        response = await chat.send_message(
            conversation.id,
            ChatRequest(message="hello"),
            db,
            user,
            authorization,
        )

    assert response.content == "ok"
    assert captured["authorization_context"] is authorization


@pytest.mark.asyncio
async def test_websocket_chat_passes_authorization_context_to_executor():
    from src.routers import websocket as websocket_router

    user = _principal()
    authorization = _authorization(
        user,
        AuthorizationBoundary.organization(uuid4()),
    )
    conversation = _conversation()
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_ScalarResult(conversation))
    session_factory = _SessionFactory(session)
    sent: list[dict] = []
    websocket = MagicMock()
    websocket.send_json = AsyncMock(side_effect=lambda payload: sent.append(payload))
    captured = {}

    class Executor:
        def __init__(self, received_session_factory):
            assert received_session_factory is session_factory

        async def chat(self, **kwargs):
            captured.update(kwargs)
            yield ChatStreamChunk(
                type="done",
                content="ok",
                message_id=str(uuid4()),
            )

    with (
        patch(
            "src.core.database.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.services.agent_executor.AgentExecutor", Executor),
    ):
        await websocket_router._process_chat_message(
            websocket,
            user,
            str(conversation.id),
            "hello",
            authorization=authorization,
        )

    assert captured["authorization_context"] is authorization
    assert sent[-1]["type"] == "done"
    assert sent[-1]["conversation_id"] == str(conversation.id)


@pytest.mark.asyncio
async def test_websocket_chat_without_authorization_has_no_admin_agent_wildcard():
    from src.routers import websocket as websocket_router

    agent = MagicMock()
    agent.id = uuid4()
    user = _principal(is_superuser=True)
    conversation = _conversation(agent=agent)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_ScalarResult(conversation))
    session_factory = _SessionFactory(session)
    sent: list[dict] = []
    websocket = MagicMock()
    websocket.send_json = AsyncMock(side_effect=lambda payload: sent.append(payload))
    captured: dict[str, object] = {}

    class Repository:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        async def get_agent_with_access_check(self, _agent_id):
            return None

    with (
        patch(
            "src.core.database.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.repositories.agents.AgentRepository", Repository),
    ):
        await websocket_router._process_chat_message(
            websocket,
            user,
            str(conversation.id),
            "hello",
            authorization=None,
        )

    assert captured["bypass_resource_roles"] is False
    assert sent[-1]["type"] == "error"
    assert sent[-1]["error"] == "You don't have access to this agent"


@pytest.mark.asyncio
async def test_websocket_platform_superuser_is_wildcard():
    from src.routers import websocket as websocket_router

    user = _principal(is_superuser=True)
    customer_org_id = uuid4()
    authorization = _authorization(
        user,
        AuthorizationBoundary.platform(),
        "apps.read",
        "platform.superuser",
    )

    assert await websocket_router._authorization_can_access_org_resource(
        authorization,
        "apps.read",
        customer_org_id,
        allow_global_cascade=True,
    )
    assert await websocket_router._authorization_can_access_org_resource(
        authorization,
        "apps.read",
        None,
        allow_global_cascade=True,
    )


@pytest.mark.asyncio
async def test_websocket_exact_org_boundary_can_cascade_to_global_only_when_allowed():
    from src.routers import websocket as websocket_router

    user = _principal()
    selected_org_id = uuid4()
    other_org_id = uuid4()
    authorization = _authorization(
        user,
        AuthorizationBoundary.organization(selected_org_id),
        "apps.read",
    )

    assert await websocket_router._authorization_can_access_org_resource(
        authorization,
        "apps.read",
        selected_org_id,
        allow_global_cascade=True,
    )
    assert await websocket_router._authorization_can_access_org_resource(
        authorization,
        "apps.read",
        None,
        allow_global_cascade=True,
    )
    assert not await websocket_router._authorization_can_access_org_resource(
        authorization,
        "apps.read",
        other_org_id,
        allow_global_cascade=True,
    )
