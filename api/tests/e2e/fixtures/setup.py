"""
Session-scoped fixtures for E2E tests.

These fixtures set up the base state needed for E2E testing:
- Platform admin (first registered user)
- Test organizations
- Org users with tokens

All fixtures are session-scoped, meaning they run ONCE per test session
and the state is shared across all test files.
"""

import logging
import os
from uuid import UUID

import httpx
import pytest

from tests.e2e.fixtures.users import E2EUser
from tests.helpers.totp import generate_totp_code

logger = logging.getLogger(__name__)

# API URLs from environment or defaults
# Default to api:8000 since tests run inside Docker network
API_BASE_URL = os.environ.get("TEST_API_URL", "http://api:8000")
WS_BASE_URL = API_BASE_URL.replace("http://", "ws://").replace("https://", "wss://")


def _register_and_authenticate_user(
    client: httpx.Client,
    user: E2EUser,
    skip_registration: bool = False,
) -> E2EUser:
    """
    Register a user (if needed) and complete MFA setup.

    Args:
        client: HTTP client
        user: User to register/authenticate
        skip_registration: If True, skip registration (user already exists)

    Returns:
        User with populated tokens
    """
    # Register if needed
    if not skip_registration:
        response = client.post(
            "/auth/register",
            json={
                "email": user.email,
                "password": user.password,
                "name": user.name,
            },
        )
        assert response.status_code == 201, f"Register failed: {response.text}"
        data = response.json()
        user.user_id = UUID(data["id"])
        user.is_superuser = data.get("is_superuser", False)

    # Login to get MFA token
    response = client.post(
        "/auth/login",
        data={
            "username": user.email,
            "password": user.password,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    login_data = response.json()
    mfa_token = login_data.get("mfa_token") or login_data.get("access_token")
    assert mfa_token, f"No MFA token in response: {login_data}"

    # Setup MFA
    response = client.post(
        "/auth/mfa/setup",
        headers={"Authorization": f"Bearer {mfa_token}"},
    )
    assert response.status_code == 200, f"MFA setup failed: {response.text}"
    user.totp_secret = response.json()["secret"]

    # Verify MFA to get tokens
    assert user.totp_secret is not None, "TOTP secret not set"
    totp_code = generate_totp_code(user.totp_secret)
    response = client.post(
        "/auth/mfa/verify",
        headers={"Authorization": f"Bearer {mfa_token}"},
        json={"code": totp_code},
    )
    assert response.status_code == 200, f"MFA verify failed: {response.text}"
    verify_data = response.json()

    user.access_token = verify_data["access_token"]
    user.refresh_token = verify_data["refresh_token"]

    logger.info(f"Authenticated user: {user.email}")
    return user


def _login_user(client: httpx.Client, user: E2EUser) -> E2EUser:
    """
    Login an existing user with MFA, refreshing tokens.

    Args:
        client: HTTP client
        user: User to login (must have totp_secret)

    Returns:
        User with refreshed tokens
    """
    response = client.post(
        "/auth/login",
        data={
            "username": user.email,
            "password": user.password,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    login_data = response.json()

    if login_data.get("mfa_required"):
        mfa_token = login_data["mfa_token"]
        assert user.totp_secret is not None, "TOTP secret not set for login"
        totp_code = generate_totp_code(user.totp_secret)
        response = client.post(
            "/auth/mfa/login",
            json={"mfa_token": mfa_token, "code": totp_code},
        )
        assert response.status_code == 200, f"MFA login failed: {response.text}"
        login_data = response.json()

    user.access_token = login_data["access_token"]
    user.refresh_token = login_data["refresh_token"]
    return user


# =============================================================================
# Session-Scoped Fixtures
# =============================================================================


@pytest.fixture(scope="session")
def e2e_ws_url() -> str:
    """WebSocket base URL for E2E tests."""
    return WS_BASE_URL


@pytest.fixture(scope="session")
def platform_admin(e2e_client: httpx.Client) -> E2EUser:
    """
    Session-scoped platform admin user.

    First user to register becomes platform admin (superuser).
    Registered and authenticated once per test session.
    """
    user = E2EUser(
        email="admin@gobifrost.com",
        password="AdminPass123!",
        name="Platform Admin",
    )
    user = _register_and_authenticate_user(e2e_client, user)
    assert user.is_superuser, "First user should be platform admin"
    logger.info("Platform admin created and authenticated")
    return user


@pytest.fixture(scope="session")
def org1(e2e_client: httpx.Client, platform_admin: E2EUser) -> dict:
    """
    Session-scoped test organization 1.

    Primary test organization for most tests.
    """
    response = e2e_client.post(
        "/api/organizations",
        headers=platform_admin.headers,
        json={
            "name": "Bifrost Dev Org",
            "domain": "gobifrost.dev",
        },
    )
    assert response.status_code == 201, f"Create org failed: {response.text}"
    org = response.json()
    logger.info(f"Created organization: {org['name']}")
    return org


@pytest.fixture(scope="session")
def org2(e2e_client: httpx.Client, platform_admin: E2EUser) -> dict:
    """
    Session-scoped test organization 2.

    Used for isolation tests to verify org boundaries.
    """
    response = e2e_client.post(
        "/api/organizations",
        headers=platform_admin.headers,
        json={
            "name": "Second Test Org",
            "domain": "org2.gobifrost.com",
        },
    )
    assert response.status_code == 201, f"Create org failed: {response.text}"
    org = response.json()
    logger.info(f"Created organization: {org['name']}")
    return org


@pytest.fixture(scope="session")
def org1_user(
    e2e_client: httpx.Client,
    platform_admin: E2EUser,
    org1: dict,
) -> E2EUser:
    """
    Session-scoped user in org1.

    Regular org user (not admin) for permission testing.
    """
    user = E2EUser(
        email="alice@gobifrost.dev",
        password="AlicePass123!",
        name="Alice Smith",
        organization_id=UUID(org1["id"]),
    )

    # Platform admin creates user stub
    response = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": user.email,
            "name": user.name,
            "organization_id": org1["id"],
            "is_superuser": False,
        },
    )
    assert response.status_code == 201, f"Create user failed: {response.text}"
    user.user_id = UUID(response.json()["id"])

    # User completes registration and MFA
    user = _register_and_authenticate_user(e2e_client, user, skip_registration=False)
    user.organization_id = UUID(org1["id"])

    # Set developer context with default org (required for CLI knowledge isolation)
    response = e2e_client.put(
        "/api/cli/context",
        headers=user.headers,
        json={"default_org_id": org1["id"]},
    )
    assert response.status_code == 200, f"Set developer context failed: {response.text}"

    logger.info(f"Created org1 user: {user.email}")
    return user


@pytest.fixture(scope="session")
def org2_user(
    e2e_client: httpx.Client,
    platform_admin: E2EUser,
    org2: dict,
) -> E2EUser:
    """
    Session-scoped user in org2.

    Used for isolation tests to verify org boundaries.
    """
    user = E2EUser(
        email="bob@org2.gobifrost.com",
        password="BobPass123!",
        name="Bob Jones",
        organization_id=UUID(org2["id"]),
    )

    # Platform admin creates user stub
    response = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": user.email,
            "name": user.name,
            "organization_id": org2["id"],
            "is_superuser": False,
        },
    )
    assert response.status_code == 201, f"Create user failed: {response.text}"
    user.user_id = UUID(response.json()["id"])

    # User completes registration and MFA
    user = _register_and_authenticate_user(e2e_client, user, skip_registration=False)
    user.organization_id = UUID(org2["id"])

    # Set developer context with default org (required for CLI knowledge isolation)
    response = e2e_client.put(
        "/api/cli/context",
        headers=user.headers,
        json={"default_org_id": org2["id"]},
    )
    assert response.status_code == 200, f"Set developer context failed: {response.text}"

    logger.info(f"Created org2 user: {user.email}")
    return user


