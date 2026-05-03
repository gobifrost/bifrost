"""Unit tests for the MCP OAuth callback router.

The callback orchestrates state-decode → nonce-consume → vendor token
exchange → token persistence → wire-up. These tests mock at the
``OAuthProviderClient._make_token_request`` layer (the bottom HTTP call)
so the rest of the orchestration runs for real.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.routers.mcp_oauth_callback import (
    _exchange_code_for_token,
    _persist_token,
    _popup_response,
    _upsert_user_credential,
    mcp_oauth_callback,
)


class TestPopupResponse:
    def test_success_html_includes_postmessage(self):
        connection_id = str(uuid4())
        response = _popup_response(success=True, connection_id=connection_id)
        body = response.body.decode()
        assert "mcp_oauth_success" in body
        assert connection_id in body
        assert "window.opener" in body
        assert "window.close" in body
        assert response.status_code == 200

    def test_error_html_includes_error_string(self):
        response = _popup_response(
            success=False, connection_id="conn-1", error="bad thing happened"
        )
        body = response.body.decode()
        assert "mcp_oauth_error" in body
        assert "bad thing happened" in body
        assert response.status_code == 400

    def test_error_html_escapes_quotes(self):
        """An error containing a single quote shouldn't break the JS literal."""
        response = _popup_response(
            success=False, connection_id="x", error="user's denied"
        )
        body = response.body.decode()
        # JS-escaped single quote
        assert "\\'" in body


class TestExchangeCodeForToken:
    @pytest.mark.asyncio
    async def test_includes_pkce_verifier_in_payload(self):
        """The token-exchange call must send ``code_verifier`` (PKCE)."""
        connection = MagicMock()
        connection.encrypted_client_secret = b"will-be-decrypted"
        connection.client_id = "vendor-client-id"

        provider = MagicMock()
        provider.token_url = "https://vendor.example.com/oauth/token"
        provider.scopes = ["read", "write"]
        provider.audience = None

        captured_payload: dict = {}

        async def fake_request(self, token_url, payload):
            captured_payload.update(payload)
            return (
                True,
                {
                    "access_token": "ok",
                    "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
                },
            )

        with patch(
            "src.services.oauth_provider.OAuthProviderClient._make_token_request",
            new=fake_request,
        ), patch(
            "src.routers.mcp_oauth_callback.decrypt_secret",
            return_value="decrypted-vendor-secret",
        ), patch(
            "src.routers.mcp_oauth_callback.get_url_resolution_defaults",
            new=AsyncMock(return_value={}),
        ):
            result = await _exchange_code_for_token(
                connection=connection,
                provider=provider,
                code="vendor-code",
                pkce_verifier="my-pkce-verifier",
                redirect_uri="https://bifrost.example.com/cb",
                db=MagicMock(),
            )
        assert result["access_token"] == "ok"
        assert captured_payload["code_verifier"] == "my-pkce-verifier"
        assert captured_payload["code"] == "vendor-code"
        assert captured_payload["client_id"] == "vendor-client-id"
        assert captured_payload["client_secret"] == "decrypted-vendor-secret"
        assert captured_payload["redirect_uri"] == "https://bifrost.example.com/cb"
        assert captured_payload["scope"] == "read write"
        assert captured_payload["grant_type"] == "authorization_code"

    @pytest.mark.asyncio
    async def test_failure_raises_400(self):
        from fastapi import HTTPException

        connection = MagicMock()
        connection.encrypted_client_secret = b"x"
        connection.client_id = "y"
        provider = MagicMock()
        provider.token_url = "https://vendor.example.com/oauth/token"
        provider.scopes = []
        provider.audience = None

        async def fake_request(self, token_url, payload):
            return (False, {"error": "invalid_grant", "error_description": "no good"})

        with patch(
            "src.services.oauth_provider.OAuthProviderClient._make_token_request",
            new=fake_request,
        ), patch(
            "src.routers.mcp_oauth_callback.decrypt_secret", return_value="s"
        ), patch(
            "src.routers.mcp_oauth_callback.get_url_resolution_defaults",
            new=AsyncMock(return_value={}),
        ):
            with pytest.raises(HTTPException) as exc:
                await _exchange_code_for_token(
                    connection=connection,
                    provider=provider,
                    code="x",
                    pkce_verifier="v",
                    redirect_uri="https://example.com/cb",
                    db=MagicMock(),
                )
        assert exc.value.status_code == 400
        assert "no good" in str(exc.value.detail)


