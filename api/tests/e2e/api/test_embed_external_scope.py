"""OPEN-D e2e: embed tokens are scope-restricted on EVERY auth path and are
external-equivalent.

Two halves (defense in depth):

1. EmbedScopeMiddleware inspects the token on every path auth accepts it —
   the Authorization header AND the access_token / embed_token cookies.
   Pre-fix, replaying the embed token as a cookie reached non-allowlisted
   endpoints (e.g. /api/sdk/config/get) and returned org+global config with
   DECRYPTED global secrets.
2. Embed tokens are minted with is_external=True, so the external data gates
   (config/knowledge/tables) engage even if a path slips past the allowlist.
   Embed rendering stays HMAC-pre-authorized: the token loads ONLY its bound
   app (and nothing else).
"""

import base64
import hashlib
import hmac as hmac_module
import json
import uuid
from urllib.parse import urlparse

import pytest

pytestmark = pytest.mark.e2e

SUFFIX = uuid.uuid4().hex[:8]


def _compute_hmac(params: dict[str, str], secret: str) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_module.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def _extract_token_from_redirect(response) -> str:
    location = response.headers.get("location", "")
    fragment = urlparse(location).fragment
    assert fragment.startswith("embed_token="), (
        f"Expected embed_token in fragment, got: {fragment}"
    )
    return fragment.split("=", 1)[1]


def _jwt_claims(token: str) -> dict:
    """Decode the JWT payload WITHOUT verification (claims inspection only)."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


@pytest.fixture(scope="module")
def embed_session(e2e_client, platform_admin):
    """An app with an embed secret + a minted embed token."""
    r = e2e_client.post(
        "/api/applications",
        headers=platform_admin.headers,
        json={"name": f"embed-ext-{SUFFIX}", "slug": f"embed-ext-{SUFFIX}", "app_model": "inline_v1"},
    )
    assert r.status_code == 201, r.text
    app = r.json()

    r = e2e_client.post(
        f"/api/applications/{app['id']}/embed-secrets",
        headers=platform_admin.headers,
        json={"name": "Test"},
    )
    assert r.status_code in (200, 201), r.text
    raw_secret = r.json()["raw_secret"]

    params = {"client": "acme"}
    r = e2e_client.get(
        f"/embed/apps/{app['slug']}",
        params={**params, "hmac": _compute_hmac(params, raw_secret)},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    token = _extract_token_from_redirect(r)

    yield {"app": app, "token": token}

    e2e_client.delete(
        f"/api/applications/{app['id']}", headers=platform_admin.headers
    )


@pytest.fixture(scope="module")
def global_secret_config(e2e_client, platform_admin):
    """A GLOBAL secret config — the decrypted-leak canary."""
    key = f"embed_ext_global_secret_{SUFFIX}"
    r = e2e_client.post(
        "/api/config",
        headers=platform_admin.headers,
        json={
            "key": key,
            "value": "EMBED-GLOBAL-SECRET",
            "type": "secret",
            "organization_id": None,
        },
    )
    assert r.status_code in (200, 201), r.text
    yield key
    e2e_client.request(
        "DELETE",
        "/api/sdk/config/delete",
        headers=platform_admin.headers,
        json={"key": key, "scope": "global"},
    )


class TestEmbedCookieScopeBypass:
    """Half 1: the middleware must inspect cookie tokens, not just the header."""

    def _replay(self, cookie_name, token, key):
        """A fresh client whose ONLY cookie is the embed token under the given
        name — avoids the module client's ambiguous per-request cookie jar."""
        import httpx

        from tests.e2e.conftest import E2E_API_URL

        with httpx.Client(
            base_url=E2E_API_URL,
            timeout=60.0,
            cookies={cookie_name: token},
        ) as c:
            return c.post("/api/sdk/config/get", json={"key": key})

    def test_access_token_cookie_replay_blocked_on_sdk_config(
        self, embed_session, global_secret_config
    ):
        resp = self._replay(
            "access_token", embed_session["token"], global_secret_config
        )
        assert resp.status_code == 403, (
            f"embed token replayed as access_token cookie must be scope-"
            f"restricted: {resp.status_code} {resp.text}"
        )
        assert "Embed tokens cannot access" in resp.text
        assert "EMBED-GLOBAL-SECRET" not in resp.text, (
            "decrypted global secret leaked to an embed cookie session"
        )

    def test_embed_token_cookie_replay_blocked_on_sdk_config(
        self, embed_session, global_secret_config
    ):
        resp = self._replay(
            "embed_token", embed_session["token"], global_secret_config
        )
        assert resp.status_code == 403, (
            f"embed token in embed_token cookie must be scope-restricted: "
            f"{resp.status_code} {resp.text}"
        )
        assert "EMBED-GLOBAL-SECRET" not in resp.text

    def test_bearer_header_still_blocked_on_sdk_config(
        self, e2e_client, embed_session, global_secret_config
    ):
        resp = e2e_client.post(
            "/api/sdk/config/get",
            json={"key": global_secret_config},
            headers={"Authorization": f"Bearer {embed_session['token']}"},
        )
        assert resp.status_code == 403, resp.text
        assert "EMBED-GLOBAL-SECRET" not in resp.text


