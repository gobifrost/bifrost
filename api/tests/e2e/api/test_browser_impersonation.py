"""Browser impersonation uses a browser-wide, fail-closed cookie marker."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from http.cookies import SimpleCookie

import httpx
import pytest

from src.core.security import create_access_token
from tests.e2e.fixtures.setup import _anonymous_client, _login_user
from tests.e2e.fixtures.users import E2EUser


def _cookie_value(response, name: str) -> str:
    for header in response.headers.get_list("set-cookie"):
        parsed = SimpleCookie()
        parsed.load(header)
        if name in parsed:
            return parsed[name].value
    raise AssertionError(f"Response did not set {name}")


@contextmanager
def _owned_browser_session(user: E2EUser) -> Iterator[tuple[httpx.Client, E2EUser]]:
    """Create and revoke a browser login without mutating shared fixture tokens."""
    caller = _login_user(replace(user))
    assert caller.access_token is not None
    assert caller.refresh_token is not None

    with _anonymous_client() as browser:
        try:
            yield browser, caller
        finally:
            ended = browser.post("/auth/impersonation/end", headers=caller.headers)
            assert ended.status_code == 204, ended.text

            logged_out = browser.post(
                "/auth/logout",
                headers=caller.headers,
                json={"refresh_token": caller.refresh_token},
            )
            assert logged_out.status_code == 200, logged_out.text


@pytest.mark.e2e
def test_browser_impersonation_cookie_overrides_human_credentials_and_exits_fail_closed(
    platform_admin, org1_user
) -> None:
    with _owned_browser_session(platform_admin) as (browser, caller):
        # The credential exchange used a separate client, so this precheck has no cookies.
        non_browser = browser.post(
            f"/auth/impersonate/{org1_user.user_id}",
            headers=caller.headers,
        )
        assert non_browser.status_code == 403

        browser.cookies.set("access_token", caller.access_token)
        browser.cookies.set("refresh_token", caller.refresh_token)
        original_cookie = (
            f"access_token={caller.access_token}; refresh_token={caller.refresh_token}"
        )
        started = browser.post(
            f"/auth/impersonate/{org1_user.user_id}",
            headers=caller.headers,
        )

        assert started.status_code == 200, started.text
        payload = started.json()
        assert payload["token_type"] == "bearer"
        assert payload["expires_in"] == 1800
        assert set(payload) == {"access_token", "token_type", "expires_in"}

        marker = _cookie_value(started, "impersonation_token")
        active_cookie = f"impersonation_token={marker}; {original_cookie}"

        # The marker drives ordinary browser requests, including cookie-only calls.
        target_me = browser.get("/auth/me")
        assert target_me.status_code == 200
        assert target_me.json()["id"] == str(org1_user.user_id)

        # A human bearer credential cannot silently escape browser-wide impersonation.
        target_with_bearer = browser.get("/auth/me", headers=caller.headers)
        assert target_with_bearer.status_code == 200
        assert target_with_bearer.json()["id"] == str(org1_user.user_id)

        # Refresh would otherwise replace the operator's login cookie and end the state.
        refresh = browser.post("/auth/refresh")
        assert refresh.status_code == 401

        for path in ("/auth/device/authorize", "/auth/device/deny"):
            device = browser.post(
                path,
                headers=caller.headers,
                json={"user_code": "ABCD-EFGH"},
            )
            assert device.status_code == 403

        # Temporary support authority cannot enroll a lasting target login.
        for path, body in (
            ("/auth/passkeys/register/options", {}),
            ("/auth/passkeys/register/verify", {"credential": {}}),
            ("/auth/mfa/totp/setup", {}),
            ("/auth/mfa/totp/verify", {"code": "123456"}),
            ("/auth/mfa/recovery-codes/regenerate", {"mfa_code": "123456"}),
        ):
            denied = browser.post(path, headers=caller.headers, json=body)
            assert denied.status_code == 403, (path, denied.text)
            assert "Exit impersonation" in denied.text

        token = payload["access_token"]
        nested = browser.post(
            f"/auth/impersonate/{caller.user_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert nested.status_code == 403

        # Exit deliberately authenticates the original operator, bypassing marker override.
        ended = browser.post("/auth/impersonation/end", headers=caller.headers)
        assert ended.status_code == 204
        assert "impersonation_token=\"\"" in ended.headers["set-cookie"]

        revoked = browser.get("/auth/me", headers={"Cookie": active_cookie})
        assert revoked.status_code == 401

        # Exit remains an idempotent cleanup when the session record is already gone.
        ended_again = browser.post(
            "/auth/impersonation/end",
            headers={**caller.headers, "Cookie": active_cookie},
        )
        assert ended_again.status_code == 204

        # An expired signed marker still cannot make the browser fall back to admin,
        # and Exit can clear it with the original live refresh session.
        expired_marker = create_access_token(
            {
                "sub": str(org1_user.user_id),
                "email": org1_user.email,
                "org_id": str(org1_user.organization_id),
                "impersonated_by_user_id": str(caller.user_id),
                "impersonation_session_id": "ed02c290-8570-4bc7-ae33-c8b391490159",
            },
            expires_delta=timedelta(seconds=-1),
        )
        expired_cookie = (
            f"impersonation_token={expired_marker}; refresh_token={caller.refresh_token}"
        )
        expired = browser.post("/auth/impersonation/end", headers={"Cookie": expired_cookie})
        assert expired.status_code == 204


@pytest.mark.e2e
def test_browser_impersonation_endpoint_rejects_a_user_without_permission(
    platform_admin, org1_user,
) -> None:
    with _owned_browser_session(org1_user) as (browser, caller):
        browser.cookies.set("access_token", caller.access_token)
        browser.cookies.set("refresh_token", caller.refresh_token)
        response = browser.post(
            f"/auth/impersonate/{platform_admin.user_id}",
            headers=caller.headers,
        )
        assert response.status_code == 403
        assert "permission" in response.json()["detail"].lower()
        assert "impersonation_token" not in response.cookies
