"""E2E tests for form embed HMAC flow."""

import hashlib
import hmac as hmac_module
import uuid
from urllib.parse import urlparse

import pytest


def _compute_hmac(params: dict, secret: str) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_module.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _extract_token_from_redirect(response) -> str:
    location = response.headers.get("location", "")
    fragment = urlparse(location).fragment
    assert fragment.startswith("embed_token="), f"Expected embed token in {location}"
    return fragment.split("=", 1)[1]


@pytest.mark.e2e
class TestFormEmbed:
    @pytest.fixture
    def form_with_secret(self, e2e_client, platform_admin):
        """Create a form with an embed secret."""
        # Create form
        r = e2e_client.post("/api/forms", headers=platform_admin.headers, json={
            "name": "Embed Test Form",
            "form_schema": {"fields": []},
        })
        assert r.status_code == 201, r.text
        form = r.json()

        # Create embed secret
        r = e2e_client.post(
            f"/api/forms/{form['id']}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "Test", "secret": "test-secret-123"},
        )
        assert r.status_code == 201, r.text

        yield {"form": form, "secret": "test-secret-123"}

    def test_embed_form_valid_hmac(self, e2e_client, form_with_secret):
        """Valid HMAC should redirect with embed token in fragment."""
        form_id = form_with_secret["form"]["id"]
        params = {"agent_id": "42", "ticket_id": "1001"}
        hmac_sig = _compute_hmac(params, form_with_secret["secret"])

        r = e2e_client.get(
            f"/embed/forms/{form_id}",
            params={**params, "hmac": hmac_sig},
            follow_redirects=False,
        )
        assert r.status_code == 302
        location = r.headers["location"]
        assert location.startswith(f"/execute/{form_id}#embed_token=")

    def test_embed_form_invalid_hmac(self, e2e_client, form_with_secret):
        """Invalid HMAC should return 403."""
        form_id = form_with_secret["form"]["id"]
        r = e2e_client.get(
            f"/embed/forms/{form_id}",
            params={"agent_id": "42", "hmac": "invalid"},
        )
        assert r.status_code == 403

    def test_embed_form_missing_hmac(self, e2e_client, form_with_secret):
        """Missing HMAC param should return 403."""
        form_id = form_with_secret["form"]["id"]
        r = e2e_client.get(
            f"/embed/forms/{form_id}",
            params={"agent_id": "42"},
        )
        assert r.status_code == 403

    def test_embed_form_nonexistent(self, e2e_client):
        """Non-existent form should return 404."""
        r = e2e_client.get(
            f"/embed/forms/{uuid.uuid4()}",
            params={"hmac": "anything"},
        )
        assert r.status_code == 404

    def test_embed_verified_params_in_token(self, e2e_client, form_with_secret):
        """Verified params from HMAC should be accessible via the embed token."""
        form_id = form_with_secret["form"]["id"]
        params = {"agent_id": "42"}
        hmac_sig = _compute_hmac(params, form_with_secret["secret"])

        # Get the embed token via the redirect
        r = e2e_client.get(
            f"/embed/forms/{form_id}",
            params={**params, "hmac": hmac_sig},
            follow_redirects=False,
        )
        assert r.status_code == 302
        location = r.headers["location"]
        embed_token = location.split("#embed_token=")[1]

        # Use the embed token to call the form endpoint
        embed_headers = {"Authorization": f"Bearer {embed_token}"}

        # Verify the form is accessible with embed token
        r = e2e_client.get(f"/api/forms/{form_id}", headers=embed_headers)
        assert r.status_code == 200

    def test_form_embed_token_cannot_replay_to_another_form(
        self, e2e_client, platform_admin, form_with_secret
    ):
        form_id = form_with_secret["form"]["id"]
        r = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Other Embed Test Form",
                "form_schema": {"fields": []},
            },
        )
        assert r.status_code == 201, r.text
        other_form_id = r.json()["id"]

        params = {"agent_id": "42"}
        hmac_sig = _compute_hmac(params, form_with_secret["secret"])
        r = e2e_client.get(
            f"/embed/forms/{form_id}",
            params={**params, "hmac": hmac_sig},
            follow_redirects=False,
        )
        assert r.status_code == 302, r.text
        embed_headers = {"Authorization": f"Bearer {_extract_token_from_redirect(r)}"}

        r = e2e_client.get(f"/api/forms/{other_form_id}", headers=embed_headers)
        assert r.status_code == 403, r.text
