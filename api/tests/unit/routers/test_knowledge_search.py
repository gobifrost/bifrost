"""Authorization tests for the canonical knowledge-search operation."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.knowledge import KnowledgeSearchRequest
from src.repositories.knowledge import KnowledgeDocument
from src.routers.knowledge import search_knowledge
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _user(*, is_superuser: bool = False, is_external: bool = False) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="user@example.com",
        organization_id=None if is_superuser else uuid4(),
        is_superuser=is_superuser,
        is_external=is_external,
    )


def _authorization(
    user: UserPrincipal,
    *,
    capabilities: set[str] | None = None,
    organization_id=None,
) -> AuthorizationContext:
    return AuthorizationContext(
        requester=user,
        effective_actor=user,
        selected_boundary=(
            AuthorizationBoundary.organization(organization_id)
            if organization_id is not None
            else AuthorizationBoundary.platform()
        ),
        effective_capabilities=frozenset(capabilities or {"knowledge.read"}),
        grant_sources=(),
    )


def _document(namespace: str = "docs") -> KnowledgeDocument:
    return KnowledgeDocument(
        id=str(uuid4()),
        namespace=namespace,
        content="Reset the failed service.",
        metadata={"kind": "runbook"},
        score=0.9,
        organization_id=None,
        key="service-reset",
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_agent_bound_search_uses_global_agent_scope_for_regular_user() -> None:
    """Agent access, not direct global-scope privilege, is the trust boundary."""
    user = _user()
    agent_id = uuid4()
    agent = SimpleNamespace(
        id=agent_id,
        is_active=True,
        organization_id=None,
        knowledge_sources=["docs"],
    )
    search = AsyncMock(return_value=[_document()])

    with (
        patch(
            "src.routers.knowledge.AgentRepository.get_agent_with_access_check",
            new=AsyncMock(return_value=agent),
        ) as get_agent,
        patch("src.routers.knowledge.search_knowledge_documents", search),
    ):
        result = await search_knowledge(
            KnowledgeSearchRequest(
                query="restart service",
                namespace=["docs"],
                limit=25,
                agent_id=agent_id,
            ),
            AsyncMock(),
            user,
            _authorization(user, organization_id=None),
        )

    get_agent.assert_awaited_once_with(agent_id)
    search.assert_awaited_once()
    assert search.await_args.kwargs["organization_id"] is None
    assert search.await_args.kwargs["namespaces"] == ["docs"]
    assert search.await_args.kwargs["limit"] == 5
    assert result[0].namespace == "docs"


@pytest.mark.asyncio
async def test_agent_bound_search_rejects_namespace_outside_agent_grants() -> None:
    user = _user()
    agent_id = uuid4()
    agent = SimpleNamespace(
        id=agent_id,
        is_active=True,
        organization_id=uuid4(),
        knowledge_sources=["docs"],
    )
    with (
        patch(
            "src.routers.knowledge.AgentRepository.get_agent_with_access_check",
            new=AsyncMock(return_value=agent),
        ),
        patch(
            "src.routers.knowledge.search_knowledge_documents",
            new=AsyncMock(),
        ) as search,
        pytest.raises(HTTPException) as exc,
    ):
        await search_knowledge(
            KnowledgeSearchRequest(
                query="secret",
                namespace=["other"],
                agent_id=agent_id,
            ),
            AsyncMock(),
            user,
            _authorization(user, organization_id=agent.organization_id),
        )

    assert exc.value.status_code == 403
    search.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_bound_search_hides_inaccessible_agent() -> None:
    user = _user()
    agent_id = uuid4()
    with patch(
        "src.routers.knowledge.AgentRepository.get_agent_with_access_check",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            await search_knowledge(
                KnowledgeSearchRequest(query="query", agent_id=agent_id),
                AsyncMock(),
                user,
                _authorization(user, organization_id=user.organization_id),
            )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_agent_bound_search_binds_repo_to_selected_customer_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user()
    selected_org_id = uuid4()
    agent_id = uuid4()
    agent = SimpleNamespace(
        id=agent_id,
        is_active=True,
        organization_id=selected_org_id,
        knowledge_sources=["docs"],
    )
    constructed = []

    class _Repo:
        def __init__(
            self,
            *,
            session,
            org_id,
            user_id,
            bypass_resource_roles,
            is_external,
        ):
            constructed.append(
                {
                    "org_id": org_id,
                    "user_id": user_id,
                    "bypass_resource_roles": bypass_resource_roles,
                    "is_external": is_external,
                }
            )

        async def get_agent_with_access_check(self, requested_agent_id):
            assert requested_agent_id == agent_id
            return agent

    search = AsyncMock(return_value=[_document()])
    monkeypatch.setattr("src.routers.knowledge.AgentRepository", _Repo)
    monkeypatch.setattr("src.routers.knowledge.search_knowledge_documents", search)

    await search_knowledge(
        KnowledgeSearchRequest(query="restart service", agent_id=agent_id),
        AsyncMock(),
        user,
        _authorization(user, organization_id=selected_org_id),
    )

    assert constructed[0]["org_id"] == selected_org_id
    assert constructed[0]["bypass_resource_roles"] is False
    assert search.await_args.kwargs["organization_id"] == selected_org_id


@pytest.mark.asyncio
async def test_agent_bound_search_rejects_unselected_customer_agent() -> None:
    user = _user()
    selected_org_id = uuid4()
    other_org_id = uuid4()
    agent_id = uuid4()
    agent = SimpleNamespace(
        id=agent_id,
        is_active=True,
        organization_id=other_org_id,
        knowledge_sources=["docs"],
    )

    with (
        patch(
            "src.routers.knowledge.AgentRepository.get_agent_with_access_check",
            new=AsyncMock(return_value=agent),
        ),
        pytest.raises(HTTPException) as exc,
    ):
        await search_knowledge(
            KnowledgeSearchRequest(query="query", agent_id=agent_id),
            AsyncMock(),
            user,
            _authorization(user, organization_id=selected_org_id),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_direct_search_rejects_regular_user_global_scope() -> None:
    user = _user()
    with pytest.raises(HTTPException) as exc:
        await search_knowledge(
            KnowledgeSearchRequest(query="query", scope="global"),
            AsyncMock(),
            user,
            _authorization(user, organization_id=user.organization_id),
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_external_user_cannot_use_direct_or_agent_bound_search() -> None:
    user = _user(is_external=True)
    with pytest.raises(HTTPException) as exc:
        await search_knowledge(
            KnowledgeSearchRequest(query="query", agent_id=uuid4()),
            AsyncMock(),
            user,
            _authorization(user, organization_id=user.organization_id),
        )

    assert exc.value.status_code == 403
