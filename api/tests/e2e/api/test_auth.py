"""
E2E tests for authentication flows.

Tests registration, MFA setup, login, logout, token management, and security.
These tests explicitly verify auth flows work correctly.

Note: The platform_admin fixture handles registration and MFA setup.
These tests verify the flows work correctly and test security aspects.
"""

import jwt
import pytest
from uuid import uuid4

from tests.helpers.totp import generate_totp_code


@pytest.mark.e2e
class TestHealthCheck:
    """Basic health check tests."""

    def test_health_check(self, e2e_client):
        """Verify API is running."""
        response = e2e_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_health_with_details(self, e2e_client, platform_admin):
        """Health check with service details (requires auth)."""
        response = e2e_client.get(
            "/health",
            headers=platform_admin.headers,
            params={"detail": "true"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_live_health_check(self, e2e_client):
        """Verify liveness probe is shallow and public."""
        response = e2e_client.get("/health/live")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"

    def test_ready_health_check(self, e2e_client):
        """Verify readiness probe checks core serving dependencies."""
        response = e2e_client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["components"]["database"]["status"] == "healthy"
        assert data["components"]["redis"]["status"] == "healthy"
        assert data["components"]["rabbitmq"]["status"] == "healthy"
        assert data["components"]["s3"]["status"] == "healthy"

    def test_detailed_health_check(self, e2e_client):
        """Verify detailed health reports core dependency components."""
        response = e2e_client.get("/health/detailed")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert set(data["components"]) == {"database", "redis", "rabbitmq", "s3"}
        assert data["components"]["database"]["type"] == "postgresql"
        assert data["components"]["redis"]["type"] == "redis"
        assert data["components"]["rabbitmq"]["type"] == "rabbitmq"
        assert data["components"]["s3"]["type"] == "s3"


@pytest.mark.e2e
class TestRegistrationFlow:
    """Test user registration flows."""

    def test_first_user_becomes_platform_admin(self, platform_admin):
        """First user to register should become platform admin."""
        # platform_admin fixture does the registration
        assert platform_admin.is_superuser is True
        assert platform_admin.access_token is not None

    def test_platform_admin_can_access_protected_endpoints(
        self, e2e_client, platform_admin
    ):
        """Verify platform admin has valid token."""
        response = e2e_client.get("/auth/me", headers=platform_admin.headers)
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == platform_admin.email
        assert data["is_superuser"] is True

    def test_org_user_not_superuser(self, org1_user):
        """Org users should not be superusers."""
        assert org1_user.is_superuser is False
        assert org1_user.access_token is not None


@pytest.mark.e2e
class TestMFAFlow:
    """Test MFA setup and verification flows."""

    def test_login_requires_mfa(self, private_client, platform_admin):
        """Login should require MFA for password auth."""
        response = private_client.post(
            "/auth/login",
            data={
                "username": platform_admin.email,
                "password": platform_admin.password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        data = response.json()
        # Already has MFA, so should require verification
        assert data.get("mfa_required") is True

    def test_mfa_status_shows_enabled(self, e2e_client, platform_admin):
        """MFA status should show enabled for enrolled user."""
        response = e2e_client.get(
            "/auth/mfa/status",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        assert response.json()["mfa_enabled"] is True

    def test_mfa_login_with_totp(self, private_client, platform_admin):
        """Complete MFA login flow with TOTP code."""
        # Login to get MFA token
        response = private_client.post(
            "/auth/login",
            data={
                "username": platform_admin.email,
                "password": platform_admin.password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data.get("mfa_required") is True
        mfa_token = data["mfa_token"]

        # Complete MFA
        totp_code = generate_totp_code(platform_admin.totp_secret)
        response = private_client.post(
            "/auth/mfa/login",
            json={"mfa_token": mfa_token, "code": totp_code},
        )
        assert response.status_code == 200
        tokens = response.json()
        assert "access_token" in tokens
        assert "refresh_token" in tokens


@pytest.mark.e2e
class TestTokenSecurity:
    """Test token security features."""

    def test_access_token_has_required_claims(self, platform_admin):
        """Access tokens must include type, iss, and aud claims."""
        access_token = platform_admin.access_token
        payload = jwt.decode(access_token, options={"verify_signature": False})

        assert payload.get("type") == "access", "Token should have type=access"
        assert payload.get("iss") == "bifrost-api", "Token should have issuer claim"
        assert payload.get("aud") == "bifrost-client", "Token should have audience claim"

    def test_refresh_token_rejected_as_access_token(self, private_client, platform_admin):
        """
        Security: Refresh tokens should be rejected when used as access tokens.
        """
        # Get a fresh refresh token
        response = private_client.post(
            "/auth/login",
            data={
                "username": platform_admin.email,
                "password": platform_admin.password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        data = response.json()

        # Complete MFA if required
        if data.get("mfa_required"):
            totp_code = generate_totp_code(platform_admin.totp_secret)
            mfa_response = private_client.post(
                "/auth/mfa/login",
                json={"mfa_token": data["mfa_token"], "code": totp_code},
            )
            assert mfa_response.status_code == 200
            data = mfa_response.json()

        refresh_token = data.get("refresh_token")
        assert refresh_token, "Should receive refresh token"

        # Try to use refresh token as access token
        response = private_client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {refresh_token}"},
        )
        assert response.status_code == 401, "Refresh token should be rejected as access token"

    def test_refresh_token_rotation(self, private_client, platform_admin):
        """
        Security: Refresh token should be rotated on use (old token invalidated).
        """
        # Login to get fresh tokens
        response = private_client.post(
            "/auth/login",
            data={
                "username": platform_admin.email,
                "password": platform_admin.password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        data = response.json()

        # Complete MFA if required
        if data.get("mfa_required"):
            totp_code = generate_totp_code(platform_admin.totp_secret)
            mfa_response = private_client.post(
                "/auth/mfa/login",
                json={"mfa_token": data["mfa_token"], "code": totp_code},
            )
            assert mfa_response.status_code == 200
            data = mfa_response.json()

        old_refresh_token = data.get("refresh_token")
        assert old_refresh_token, "Should receive refresh token"

        # Use refresh token to get new tokens
        response = private_client.post(
            "/auth/refresh",
            json={"refresh_token": old_refresh_token},
        )
        assert response.status_code == 200, f"Refresh should succeed: {response.text}"
        new_data = response.json()
        new_refresh_token = new_data.get("refresh_token")
        assert new_refresh_token != old_refresh_token, "Should receive new refresh token"

        # Try to use old refresh token again (should fail - single use)
        response = private_client.post(
            "/auth/refresh",
            json={"refresh_token": old_refresh_token},
        )
        assert response.status_code == 401, "Old refresh token should be rejected after rotation"


@pytest.mark.e2e
class TestCSRFProtection:
    """Test CSRF protection."""

    def test_bearer_auth_no_csrf_required(self, e2e_client, platform_admin):
        """Bearer auth should work without CSRF token."""
        response = e2e_client.post(
            "/api/config",
            json={
                "key": "csrf_test_key",
                "value": "test",
                "is_secret": False,
                "type": "string",
            },
            headers=platform_admin.headers,
        )
        # This should succeed (bearer auth, no CSRF needed)
        assert response.status_code == 201, f"Bearer auth should not require CSRF: {response.text}"

        # Cleanup
        e2e_client.delete(
            "/api/config/csrf_test_key",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestOrgValidation:
    """Test organization scope validation via query params."""

    def test_invalid_scope_returns_empty(self, e2e_client, platform_admin):
        """Valid but non-existent org UUID in scope returns empty results (org filtering)."""
        fake_org_id = str(uuid4())

        # With scope param filtering, a non-existent org just returns no results
        # rather than a 400 error (the org is used for filtering, not validation)
        response = e2e_client.get(
            "/api/config",
            params={"scope": fake_org_id},
            headers=platform_admin.headers,
        )
        # Platform admins can filter by any org - returns empty if org doesn't exist
        assert response.status_code == 200
        assert response.json() == []


@pytest.mark.e2e
class TestLogoutAndRevocation:
    """Test logout and session revocation."""

    def test_logout_revokes_refresh_token(self, private_client, platform_admin):
        """Logout should revoke the refresh token."""
        # Login to get fresh tokens
        response = private_client.post(
            "/auth/login",
            data={
                "username": platform_admin.email,
                "password": platform_admin.password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        data = response.json()

        # Complete MFA if required
        if data.get("mfa_required"):
            totp_code = generate_totp_code(platform_admin.totp_secret)
            mfa_response = private_client.post(
                "/auth/mfa/login",
                json={"mfa_token": data["mfa_token"], "code": totp_code},
            )
            assert mfa_response.status_code == 200
            data = mfa_response.json()

        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        assert access_token and refresh_token

        # Logout - pass refresh_token in body for API clients
        response = private_client.post(
            "/auth/logout",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 200

        # Try to use refresh token (should fail)
        response = private_client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 401, "Refresh token should be revoked after logout"

    def test_revoke_all_sessions(self, private_client, platform_admin):
        """Revoke-all should invalidate all refresh tokens."""
        # Login to get fresh tokens
        response = private_client.post(
            "/auth/login",
            data={
                "username": platform_admin.email,
                "password": platform_admin.password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        data = response.json()

        # Complete MFA if required
        if data.get("mfa_required"):
            totp_code = generate_totp_code(platform_admin.totp_secret)
            mfa_response = private_client.post(
                "/auth/mfa/login",
                json={"mfa_token": data["mfa_token"], "code": totp_code},
            )
            assert mfa_response.status_code == 200
            data = mfa_response.json()

        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        assert access_token and refresh_token

        # Revoke all sessions
        response = private_client.post(
            "/auth/revoke-all",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        revoke_data = response.json()
        assert "sessions_revoked" in revoke_data

        # Try to use refresh token (should fail)
        response = private_client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 401, "Refresh token should be revoked after revoke-all"


# NOTE: Rate limiting tests have been moved to test_security.py
# They run last to avoid affecting other tests that need login
