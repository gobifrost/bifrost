"""Boundary/capability gates for Event administration routes."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.events import (
    CreateDeliveryRequest,
    DynamicValuesRequest,
    EmitEventRequest,
)
from src.routers import events
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    organization_id: UUID,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email="builder@example.com",
        organization_id=organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary
        or AuthorizationBoundary.organization(organization_id),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_name", "args"),
    [
        (
            "get_dynamic_values",
            (
                "adapter",
                DynamicValuesRequest(operation="list"),
                None,
            ),
        ),
        ("list_events", (uuid4(), None)),
        ("get_event", (uuid4(), None)),
        ("list_deliveries", (uuid4(), None)),
    ],
)
async def test_event_read_routes_require_events_read(
    route_name: str,
    args: tuple[object, ...],
) -> None:
    authorization = _authorization(organization_id=uuid4(), capabilities=set())
    route = getattr(events, route_name)

    with pytest.raises(HTTPException) as exc:
        if route_name == "get_dynamic_values":
            adapter_name, request, _ = args
            await route(adapter_name, request, authorization, SimpleNamespace())
        elif route_name == "list_events":
            source_id, _ = args
            await route(source_id, authorization, SimpleNamespace())
        else:
            event_id, _ = args
            await route(event_id, authorization, SimpleNamespace())

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: events.read"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_name", "args"),
    [
        (
            "create_delivery",
            (uuid4(), CreateDeliveryRequest(subscription_id=uuid4())),
        ),
        ("retry_delivery", (uuid4(),)),
    ],
)
async def test_event_mutation_routes_require_events_readwrite(
    route_name: str,
    args: tuple[object, ...],
) -> None:
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"events.read"},
    )
    route = getattr(events, route_name)

    with pytest.raises(HTTPException) as exc:
        if route_name == "create_delivery":
            event_id, request = args
            await route(event_id, request, authorization, SimpleNamespace())
        else:
            (delivery_id,) = args
            await route(delivery_id, SimpleNamespace(), authorization)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: events.readwrite"


@pytest.mark.asyncio
async def test_emit_topic_event_defaults_to_selected_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    emitted: dict[str, object] = {}
    authorization = _authorization(
        organization_id=organization_id,
        capabilities={"events.readwrite"},
    )

    async def _emit_event(topic, data, **kwargs):  # noqa: ANN001, ANN201
        emitted.update({"topic": topic, "data": data, **kwargs})
        return uuid4(), 2

    monkeypatch.setattr(events, "emit_event", _emit_event)

    response = await events.emit_topic_event(
        EmitEventRequest(topic="customer.created", data={"name": "Acme"}),
        SimpleNamespace(solution_id=None),
        authorization,
    )

    assert response.subscribers_notified == 2
    assert emitted["topic"] == "customer.created"
    assert emitted["organization_id"] == organization_id
    assert emitted["solution_id"] is None
    assert emitted["triggered_by"] == str(authorization.effective_actor.user_id)


@pytest.mark.asyncio
async def test_emit_topic_event_rejects_managed_collection_boundary() -> None:
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"events.readwrite"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    with pytest.raises(HTTPException) as exc:
        await events.emit_topic_event(
            EmitEventRequest(topic="customer.created", data={}),
            SimpleNamespace(solution_id=None),
            authorization,
        )

    assert exc.value.status_code == 409
    assert "Select one organization or Global" in exc.value.detail


@pytest.mark.asyncio
async def test_emit_topic_event_requires_explicit_boundary_for_scope_override() -> None:
    selected_org_id = uuid4()
    other_org_id = uuid4()
    authorization = _authorization(
        organization_id=selected_org_id,
        capabilities={"events.readwrite"},
    )

    with pytest.raises(HTTPException) as exc:
        await events.emit_topic_event(
            EmitEventRequest(
                topic="customer.created",
                data={},
                scope=str(other_org_id),
            ),
            SimpleNamespace(solution_id=None),
            authorization,
        )

    assert exc.value.status_code == 409
