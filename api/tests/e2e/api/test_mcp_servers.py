"""E2E tests for the MCP server templates router.

Covers list / create / get / patch / delete and the discovery endpoint
against an unreachable URL (the discovery helper returns ``None`` on
network failure, which is the negative path).

The discovery happy-path (real well-known JSON parsed into form fields)
is exercised in ``tests/unit/services/test_mcp_client_discovery.py`` —
the cross-process e2e runner cannot mock into the API container.
"""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.mark.e2e
class TestMCPServersCRUD:
    """Server-template CRUD."""

    @pytest.fixture
    def server(self, e2e_client, platform_admin):
        """Create a platform-level server template; clean up after."""
        name = f"e2e_mcp_server_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/mcp-servers",
            headers=platform_admin.headers,
            json={
                "name": name,
                "server_url": "https://example.com/mcp",
                "is_active": True,
            },
        )
        assert response.status_code == 201, f"Create failed: {response.text}"
        server = response.json()
        yield server
        e2e_client.delete(
            f"/api/mcp-servers/{server['id']}?hard=true",
            headers=platform_admin.headers,
        )

    def test_create_server(self, e2e_client, platform_admin):
        name = f"e2e_create_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/mcp-servers",
            headers=platform_admin.headers,
            json={
                "name": name,
                "server_url": "https://example.com/mcp",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["name"] == name
        assert body["server_url"] == "https://example.com/mcp"
        assert body["is_active"] is True
        assert body["organization_id"] is None
        assert body["connections"] == []

        # cleanup
        e2e_client.delete(
            f"/api/mcp-servers/{body['id']}?hard=true",
            headers=platform_admin.headers,
        )

    def test_create_server_requires_admin(self, e2e_client, org1_user):
        response = e2e_client.post(
            "/api/mcp-servers",
            headers=org1_user.headers,
            json={
                "name": f"e2e_unauth_{uuid4().hex[:8]}",
                "server_url": "https://example.com/mcp",
            },
        )
        assert response.status_code == 403, response.text

    def test_list_servers_admin_sees_all(self, e2e_client, platform_admin, server):
        response = e2e_client.get(
            "/api/mcp-servers",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, response.text
        names = [s["name"] for s in response.json()]
        assert server["name"] in names

    def test_list_servers_org_user_sees_platform_level(
        self, e2e_client, org1_user, server
    ):
        """Platform-level (org_id NULL) templates are visible to org users."""
        response = e2e_client.get(
            "/api/mcp-servers",
            headers=org1_user.headers,
        )
        assert response.status_code == 200, response.text
        names = [s["name"] for s in response.json()]
        assert server["name"] in names

    def test_get_server(self, e2e_client, platform_admin, server):
        response = e2e_client.get(
            f"/api/mcp-servers/{server['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == server["id"]
        assert body["connections"] == []

    def test_get_server_404(self, e2e_client, platform_admin):
        response = e2e_client.get(
            f"/api/mcp-servers/{uuid4()}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404

    def test_update_server(self, e2e_client, platform_admin, server):
        response = e2e_client.patch(
            f"/api/mcp-servers/{server['id']}",
            headers=platform_admin.headers,
            json={"server_url": "https://updated.example.com/mcp", "is_active": False},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["server_url"] == "https://updated.example.com/mcp"
        assert body["is_active"] is False

    def test_update_server_requires_admin(self, e2e_client, org1_user, server):
        response = e2e_client.patch(
            f"/api/mcp-servers/{server['id']}",
            headers=org1_user.headers,
            json={"server_url": "https://hax.example.com/mcp"},
        )
        assert response.status_code == 403

    def test_soft_delete_marks_inactive(self, e2e_client, platform_admin):
        # Create then soft-delete
        name = f"e2e_softdel_{uuid4().hex[:8]}"
        create = e2e_client.post(
            "/api/mcp-servers",
            headers=platform_admin.headers,
            json={"name": name, "server_url": "https://example.com/mcp"},
        )
        assert create.status_code == 201
        server_id = create.json()["id"]

        delete = e2e_client.delete(
            f"/api/mcp-servers/{server_id}",
            headers=platform_admin.headers,
        )
        assert delete.status_code == 204

        # Still exists with is_active=False
        get = e2e_client.get(
            f"/api/mcp-servers/{server_id}",
            headers=platform_admin.headers,
        )
        assert get.status_code == 200
        assert get.json()["is_active"] is False

        # active_only=true filters it out of list
        listing = e2e_client.get(
            "/api/mcp-servers?active_only=true",
            headers=platform_admin.headers,
        )
        ids = [s["id"] for s in listing.json()]
        assert server_id not in ids

        # active_only=false includes it
        listing_all = e2e_client.get(
            "/api/mcp-servers?active_only=false",
            headers=platform_admin.headers,
        )
        ids_all = [s["id"] for s in listing_all.json()]
        assert server_id in ids_all

        # Cleanup
        e2e_client.delete(
            f"/api/mcp-servers/{server_id}?hard=true",
            headers=platform_admin.headers,
        )

    def test_hard_delete_removes_row(self, e2e_client, platform_admin):
        name = f"e2e_harddel_{uuid4().hex[:8]}"
        create = e2e_client.post(
            "/api/mcp-servers",
            headers=platform_admin.headers,
            json={"name": name, "server_url": "https://example.com/mcp"},
        )
        assert create.status_code == 201
        server_id = create.json()["id"]

        delete = e2e_client.delete(
            f"/api/mcp-servers/{server_id}?hard=true",
            headers=platform_admin.headers,
        )
        assert delete.status_code == 204

        get = e2e_client.get(
            f"/api/mcp-servers/{server_id}",
            headers=platform_admin.headers,
        )
        assert get.status_code == 404


@pytest.mark.e2e
class TestMCPServerDiscover:
    """Discovery endpoint negative path (positive path is unit-tested).

    A non-routable hostname triggers a fast connect failure inside the
    discovery helper, which returns ``None`` and the router echoes that
    back to the caller.
    """

    def test_discover_returns_null_when_endpoint_unreachable(
        self, e2e_client, platform_admin
    ):
        # 192.0.2.0/24 is RFC 5737 TEST-NET-1 — guaranteed not to resolve
        # to anything routable.
        response = e2e_client.post(
            "/api/mcp-servers/discover",
            headers=platform_admin.headers,
            json={"server_url": "http://192.0.2.1:1/mcp"},
            timeout=15.0,
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"metadata": None}

    def test_discover_requires_admin(self, e2e_client, org1_user):
        response = e2e_client.post(
            "/api/mcp-servers/discover",
            headers=org1_user.headers,
            json={"server_url": "https://example.com/mcp"},
        )
        assert response.status_code == 403
