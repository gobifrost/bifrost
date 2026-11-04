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

    def test_bad_credentials_do_not_disclose_enforcement_or_account_membership(
        self, e2e_client, private_client, platform_admin, org1_user
    ):
        headers = platform_admin.headers
        try:
            _enable_sso_only(e2e_client, headers)
            responses = [private_client.post('/auth/login', data={
                'username': email, 'password': 'WrongPassword123!',
            }) for email in (org1_user.email, platform_admin.email, 'unknown-sso@gobifrost.dev')]
            assert all(response.status_code == 401 for response in responses)
            assert responses[0].json() == responses[1].json() == responses[2].json()
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
            assert refused.status_code == 200

            refused_anonymous = e2e_client.post(
                "/auth/passkeys/authenticate/options", json={}
            )
            assert refused_anonymous.status_code == 200

            # Unknown emails keep the generic path (no user enumeration change).
            unknown = e2e_client.post(
                "/auth/passkeys/authenticate/options",
                json={"email": "nobody@gobifrost.dev"},
            )
            assert unknown.status_code == 200
            for options_response in (refused, refused_anonymous, unknown):
                assert not options_response.json()["options"].get("allowCredentials")

            allowed = e2e_client.post(
                "/auth/passkeys/authenticate/options",
                json={"email": platform_admin.email},
            )
            assert allowed.status_code == 200, allowed.text
        finally:
            _reset_policy(e2e_client, headers)


@pytest.mark.e2e
class TestSSOExclusions:
    def test_excluded_user_and_organization_are_shared_login_policy(
        self, e2e_client, private_client, platform_admin, org1_user, org1, org2_user
    ):
        headers = platform_admin.headers
        try:
            _enable_sso_only(e2e_client, headers)
            preference = e2e_client.get('/api/settings/oauth', headers=headers).json()['login_preference']
            preference['excluded_user_ids'].append(str(org1_user.user_id))
            response = e2e_client.put('/api/settings/oauth/login-preference', headers=headers, json=preference)
            assert response.status_code == 200, response.text
            login = private_client.post('/auth/login', data={'username': org1_user.email, 'password': org1_user.password})
            assert login.status_code == 200, login.text
            preference['excluded_user_ids'].remove(str(org1_user.user_id))
            preference['excluded_organization_ids'] = [str(org1['id'])]
            response = e2e_client.put('/api/settings/oauth/login-preference', headers=headers, json=preference)
            assert response.status_code == 200, response.text
            login = private_client.post('/auth/login', data={'username': org1_user.email, 'password': org1_user.password})
            assert login.status_code == 200, login.text
            other_org = private_client.post('/auth/login', data={'username': org2_user.email, 'password': org2_user.password})
            assert other_org.status_code == 403
            preference['excluded_organization_ids'] = []
            assert e2e_client.put('/api/settings/oauth/login-preference', headers=headers, json=preference).status_code == 200
            login = private_client.post('/auth/login', data={'username': org1_user.email, 'password': org1_user.password})
            assert login.status_code == 403
        finally:
            _reset_policy(e2e_client, headers)

    def test_last_excluded_admin_guard_serializes_policy_and_deactivation(
        self, e2e_client, platform_admin, org1
    ):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        headers = platform_admin.headers
        temp = E2EUser(email='sso-recovery-race@gobifrost.dev', name='SSO Recovery Race', password='RecoveryRace123!')
        created = e2e_client.post('/api/users', headers=headers, json={
            'email': temp.email, 'name': temp.name, 'organization_id': org1['id'], 'is_superuser': True,
        })
        assert created.status_code == 201, created.text
        temp.user_id = created.json()['id']
        _register_and_authenticate_user(temp)
        try:
            _enable_sso_only(e2e_client, headers)
            preference = e2e_client.get('/api/settings/oauth', headers=headers).json()['login_preference']
            # The existing maintainer admin is the sole recovery account.
            preference['excluded_user_ids'] = [str(platform_admin.user_id)]
            assert e2e_client.put('/api/settings/oauth/login-preference', headers=headers, json=preference).status_code == 200
            nonexcluded_admin = e2e_client.post('/auth/login', data={'username': temp.email, 'password': temp.password})
            assert nonexcluded_admin.status_code == 403, nonexcluded_admin.text
            rejected = e2e_client.patch(f'/api/users/{platform_admin.user_id}', headers=temp.headers, json={'is_active': False})
            assert rejected.status_code == 409, rejected.text
            rejected = e2e_client.put('/api/settings/oauth/login-preference', headers=headers, json={**preference, 'excluded_user_ids': []})
            assert rejected.status_code == 400, rejected.text

            # Policy moves recovery to temp while the current admin deactivates
            # temp. Exactly one operation must lose the shared role-lock race.
            barrier = Barrier(2)
            def move_exclusion():
                barrier.wait()
                return e2e_client.put('/api/settings/oauth/login-preference', headers=platform_admin.headers,
                    json={**preference, 'excluded_user_ids': [str(temp.user_id)]}, timeout=30)
            def deactivate():
                barrier.wait()
                return e2e_client.patch(f'/api/users/{temp.user_id}', headers=platform_admin.headers,
                    json={'is_active': False}, timeout=30)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(move_exclusion)
                second = pool.submit(deactivate)
                responses = [first.result(), second.result()]
            assert sorted(r.status_code for r in responses) in ([200, 400], [200, 409]), [r.text for r in responses]
        finally:
            _reset_policy(e2e_client, headers)
            _deactivate_and_delete(e2e_client, headers, str(temp.user_id))

    def test_enabling_policy_and_removing_last_provider_are_serialized(
        self, e2e_client, platform_admin
    ):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier

        headers = platform_admin.headers
        try:
            _reset_policy(e2e_client, headers)
            configured = e2e_client.put('/api/settings/oauth/microsoft', headers=headers, json=MICROSOFT_CONFIG)
            assert configured.status_code == 200, configured.text
            barrier = Barrier(2)
            def enforce():
                barrier.wait()
                return e2e_client.put('/api/settings/oauth/login-preference',
                    headers=platform_admin.headers, json=SSO_ONLY_PREFERENCE)
            def remove():
                barrier.wait()
                return e2e_client.delete('/api/settings/oauth/microsoft', headers=platform_admin.headers)
            with ThreadPoolExecutor(max_workers=2) as pool:
                enforcing = pool.submit(enforce)
                removing = pool.submit(remove)
                results = (enforcing.result(), removing.result())
            assert tuple(response.status_code for response in results) in ((200, 409), (400, 204)), [response.text for response in results]
        finally:
            _reset_policy(e2e_client, headers)


