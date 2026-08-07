"""E2E tests for form embed HMAC flow."""

import hashlib
import hmac as hmac_module
import uuid

import pytest

from src.core.security import decode_token


def _compute_hmac(params: dict, secret: str) -> str:
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac_module.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


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
        assert location.startswith(
            f"/embedded/forms/hmac/{form_id}#embed_token="
        )

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

    def test_embed_verified_context_and_sanitized_runtime(
        self, e2e_client, form_with_secret
    ):
        """HMAC context is namespaced and only runtime data is exposed."""
        form_id = form_with_secret["form"]["id"]
        params = {
            "agent_id": "42",
            "theme": "dark",
            "header": "false",
            "background": "transparent",
        }
        hmac_sig = _compute_hmac(params, form_with_secret["secret"])

        # Get the embed token via the redirect
        r = e2e_client.get(
            f"/embed/forms/{form_id}",
            params={**params, "hmac": hmac_sig},
            follow_redirects=False,
        )
        assert r.status_code == 302
        location = r.headers["location"]
        assert location.startswith(
            f"/embedded/forms/hmac/{form_id}"
            "?theme=dark&header=false&background=transparent#embed_token="
        )
        embed_token = location.split("#embed_token=")[1]

        payload = decode_token(embed_token, expected_type="access")
        assert payload is not None
        assert payload["embed_kind"] == "form"
        assert payload["grant"] == "hmac"
        assert payload["form_id"] == form_id
        assert payload["verified_context"] == {"agent_id": "42"}
        assert "verified_params" not in payload

        embed_headers = {"Authorization": f"Bearer {embed_token}"}

        r = e2e_client.get(f"/api/forms/{form_id}/runtime", headers=embed_headers)
        assert r.status_code == 200
        runtime = r.json()
        assert runtime["id"] == form_id
        assert set(runtime) == {
            "id",
            "name",
            "description",
            "form_schema",
            "allowed_query_params",
            "has_startup",
            "captcha_required",
            "is_active",
        }
        assert runtime["captcha_required"] is False

        # Every credential location accepted by middleware must also reach the
        # same capability-bound principal in the authentication dependency.
        import httpx

        from tests.e2e.conftest import E2E_API_URL

        with httpx.Client(
            base_url=E2E_API_URL,
            timeout=60.0,
            cookies={"embed_token": embed_token},
        ) as cookie_client:
            cookie_runtime = cookie_client.get(f"/api/forms/{form_id}/runtime")
        assert cookie_runtime.status_code == 200, cookie_runtime.text
        assert cookie_runtime.json()["id"] == form_id

    def test_startup_handle_is_authoritative_bound_and_consumed(
        self, e2e_client, platform_admin, request
    ):
        from tests.e2e.conftest import write_and_register

        launch = write_and_register(
            e2e_client,
            platform_admin.headers,
            f"embed_launch_{uuid.uuid4().hex[:8]}.py",
            (
                "from bifrost import workflow\n\n"
                "@workflow(name='embed_launch')\n"
                "async def embed_launch():\n"
                "    return {'trusted': 'server'}\n"
            ),
            "embed_launch",
        )
        submit = write_and_register(
            e2e_client,
            platform_admin.headers,
            f"embed_submit_{uuid.uuid4().hex[:8]}.py",
            (
                "from bifrost import workflow\n\n"
                "@workflow(name='embed_submit')\n"
                "async def embed_submit(trusted: str = ''):\n"
                "    return {'received': trusted}\n"
            ),
            "embed_submit",
        )
        secret = f"startup-{uuid.uuid4().hex}"
        created = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Startup handle form",
                "workflow_id": submit["id"],
                "launch_workflow_id": launch["id"],
                "form_schema": {
                    "fields": [
                        {"name": "trusted", "label": "Trusted", "type": "text"}
                    ]
                },
            },
        )
        assert created.status_code == 201, created.text
        form_id = created.json()["id"]

        def cleanup_form() -> None:
            e2e_client.delete(
                f"/api/forms/{form_id}", headers=platform_admin.headers
            )

        request.addfinalizer(cleanup_form)
        assert e2e_client.post(
            f"/api/forms/{form_id}/embed-secrets",
            headers=platform_admin.headers,
            json={"name": "startup", "secret": secret},
        ).status_code == 201

        def mint_headers() -> dict[str, str]:
            params = {"trusted": "signed"}
            bootstrap = e2e_client.get(
                f"/embed/forms/{form_id}",
                params={**params, "hmac": _compute_hmac(params, secret)},
                follow_redirects=False,
            )
            token = bootstrap.headers["location"].split("#embed_token=")[1]
            return {"Authorization": f"Bearer {token}"}

        first_headers = mint_headers()
        startup = e2e_client.post(
            f"/api/forms/{form_id}/startup", headers=first_headers, json={}
        )
        assert startup.status_code == 200, startup.text
        startup_body = startup.json()
        assert startup_body["result"] == {"trusted": "server"}
        assert len(startup_body["startup_handle"]) >= 64
        assert startup_body["expires_at"]

        forged_echo = e2e_client.post(
            f"/api/forms/{form_id}/submissions",
            headers=first_headers,
            json={
                "form_data": {"trusted": "visitor"},
                "startup_data": {"trusted": "forged"},
                "startup_handle": startup_body["startup_handle"],
                "submission_nonce": "nonce-startup-000001",
            },
        )
        assert forged_echo.status_code == 422, forged_echo.text

        wrong_session = e2e_client.post(
            f"/api/forms/{form_id}/submissions",
            headers=mint_headers(),
            json={
                "form_data": {"trusted": "visitor"},
                "startup_handle": startup_body["startup_handle"],
                "submission_nonce": "nonce-startup-000002",
            },
        )
        assert wrong_session.status_code == 422, wrong_session.text

        accepted = e2e_client.post(
            f"/api/forms/{form_id}/submissions",
            headers=first_headers,
            json={
                "form_data": {"trusted": "visitor"},
                "startup_handle": startup_body["startup_handle"],
                "submission_nonce": "nonce-startup-000003",
            },
        )
        assert accepted.status_code == 200, accepted.text
        accepted_body = accepted.json()
        assert accepted_body["mode"] == "execution"
        assert accepted_body["execution_id"]
        assert "confirmation_markdown" not in accepted_body

        own_execution = e2e_client.get(
            f"/api/executions/{accepted_body['execution_id']}",
            headers=first_headers,
        )
        assert own_execution.status_code == 200, own_execution.text
        assert own_execution.json()["execution_id"] == accepted_body["execution_id"]

        unrelated_execution = e2e_client.get(
            f"/api/executions/{uuid.uuid4()}",
            headers=first_headers,
        )
        assert unrelated_execution.status_code == 403, unrelated_execution.text


