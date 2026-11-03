"""E2E coverage for SSO-only login enforcement (admin-exempt).

Exercises the backend boundary directly: preference validators, the
delete-last-provider guard, ``GET /auth/status`` flags, and the 403 gates on
password/passkey surfaces. Non-admin is refused, platform admins stay exempt,
and the token-free bootstrap path is covered by the service unit tests
(``test_login_method_allowed_truth_table``) since a live stack always has
users.
"""

import pytest

from tests.e2e.fixtures.setup import _register_and_authenticate_user
from tests.e2e.fixtures.users import E2EUser
from tests.helpers.totp import generate_totp_code

MICROSOFT_CONFIG = {
    "client_id": "sso-only-client",
    "client_secret": "sso-only-secret",
    "tenant_id": "organizations",
}

OPEN_PREFERENCE = {
    "auto_redirect_to_sso": False,
    "default_sso_provider": None,
    "password_login_enabled": True,
    "passkey_login_enabled": True,
}

SSO_ONLY_PREFERENCE = {
    "auto_redirect_to_sso": False,
    "default_sso_provider": None,
    "password_login_enabled": False,
    "passkey_login_enabled": False,
}

PASSWORD_SSO_DETAIL = "Password login is disabled (SSO-only). Sign in with SSO."
PASSKEY_SSO_DETAIL = "Passkey login is disabled (SSO-only)."


def _enable_sso_only(e2e_client, headers) -> None:
    response = e2e_client.put(
        "/api/settings/oauth/microsoft", headers=headers, json=MICROSOFT_CONFIG
    )
    assert response.status_code == 200, response.text
    response = e2e_client.put(
        "/api/settings/oauth/login-preference",
        headers=headers,
        json=SSO_ONLY_PREFERENCE,
    )
    assert response.status_code == 200, response.text


def _reset_policy(e2e_client, headers) -> None:
    e2e_client.put(
        "/api/settings/oauth/login-preference", headers=headers, json=OPEN_PREFERENCE
    )
    e2e_client.delete("/api/settings/oauth/microsoft", headers=headers)


def _deactivate_and_delete(e2e_client, headers, user_id: str) -> None:
    e2e_client.patch(
        f"/api/users/{user_id}", headers=headers, json={"is_active": False}
    )
    e2e_client.delete(f"/api/users/{user_id}", headers=headers)


@pytest.mark.e2e
class TestSSOOnlyPreferenceValidation:
    def test_enable_without_provider_rejected(self, e2e_client, platform_admin):
        headers = platform_admin.headers
        try:
            e2e_client.delete("/api/settings/oauth/microsoft", headers=headers)
            response = e2e_client.put(
                "/api/settings/oauth/login-preference",
                headers=headers,
                json={**OPEN_PREFERENCE, "password_login_enabled": False},
            )
            assert response.status_code == 400
            assert "must be configured" in response.json()["detail"]

            response = e2e_client.put(
                "/api/settings/oauth/login-preference",
                headers=headers,
                json={**OPEN_PREFERENCE, "passkey_login_enabled": False},
            )
            assert response.status_code == 400
        finally:
            _reset_policy(e2e_client, headers)

    def test_delete_last_provider_conflicts_while_enforced(
        self, e2e_client, platform_admin
    ):
        headers = platform_admin.headers
        try:
            _enable_sso_only(e2e_client, headers)
            response = e2e_client.delete(
                "/api/settings/oauth/microsoft", headers=headers
            )
            assert response.status_code == 409
            assert "last SSO provider" in response.json()["detail"]
        finally:
            _reset_policy(e2e_client, headers)

    def test_status_reflects_flags(self, e2e_client, platform_admin):
        headers = platform_admin.headers
        try:
            _enable_sso_only(e2e_client, headers)
            response = e2e_client.get("/auth/status")
            assert response.status_code == 200
            body = response.json()
            assert body["password_login_enabled"] is False
            assert body["passkey_login_enabled"] is False
        finally:
            _reset_policy(e2e_client, headers)

        response = e2e_client.get("/auth/status")
        assert response.status_code == 200
        assert response.json()["password_login_enabled"] is True
        assert response.json()["passkey_login_enabled"] is True


