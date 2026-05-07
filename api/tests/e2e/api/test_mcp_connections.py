"""E2E tests for the MCP connections router.

Covers per-org connection CRUD plus the router shape of refresh-tools
and /connect (negative paths). The success path of refresh-tools and
the OAuth state-encoding are exercised in unit tests
(``tests/unit/services/test_mcp_oauth_state.py`` and
``tests/unit/routers/test_mcp_connections.py``); the cross-process e2e
runner cannot mock into the API container.
"""

from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture
def server_template(e2e_client, platform_admin):
    """A minimal server template for connection tests."""
    name = f"e2e_mcp_srv_{uuid4().hex[:8]}"
    resp = e2e_client.post(
        "/api/mcp-servers",
        headers=platform_admin.headers,
        json={"name": name, "server_url": "https://example.com/mcp"},
    )
    assert resp.status_code == 201, resp.text
    server = resp.json()
    yield server
    e2e_client.delete(
        f"/api/mcp-servers/{server['id']}?hard=true",
        headers=platform_admin.headers,
    )


@pytest.fixture
def connection(e2e_client, platform_admin, server_template, org1):
    """Create a connection bound to ``server_template`` in ``org1``."""
    resp = e2e_client.post(
        "/api/mcp-connections",
        headers=platform_admin.headers,
        json={
            "server_id": server_template["id"],
            "organization_id": str(org1["id"]),
            "client_id": "vendor-client-id",
            "client_secret": "vendor-client-secret-PLAINTEXT",
            "available_in_chat": True,
            "available_to_autonomous": False,
        },
    )
    assert resp.status_code == 201, resp.text
    conn = resp.json()
    yield conn
    e2e_client.delete(
        f"/api/mcp-connections/{conn['id']}",
        headers=platform_admin.headers,
    )