def _mint_form_embed_token(e2e_client, platform_admin, *, organization_id=None):
    """Create a form (optionally in a specific org) with an embed secret and
    return (form, form-embed bearer token)."""
    secret = f"sec-{uuid.uuid4().hex[:8]}"
    body = {"name": f"FE-{uuid.uuid4().hex[:6]}", "form_schema": {"fields": []}}
    if organization_id is not None:
        body["organization_id"] = organization_id
    r = e2e_client.post("/api/forms", headers=platform_admin.headers, json=body)
    assert r.status_code == 201, r.text
    form = r.json()
    r = e2e_client.post(
        f"/api/forms/{form['id']}/embed-secrets",
        headers=platform_admin.headers,
        json={"name": "T", "secret": secret},
    )
    assert r.status_code == 201, r.text
    params = {"agent_id": "1"}
    r = e2e_client.get(
        f"/embed/forms/{form['id']}",
        params={**params, "hmac": _compute_hmac(params, secret)},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    token = r.headers["location"].split("#embed_token=")[1]
    return form, token


def _mint_app_embed_token(e2e_client, platform_admin, *, organization_id=None):
    """Create an app (optionally in a specific org) with an embed secret and
    return (app, app-embed bearer token)."""
    secret_name = f"app-{uuid.uuid4().hex[:8]}"
    body = {"name": secret_name, "slug": secret_name, "app_model": "inline_v1"}
    if organization_id is not None:
        body["organization_id"] = organization_id
    r = e2e_client.post("/api/applications", headers=platform_admin.headers, json=body)
    assert r.status_code == 201, r.text
    app = r.json()
    r = e2e_client.post(
        f"/api/applications/{app['id']}/embed-secrets",
        headers=platform_admin.headers,
        json={"name": "T"},
    )
    assert r.status_code in (200, 201), r.text
    raw_secret = r.json()["raw_secret"]
    params = {"agent_id": "1"}
    r = e2e_client.get(
        f"/embed/apps/{app['slug']}",
        params={**params, "hmac": _compute_hmac(params, raw_secret)},
        follow_redirects=False,
    )
    assert r.status_code == 302, r.text
    token = r.headers["location"].split("#embed_token=")[1]
    return app, token


@pytest.mark.e2e
class TestEmbedFormCrossTenantBinding:
    """EXT-1 NEW-I (HIGH, cross-tenant code execution): the embed short-circuits
    in get_form / execute_form / execute_startup_workflow / generate_upload_url
    skipped access control for ANY embed token with no binding between the
    token's form_id/app_id and the path form. An embed token minted for one
    resource in org H could READ and EXECUTE any form in any other org — a
    workflow run as sentinel in the victim's org, output returned to the
    attacker. The fix binds every embed short-circuit to the path form
    (form_id match, or app-embed → same-org only).

    These tests exercise all four sites with both attack vectors (form-embed
    token and app-embed token) and confirm the legitimate paths still work.
    """

    @pytest.fixture
    def victim_form(self, e2e_client, platform_admin, org2):
        """A form in org2 (the victim tenant) with a real workflow so the
        execute path would actually RUN it if the gate were skipped."""
        from tests.e2e.conftest import write_and_register

        wf = write_and_register(
            e2e_client,
            platform_admin.headers,
            f"newi_victim_{uuid.uuid4().hex[:8]}.py",
            (
                '"""NEW-I victim workflow"""\n'
                "from bifrost import workflow\n\n"
                "@workflow(name='newi_victim')\n"
                "async def newi_victim() -> dict:\n"
                "    return {'pwned': True}\n"
            ),
            "newi_victim",
        )
        r = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": f"victim-{uuid.uuid4().hex[:6]}",
                "form_schema": {"fields": []},
                "workflow_id": wf["id"],
                "launch_workflow_id": wf["id"],
                "access_level": "role_based",
                "organization_id": org2["id"],
            },
        )
        assert r.status_code == 201, r.text
        form = r.json()
        yield form
        e2e_client.delete(
            f"/api/forms/{form['id']}", headers=platform_admin.headers
        )

    # ---- attack vector 1: a form-embed token bound to a DIFFERENT form ----

    def _form_embed_headers(self, e2e_client, platform_admin, org1):
        _f1, token = _mint_form_embed_token(
            e2e_client, platform_admin, organization_id=org1["id"]
        )
        return {"Authorization": f"Bearer {token}"}

    def test_form_embed_cannot_read_other_form(
        self, e2e_client, platform_admin, org1, victim_form
    ):
        headers = self._form_embed_headers(e2e_client, platform_admin, org1)
        r = e2e_client.get(
            f"/api/forms/{victim_form['id']}/runtime", headers=headers
        )
        assert r.status_code in (403, 404), (
            f"form-embed token must not read a different/cross-tenant form: "
            f"{r.status_code} {r.text}"
        )

    def test_form_embed_cannot_execute_other_form(
        self, e2e_client, platform_admin, org1, victim_form
    ):
        headers = self._form_embed_headers(e2e_client, platform_admin, org1)
        r = e2e_client.post(
            f"/api/forms/{victim_form['id']}/submissions",
            headers=headers,
            json={"form_data": {}},
        )
        assert r.status_code in (403, 404), (
            f"form-embed token must not EXECUTE a cross-tenant form: "
            f"{r.status_code} {r.text}"
        )
        assert "pwned" not in r.text

    def test_form_embed_cannot_run_other_startup(
        self, e2e_client, platform_admin, org1, victim_form
    ):
        headers = self._form_embed_headers(e2e_client, platform_admin, org1)
        r = e2e_client.post(
            f"/api/forms/{victim_form['id']}/startup",
            headers=headers,
            json={},
        )
        assert r.status_code in (403, 404), (
            f"form-embed token must not run a cross-tenant launch workflow: "
            f"{r.status_code} {r.text}"
        )
        assert "pwned" not in r.text

    def test_form_embed_cannot_upload_to_other_form(
        self, e2e_client, platform_admin, org1, victim_form
    ):
        headers = self._form_embed_headers(e2e_client, platform_admin, org1)
        r = e2e_client.post(
            f"/api/forms/{victim_form['id']}/upload",
            headers=headers,
            json={"file_name": "x.txt", "content_type": "text/plain", "file_size": 4},
        )
        assert r.status_code in (403, 404), (
            f"form-embed token must not mint an upload URL for a cross-tenant "
            f"form: {r.status_code} {r.text}"
        )

    # ---- attack vector 2: an app-embed token in a DIFFERENT org ----

    def test_app_embed_cannot_read_cross_org_form(
        self, e2e_client, platform_admin, org1, victim_form
    ):
        _app, token = _mint_app_embed_token(
            e2e_client, platform_admin, organization_id=org1["id"]
        )
        headers = {"Authorization": f"Bearer {token}"}
        r = e2e_client.get(
            f"/api/forms/{victim_form['id']}/runtime", headers=headers
        )
        assert r.status_code in (403, 404), (
            f"app-embed token (org1) must not read an org2 form: "
            f"{r.status_code} {r.text}"
        )

    def test_app_embed_cannot_execute_cross_org_form(
        self, e2e_client, platform_admin, org1, victim_form
    ):
        _app, token = _mint_app_embed_token(
            e2e_client, platform_admin, organization_id=org1["id"]
        )
        headers = {"Authorization": f"Bearer {token}"}
        r = e2e_client.post(
            f"/api/forms/{victim_form['id']}/submissions",
            headers=headers,
            json={"form_data": {}},
        )
        assert r.status_code in (403, 404), (
            f"app-embed token (org1) must not EXECUTE an org2 form: "
            f"{r.status_code} {r.text}"
        )
        assert "pwned" not in r.text

    # ---- legitimate paths still work ----

    def test_form_embed_can_read_and_execute_own_form(
        self, e2e_client, platform_admin, org1
    ):
        # A form-embed token bound to F1 still reads + reaches the access gate
        # for F1 itself (execute returns a non-auth error only because the form
        # has no workflow — proving the gate let it THROUGH, not 403/404).
        form, token = _mint_form_embed_token(
            e2e_client, platform_admin, organization_id=org1["id"]
        )
        headers = {"Authorization": f"Bearer {token}"}
        try:
            r = e2e_client.get(f"/api/forms/{form['id']}/runtime", headers=headers)
            assert r.status_code == 200, r.text

            r = e2e_client.post(
                f"/api/forms/{form['id']}/submissions",
                headers=headers,
                json={"form_data": {}},
            )
            # The embed gate passed (not 403/404); the form has no workflow so
            # it 400s on the workflow-required check downstream.
            assert r.status_code not in (403,), r.text
            assert r.status_code != 404, r.text
        finally:
            e2e_client.delete(
                f"/api/forms/{form['id']}", headers=platform_admin.headers
            )

    def test_app_embed_can_read_same_org_form(
        self, e2e_client, platform_admin, org1
    ):
        # An app-embed token in org1 CAN read a form in org1 (same-org binding).
        _app, token = _mint_app_embed_token(
            e2e_client, platform_admin, organization_id=org1["id"]
        )
        r = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": f"same-org-{uuid.uuid4().hex[:6]}",
                "form_schema": {"fields": []},
                "organization_id": org1["id"],
            },
        )
        assert r.status_code == 201, r.text
        form = r.json()
        try:
            headers = {"Authorization": f"Bearer {token}"}
            r = e2e_client.get(f"/api/forms/{form['id']}/runtime", headers=headers)
            assert r.status_code == 200, (
                f"app-embed token (org1) must read an org1 form: "
                f"{r.status_code} {r.text}"
            )
        finally:
            e2e_client.delete(
                f"/api/forms/{form['id']}", headers=platform_admin.headers
            )
