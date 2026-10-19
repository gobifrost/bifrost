"""Browser impersonation uses a browser-wide, fail-closed cookie marker."""

from __future__ import annotations

from datetime import timedelta
from http.cookies import SimpleCookie

import pytest

from src.core.security import create_access_token


def _cookie_value(response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        parsed = SimpleCookie()
        parsed.load(header)
        if name in parsed:
            return parsed[name].value
    raise AssertionError(f"Response did not set {name}")


@pytest.mark.e2e
def test_browser_impersonation_cookie_overrides_human_credentials_and_exits_fail_closed(
    e2e_client, platform_admin, org1_user
) -> None:
    non_browser = e2e_client.post(
        f"/auth/impersonate/{org1_user.user_id}",
        headers=platform_admin.headers,
    )
    assert non_browser.status_code == 403

    original_cookie = (
        f"access_token={platform_admin.access_token}; "
        f"refresh_token={platform_admin.refresh_token}"
    )
    started = e2e_client.post(
        f"/auth/impersonate/{org1_user.user_id}",
        headers={
            **platform_admin.headers,
            "Cookie": original_cookie,
        },
    )

    assert started.status_code == 200, started.text
    payload = started.json()
    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] == 1800
    assert set(payload) == {"access_token", "token_type", "expires_in"}

    marker = _cookie_value(started, "impersonation_token")
    active_cookie = f"impersonation_token={marker}; {original_cookie}"

    # The marker drives ordinary browser requests, including cookie-only calls.
    target_me = e2e_client.get("/auth/me", headers={"Cookie": active_cookie})
    assert target_me.status_code == 200
    assert target_me.json()["id"] == str(org1_user.user_id)

    # A human bearer credential cannot silently escape browser-wide impersonation.
    target_with_bearer = e2e_client.get(
        "/auth/me",
        headers={**platform_admin.headers, "Cookie": active_cookie},
    )
    assert target_with_bearer.status_code == 200
    assert target_with_bearer.json()["id"] == str(org1_user.user_id)

    # Refresh would otherwise replace the operator's login cookie and end the state.
    refresh = e2e_client.post("/auth/refresh", headers={"Cookie": active_cookie})
    assert refresh.status_code == 401

    device = e2e_client.post(
        "/auth/device/authorize",
        headers={**platform_admin.headers, "Cookie": active_cookie},
        json={"user_code": "ABCD-EFGH"},
    )
    assert device.status_code == 403

    token = payload["access_token"]
    nested = e2e_client.post(
        f"/auth/impersonate/{platform_admin.user_id}",
        headers={"Authorization": f"Bearer {token}", "Cookie": active_cookie},
    )
    assert nested.status_code == 403

    # Exit deliberately authenticates the original operator, bypassing marker override.
    ended = e2e_client.post(
        "/auth/impersonation/end",
        headers={**platform_admin.headers, "Cookie": active_cookie},
    )
    assert ended.status_code == 204
    assert "impersonation_token=\"\"" in ended.headers["set-cookie"]

    revoked = e2e_client.get("/auth/me", headers={"Cookie": active_cookie})
    assert revoked.status_code == 401

    # Exit remains an idempotent cleanup when the session record is already gone.
    ended_again = e2e_client.post(
        "/auth/impersonation/end",
        headers={**platform_admin.headers, "Cookie": active_cookie},
    )
    assert ended_again.status_code == 204

    # An expired signed marker still cannot make the browser fall back to admin,
    # and Exit can clear it with the original live refresh session.
    expired_marker = create_access_token(
        {
            "sub": str(org1_user.user_id),
            "email": org1_user.email,
            "org_id": str(org1_user.organization_id),
            "impersonated_by_user_id": str(platform_admin.user_id),
            "impersonation_session_id": "ed02c290-8570-4bc7-ae33-c8b391490159",
        },
        expires_delta=timedelta(seconds=-1),
    )
    expired_cookie = f"impersonation_token={expired_marker}; refresh_token={platform_admin.refresh_token}"
    expired = e2e_client.post("/auth/impersonation/end", headers={"Cookie": expired_cookie})
    assert expired.status_code == 204
