from __future__ import annotations

from uuid import uuid4

import httpx
from fastapi import FastAPI, Request

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools import _http_bridge


async def test_rest_bridge_forwards_server_bound_authorization_boundary(
    monkeypatch,
) -> None:
    app = FastAPI()

    @app.get("/boundary")
    async def boundary(request: Request) -> dict[str, str | None]:
        return {"value": request.headers.get("X-Bifrost-Boundary")}

    monkeypatch.delenv("BIFROST_MCP_HTTP_BRIDGE_URL", raising=False)
    monkeypatch.setattr(
        _http_bridge,
        "_build_in_process_transport",
        lambda: httpx.ASGITransport(app=app),
    )
    context = MCPContext(
        user_id=uuid4(),
        org_id=uuid4(),
        authorization_boundary=f"organization:{uuid4()}",
    )

    status_code, body = await _http_bridge.call_rest(
        context,
        "GET",
        "/boundary",
    )

    assert status_code == 200
    assert body == {"value": context.authorization_boundary}


async def test_rest_bridge_does_not_invent_boundary_for_regular_mcp_context(
    monkeypatch,
) -> None:
    app = FastAPI()

    @app.get("/boundary")
    async def boundary(request: Request) -> dict[str, str | None]:
        return {"value": request.headers.get("X-Bifrost-Boundary")}

    monkeypatch.delenv("BIFROST_MCP_HTTP_BRIDGE_URL", raising=False)
    monkeypatch.setattr(
        _http_bridge,
        "_build_in_process_transport",
        lambda: httpx.ASGITransport(app=app),
    )
    context = MCPContext(user_id=uuid4(), org_id=uuid4())

    status_code, body = await _http_bridge.call_rest(
        context,
        "GET",
        "/boundary",
    )

    assert status_code == 200
    assert body == {"value": None}