class TestPersistToken:
    @pytest.mark.asyncio
    async def test_persist_token_inserts_row_with_encryption(self):
        added = []
        db = MagicMock()
        db.add = lambda obj: added.append(obj)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        await _persist_token(
            provider_id=uuid4(),
            organization_id=uuid4(),
            user_id=None,
            access_token="vendor-access",
            refresh_token="vendor-refresh",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            scopes=["read"],
            db=db,
        )
        assert len(added) == 1
        token = added[0]
        # Encrypted bytes, not the plaintext
        assert token.encrypted_access_token != b"vendor-access"
        assert token.encrypted_refresh_token is not None
        assert token.scopes == ["read"]


class TestUpsertUserCredential:
    @pytest.mark.asyncio
    async def test_upsert_creates_new_row(self):
        db = MagicMock()
        db.execute = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        db.execute.return_value = result
        added = []
        db.add = lambda obj: added.append(obj)
        db.flush = AsyncMock()

        user_id = uuid4()
        connection_id = uuid4()
        token_id = uuid4()

        await _upsert_user_credential(
            user_id=user_id,
            connection_id=connection_id,
            oauth_token_id=token_id,
            granted_scopes=["read", "write"],
            db=db,
        )
        assert len(added) == 1
        cred = added[0]
        assert cred.user_id == user_id
        assert cred.connection_id == connection_id
        assert cred.oauth_token_id == token_id
        assert cred.granted_scopes == ["read", "write"]

    @pytest.mark.asyncio
    async def test_upsert_updates_existing_row(self):
        existing = MagicMock()
        existing.user_id = uuid4()
        existing.connection_id = uuid4()
        existing.oauth_token_id = uuid4()
        existing.granted_scopes = ["read"]

        db = MagicMock()
        db.execute = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=existing)
        db.execute.return_value = result
        db.add = MagicMock()  # Should NOT be called on update path
        db.flush = AsyncMock()

        new_token_id = uuid4()
        await _upsert_user_credential(
            user_id=existing.user_id,
            connection_id=existing.connection_id,
            oauth_token_id=new_token_id,
            granted_scopes=["read", "write"],
            db=db,
        )
        # Pointed at the new token
        assert existing.oauth_token_id == new_token_id
        assert existing.granted_scopes == ["read", "write"]
        db.add.assert_not_called()


class TestCallbackHandler:
    """Higher-level orchestration tests for the GET handler itself."""

    @pytest.mark.asyncio
    async def test_vendor_error_returns_error_html_immediately(self):
        # No DB or state work needed — caller exits before any of that.
        response = await mcp_oauth_callback(
            db=MagicMock(),
            code="",
            state="ignored",
            error="access_denied",
            error_description="user clicked cancel",
        )
        assert response.status_code == 400
        body = response.body.decode()
        assert "access_denied" in body
        assert "user clicked cancel" in body

    @pytest.mark.asyncio
    async def test_invalid_state_returns_error_without_db_io(self):
        db = MagicMock()
        db.execute = AsyncMock()  # Should NOT be called

        response = await mcp_oauth_callback(
            db=db,
            code="x",
            state="totally-bogus",
        )
        assert response.status_code == 400
        body = response.body.decode()
        assert "mcp_oauth_error" in body
        db.execute.assert_not_called()