@pytest.fixture(scope="session")
def non_admin_user(
    e2e_client: httpx.Client,
    platform_admin: E2EUser,
    org1: dict,
) -> E2EUser:
    """
    Session-scoped non-admin user in org1.

    Regular (non-superuser) user for access-control testing.
    """
    user = E2EUser(
        email="non-admin@gobifrost.com",
        password="NonAdminPass123!",
        name="Non Admin",
        organization_id=UUID(org1["id"]),
    )

    response = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": user.email,
            "name": user.name,
            "organization_id": org1["id"],
            "is_superuser": False,
        },
    )
    assert response.status_code == 201, f"Create user failed: {response.text}"
    user.user_id = UUID(response.json()["id"])

    user = _register_and_authenticate_user(e2e_client, user, skip_registration=False)
    user.organization_id = UUID(org1["id"])

    response = e2e_client.put(
        "/api/cli/context",
        headers=user.headers,
        json={"default_org_id": org1["id"]},
    )
    assert response.status_code == 200, f"Set developer context failed: {response.text}"

    logger.info(f"Created non_admin_user: {user.email}")
    return user


@pytest.fixture(scope="session")
def alice_user(
    e2e_client: httpx.Client,
    platform_admin: E2EUser,
    org1: dict,
) -> E2EUser:
    """
    Session-scoped Alice user in org1.

    Distinct regular user for multi-user access-control tests.
    """
    user = E2EUser(
        email="alice@gobifrost.com",
        password="AlicePass123!",
        name="Alice Table",
        organization_id=UUID(org1["id"]),
    )

    response = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": user.email,
            "name": user.name,
            "organization_id": org1["id"],
            "is_superuser": False,
        },
    )
    assert response.status_code == 201, f"Create user failed: {response.text}"
    user.user_id = UUID(response.json()["id"])

    user = _register_and_authenticate_user(e2e_client, user, skip_registration=False)
    user.organization_id = UUID(org1["id"])

    response = e2e_client.put(
        "/api/cli/context",
        headers=user.headers,
        json={"default_org_id": org1["id"]},
    )
    assert response.status_code == 200, f"Set developer context failed: {response.text}"

    logger.info(f"Created alice_user: {user.email}")
    return user


