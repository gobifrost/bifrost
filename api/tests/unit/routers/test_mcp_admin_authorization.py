"""Authorization boundaries for MCP administration routes."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.contracts.mcp import MCPConfigRequest, MCPGatewayExecuteRequest
from src.models.orm.external_mcp import MCPConnection, MCPServer
from src.models.orm.organizations import Organization
from src.routers import mcp, mcp_connections
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
    email: str = "integrations-operator@example.com",
    organization_id=None,
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email=email,
        name="Integrations Operator",
        organization_id=organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary or AuthorizationBoundary.platform(),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def test_gateway_service_uses_selected_platform_boundary_without_admin_wildcard():
    user = UserPrincipal(
        user_id=uuid4(),
        email="admin@example.com",
        name="Admin",
        organization_id=uuid4(),
        is_superuser=True,
    )
    service = mcp._gateway_service(
        user,
        _authorization(
            capabilities={"platform.superuser"},
            boundary=AuthorizationBoundary.platform(),
            organization_id=user.organization_id,
        ),
    )

    assert service.context.org_id is None
    assert service.context.is_platform_admin is False
    assert service.context.authorization_boundary == "platform"
    assert service.context.resource_gate_bypass is True


def test_gateway_service_uses_exact_selected_organization_boundary():
    home_org_id = uuid4()
    selected_org_id = uuid4()
    user = UserPrincipal(
        user_id=uuid4(),
        email="operator@example.com",
        name="Operator",
        organization_id=home_org_id,
    )
    service = mcp._gateway_service(
        user,
        _authorization(
            capabilities={"agents.read"},
            boundary=AuthorizationBoundary.organization(selected_org_id),
            organization_id=home_org_id,
        ),
    )

    assert service.context.org_id == selected_org_id
    assert service.context.is_platform_admin is False
    assert (
        service.context.authorization_boundary
        == f"organization:{selected_org_id}"
    )
    assert service.context.resource_gate_bypass is False


def test_gateway_service_rejects_managed_organizations_boundary():
    user = UserPrincipal(
        user_id=uuid4(),
        email="operator@example.com",
        name="Operator",
        organization_id=uuid4(),
    )

    with pytest.raises(HTTPException) as exc_info:
        mcp._gateway_service(
            user,
            _authorization(
                capabilities={"agents.read"},
                boundary=AuthorizationBoundary.managed_organizations(),
                organization_id=user.organization_id,
            ),
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_canonical_agent_execute_route_binds_agent_id_from_path(monkeypatch):
    user = UserPrincipal(
        user_id=uuid4(),
        email="operator@example.com",
        name="Operator",
        organization_id=uuid4(),
    )
    authorization = _authorization(
        capabilities={"agents.read"},
        boundary=AuthorizationBoundary.organization(user.organization_id),
        organization_id=user.organization_id,
    )
    service = MagicMock()
    service.execute_agent_tool = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(mcp, "_require_mcp_enabled", AsyncMock())
    monkeypatch.setattr(mcp, "_gateway_service", lambda *_args: service)
    agent_id = str(uuid4())
    tool_ref = str(uuid4())

    result = await mcp.execute_gateway_agent_tool(
        agent_id,
        tool_ref,
        MCPGatewayExecuteRequest(arguments={"message": "hi"}),
        user,
        MagicMock(),
        authorization,
    )

    assert result == {"ok": True}
    service.execute_agent_tool.assert_awaited_once_with(
        agent_id,
        tool_ref,
        {"message": "hi"},
        async_execution=None,
    )


@pytest.mark.asyncio
async def test_canonical_builder_execute_route_binds_session_id_from_path(monkeypatch):
    user = UserPrincipal(
        user_id=uuid4(),
        email="operator@example.com",
        name="Operator",
        organization_id=uuid4(),
    )
    authorization = _authorization(
        capabilities={"agents.read"},
        boundary=AuthorizationBoundary.organization(user.organization_id),
        organization_id=user.organization_id,
    )
    service = MagicMock()
    service.execute_builder_tool = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(mcp, "_require_mcp_enabled", AsyncMock())
    monkeypatch.setattr(mcp, "_gateway_service", lambda *_args: service)
    builder_session_id = str(uuid4())
    tool_ref = str(uuid4())

    result = await mcp.execute_gateway_builder_session_tool(
        builder_session_id,
        tool_ref,
        MCPGatewayExecuteRequest(arguments={"path": "README.md"}),
        user,
        MagicMock(),
        authorization,
    )

    assert result == {"ok": True}
    service.execute_builder_tool.assert_awaited_once_with(
        builder_session_id,
        tool_ref,
        {"path": "README.md"},
        async_execution=None,
    )


async def _organization(
    db: AsyncSession,
    *,
    is_provider: bool = False,
) -> Organization:
    organization = Organization(
        id=uuid4(),
        name=f"mcp-authz-org-{uuid4().hex[:8]}",
        is_active=True,
        is_provider=is_provider,
        created_by="test@example.com",
    )
    db.add(organization)
    await db.flush()
    return organization


async def _server(db: AsyncSession) -> MCPServer:
    server = MCPServer(
        id=uuid4(),
        name=f"mcp-authz-server-{uuid4().hex[:8]}",
        server_url="https://vendor.example.com/mcp",
        is_active=True,
    )
    db.add(server)
    await db.flush()
    return server


async def _connection(
    db: AsyncSession,
    organization: Organization | None,
    *,
    server: MCPServer | None = None,
) -> MCPConnection:
    server = server or await _server(db)
    connection = MCPConnection(
        id=uuid4(),
        server_id=server.id,
        organization_id=organization.id if organization else None,
        client_id="client-id",
        encrypted_client_secret="encrypted",
    )
    db.add(connection)
    await db.flush()
    return connection


def test_mcp_config_requires_explicit_platform_boundary() -> None:
    authorization = _authorization(
        capabilities={"integrations.read"},
        boundary=AuthorizationBoundary.organization(uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        mcp._require_mcp_config(authorization)

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        "select platform"
    )


def test_mcp_connection_managed_collection_cannot_mutate() -> None:
    authorization = _authorization(
        capabilities={"integrations.readwrite"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    with pytest.raises(HTTPException) as exc:
        mcp_connections._require_mcp_connection_mutation_boundary(
            authorization,
            uuid4(),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "Select one organization or Platform before changing MCP connections"
    )


def test_mcp_connection_org_mutation_requires_exact_org_boundary() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        capabilities={"integrations.readwrite"},
        boundary=AuthorizationBoundary.organization(uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        mcp_connections._require_mcp_connection_mutation_boundary(
            authorization,
            organization_id,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        f"select organization:{organization_id}"
    )


def test_mcp_connection_platform_boundary_cannot_mutate_org_connections() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        capabilities={"integrations.readwrite"},
        boundary=AuthorizationBoundary.platform(),
    )

    with pytest.raises(HTTPException) as exc:
        mcp_connections._require_mcp_connection_mutation_boundary(
            authorization,
            organization_id,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        f"select organization:{organization_id}"
    )


def test_mcp_connection_exact_org_boundary_can_mutate_org_connections() -> None:
    organization_id = uuid4()
    authorization = _authorization(
        capabilities={"integrations.readwrite"},
        boundary=AuthorizationBoundary.organization(organization_id),
    )

    mcp_connections._require_mcp_connection_mutation_boundary(
        authorization,
        organization_id,
    )


def test_mcp_connection_platform_admin_wildcard_can_mutate_org_connections() -> None:
    authorization = _authorization(
        capabilities={"platform.superuser"},
        boundary=AuthorizationBoundary.platform(),
    )

    mcp_connections._require_mcp_connection_mutation_boundary(
        authorization,
        uuid4(),
    )


@pytest.mark.asyncio
async def test_platform_integrations_read_cannot_fetch_org_connection(
    db_session: AsyncSession,
) -> None:
    organization = await _organization(db_session)
    connection = await _connection(db_session, organization)
    ctx = type("Ctx", (), {"db": db_session})()

    with pytest.raises(HTTPException) as exc:
        await mcp_connections._get_admin_connection_or_404(
            ctx,
            connection.id,
            _authorization(
                capabilities={"integrations.read"},
                boundary=AuthorizationBoundary.platform(),
            ),
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_exact_org_integrations_read_can_fetch_org_connection(
    db_session: AsyncSession,
) -> None:
    organization = await _organization(db_session)
    connection = await _connection(db_session, organization)
    ctx = type("Ctx", (), {"db": db_session})()

    loaded = await mcp_connections._get_admin_connection_or_404(
        ctx,
        connection.id,
        _authorization(
            capabilities={"integrations.read"},
            boundary=AuthorizationBoundary.organization(organization.id),
            organization_id=organization.id,
        ),
    )

    assert loaded.id == connection.id


@pytest.mark.asyncio
async def test_platform_admin_wildcard_can_fetch_org_connection(
    db_session: AsyncSession,
) -> None:
    organization = await _organization(db_session)
    connection = await _connection(db_session, organization)
    ctx = type("Ctx", (), {"db": db_session})()

    loaded = await mcp_connections._get_admin_connection_or_404(
        ctx,
        connection.id,
        _authorization(
            capabilities={"platform.superuser"},
            boundary=AuthorizationBoundary.platform(),
        ),
    )

    assert loaded.id == connection.id


@pytest.mark.asyncio
async def test_platform_integrations_read_lists_no_org_connections(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization = await _organization(db_session)
    org_connection = await _connection(db_session, organization)
    ctx = type("Ctx", (), {"db": db_session})()

    monkeypatch.setattr(
        mcp_connections.MCPConnectionSummary,
        "model_validate",
        staticmethod(lambda connection: connection.id),
    )

    result = await mcp_connections.list_mcp_connections(
        ctx,
        _authorization(
            capabilities={"integrations.read"},
            boundary=AuthorizationBoundary.platform(),
        ),
    )

    assert result == []
    assert org_connection.id not in result


@pytest.mark.asyncio
async def test_platform_integrations_read_cannot_scope_filter_to_org(
    db_session: AsyncSession,
) -> None:
    ctx = type("Ctx", (), {"db": db_session})()
    organization_id = uuid4()

    with pytest.raises(HTTPException) as exc:
        await mcp_connections.list_mcp_connections(
            ctx,
            _authorization(
                capabilities={"integrations.read"},
                boundary=AuthorizationBoundary.platform(),
            ),
            scope=str(organization_id),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "Only Platform Admin can use scope filters from Platform context"
    )


@pytest.mark.asyncio
async def test_platform_admin_wildcard_lists_all_and_legacy_scope_filter(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization = await _organization(db_session)
    other_organization = await _organization(db_session)
    org_connection = await _connection(db_session, organization)
    other_connection = await _connection(db_session, other_organization)
    ctx = type("Ctx", (), {"db": db_session})()

    monkeypatch.setattr(
        mcp_connections.MCPConnectionSummary,
        "model_validate",
        staticmethod(lambda connection: connection.id),
    )
    authorization = _authorization(
        capabilities={"platform.superuser"},
        boundary=AuthorizationBoundary.platform(),
    )

    all_result = await mcp_connections.list_mcp_connections(ctx, authorization)
    scoped_result = await mcp_connections.list_mcp_connections(
        ctx,
        authorization,
        scope=str(organization.id),
    )

    assert set(all_result) == {
        org_connection.id,
        other_connection.id,
    }
    assert scoped_result == [org_connection.id]


@pytest.mark.asyncio
async def test_exact_org_lists_only_that_org(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization = await _organization(db_session)
    other_organization = await _organization(db_session)
    org_connection = await _connection(db_session, organization)
    other_connection = await _connection(db_session, other_organization)
    ctx = type("Ctx", (), {"db": db_session})()

    monkeypatch.setattr(
        mcp_connections.MCPConnectionSummary,
        "model_validate",
        staticmethod(lambda connection: connection.id),
    )

    result = await mcp_connections.list_mcp_connections(
        ctx,
        _authorization(
            capabilities={"integrations.read"},
            boundary=AuthorizationBoundary.organization(organization.id),
            organization_id=organization.id,
        ),
    )

    assert result == [org_connection.id]
    assert other_connection.id not in result


@pytest.mark.asyncio
async def test_managed_lists_only_customer_connections(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer = await _organization(db_session)
    provider = await _organization(db_session, is_provider=True)
    customer_connection = await _connection(db_session, customer)
    provider_connection = await _connection(db_session, provider)
    ctx = type("Ctx", (), {"db": db_session})()

    monkeypatch.setattr(
        mcp_connections.MCPConnectionSummary,
        "model_validate",
        staticmethod(lambda connection: connection.id),
    )

    result = await mcp_connections.list_mcp_connections(
        ctx,
        _authorization(
            capabilities={"integrations.read"},
            boundary=AuthorizationBoundary.managed_organizations(),
        ),
    )

    assert result == [customer_connection.id]
    assert provider_connection.id not in result


@pytest.mark.asyncio
async def test_update_mcp_config_uses_effective_actor_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated_by_values: list[str] = []
    audit_events: list[tuple[str, dict[str, object]]] = []
    cache_invalidations = 0

    class _Config:
        enabled = False
        allowed_tool_ids = ["tool-a"]
        blocked_tool_ids = ["tool-b"]
        is_configured = True
        configured_at = datetime.now(timezone.utc)
        configured_by = "operator@example.com"

    class _Service:
        def __init__(self, db):  # noqa: ANN001
            self.db = db

        async def save_config(
            self,
            *,
            enabled: bool,
            allowed_tool_ids: list[str] | None,
            blocked_tool_ids: list[str],
            updated_by: str,
        ) -> _Config:
            assert enabled is False
            assert allowed_tool_ids == ["tool-a"]
            assert blocked_tool_ids == ["tool-b"]
            updated_by_values.append(updated_by)
            return _Config()

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audit_events.append((action, kwargs))

    def _invalidate_cache() -> None:
        nonlocal cache_invalidations
        cache_invalidations += 1

    monkeypatch.setattr(mcp, "MCPConfigService", _Service)
    monkeypatch.setattr(mcp, "emit_audit", _emit_audit)
    monkeypatch.setattr(mcp, "invalidate_mcp_config_cache", _invalidate_cache)

    result = await mcp.update_mcp_config(
        _authorization(
            capabilities={"integrations.readwrite"},
            email="operator@example.com",
        ),
        object(),
        MCPConfigRequest(
            enabled=False,
            allowed_tool_ids=["tool-a"],
            blocked_tool_ids=["tool-b"],
        ),
    )

    assert result.enabled is False
    assert updated_by_values == ["operator@example.com"]
    assert cache_invalidations == 1
    assert audit_events == [
        (
            "mcp_config.update",
            {
                "resource_type": "mcp_config",
                "details": {
                    "enabled": False,
                    "allowed_tool_ids": ["tool-a"],
                    "blocked_tool_ids": ["tool-b"],
                },
            },
        )
    ]