@pytest.mark.e2e
class TestSSOInvitePasskeys:
    def test_invite_enrollment_uses_the_same_user_exclusion(self, e2e_client, platform_admin, org1):
        headers = platform_admin.headers
        created = e2e_client.post('/api/users', headers=headers, json={
            'email': 'sso-invite-passkey@gobifrost.dev', 'name': 'SSO Invite Example',
            'organization_id': org1['id'],
        })
        assert created.status_code == 201, created.text
        user = created.json()
        token = user['registration_url'].split('token=', 1)[1]
        try:
            _enable_sso_only(e2e_client, headers)
            options = e2e_client.post('/auth/register-from-invite/passkey/options', json={'token': token})
            assert options.status_code == 403, options.text
            verify = e2e_client.post('/auth/register-from-invite/passkey/verify', json={'token': token, 'credential': {}})
            assert verify.status_code == 403, verify.text
            policy = e2e_client.get('/api/settings/oauth', headers=headers).json()['login_preference']
            policy['excluded_user_ids'].append(user['id'])
            updated = e2e_client.put('/api/settings/oauth/login-preference', headers=headers, json=policy)
            assert updated.status_code == 200, updated.text
            options = e2e_client.post('/auth/register-from-invite/passkey/options', json={'token': token})
            assert options.status_code == 200, options.text
        finally:
            _reset_policy(e2e_client, headers)
            _deactivate_and_delete(e2e_client, headers, user['id'])
