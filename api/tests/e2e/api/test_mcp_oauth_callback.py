"""E2E tests for the MCP OAuth callback endpoint.

The vendor token-exchange happy path requires mocking
``OAuthProviderClient._make_token_request`` inside the API process —
which the cross-process e2e runner can't do. That path is covered in
``tests/unit/routers/test_mcp_oauth_callback.py``. Here we exercise the
HTTP surface: invalid state, vendor errors, and the popup-HTML shape.
"""

from __future__ import annotations

import pytest


@pytest.mark.e2e
class TestMCPOAuthCallbackErrors:
    def test_invalid_state_returns_error_html(self, e2e_client):
        resp = e2e_client.get(
            "/api/mcp/oauth/callback",
            params={"code": "x", "state": "totally-bogus-jwt"},
        )
        assert resp.status_code == 400
        assert "mcp_oauth_error" in resp.text
        assert "text/html" in resp.headers.get("content-type", "")

    def test_vendor_error_short_circuits(self, e2e_client):
        resp = e2e_client.get(
            "/api/mcp/oauth/callback",
            params={
                "code": "",
                "state": "ignored",
                "error": "access_denied",
                "error_description": "user clicked cancel",
            },
        )
        assert resp.status_code == 400
        assert "access_denied" in resp.text
        assert "user clicked cancel" in resp.text

    def test_callback_renders_popup_html(self, e2e_client):
        """The callback always renders an HTML page (window.opener
        postMessage + close), never JSON."""
        resp = e2e_client.get(
            "/api/mcp/oauth/callback",
            params={"code": "x", "state": "bogus"},
        )
        assert "text/html" in resp.headers.get("content-type", "")
        assert "window.opener" in resp.text
        assert "postMessage" in resp.text
        assert "window.close" in resp.text