@pytest.mark.e2e
class TestSSOOnlyPasswordGates:
    def test_login_non_admin_refused_admin_allowed(
        self, e2e_client, private_client, platform_admin, org1_user
    ):
        headers = platform_admin.headers
        try:
            _enable_sso_only(e2e_client, headers)

            refused = private_client.post(
                "/auth/login",
                data={"username": org1_user.email, "password": org1_user.password},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert refused.status_code == 403
            assert refused.json()["detail"] == PASSWORD_SSO_DETAIL

            allowed = private_client.post(
                "/auth/login",
                data={
                    "username": platform_admin.email,
                    "password": platform_admin.password,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert allowed.status_code == 200
            body = allowed.json()
            assert body.get("mfa_required") is True
            mfa_response = private_client.post(
                "/auth/mfa/login",
                json={
                    "mfa_token": body["mfa_token"],
                    "code": generate_totp_code(platform_admin.totp_secret),
                },
            )
            assert mfa_response.status_code == 200
            assert mfa_response.json()["access_token"]
        finally:
            _reset_policy(e2e_client, headers)

    def test_mfa_login_refused_for_non_admin(
        self, e2e_client, private_client, platform_admin, org1_user
    ):
        # Capture an MFA token before enforcement, then verify it is refused.
        login = private_client.post(
            "/auth/login",
            data={"username": org1_user.email, "password": org1_user.password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert login.status_code == 200
        mfa_token = login.json().get("mfa_token")
        assert mfa_token, f"No MFA token: {login.text}"

        headers = platform_admin.headers
        try:
            _enable_sso_only(e2e_client, headers)
            response = private_client.post(
                "/auth/mfa/login",
                json={
                    "mfa_token": mfa_token,
                    "code": generate_totp_code(org1_user.totp_secret),
                },
            )
            assert response.status_code == 403
            assert response.json()["detail"] == PASSWORD_SSO_DETAIL
        finally:
            _reset_policy(e2e_client, headers)

    def test_mfa_verify_refused_for_non_admin(
        self, e2e_client, private_client, platform_admin, org1
    ):
        # A freshly registered user holds an mfa_setup token (no MFA enrolled).
        email = "sso-mfa-verify@gobifrost.dev"
        register = private_client.post(
            "/auth/register",
            json={"email": email, "password": "SsoVerify123!", "name": "SSO MFA"},
        )
        assert register.status_code == 201, register.text
        user_id = register.json()["id"]

        login = private_client.post(
            "/auth/login",
            data={"username": email, "password": "SsoVerify123!"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert login.status_code == 200
        setup_token = login.json().get("mfa_token")
        assert setup_token, f"No setup token: {login.text}"

        headers = platform_admin.headers
        try:
            _enable_sso_only(e2e_client, headers)
            response = private_client.post(
                "/auth/mfa/verify",
                headers={"Authorization": f"Bearer {setup_token}"},
                json={"code": "123456"},
            )
            assert response.status_code == 403
            assert response.json()["detail"] == PASSWORD_SSO_DETAIL
        finally:
            _reset_policy(e2e_client, headers)
            _deactivate_and_delete(e2e_client, headers, user_id)

    def test_register_refused_while_enforced(
        self, e2e_client, private_client, platform_admin
    ):
        headers = platform_admin.headers
        try:
            _enable_sso_only(e2e_client, headers)
            response = private_client.post(
                "/auth/register",
                json={
                    "email": "sso-blocked@gobifrost.dev",
                    "password": "SsoBlocked123!",
                    "name": "SSO Blocked",
                },
            )
            assert response.status_code == 403
            assert response.json()["detail"] == PASSWORD_SSO_DETAIL
        finally:
            _reset_policy(e2e_client, headers)

    def test_register_from_invite_password_variant_refused(
        self, e2e_client, platform_admin, org1
    ):
        headers = platform_admin.headers
        create_resp = e2e_client.post(
            "/api/users",
            headers=headers,
            json={
                "email": "sso-inv@gobifrost.dev",
                "name": "SSO Invite",
                "organization_id": org1["id"],
                "is_superuser": False,
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        user_id = create_resp.json()["id"]
        regen = e2e_client.post(
            f"/api/users/{user_id}/invite/regenerate", headers=headers
        )
        token = regen.json()["registration_url"].split("token=", 1)[1]

        try:
            _enable_sso_only(e2e_client, headers)

            refused = e2e_client.post(
                "/auth/register-from-invite",
                json={"token": token, "password": "SsoInvite123!"},
            )
            assert refused.status_code == 403
            assert refused.json()["detail"] == PASSWORD_SSO_DETAIL

            # The passwordless (passkey/SSO-compatible) variant stays open.
            allowed = e2e_client.post(
                "/auth/register-from-invite", json={"token": token}
            )
            assert allowed.status_code == 200, allowed.text
        finally:
            _reset_policy(e2e_client, headers)
            _deactivate_and_delete(e2e_client, headers, user_id)

    def test_change_password_non_admin_refused_admin_allowed(
        self, e2e_client, platform_admin, org1_user, org1
    ):
        headers = platform_admin.headers
        temp_admin = E2EUser(
            email="sso-temp-admin@gobifrost.dev",
            password="TempAdmin123!",
            name="SSO Temp Admin",
            organization_id=org1["id"],
        )
        create_resp = e2e_client.post(
            "/api/users",
            headers=headers,
            json={
                "email": temp_admin.email,
                "name": temp_admin.name,
                "organization_id": org1["id"],
                "is_superuser": True,
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        temp_admin.user_id = create_resp.json()["id"]
        _register_and_authenticate_user(temp_admin)

        try:
            _enable_sso_only(e2e_client, headers)

            refused = e2e_client.post(
                "/api/profile/password",
                headers=org1_user.headers,
                json={
                    "current_password": org1_user.password,
                    "new_password": "NewAlicePass123!",
                },
            )
            assert refused.status_code == 403
            assert refused.json()["detail"] == PASSWORD_SSO_DETAIL

            allowed = e2e_client.post(
                "/api/profile/password",
                headers=temp_admin.headers,
                json={
                    "current_password": temp_admin.password,
                    "new_password": "NewTempAdmin123!",
                },
            )
            assert allowed.status_code == 204, allowed.text
        finally:
            _reset_policy(e2e_client, headers)
            _deactivate_and_delete(e2e_client, headers, str(temp_admin.user_id))


@pytest.mark.e2e
class TestSSOOnlyPasskeyGates:
    def test_registration_options_admin_only(
        self, e2e_client, platform_admin, org1_user
    ):
        headers = platform_admin.headers
        try:
            _enable_sso_only(e2e_client, headers)

            refused = e2e_client.post(
                "/auth/passkeys/register/options",
                headers=org1_user.headers,
                json={},
            )
            assert refused.status_code == 403
            assert refused.json()["detail"] == PASSKEY_SSO_DETAIL

            allowed = e2e_client.post(
                "/auth/passkeys/register/options",
                headers=headers,
                json={},
            )
            assert allowed.status_code == 200, allowed.text
            assert "options" in allowed.json()
        finally:
            _reset_policy(e2e_client, headers)

    def test_authenticate_options_gated_by_email(
        self, e2e_client, platform_admin, org1_user
    ):
        headers = platform_admin.headers
        try:
            _enable_sso_only(e2e_client, headers)

            refused = e2e_client.post(
                "/auth/passkeys/authenticate/options",
                json={"email": org1_user.email},
            )
            assert refused.status_code == 403
            assert refused.json()["detail"] == PASSKEY_SSO_DETAIL

            refused_anonymous = e2e_client.post(
                "/auth/passkeys/authenticate/options", json={}
            )
            assert refused_anonymous.status_code == 403

            # Unknown emails keep the generic path (no user enumeration change).
            unknown = e2e_client.post(
                "/auth/passkeys/authenticate/options",
                json={"email": "nobody@gobifrost.dev"},
            )
            assert unknown.status_code == 200

            allowed = e2e_client.post(
                "/auth/passkeys/authenticate/options",
                json={"email": platform_admin.email},
            )
            assert allowed.status_code == 200, allowed.text
        finally:
            _reset_policy(e2e_client, headers)
