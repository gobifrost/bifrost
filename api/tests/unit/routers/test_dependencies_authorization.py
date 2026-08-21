"""Capability and boundary authorization for dependency graphs."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.routers import dependencies
from src.services.authorization import AuthorizationBoundary, AuthorizationContext
from src.services.dependency_graph import DependencyGraph, GraphNode


def _authorization(
    *, capabilities: set[str], boundary: AuthorizationBoundary
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="builder@example.com",
        organization_id=boundary.organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary,
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


@pytest.mark.asyncio
async def test_dependency_graph_requires_root_domain_read_capability(
    monkeypatch,
) -> None:
    monkeypatch.setattr(dependencies, "DependencyGraphService", pytest.fail)

    with pytest.raises(HTTPException) as exc:
        await dependencies.get_dependency_graph(
            "agent",
            uuid4(),
            SimpleNamespace(),
            _authorization(
                capabilities={"apps.read"},
                boundary=AuthorizationBoundary.platform(),
            ),
            2,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: agents.read"


@pytest.mark.asyncio
async def test_dependency_graph_honors_exact_boundary_and_filters_cross_org_nodes(
    monkeypatch,
) -> None:
    root_id = uuid4()
    selected_org_id = uuid4()
    other_org_id = uuid4()
    graph = DependencyGraph(f"app:{root_id}")
    graph.add_node(GraphNode(f"app:{root_id}", "app", "Root", selected_org_id))
    graph.add_node(GraphNode("workflow:global", "workflow", "Global", None))
    graph.add_node(GraphNode("agent:other", "agent", "Other", other_org_id))
    graph.add_edge(f"app:{root_id}", "workflow:global", "uses")
    graph.add_edge(f"app:{root_id}", "agent:other", "used_by")

    class _Service:
        def __init__(self, db) -> None:  # noqa: ANN001
            pass

        async def build_graph(self, entity_type, entity_id, depth):  # noqa: ANN001, ANN201
            return graph

    monkeypatch.setattr(dependencies, "DependencyGraphService", _Service)

    response = await dependencies.get_dependency_graph(
        "app",
        root_id,
        SimpleNamespace(),
        _authorization(
            capabilities={"apps.read"},
            boundary=AuthorizationBoundary.organization(selected_org_id),
        ),
        2,
    )

    assert {node.id for node in response.nodes} == {
        f"app:{root_id}",
        "workflow:global",
    }
    assert [(edge.source, edge.target) for edge in response.edges] == [
        (f"app:{root_id}", "workflow:global")
    ]


@pytest.mark.asyncio
async def test_dependency_graph_hides_root_outside_selected_boundary(
    monkeypatch,
) -> None:
    root_id = uuid4()
    graph = DependencyGraph(f"workflow:{root_id}")
    graph.add_node(GraphNode(f"workflow:{root_id}", "workflow", "Root", uuid4()))

    class _Service:
        def __init__(self, db) -> None:  # noqa: ANN001
            pass

        async def build_graph(self, entity_type, entity_id, depth):  # noqa: ANN001, ANN201
            return graph

    monkeypatch.setattr(dependencies, "DependencyGraphService", _Service)

    with pytest.raises(HTTPException) as exc:
        await dependencies.get_dependency_graph(
            "workflow",
            root_id,
            SimpleNamespace(),
            _authorization(
                capabilities={"workflows.read"},
                boundary=AuthorizationBoundary.organization(uuid4()),
            ),
            2,
        )

    assert exc.value.status_code == 404
