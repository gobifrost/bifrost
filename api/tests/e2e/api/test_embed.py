"""E2E tests for HMAC-authenticated embed entry point."""

import hashlib
import hmac as hmac_module
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import delete

from tests.e2e.conftest import write_and_register


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
class TestEmbedAppWorkflowBinding:
    """An app-embed token may execute only workflows its own app references
    (or its own Solution install); it must not be able to reach another
    workflow via a raw workflow_id, nor retarget itself to another app via
    the X-Bifrost-App header or body app_id/form_id/solution_id overrides."""

    @pytest.fixture
    async def bound_app(self, e2e_client, platform_admin, db_session):
        """An inline_v1 app whose source references exactly one workflow,
        plus a second, unreferenced workflow the app has no ties to."""
        from src.models.orm.file_index import FileIndex

        tag = uuid4().hex[:8]
        slug = f"embed-wf-bind-{tag}"
        fn_slug = f"embed_wf_bind_{tag}"
        app = _create_app(e2e_client, platform_admin.headers, slug)

        allowed = write_and_register(
            e2e_client,
            platform_admin.headers,
            f"{fn_slug}_allowed.py",
            f'''"""E2E embed-bound workflow."""
from bifrost import workflow

@workflow(name="{fn_slug}_allowed", description="Referenced by the bound app")
async def {fn_slug}_allowed():
    return {{"ok": True}}
''',
            f"{fn_slug}_allowed",
        )
        denied = write_and_register(
            e2e_client,
            platform_admin.headers,
            f"{fn_slug}_denied.py",
            f'''"""E2E embed-unbound workflow."""
from bifrost import workflow

@workflow(name="{fn_slug}_denied", description="NOT referenced by the bound app")
async def {fn_slug}_denied():
    return {{"ok": True}}
''',
            f"{fn_slug}_denied",
        )
        for wf_id in (allowed["id"], denied["id"]):
            patched = e2e_client.patch(
                f"/api/workflows/{wf_id}",
                headers=platform_admin.headers,
                json={"access_level": "authenticated"},
            )
            assert patched.status_code == 200, patched.text

        db_session.add(
            FileIndex(
                path=f"apps/{slug}/page.tsx",
                content=f"useWorkflowQuery('{allowed['name']}')",
            )
        )
        await db_session.commit()

        secret_resp = e2e_client.post(
            f"/api/applications/{app['id']}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "Test"},
        )
        assert secret_resp.status_code == 201, secret_resp.text
        raw_secret = secret_resp.json()["raw_secret"]

        params = {"agent_id": "42"}
        hmac_val = _compute_hmac(params, raw_secret)
        embed_resp = e2e_client.get(
            f"/embed/apps/{app['slug']}",
            params={**params, "hmac": hmac_val},
            follow_redirects=False,
        )
        assert embed_resp.status_code == 302, embed_resp.text
        location = embed_resp.headers.get("location", "")
        embed_token = location.split("#embed_token=", 1)[1]

        yield {
            "app": app,
            "allowed_workflow_id": allowed["id"],
            "denied_workflow_id": denied["id"],
            "embed_token": embed_token,
        }

        _delete_app(e2e_client, platform_admin.headers, app["id"])
        for path in (f"{fn_slug}_allowed.py", f"{fn_slug}_denied.py"):
            e2e_client.delete(
                "/api/files/editor",
                headers=platform_admin.headers,
                params={"path": path},
            )
        from sqlalchemy import delete as sa_delete

        await db_session.execute(
            sa_delete(FileIndex).where(FileIndex.path == f"apps/{slug}/page.tsx")
        )
        await db_session.commit()

    async def test_embed_token_can_execute_its_own_referenced_workflow(
        self, e2e_client, bound_app
    ):
        r = e2e_client.post(
            "/api/workflows/execute",
            headers={"Authorization": f"Bearer {bound_app['embed_token']}"},
            json={"workflow_id": bound_app["allowed_workflow_id"], "input_data": {}},
        )
        assert r.status_code in (200, 201), r.text

    async def test_embed_token_cannot_execute_an_unreferenced_workflow(
        self, e2e_client, bound_app
    ):
        r = e2e_client.post(
            "/api/workflows/execute",
            headers={"Authorization": f"Bearer {bound_app['embed_token']}"},
            json={"workflow_id": bound_app["denied_workflow_id"], "input_data": {}},
        )
        assert r.status_code == 403, r.text

    async def test_embed_token_cannot_override_app_via_header(
        self, e2e_client, bound_app
    ):
        r = e2e_client.post(
            "/api/workflows/execute",
            headers={
                "Authorization": f"Bearer {bound_app['embed_token']}",
                "X-Bifrost-App": str(uuid4()),
            },
            json={"workflow_id": bound_app["allowed_workflow_id"], "input_data": {}},
        )
        assert r.status_code == 403, r.text

    async def test_embed_token_cannot_override_app_via_body(
        self, e2e_client, bound_app
    ):
        r = e2e_client.post(
            "/api/workflows/execute",
            headers={"Authorization": f"Bearer {bound_app['embed_token']}"},
            json={
                "workflow_id": bound_app["allowed_workflow_id"],
                "input_data": {},
                "app_id": str(uuid4()),
            },
        )
        assert r.status_code == 403, r.text

    async def test_embed_token_cannot_override_solution_via_body(
        self, e2e_client, bound_app
    ):
        r = e2e_client.post(
            "/api/workflows/execute",
            headers={"Authorization": f"Bearer {bound_app['embed_token']}"},
            json={
                "workflow_id": bound_app["allowed_workflow_id"],
                "input_data": {},
                "solution_id": str(uuid4()),
            },
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