@pytest.fixture(scope="session")
def bob_user(
    e2e_client: httpx.Client,
    platform_admin: E2EUser,
    org1: dict,
) -> E2EUser:
    """
    Session-scoped Bob user in org1.

    Distinct regular user for multi-user access-control tests.
    """
    user = E2EUser(
        email="bob@gobifrost.com",
        password="BobPass123!",
        name="Bob Table",
        organization_id=UUID(org1["id"]),
    )

    response = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": user.email,
            "name": user.name,
            "organization_id": org1["id"],
            "is_superuser": False,
        },
    )
    assert response.status_code == 201, f"Create user failed: {response.text}"
    user.user_id = UUID(response.json()["id"])

    user = _register_and_authenticate_user(e2e_client, user, skip_registration=False)
    user.organization_id = UUID(org1["id"])

    response = e2e_client.put(
        "/api/cli/context",
        headers=user.headers,
        json={"default_org_id": org1["id"]},
    )
    assert response.status_code == 200, f"Set developer context failed: {response.text}"

    logger.info(f"Created bob_user: {user.email}")
    return user


# =============================================================================
# Helper Fixtures (function-scoped)
# =============================================================================


@pytest.fixture
def refresh_user_tokens(e2e_client: httpx.Client):
    """
    Function fixture to refresh a user's tokens mid-test.

    Usage:
        def test_something(refresh_user_tokens, platform_admin):
            # ... do something that might invalidate tokens ...
            platform_admin = refresh_user_tokens(platform_admin)
            # ... continue with fresh tokens ...
    """

    def _refresh(user: E2EUser) -> E2EUser:
        return _login_user(e2e_client, user)

    return _refresh