@pytest.mark.e2e
class TestMCPConnectionsCRUD:
    def test_create_connection(self, e2e_client, platform_admin, server_template, org1):
        resp = e2e_client.post(
            "/api/mcp-connections",
            headers=platform_admin.headers,
            json={
                "server_id": server_template["id"],
                "organization_id": str(org1["id"]),
                "client_id": "client-id-create",
                "client_secret": "secret-plaintext",
                "available_in_chat": False,
                "available_to_autonomous": True,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # encrypted_client_secret must NOT leak
        assert "encrypted_client_secret" not in body
        assert body["client_id"] == "client-id-create"
        assert body["available_to_autonomous"] is True
        assert body["available_in_chat"] is False
        assert body["organization_id"] == str(org1["id"])
        e2e_client.delete(
            f"/api/mcp-connections/{body['id']}",
            headers=platform_admin.headers,
        )

    def test_create_connection_unknown_server_404(
        self, e2e_client, platform_admin, org1
    ):
        resp = e2e_client.post(
            "/api/mcp-connections",
            headers=platform_admin.headers,
            json={
                "server_id": str(uuid4()),
                "organization_id": str(org1["id"]),
                "client_id": "x",
                "client_secret": "y",
            },
        )
        assert resp.status_code == 404

    def test_get_connection(self, e2e_client, platform_admin, connection):
        resp = e2e_client.get(
            f"/api/mcp-connections/{connection['id']}",
            headers=platform_admin.headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == connection["id"]
        assert "encrypted_client_secret" not in body
        assert body["tools"] == []

    def test_get_connection_404(self, e2e_client, platform_admin):
        resp = e2e_client.get(
            f"/api/mcp-connections/{uuid4()}",
            headers=platform_admin.headers,
        )
        assert resp.status_code == 404

    def test_org_user_cannot_see_other_orgs_connection(
        self, e2e_client, org2_user, connection
    ):
        """A connection in org1 must 404 for an org2 user."""
        resp = e2e_client.get(
            f"/api/mcp-connections/{connection['id']}",
            headers=org2_user.headers,
        )
        assert resp.status_code == 404

    def test_list_connections(self, e2e_client, platform_admin, connection):
        resp = e2e_client.get(
            "/api/mcp-connections",
            headers=platform_admin.headers,
        )
        assert resp.status_code == 200, resp.text
        ids = [c["id"] for c in resp.json()]
        assert connection["id"] in ids

    def test_update_connection_flags_and_secret(
        self, e2e_client, platform_admin, connection
    ):
        resp = e2e_client.patch(
            f"/api/mcp-connections/{connection['id']}",
            headers=platform_admin.headers,
            json={
                "available_in_chat": False,
                "available_to_autonomous": True,
                "client_secret": "rotated-plaintext",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["available_in_chat"] is False
        assert body["available_to_autonomous"] is True

    def test_delete_connection(self, e2e_client, platform_admin, server_template, org1):
        resp = e2e_client.post(
            "/api/mcp-connections",
            headers=platform_admin.headers,
            json={
                "server_id": server_template["id"],
                "organization_id": str(org1["id"]),
                "client_id": "client-del",
                "client_secret": "secret-del",
            },
        )
        assert resp.status_code == 201
        conn_id = resp.json()["id"]

        delete = e2e_client.delete(
            f"/api/mcp-connections/{conn_id}",
            headers=platform_admin.headers,
        )
        assert delete.status_code == 204

        get = e2e_client.get(
            f"/api/mcp-connections/{conn_id}",
            headers=platform_admin.headers,
        )
        assert get.status_code == 404


@pytest.mark.e2e
class TestMCPConnectionRefreshToolsErrors:
    """Negative-path tests — success path is exercised by unit tests."""

    def test_refresh_tools_without_service_token_400s(
        self, e2e_client, platform_admin, connection
    ):
        """A connection with no service token cannot run catalog sync."""
        resp = e2e_client.post(
            f"/api/mcp-connections/{connection['id']}/refresh-tools",
            headers=platform_admin.headers,
        )
        # MisconfigError raised by sync_catalog → 400
        assert resp.status_code == 400, resp.text

    def test_refresh_tools_404(self, e2e_client, platform_admin):
        resp = e2e_client.post(
            f"/api/mcp-connections/{uuid4()}/refresh-tools",
            headers=platform_admin.headers,
        )
        assert resp.status_code == 404


@pytest.mark.e2e
class TestMCPConnectionConnectErrors:
    """Negative-path tests for the connect endpoints — happy path is
    tested in ``tests/unit/services/test_mcp_oauth_state.py``.
    """

    def test_connect_without_oauth_provider_400s(
        self, e2e_client, platform_admin, connection
    ):
        """Server template has no OAuth provider attached → 400."""
        resp = e2e_client.post(
            f"/api/mcp-connections/{connection['id']}/connect",
            headers=platform_admin.headers,
        )
        assert resp.status_code == 400, resp.text

    def test_connect_404(self, e2e_client, platform_admin):
        resp = e2e_client.post(
            f"/api/mcp-connections/{uuid4()}/connect",
            headers=platform_admin.headers,
        )
        assert resp.status_code == 404

    def test_user_connect_without_oauth_provider_400s(
        self, e2e_client, org1_user, connection
    ):
        resp = e2e_client.get(
            f"/api/me/mcp-connections/{connection['id']}/connect",
            headers=org1_user.headers,
        )
        assert resp.status_code == 400, resp.text


@pytest.mark.e2e
class TestMCPServerCreateWithOAuthProvider:
    """Inline OAuth provider creation on ``POST /api/mcp-servers``."""

    def test_server_create_with_inline_authorization_code_provider(
        self, e2e_client, platform_admin
    ):
        """Inline OAuth provider for authorization_code flow."""
        name = f"e2e_mcp_srv_{uuid4().hex[:8]}"
        resp = e2e_client.post(
            "/api/mcp-servers",
            headers=platform_admin.headers,
            json={
                "name": name,
                "server_url": "https://example.com/mcp",
                "oauth_provider": {
                    "oauth_flow_type": "authorization_code",
                    "token_url": "https://example.com/oauth/token",
                    "authorization_url": "https://example.com/oauth/authorize",
                    "scopes": ["read", "write"],
                },
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["oauth_provider_id"] is not None
        # cleanup
        e2e_client.delete(
            f"/api/mcp-servers/{body['id']}?hard=true",
            headers=platform_admin.headers,
        )

    def test_server_create_with_inline_client_credentials_provider(
        self, e2e_client, platform_admin
    ):
        """Inline OAuth provider for client_credentials flow — no
        authorization_url required."""
        name = f"e2e_mcp_srv_{uuid4().hex[:8]}"
        resp = e2e_client.post(
            "/api/mcp-servers",
            headers=platform_admin.headers,
            json={
                "name": name,
                "server_url": "https://example.com/mcp",
                "oauth_provider": {
                    "oauth_flow_type": "client_credentials",
                    "token_url": "https://example.com/oauth/token",
                    "scopes": ["read"],
                },
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["oauth_provider_id"] is not None
        # cleanup
        e2e_client.delete(
            f"/api/mcp-servers/{body['id']}?hard=true",
            headers=platform_admin.headers,
        )

    def test_server_create_authorization_code_requires_authorization_url(
        self, e2e_client, platform_admin
    ):
        """authorization_code flow without authorization_url → 422."""
        name = f"e2e_mcp_srv_{uuid4().hex[:8]}"
        resp = e2e_client.post(
            "/api/mcp-servers",
            headers=platform_admin.headers,
            json={
                "name": name,
                "server_url": "https://example.com/mcp",
                "oauth_provider": {
                    "oauth_flow_type": "authorization_code",
                    "token_url": "https://example.com/oauth/token",
                    "scopes": ["read"],
                },
            },
        )
        assert resp.status_code == 422, resp.text

    def test_server_create_rejects_both_provider_id_and_inline(
        self, e2e_client, platform_admin
    ):
        """Cannot pass both ``oauth_provider_id`` and ``oauth_provider``."""
        name = f"e2e_mcp_srv_{uuid4().hex[:8]}"
        resp = e2e_client.post(
            "/api/mcp-servers",
            headers=platform_admin.headers,
            json={
                "name": name,
                "server_url": "https://example.com/mcp",
                "oauth_provider_id": str(uuid4()),
                "oauth_provider": {
                    "oauth_flow_type": "client_credentials",
                    "token_url": "https://example.com/oauth/token",
                },
            },
        )
        assert resp.status_code == 422, resp.text


@pytest.mark.e2e
class TestMCPConnectClientCredentials:
    """Negative-path checks for the client_credentials connect flow.

    The happy-path success of the token exchange is exercised in the unit
    test ``tests/unit/routers/test_mcp_connections_connect.py`` (the e2e
    process can't mock the vendor's ``/token`` endpoint).
    """

    def test_user_connect_rejects_client_credentials_400(
        self, e2e_client, platform_admin, org1_user, org1
    ):
        """Per-user delegation is meaningless for client_credentials → 400."""
        # Create a server template with a client_credentials inline provider
        srv_resp = e2e_client.post(
            "/api/mcp-servers",
            headers=platform_admin.headers,
            json={
                "name": f"e2e_mcp_srv_{uuid4().hex[:8]}",
                "server_url": "https://example.com/mcp",
                "oauth_provider": {
                    "oauth_flow_type": "client_credentials",
                    "token_url": "https://example.com/oauth/token",
                    "scopes": ["read"],
                },
            },
        )
        assert srv_resp.status_code == 201, srv_resp.text
        server = srv_resp.json()
        try:
            conn_resp = e2e_client.post(
                "/api/mcp-connections",
                headers=platform_admin.headers,
                json={
                    "server_id": server["id"],
                    "organization_id": str(org1["id"]),
                    "client_id": "vendor-client-id",
                    "client_secret": "vendor-secret-PLAINTEXT",
                    "available_in_chat": False,
                    "available_to_autonomous": True,
                },
            )
            assert conn_resp.status_code == 201, conn_resp.text
            connection = conn_resp.json()
            try:
                user_resp = e2e_client.get(
                    f"/api/me/mcp-connections/{connection['id']}/connect",
                    headers=org1_user.headers,
                )
                assert user_resp.status_code == 400, user_resp.text
                assert (
                    "authorization_code"
                    in user_resp.json().get("detail", "").lower()
                )
            finally:
                e2e_client.delete(
                    f"/api/mcp-connections/{connection['id']}",
                    headers=platform_admin.headers,
                )
        finally:
            e2e_client.delete(
                f"/api/mcp-servers/{server['id']}?hard=true",
                headers=platform_admin.headers,
            )
