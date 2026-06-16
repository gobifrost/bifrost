"""E2E tests for HMAC-authenticated embed entry point."""

import hashlib
import hmac as hmac_module
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import delete


def _create_app(client, headers, slug):
    r = client.post("/api/applications", headers=headers, json={"name": slug, "slug": slug, "app_model": "inline_v1"})
    assert r.status_code == 201, r.text
    return r.json()


def _delete_app(client, headers, app_id):
    r = client.delete(f"/api/applications/{app_id}", headers=headers)
    assert r.status_code in (200, 204), r.text


def _compute_hmac(params: dict[str, str], secret: str) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_module.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


@pytest.mark.e2e
class TestEmbedEntryPoint:
    @pytest.fixture
    def test_app_with_secret(self, e2e_client, platform_admin):
        app = _create_app(e2e_client, platform_admin.headers, "embed-entry-test")
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
        app = _create_app(e2e_client, platform_admin.headers, "embed-no-secret")
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


@pytest.mark.e2e
class TestEmbedMultiInstallSlug:
    """A slug shared by multiple solution installs must resolve via HMAC.

    Slug uniqueness is per-install (migration 20260605_app_identity_per_install):
    the same solution installed for two orgs yields 2+ Application rows with one
    slug. The embed secret is bound to ONE row, so HMAC verification picks the app.
    """

    @pytest.fixture
    async def multi_install(self, db_session, org1, org2):
        from src.core.security import encrypt_secret
        from src.models.orm.app_embed_secrets import AppEmbedSecret
        from src.models.orm.applications import Application
        from src.models.orm.solutions import Solution

        slug = "embed-multi-install"
        sol_a = Solution(
            id=uuid4(),
            slug="embed-multi-sol",
            name="Embed Multi Sol A",
            organization_id=UUID(org1["id"]),
        )
        sol_b = Solution(
            id=uuid4(),
            slug="embed-multi-sol",
            name="Embed Multi Sol B",
            organization_id=UUID(org2["id"]),
        )
        app_a = Application(
            id=uuid4(),
            name="Embed Multi A",
            slug=slug,
            repo_path=f"_solutions/{sol_a.id}/apps/{slug}",
            organization_id=UUID(org1["id"]),
            solution_id=sol_a.id,
        )
        app_b = Application(
            id=uuid4(),
            name="Embed Multi B",
            slug=slug,
            repo_path=f"_solutions/{sol_b.id}/apps/{slug}",
            organization_id=UUID(org2["id"]),
            solution_id=sol_b.id,
        )
        raw_secret = "embed-multi-install-org-b-secret"
        secret_b = AppEmbedSecret(
            id=uuid4(),
            application_id=app_b.id,
            name="Org B secret",
            secret_encrypted=encrypt_secret(raw_secret),
            hmac_scheme="shopify",
            is_active=True,
        )
        # No ORM relationship links Application to Solution, so the unit of
        # work won't order these inserts — flush solutions before apps.
        db_session.add_all([sol_a, sol_b])
        await db_session.flush()
        db_session.add_all([app_a, app_b])
        await db_session.flush()
        db_session.add(secret_b)
        await db_session.commit()

        yield {
            "slug": slug,
            "app_a_id": app_a.id,
            "app_b_id": app_b.id,
            "secret_b": raw_secret,
        }

        await db_session.execute(
            delete(Application).where(Application.id.in_([app_a.id, app_b.id]))
        )
        await db_session.execute(
            delete(Solution).where(Solution.id.in_([sol_a.id, sol_b.id]))
        )
        await db_session.commit()

    async def test_hmac_disambiguates_multi_install_slug(self, e2e_client, multi_install):
        """Signing with org B's secret must resolve org B's Application row."""
        params = {"agent_id": "42"}
        hmac_val = _compute_hmac(params, multi_install["secret_b"])

        r = e2e_client.get(
            f"/embed/apps/{multi_install['slug']}",
            params={**params, "hmac": hmac_val},
            follow_redirects=False,
        )
        assert r.status_code == 302, r.text
        location = r.headers.get("location", "")
        assert "#embed_token=" in location
        token = location.split("#embed_token=", 1)[1]
        payload = jwt.decode(token, options={"verify_signature": False})
        assert payload["app_id"] == str(multi_install["app_b_id"])

    async def test_hmac_matching_neither_install_rejected(self, e2e_client, multi_install):
        """An HMAC signed by neither install's secret is rejected with 403."""
        params = {"agent_id": "42"}
        hmac_val = _compute_hmac(params, "not-anybodys-secret")

        r = e2e_client.get(
            f"/embed/apps/{multi_install['slug']}",
            params={**params, "hmac": hmac_val},
            follow_redirects=False,
        )
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "Invalid HMAC signature"
