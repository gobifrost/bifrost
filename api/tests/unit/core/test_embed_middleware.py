"""Unit tests for the deny-by-default typed embed HTTP policy."""

from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.embed_middleware import EmbedScopeMiddleware, embed_request_allowed
from src.core.security import create_embed_access_token


FORM_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_FORM_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _form_claims(grant: str = "public") -> dict[str, object]:
    return {
        "embed": True,
        "embed_kind": "form",
        "form_id": FORM_ID,
        "grant": grant,
    }


def _app_claims() -> dict[str, object]:
    return {"embed": True, "embed_kind": "app", "app_id": "app-slug"}


def test_form_session_can_only_use_its_runtime_surface():
    claims = _form_claims()
    assert embed_request_allowed("GET", f"/api/forms/{FORM_ID}/runtime", claims)
    assert embed_request_allowed("POST", f"/api/forms/{FORM_ID}/startup", claims)
    assert embed_request_allowed("POST", f"/api/forms/{FORM_ID}/upload", claims)
    assert embed_request_allowed("POST", f"/api/forms/{FORM_ID}/submissions", claims)
    assert embed_request_allowed(
        "POST", f"/api/forms/{FORM_ID}/captcha/challenge", claims
    )
    assert embed_request_allowed(
        "POST", f"/api/forms/{FORM_ID}/fields/customer/options", claims
    )


def test_form_session_is_bound_to_one_form_and_denies_management_surfaces():
    claims = _form_claims()
    denied = (
        ("GET", f"/api/forms/{FORM_ID}"),
        ("GET", f"/api/forms/{OTHER_FORM_ID}/runtime"),
        ("POST", "/api/workflows/execute"),
        ("POST", f"/api/forms/{FORM_ID}/execute"),
        ("GET", "/api/executions/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        ("GET", "/api/forms"),
        ("PUT", f"/api/forms/{FORM_ID}"),
        ("GET", f"/api/forms/{FORM_ID}/embed-secrets"),
    )
    for method, path in denied:
        assert not embed_request_allowed(method, path, claims), (method, path)


def test_only_hmac_form_sessions_can_read_execution_details():
    path = "/api/executions/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert embed_request_allowed("GET", path, _form_claims("hmac"))
    assert not embed_request_allowed("GET", f"{path}/result", _form_claims("hmac"))
    assert not embed_request_allowed("POST", path, _form_claims("hmac"))
    assert not embed_request_allowed("GET", path, _form_claims("public"))
    assert not embed_request_allowed(
        "POST",
        f"/api/forms/{FORM_ID}/captcha/challenge",
        _form_claims("hmac"),
    )


def test_untyped_embed_claims_are_denied():
    assert not embed_request_allowed(
        "GET", f"/api/forms/{FORM_ID}/runtime", {"embed": True}
    )


def test_app_embed_retains_legacy_app_and_execution_surfaces():
    claims = _app_claims()
    assert embed_request_allowed("GET", "/api/applications/app-slug", claims)
    assert embed_request_allowed("POST", "/api/workflows/execute", claims)
    assert embed_request_allowed(
        "GET", "/api/executions/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", claims
    )


def _form_token() -> str:
    return create_embed_access_token(
        embed_kind="form",
        grant="hmac",
        resource_id=FORM_ID,
        org_id=None,
        verified_context={},
        expires_delta=timedelta(minutes=5),
    )


def test_form_policy_is_identical_for_bearer_and_embed_cookies():
    app = FastAPI()
    app.add_middleware(EmbedScopeMiddleware)

    @app.post("/api/workflows/execute")
    async def generic_execute():
        return {"unexpected": True}

    token = _form_token()
    with TestClient(app) as client:
        bearer = client.post(
            "/api/workflows/execute",
            headers={"Authorization": f"Bearer {token}"},
        )
        access_cookie = client.post(
            "/api/workflows/execute",
            cookies={"access_token": token},
        )
        embed_cookie = client.post(
            "/api/workflows/execute",
            cookies={"embed_token": token},
        )

    assert bearer.status_code == 403
    assert access_cookie.status_code == 403
    assert embed_cookie.status_code == 403


def test_form_session_rejects_oversized_body_before_router_invocation():
    app = FastAPI()
    app.add_middleware(EmbedScopeMiddleware)
    invoked = False

    @app.post(f"/api/forms/{FORM_ID}/submissions")
    async def submit():
        nonlocal invoked
        invoked = True
        return {"unexpected": True}

    with TestClient(app) as client:
        response = client.post(
            f"/api/forms/{FORM_ID}/submissions",
            content=b"x" * (512 * 1024 + 1),
            headers={"Authorization": f"Bearer {_form_token()}"},
        )

    assert response.status_code == 413
    assert invoked is False
