"""E2E tests for workflow execution via embed token."""

import hashlib
import hmac as hmac_module
from urllib.parse import urlparse

import pytest


def _compute_hmac(params: dict[str, str], secret: str) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_module.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _extract_token_from_redirect(response) -> str:
    """Extract the access token from the redirect URL fragment."""
    location = response.headers.get("location", "")
    parsed = urlparse(location)
    fragment = parsed.fragment  # e.g. "embed_token=eyJ..."
    assert fragment.startswith("embed_token="), f"Expected embed_token in fragment, got: {fragment}"
    return fragment.split("=", 1)[1]


@pytest.mark.e2e
class TestEmbedWorkflowExecution:
    """Test that embed tokens can authenticate workflow execution."""

    @pytest.fixture
    def embed_session(self, e2e_client, platform_admin):
        """Create an app with embed secret and get an embed token."""
        # Create app
        r = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": "embed-wf-test", "slug": "embed-wf-test", "app_model": "inline_v1"},
        )
        assert r.status_code == 201, r.text
        app = r.json()

        # Create embed secret
        r = e2e_client.post(
            f"/api/applications/{app['id']}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "Test"},
        )
        raw_secret = r.json()["raw_secret"]

        # Get embed token via HMAC-verified entry point
        params = {"agent_id": "42"}
        hmac_val = _compute_hmac(params, raw_secret)
        r = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={**params, "hmac": hmac_val},
            follow_redirects=False,
        )
        assert r.status_code == 302, r.text
        embed_token = _extract_token_from_redirect(r)

        yield {
            "app": app,
            "embed_token": embed_token,
            "verified_params": params,
        }

        # Cleanup
        e2e_client.delete(f"/api/applications/{app['id']}", headers=platform_admin.headers)

    def test_embed_token_authenticates_workflow_execute(self, e2e_client, embed_session):
        """An embed token should be accepted by the workflow execute endpoint.

        We send a request with a nonexistent workflow — we expect 404 (not found)
        rather than 401/403 (unauthorized), proving the token was accepted.
        """
        r = e2e_client.post(
            "/api/workflows/execute",
            headers={"Authorization": f"Bearer {embed_session['embed_token']}"},
            json={
                "workflow_id": "nonexistent-workflow-for-auth-test",
                "parameters": {},
            },
        )
        # Should get 404 (workflow not found) rather than 401/403 (unauthorized)
        assert r.status_code != 401, f"Embed token rejected as unauthorized: {r.text}"
        assert r.status_code != 403, f"Embed token rejected as forbidden: {r.text}"

    def test_embed_token_cannot_access_admin_endpoints(self, e2e_client, embed_session):
        """Embed tokens should be blocked from admin endpoints by EmbedScopeMiddleware."""
        r = e2e_client.get(
            "/api/users",
            headers={"Authorization": f"Bearer {embed_session['embed_token']}"},
        )
        assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"
        assert "Embed tokens cannot access" in r.text
