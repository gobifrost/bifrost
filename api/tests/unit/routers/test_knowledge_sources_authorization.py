"""Authorization boundaries for Knowledge administration."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.knowledge import KnowledgeDocumentCreate
from src.routers import knowledge_sources
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    capabilities: set[str],
    boundary: AuthorizationBoundary,
    home_organization_id: UUID | None = None,
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="knowledge-builder@example.com",
        organization_id=home_organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary,
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


@pytest.mark.asyncio
async def test_namespace_list_requires_knowledge_read() -> None:
    authorization = _authorization(
        capabilities=set(),
        boundary=AuthorizationBoundary.platform(),
    )

    with pytest.raises(HTTPException) as exc:
        await knowledge_sources.list_namespaces(
            SimpleNamespace(execute=pytest.fail),
            authorization,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: knowledge.read"


@pytest.mark.asyncio
async def test_create_requires_knowledge_readwrite() -> None:
    authorization = _authorization(
        capabilities={"knowledge.read"},
        boundary=AuthorizationBoundary.platform(),
    )

    with pytest.raises(HTTPException) as exc:
        await knowledge_sources.create_document(
            "runbooks",
            KnowledgeDocumentCreate(content="Restart the service."),
            SimpleNamespace(),
            authorization,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: knowledge.readwrite"


def test_managed_collection_cannot_mutate_knowledge() -> None:
    authorization = _authorization(
        capabilities={"knowledge.readwrite"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    with pytest.raises(HTTPException) as exc:
        knowledge_sources._selected_knowledge_organization(authorization)

    assert exc.value.status_code == 409


def test_organization_mutation_requires_matching_resource_boundary() -> None:
    selected_org_id = uuid4()
    authorization = _authorization(
        capabilities={"knowledge.readwrite"},
        boundary=AuthorizationBoundary.organization(selected_org_id),
        home_organization_id=selected_org_id,
    )

    knowledge_sources._require_knowledge_mutation(authorization, selected_org_id)

    with pytest.raises(HTTPException) as exc:
        knowledge_sources._require_knowledge_mutation(authorization, uuid4())

    assert exc.value.status_code == 409


def test_platform_and_organization_targets_are_derived_from_selected_context() -> None:
    organization_id = uuid4()
    platform = _authorization(
        capabilities={"knowledge.readwrite"},
        boundary=AuthorizationBoundary.platform(),
    )
    organization = _authorization(
        capabilities={"knowledge.readwrite"},
        boundary=AuthorizationBoundary.organization(organization_id),
        home_organization_id=organization_id,
    )

    assert knowledge_sources._selected_knowledge_organization(platform) is None
    assert (
        knowledge_sources._selected_knowledge_organization(organization)
        == organization_id
    )
