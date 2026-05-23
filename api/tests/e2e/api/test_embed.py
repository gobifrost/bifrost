"""E2E tests for HMAC-authenticated embed entry point."""

import base64
import hashlib
import hmac as hmac_module
import uuid
from urllib.parse import urlparse

import pytest


def _create_app(client, headers, slug):
    r = client.post("/api/applications", headers=headers, json={"name": slug, "slug": slug})
    assert r.status_code == 201, r.text
    return r.json()


def _delete_app(client, headers, app_id):
    r = client.delete(f"/api/applications/{app_id}", headers=headers)
    assert r.status_code in (200, 204), r.text


def _compute_hmac(params: dict[str, str], secret: str) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_module.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _compute_halo_hmac(agent_id: str, secret: str) -> str:
    digest = hmac_module.new(secret.encode(), agent_id.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _extract_token_from_redirect(response) -> str:
    location = response.headers.get("location", "")
    fragment = urlparse(location).fragment
    assert fragment.startswith("embed_token="), f"Expected embed token in {location}"
    return fragment.split("=", 1)[1]


@pytest.mark.e2e
class TestEmbedEntryPoint:
    @pytest.fixture
    def test_app_with_secret(self, e2e_client, platform_admin):
        slug = f"embed-entry-test-{uuid.uuid4().hex[:8]}"
        app = _create_app(e2e_client, platform_admin.headers, slug)
        r = e2e_client.post(
            f"/api/applications/{app['id']}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "Test"},
        )
        assert r.status_code == 201, r.text
        raw_secret = r.json()["raw_secret"]
        yield {"app": app, "secret": raw_secret}
        _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_valid_hmac_returns_embed_token(self, e2e_client, test_app_with_secret):
        app = test_app_with_secret["app"]
        secret = test_app_with_secret["secret"]
        params = {"agent_id": "42"}
        hmac_val = _compute_hmac(params, secret)

        r = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={**params, "hmac": hmac_val},
            follow_redirects=False,
        )
        # Should redirect to /apps/{slug}#embed_token=<jwt>
        assert r.status_code == 302, r.text
        location = r.headers.get("location", "")
        assert f"/apps/{app['slug']}#embed_token=" in location

    def test_invalid_hmac_rejected(self, e2e_client, test_app_with_secret):
        app = test_app_with_secret["app"]
        r = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={"agent_id": "42", "hmac": "invalid-garbage"},
        )
        assert r.status_code == 403, r.text

    def test_missing_hmac_rejected(self, e2e_client, test_app_with_secret):
        app = test_app_with_secret["app"]
        r = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={"agent_id": "42"},
        )
        assert r.status_code == 403, r.text

    def test_no_embed_secrets_configured(self, e2e_client, platform_admin):
        """App with no embed secrets should reject all embed requests."""
        app = _create_app(
            e2e_client,
            platform_admin.headers,
            f"embed-no-secret-{uuid.uuid4().hex[:8]}",
        )
        try:
            r = e2e_client.get(
                f"/embed/apps/{app['slug']}",
                params={"agent_id": "42", "hmac": "anything"},
            )
            assert r.status_code == 403, r.text
        finally:
            _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_deactivated_secret_rejected(self, e2e_client, platform_admin, test_app_with_secret):
        """Deactivated secrets should not verify."""
        app = test_app_with_secret["app"]
        secret = test_app_with_secret["secret"]

        # Get the secret ID and deactivate it
        r = e2e_client.get(
            f"/api/applications/{app['id']}/embed-secrets",
            headers=platform_admin.headers,
        )
        secret_id = r.json()[0]["id"]
        e2e_client.patch(
            f"/api/applications/{app['id']}/embed-secrets/{secret_id}",
            headers=platform_admin.headers,
            json={"is_active": False},
        )

        # Now try to use it
        params = {"agent_id": "42"}
        hmac_val = _compute_hmac(params, secret)
        r = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={**params, "hmac": hmac_val},
        )
        assert r.status_code == 403, r.text

    def test_halopsa_unsigned_params_rejected(self, e2e_client, platform_admin):
        app = _create_app(
            e2e_client,
            platform_admin.headers,
            f"embed-halo-extra-{uuid.uuid4().hex[:8]}",
        )
        try:
            r = e2e_client.post(
                f"/api/applications/{app['id']}/embed-secrets",
                headers=platform_admin.headers,
                json={
                    "name": "Halo",
                    "secret": "halo-secret",
                    "hmac_scheme": "halopsa",
                },
            )
            assert r.status_code == 201, r.text

            params = {"agent_id": "42", "ticket_id": "1001"}
            r = e2e_client.get(
                f"/embed/apps/{app['slug']}",
                params={**params, "hmac": _compute_halo_hmac("42", "halo-secret")},
                follow_redirects=False,
            )
            assert r.status_code == 403, r.text
        finally:
            _delete_app(e2e_client, platform_admin.headers, app["id"])

    def test_embed_token_cannot_replay_to_other_app_or_mutate_app(
        self, e2e_client, platform_admin, test_app_with_secret
    ):
        app = test_app_with_secret["app"]
        other_app = _create_app(
            e2e_client,
            platform_admin.headers,
            f"embed-other-app-{uuid.uuid4().hex[:8]}",
        )
        try:
            params = {"agent_id": "42"}
            hmac_val = _compute_hmac(params, test_app_with_secret["secret"])
            r = e2e_client.get(
                f"/embed/apps/{app['slug']}",
                params={**params, "hmac": hmac_val},
                follow_redirects=False,
            )
            assert r.status_code == 302, r.text
            embed_headers = {"Authorization": f"Bearer {_extract_token_from_redirect(r)}"}

            r = e2e_client.get(
                f"/api/applications/{other_app['id']}/render",
                headers=embed_headers,
            )
            assert r.status_code == 403, r.text

            r = e2e_client.patch(
                f"/api/applications/{app['id']}",
                headers=embed_headers,
                json={"name": "mutated-by-embed"},
            )
            assert r.status_code == 403, r.text
        finally:
            _delete_app(e2e_client, platform_admin.headers, other_app["id"])