class TestEmbedTokenExternalEquivalence:
    """Half 2: embed tokens are minted external-equivalent."""

    def test_embed_token_carries_is_external(self, embed_session):
        claims = _jwt_claims(embed_session["token"])
        assert claims.get("embed") is True
        assert claims.get("is_external") is True, (
            "embed tokens must be minted is_external=True (OPEN-D)"
        )
        assert claims.get("is_superuser") is False


class TestEmbedAppBinding:
    """Embed rendering keeps working under is_external=True — via HMAC
    pre-authorization bound to the token's app_id, not via access tiers."""

    def test_embed_token_loads_its_bound_app(self, e2e_client, embed_session):
        app = embed_session["app"]
        resp = e2e_client.get(
            f"/api/applications/{app['slug']}",
            headers={"Authorization": f"Bearer {embed_session['token']}"},
        )
        assert resp.status_code == 200, (
            f"embed token must load its own app: {resp.status_code} {resp.text}"
        )
        assert resp.json()["id"] == app["id"]

    def test_embed_token_renders_its_bound_app(self, e2e_client, embed_session):
        # The render endpoint (app_code_files.get_application_or_404 path) must
        # also stay open for the bound app under is_external=True.
        app = embed_session["app"]
        resp = e2e_client.get(
            f"/api/applications/{app['id']}/render",
            headers={"Authorization": f"Bearer {embed_session['token']}"},
        )
        assert resp.status_code == 200, (
            f"embed token must render its own app: {resp.status_code} {resp.text}"
        )

    def test_embed_token_cannot_load_other_apps(
        self, e2e_client, platform_admin, embed_session
    ):
        r = e2e_client.post(
            "/api/applications",
            headers=platform_admin.headers,
            json={"name": f"embed-other-{SUFFIX}", "slug": f"embed-other-{SUFFIX}", "app_model": "inline_v1"},
        )
        assert r.status_code == 201, r.text
        other = r.json()
        try:
            # Neither the slug helper (applications.py) nor the by-id helper
            # (app_code_files.py /render) may resolve an unbound app.
            resp = e2e_client.get(
                f"/api/applications/{other['slug']}",
                headers={"Authorization": f"Bearer {embed_session['token']}"},
            )
            assert resp.status_code == 404, (
                f"embed token must NOT load an app it is not bound to: "
                f"{resp.status_code} {resp.text}"
            )
            resp = e2e_client.get(
                f"/api/applications/{other['id']}/render",
                headers={"Authorization": f"Bearer {embed_session['token']}"},
            )
            assert resp.status_code == 404, (
                f"embed token must NOT render an app it is not bound to: "
                f"{resp.status_code} {resp.text}"
            )
        finally:
            e2e_client.delete(
                f"/api/applications/{other['id']}",
                headers=platform_admin.headers,
            )
