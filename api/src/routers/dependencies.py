"""
Dependencies Router

Endpoint for fetching entity dependency graphs for visualization.
Platform admin only - used by the Dependency Canvas feature.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from src.core.auth import CurrentSuperuser
from src.core.db_deps import DbSession
from src.services.dependency_graph import DependencyGraphService


router = APIRouter(prefix="/api/dependencies", tags=["Dependencies"])


# =============================================================================
# Response Models
# =============================================================================


class GraphNodeResponse(BaseModel):
    """Node in the dependency graph."""

    id: str = Field(..., description="Unique node ID in format 'type:uuid'")
    type: Literal["workflow", "form", "app", "agent"] = Field(
        ..., description="Entity type"
    )
    name: str = Field(..., description="Entity name")
    org_id: str | None = Field(None, description="Organization ID if scoped")


class GraphEdgeResponse(BaseModel):
    """Edge in the dependency graph."""

    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    relationship: str = Field(
        ..., description="Relationship type (uses, used_by)"
    )


class DependencyGraphResponse(BaseModel):
    """Complete dependency graph for visualization."""

    nodes: list[GraphNodeResponse] = Field(
        default_factory=list, description="All nodes in the graph"
    )
    edges: list[GraphEdgeResponse] = Field(
        default_factory=list, description="All edges in the graph"
    )
    root_id: str = Field(..., description="ID of the root node")


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/{entity_type}/{entity_id}", response_model=DependencyGraphResponse)
async def get_dependency_graph(
    entity_type: Literal["workflow", "form", "app", "agent"],
    entity_id: UUID,
    db: DbSession,
    user: CurrentSuperuser,
    depth: int = Query(
        default=2,
        ge=1,
        le=5,
        description="Maximum traversal depth (1-5)",
    ),
) -> DependencyGraphResponse:
    """
    Get dependency graph for an entity.

    Returns a graph of nodes and edges representing dependencies
    between workflows, forms, apps, and agents.

    - **entity_type**: Type of the root entity (workflow, form, app, agent)
    - **entity_id**: UUID of the root entity
    - **depth**: How many levels to traverse (default 2, max 5)

    Relationships:
    - Forms USE workflows (main, launch, data providers)
    - Apps USE workflows (page launch, data sources, component actions)
    - Agents USE workflows (via tools)
    - Workflows are USED BY forms, apps, and agents

    Platform admin only.
    """
    service = DependencyGraphService(db)
    graph = await service.build_graph(entity_type, entity_id, depth)

    # Check if root entity was found
    root_key = f"{entity_type}:{entity_id}"
    if root_key not in graph.nodes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{entity_type.title()} {entity_id} not found",
        )

    # Convert to response model
    return DependencyGraphResponse(
        nodes=[
            GraphNodeResponse(
                id=node.id,
                type=node.type,
                name=node.name,
                org_id=str(node.org_id) if node.org_id else None,
            )
            for node in graph.nodes.values()
        ],
        edges=[
            GraphEdgeResponse(
                source=edge.source,
                target=edge.target,
                relationship=edge.relationship,
            )
            for edge in graph.edges
        ],
        root_id=graph.root_id,
    )
