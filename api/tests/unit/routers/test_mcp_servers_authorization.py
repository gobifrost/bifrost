"""Authorization boundaries for external MCP server templates."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.external_mcp import MCPServerCreate, MCPServerUpdate
from src.models.orm.external_mcp import MCPConnection, MCPServer
from src.routers import mcp_servers
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    organization_id: UUID,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
    email: str = "builder@example.com",
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email=email,
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


def _server(*, organization_id: UUID | None = None) -> MCPServer:
    now = datetime.now(timezone.utc)
    server = MCPServer(
        id=uuid4(),
        name="Vendor MCP",
        server_url="https://vendor.example.com/mcp",
        organization_id=organization_id,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    server.connections = []
    return server


def _connection(*, server_id: UUID, organization_id: UUID) -> MCPConnection:
    now = datetime.now(timezone.utc)
    connection = MCPConnection(
        id=uuid4(),
        server_id=server_id,
        organization_id=organization_id,
        client_id="client",
        encrypted_client_secret=b"secret",
        available_in_chat=True,
        available_to_autonomous=False,
        created_at=now,
        updated_at=now,
    )
    connection.tools = []
    return connection


def test_server_to_public_filters_nested_connections_to_selected_org() -> None:
    selected_org_id = uuid4()
    other_org_id = uuid4()
    server = _server()
    server.connections = [
        _connection(server_id=server.id, organization_id=selected_org_id),
        _connection(server_id=server.id, organization_id=other_org_id),
    ]

    response = mcp_servers._server_to_public(
        server,
        visible_organization_ids={selected_org_id},
    )

    assert [connection.organization_id for connection in response.connections] == [
        selected_org_id
    ]


def test_managed_collection_has_no_nested_connection_visibility() -> None:
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"integrations.read"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    assert mcp_servers._connection_visibility_filter(authorization) == set()


def test_managed_collection_cannot_mutate_mcp_server_templates() -> None:
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"integrations.readwrite"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    with pytest.raises(HTTPException) as exc:
        mcp_servers._require_mcp_server_mutation_boundary(authorization, uuid4())

    assert exc.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_name", "args", "capability"),
    [
        ("list_mcp_servers", (), "integrations.read"),
        ("get_mcp_server", (uuid4(),), "integrations.read"),
        (
            "create_mcp_server",
            (
                MCPServerCreate(
                    name="Vendor MCP",
                    server_url="https://vendor.example.com/mcp",
                ),
            ),
            "integrations.readwrite",
        ),
        (
            "update_mcp_server",
            (uuid4(), MCPServerUpdate(name="Updated")),
            "integrations.readwrite",
        ),
        ("delete_mcp_server", (uuid4(),), "integrations.readwrite"),
        (
            "discover_mcp_server",
            (
                mcp_servers.MCPServerDiscoverRequest(
                    server_url="https://vendor.example.com/mcp"
                ),
            ),
            "integrations.readwrite",
        ),
    ],
)
async def test_mcp_server_routes_require_declared_capability(
    route_name: str,
    args: tuple[object, ...],
    capability: str,
) -> None:
    authorization = _authorization(organization_id=uuid4(), capabilities=set())
    ctx = SimpleNamespace(db=SimpleNamespace(add=pytest.fail))
    route = getattr(mcp_servers, route_name)

    with pytest.raises(HTTPException) as exc:
        if route_name == "list_mcp_servers":
            await route(ctx, authorization)
        elif route_name == "get_mcp_server":
            await route(args[0], ctx, authorization)
        elif route_name == "create_mcp_server":
            await route(args[0], ctx, authorization)
        elif route_name == "update_mcp_server":
            await route(args[0], args[1], ctx, authorization)
        elif route_name == "delete_mcp_server":
            await route(args[0], ctx, authorization)
        else:
            await route(args[0], authorization)

    assert exc.value.status_code == 403
    assert exc.value.detail == f"Missing required capability: {capability}"


@pytest.mark.asyncio
async def test_create_mcp_server_requires_integrations_readwrite() -> None:
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"integrations.read"},
    )

    with pytest.raises(HTTPException) as exc:
        await mcp_servers.create_mcp_server(
            MCPServerCreate(
                name="Vendor MCP",
                server_url="https://vendor.example.com/mcp",
            ),
            SimpleNamespace(db=SimpleNamespace(add=pytest.fail)),
            authorization,
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: integrations.readwrite"


@pytest.mark.asyncio
async def test_create_mcp_server_defaults_to_selected_org_and_effective_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid4()
    added: list[object] = []
    audit_actions: list[str] = []
    manifest_regenerations: list[object] = []
    authorization = _authorization(
        organization_id=organization_id,
        capabilities={"integrations.readwrite"},
        email="ops@example.com",
    )

    class _DB:
        def add(self, row):  # noqa: ANN001, ANN201
            added.append(row)

        async def flush(self):  # noqa: ANN201
            for row in added:
                if isinstance(row, MCPServer):
                    row.id = row.id or uuid4()
                    row.created_at = row.created_at or datetime.now(timezone.utc)
                    row.updated_at = row.updated_at or row.created_at
            return None

        async def refresh(self, row, relationships):  # noqa: ANN001, ANN201
            assert relationships == ["connections"]
            row.connections = []

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audit_actions.append(action)

    class _RepoSyncWriter:
        def __init__(self, db):  # noqa: ANN001
            self.db = db

        async def regenerate_manifest(self):  # noqa: ANN201
            manifest_regenerations.append(self.db)

    monkeypatch.setattr(mcp_servers, "emit_audit", _emit_audit)
    monkeypatch.setattr(mcp_servers, "RepoSyncWriter", _RepoSyncWriter)

    result = await mcp_servers.create_mcp_server(
        MCPServerCreate(
            name="Vendor MCP",
            server_url="https://vendor.example.com/mcp",
        ),
        SimpleNamespace(db=_DB()),
        authorization,
    )

    assert result.organization_id == organization_id
    assert len(added) == 1
    assert isinstance(added[0], MCPServer)
    assert added[0].organization_id == organization_id
    assert audit_actions == ["mcp_server.create"]
    assert len(manifest_regenerations) == 1


@pytest.mark.asyncio
async def test_discover_rejects_managed_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorization = _authorization(
        organization_id=uuid4(),
        capabilities={"integrations.readwrite"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )
    monkeypatch.setattr(
        mcp_servers,
        "discover_oauth_metadata",
        pytest.fail,
    )

    with pytest.raises(HTTPException) as exc:
        await mcp_servers.discover_mcp_server(
            mcp_servers.MCPServerDiscoverRequest(
                server_url="https://vendor.example.com/mcp"
            ),
            authorization,
        )

    assert exc.value.status_code == 409
