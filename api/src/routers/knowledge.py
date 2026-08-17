"""Canonical user-facing knowledge operations."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from shared.scope_resolver import (
    UNSET,
    RequestedScope,
    ScopeNotAllowed,
    resolve_effective_scope,
)
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.models.contracts.knowledge import KnowledgeSearchRequest, KnowledgeSearchResult
from src.repositories.agents import AgentRepository
from src.services.knowledge.search import search_knowledge_documents
from src.services.knowledge.search_budget import clamp_knowledge_result_limit
from src.services.operation_catalog import operation_route

router = APIRouter(prefix="/api/knowledge", tags=["Knowledge"])


def _requested_scope(scope: str | None) -> RequestedScope:
    if scope is None or scope == "":
        return UNSET
    if scope == "global":
        return None
    try:
        return UUID(scope)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"scope must be 'global', a UUID, or null; got {scope!r}",
        ) from exc


async def _agent_search_boundary(
    db: DbSession,
    user: CurrentActiveUser,
    request: KnowledgeSearchRequest,
) -> tuple[UUID | None, list[str], int]:
    """Derive scope and namespaces from an Agent the caller can access."""
    if request.scope is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="scope cannot be combined with agent_id",
        )

    agent_id = request.agent_id
    if agent_id is None:  # Narrowed by the caller; keeps this helper standalone.
        raise RuntimeError("Agent-bound search requires agent_id")

    repo = AgentRepository(
        session=db,
        org_id=user.organization_id,
        user_id=user.user_id,
        is_superuser=user.is_platform_admin,
        is_external=user.is_external,
    )
    agent = await repo.get_agent_with_access_check(agent_id)
    if agent is None or not agent.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    allowed_namespaces = list(agent.knowledge_sources or [])
    requested_namespaces = request.namespace or allowed_namespaces
    denied = sorted(set(requested_namespaces) - set(allowed_namespaces))
    if denied:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Agent is not granted access to knowledge namespace(s): "
                + ", ".join(denied)
            ),
        )

    return (
        agent.organization_id,
        requested_namespaces,
        clamp_knowledge_result_limit(request.limit),
    )


@router.post(
    "/search",
    response_model=list[KnowledgeSearchResult],
    summary="Hybrid-search knowledge documents",
    **operation_route("knowledge.search"),
)
async def search_knowledge(
    request: KnowledgeSearchRequest,
    db: DbSession,
    user: CurrentActiveUser,
) -> list[KnowledgeSearchResult]:
    """Search direct scope or an accessible Agent's trusted knowledge boundary."""
    if user.is_external:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="External users cannot access the knowledge store directly",
        )

    if request.agent_id is not None:
        organization_id, namespaces, limit = await _agent_search_boundary(
            db, user, request
        )
    else:
        try:
            organization_id = resolve_effective_scope(
                caller_org_id=user.organization_id,
                is_platform_admin=user.is_platform_admin,
                is_provider_org=user.is_provider_org,
                requested_scope=_requested_scope(request.scope),
            )
        except ScopeNotAllowed as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc
        namespaces = request.namespace or ["default"]
        limit = request.limit

    if not namespaces:
        return []

    try:
        results = await search_knowledge_documents(
            db,
            query=request.query,
            namespaces=namespaces,
            organization_id=organization_id,
            limit=limit,
            min_score=request.min_score,
            metadata_filter=request.metadata_filter,
            fallback=request.fallback,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return [
        KnowledgeSearchResult(
            id=doc.id,
            namespace=doc.namespace,
            content=doc.content,
            metadata=doc.metadata,
            score=doc.score,
            organization_id=doc.organization_id,
            key=doc.key,
            created_at=doc.created_at,
        )
        for doc in results
    ]


__all__ = ["router", "search_knowledge"]
