"""E2E tests for the MCP connections router.

Covers per-org connection CRUD plus the router shape of refresh-tools
and /connect (negative paths). The success path of refresh-tools and
the OAuth state-encoding are exercised in unit tests
(``tests/unit/services/test_mcp_oauth_state.py`` and
``tests/unit/routers/test_mcp_connections.py``); the cross-process e2e
runner cannot mock into the API container.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.models.orm.external_mcp import MCPConnection
from src.models.orm.oauth import OAuthProvider, OAuthToken


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


def _create_connection(e2e_client, headers, server_id: str, organization_id: str) -> dict:
    resp = e2e_client.post(
        "/api/mcp-connections",
        headers=headers,
        json={
            "server_id": server_id,
            "organization_id": organization_id,
            "client_id": "vendor-client-id",
            "client_secret": "vendor-client-secret-PLAINTEXT",
            "available_in_chat": True,
            "available_to_autonomous": False,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def connection(e2e_client, platform_admin, server_template, org1):
    """Create a connection bound to ``server_template`` in Contoso."""
    conn = _create_connection(
        e2e_client, platform_admin.headers, server_template["id"], str(org1["id"])
    )
    yield conn
    deleted = e2e_client.delete(
        f"/api/mcp-connections/{conn['id']}",
        headers=platform_admin.headers,
    )
    assert deleted.status_code == 204, deleted.text


@pytest.fixture
def org2_connection(e2e_client, platform_admin, server_template, org2):
    """Create a connection in Fabrikam for the scoped read control."""
    conn = _create_connection(
        e2e_client, platform_admin.headers, server_template["id"], str(org2["id"])
    )
    yield conn
    deleted = e2e_client.delete(
        f"/api/mcp-connections/{conn['id']}",
        headers=platform_admin.headers,
    )
    assert deleted.status_code == 204, deleted.text


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

    def test_org_user_can_read_own_org_connection(
        self, e2e_client, org2_user, org2_connection
    ):
        """The User base role's mcp.readbasic applies at Fabrikam only."""
        resp = e2e_client.get(
            f"/api/mcp-connections/{org2_connection['id']}",
            headers=org2_user.headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["organization_id"] == str(org2_user.organization_id)

    def test_org_user_cannot_see_other_orgs_connection(
        self, e2e_client, org2_user, connection
    ):
        """A Contoso connection is hidden from mcp.readbasic at Fabrikam."""
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

    def test_org_user_cannot_update_own_org_connection(
        self, e2e_client, org1_user, connection
    ):
        """Managing an MCP connection is bypass-only, even within the
        caller's own org."""
        resp = e2e_client.patch(
            f"/api/mcp-connections/{connection['id']}",
            headers=org1_user.headers,
            json={"available_in_chat": False},
        )
        assert resp.status_code == 403, resp.text

    def test_org_user_cannot_create_connection(
        self, e2e_client, org1_user, server_template, org1
    ):
        resp = e2e_client.post(
            "/api/mcp-connections",
            headers=org1_user.headers,
            json={
                "server_id": server_template["id"],
                "organization_id": org1["id"],
                "client_id": "denied-client",
                "client_secret": "denied-secret",
            },
        )
        assert resp.status_code == 403, resp.text

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
class TestMCPConnectionServiceTokenBinding:
    async def test_scoped_writer_binds_only_own_provider_service_tokens(
        self,
        e2e_client,
        platform_admin,
        org1,
        org2,
        org1_user,
        db_session,
    ):
        """A same-org writer cannot attach another organization's token."""
        from src.services.role_permissions import set_role_permissions

        server = e2e_client.post(
            "/api/mcp-servers",
            headers=platform_admin.headers,
            json={
                "name": f"e2e_mcp_service_binding_{uuid4().hex[:8]}",
                "server_url": "https://example.com/mcp",
                "oauth_provider": {
                    "oauth_flow_type": "client_credentials",
                    "token_url": "https://example.com/oauth/token",
                    "scopes": ["read"],
                },
            },
        )
        assert server.status_code == 201, server.text
        server_body = server.json()
        assert server_body["oauth_provider_id"] is not None
        server_id = server_body["id"]

        role = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": f"Contoso MCP Writer {uuid4().hex[:8]}"},
        )
        assert role.status_code == 201, role.text
        role_id = role.json()["id"]

        original_assignments = e2e_client.get(
            f"/api/users/{org1_user.user_id}/role-assignments",
            headers=platform_admin.headers,
        )
        assert original_assignments.status_code == 200, original_assignments.text
        original = original_assignments.json()

        connection_id: str | None = None
        provider_id = UUID(server_body["oauth_provider_id"])
        token_ids: list[UUID] = []
        try:
            await set_role_permissions(
                db_session,
                role_id=UUID(role_id),
                permissions=frozenset({"mcp.readwrite"}),
            )
            await db_session.commit()

            assigned = e2e_client.put(
                f"/api/users/{org1_user.user_id}/role-assignments",
                headers=platform_admin.headers,
                json={
                    "base_role_id": original["base_role"]["id"],
                    "additional": [
                        {
                            "role_id": assignment["role_id"],
                            "boundaries": assignment["boundaries"],
                        }
                        for assignment in original["additional"]
                    ]
                    + [
                        {
                            "role_id": role_id,
                            "boundaries": [
                                {
                                    "kind": "organization",
                                    "organization_id": org1["id"],
                                }
                            ],
                        }
                    ],
                },
            )
            assert assigned.status_code == 200, assigned.text

            connection = _create_connection(
                e2e_client,
                platform_admin.headers,
                server_id,
                str(org1["id"]),
            )
            connection_id = connection["id"]
            own_org_id = UUID(str(org1["id"]))

            initial_token = OAuthToken(
                provider_id=provider_id,
                organization_id=own_org_id,
                encrypted_access_token=b"contoso-initial-service-token",
            )
            replacement_token = OAuthToken(
                provider_id=provider_id,
                organization_id=own_org_id,
                encrypted_access_token=b"contoso-replacement-service-token",
            )
            foreign_token = OAuthToken(
                provider_id=provider_id,
                organization_id=UUID(str(org2["id"])),
                encrypted_access_token=b"fabrikam-service-token",
            )
            db_session.add_all([initial_token, replacement_token, foreign_token])
            await db_session.commit()
            token_ids = [initial_token.id, replacement_token.id, foreign_token.id]

            initial_bind = e2e_client.patch(
                f"/api/mcp-connections/{connection_id}",
                headers=org1_user.headers,
                json={"service_oauth_token_id": str(initial_token.id)},
            )
            assert initial_bind.status_code == 200, initial_bind.text
            assert initial_bind.json()["service_oauth_token_id"] == str(
                initial_token.id
            )

            rejected = e2e_client.patch(
                f"/api/mcp-connections/{connection_id}",
                headers=org1_user.headers,
                json={"service_oauth_token_id": str(foreign_token.id)},
            )
            assert rejected.status_code == 422, rejected.text
            assert rejected.json()["detail"] == (
                "Service OAuth token is not valid for this connection"
            )

            db_session.expunge_all()
            persisted = await db_session.get(MCPConnection, UUID(connection_id))
            assert persisted is not None
            assert persisted.service_oauth_token_id == initial_token.id

            accepted = e2e_client.patch(
                f"/api/mcp-connections/{connection_id}",
                headers=org1_user.headers,
                json={"service_oauth_token_id": str(replacement_token.id)},
            )
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["service_oauth_token_id"] == str(
                replacement_token.id
            )

            unbound = e2e_client.patch(
                f"/api/mcp-connections/{connection_id}",
                headers=org1_user.headers,
                json={"service_oauth_token_id": None},
            )
            assert unbound.status_code == 200, unbound.text
            assert unbound.json()["service_oauth_token_id"] is None
        finally:
            restored = e2e_client.put(
                f"/api/users/{org1_user.user_id}/role-assignments",
                headers=platform_admin.headers,
                json={
                    "base_role_id": original["base_role"]["id"],
                    "additional": [
                        {
                            "role_id": assignment["role_id"],
                            "boundaries": assignment["boundaries"],
                        }
                        for assignment in original["additional"]
                    ],
                },
            )
            assert restored.status_code == 200, restored.text
            if connection_id is not None:
                deleted = e2e_client.delete(
                    f"/api/mcp-connections/{connection_id}",
                    headers=platform_admin.headers,
                )
                assert deleted.status_code == 204, deleted.text
            deleted_server = e2e_client.delete(
                f"/api/mcp-servers/{server_id}?hard=true",
                headers=platform_admin.headers,
            )
            assert deleted_server.status_code == 204, deleted_server.text
            if token_ids:
                await db_session.execute(
                    delete(OAuthToken).where(OAuthToken.id.in_(token_ids))
                )
            await db_session.execute(
                delete(OAuthProvider).where(OAuthProvider.id == provider_id)
            )
            await db_session.commit()
            deleted_role = e2e_client.delete(
                f"/api/roles/{role_id}", headers=platform_admin.headers
            )
            assert deleted_role.status_code == 204, deleted_role.text


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
