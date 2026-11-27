"""Exercise the settings and separate frame session against live services."""

from base64 import urlsafe_b64encode
from hashlib import sha256
from secrets import token_urlsafe

import httpx
import pytest

from src.core.security import decode_token
from tests.e2e.conftest import E2E_API_URL


@pytest.mark.e2e
def test_app_frame_signin_uses_a_separate_one_use_session(e2e_client, platform_admin, org1_user):
    settings = "/auth/app-frame-signin/settings"
    policy = "/embed/apps/frame-signin/frame-policy"
    code_path = "/auth/app-frame-signin/code"
    exchange = "/auth/app-frame-signin/exchange"
    parent_origin = "https://halo.example.com"
    initial = e2e_client.get(settings, headers=platform_admin.headers)
    assert initial.status_code == 200, initial.text

    with httpx.Client(base_url=E2E_API_URL) as popup, httpx.Client(base_url=E2E_API_URL) as frame:
        login = popup.post("/auth/refresh", json={"refresh_token": platform_admin.refresh_token})
        assert login.status_code == 200, login.text
        top_tokens = login.json()
        platform_admin.set_tokens(top_tokens["access_token"], top_tokens["refresh_token"])
        popup_headers = {"Sec-Fetch-Site": "same-origin", "X-CSRF-Token": popup.cookies["csrf_token"]}
        proof = token_urlsafe(32)
        challenge = urlsafe_b64encode(sha256(proof.encode()).digest()).decode().rstrip("=")
        popup_body = {"code": challenge}
        frame_headers = {"Sec-Fetch-Site": "same-origin"}
        try:
            denied = e2e_client.put(
                settings, headers=org1_user.headers, json={"allowed_origins": [parent_origin]},
            )
            assert denied.status_code == 403
            disabled = e2e_client.put(settings, headers=platform_admin.headers, json={"allowed_origins": []})
            assert disabled.status_code == 200, disabled.text
            assert frame.get(policy).headers["content-security-policy"] == "frame-ancestors 'none'"
            assert popup.post(code_path, headers=popup_headers, json=popup_body).status_code == 403

            saved = e2e_client.put(
                settings, headers=platform_admin.headers, json={"allowed_origins": [parent_origin]},
            )
            assert saved.status_code == 200, saved.text
            assert frame.get(policy).headers["content-security-policy"] == f"frame-ancestors {parent_origin}"

            assert frame.post(exchange, headers=frame_headers, json={"code": proof}).status_code == 202
            issued = popup.post(code_path, headers=popup_headers, json=popup_body)
            assert issued.status_code == 204, issued.text
            assert frame.post(exchange, headers=frame_headers, json=popup_body).status_code == 202
            redeemed = frame.post(exchange, headers=frame_headers, json={"code": proof})
            assert redeemed.status_code == 200, redeemed.text
            assert frame.post(exchange, headers=frame_headers, json={"code": proof}).status_code == 202

            frame_tokens = redeemed.json()
            top_claims = decode_token(top_tokens["access_token"], expected_type="access")
            frame_claims = decode_token(frame_tokens["access_token"], expected_type="access")
            assert top_claims and frame_claims
            assert frame_claims["sub"] == top_claims["sub"]
            assert frame_claims["sid"] != top_claims["sid"]
            transport_cookies = redeemed.headers.get_list("set-cookie")
            assert len(transport_cookies) == 2
            assert all("Secure; HttpOnly; SameSite=None; Partitioned" in value for value in transport_cookies)

            refreshed = frame.post(
                "/auth/app-frame-signin/refresh", json={"refresh_token": frame_tokens["refresh_token"]},
            )
            assert refreshed.status_code == 200, refreshed.text
            frame_tokens = refreshed.json()
            ended = frame.post(
                "/auth/app-frame-signin/logout",
                headers={"Authorization": f"Bearer {frame_tokens['access_token']}"},
            )
            assert ended.status_code == 200, ended.text
            assert frame.post(
                "/auth/app-frame-signin/refresh", json={"refresh_token": frame_tokens["refresh_token"]},
            ).status_code == 401

            top_refreshed = popup.post("/auth/refresh")
            assert top_refreshed.status_code == 200, top_refreshed.text
            top_tokens = top_refreshed.json()
            platform_admin.set_tokens(top_tokens["access_token"], top_tokens["refresh_token"])
        finally:
            restored = e2e_client.put(settings, headers=platform_admin.headers, json=initial.json())
            assert restored.status_code == 200, restored.text
