"""E2E: an MCP tool call through the thin-wrapper REST bridge produces an
audit row tagged ``surface='mcp'`` with the route's catalog operation id
(R2a-2 Part 1).

The bridge (``services/mcp_server/tools/_http_bridge.py``) sends
``X-Bifrost-Surface: mcp`` on every REST call it makes on the caller's
behalf; this proves the header survives the round trip into the emitted
``audit_logs`` row exactly like the direct-transport case covered in
test_mcp_parity.py's DTO/CLI parity tests.
"""

from __future__ import annotations

import os
from typing import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio


class MockMCPContext:
    """Minimal MCP context for driving tool handlers in tests."""

    def __init__(self, user_id: str, user_email: str, user_name: str = "E2E Admin"):
        self.user_id = user_id
        self.user_email = user_email
        self.is_platform_admin = True
        self.org_id = None
        self.user_name = user_name
        self.accessible_namespaces: list[str] = []
        self.session = None


@pytest_asyncio.fixture
async def mcp_bridge_env(e2e_api_url) -> AsyncIterator[str]:
    """Point the parity tools' HTTP bridge at the running API container."""
    prev = os.environ.get("BIFROST_MCP_HTTP_BRIDGE_URL")
    os.environ["BIFROST_MCP_HTTP_BRIDGE_URL"] = e2e_api_url
    try:
        yield e2e_api_url
    finally:
        if prev is None:
            os.environ.pop("BIFROST_MCP_HTTP_BRIDGE_URL", None)
        else:
            os.environ["BIFROST_MCP_HTTP_BRIDGE_URL"] = prev


@pytest.fixture
def admin_context(platform_admin, mcp_bridge_env) -> MockMCPContext:
    return MockMCPContext(
        user_id=str(platform_admin.user_id) if platform_admin.user_id else "",
        user_email=platform_admin.email,
    )


class TestMcpBridgeAuditSurface:
    @pytest.mark.asyncio
    async def test_role_create_via_mcp_tags_surface_and_operation_id(
        self, admin_context, e2e_client, platform_admin, async_session_factory
    ) -> None:
        from sqlalchemy import select

        from src.models.orm.audit import AuditLog
        from src.services.mcp_server.tools.roles import bifrost_role_create

        name = f"mcp-audit-surface-{uuid4().hex[:8]}"
        role_id: str | None = None
        try:
            result = await bifrost_role_create(
                admin_context,
                name=name,
                description="mcp audit surface e2e",
                permissions={"workflows.read": True},
            )
            created = result.structured_content or {}
            assert "error" not in created, created
            role_id = str(created["id"])

            async with async_session_factory() as session:
                row = (
                    await session.execute(
                        select(AuditLog).where(
                            AuditLog.action == "role.create",
                            AuditLog.resource_id == role_id,
                        )
                    )
                ).scalar_one()

            assert row.surface == "mcp"
            assert row.operation_id == "roles.create"
        finally:
            if role_id is not None:
                e2e_client.delete(
                    f"/api/roles/{role_id}", headers=platform_admin.headers
                )
