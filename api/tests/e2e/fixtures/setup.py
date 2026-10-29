"""
Session-scoped fixtures for E2E tests.

These fixtures set up the base state needed for E2E testing:
- Platform admin (first registered user)
- Test organizations
- Org users with tokens

All fixtures are session-scoped, meaning they run ONCE per test session
and the state is shared across all test files.
"""

import asyncio
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from tests.e2e.fixtures.users import E2EUser
from tests.helpers.totp import generate_totp_code

logger = logging.getLogger(__name__)

# API URLs from environment or defaults
# Default to api:8000 since tests run inside Docker network
API_BASE_URL = os.environ.get("TEST_API_URL", "http://api:8000")
WS_BASE_URL = API_BASE_URL.replace("http://", "ws://").replace("https://", "wss://")


def _anonymous_client() -> httpx.Client:
    """Throwaway client for credential exchanges.

    Login, MFA and refresh responses set ``access_token`` / ``refresh_token`` /
    ``csrf_token`` cookies. Doing the exchange on a private client keeps those
    cookies out of the shared ``e2e_client`` jar, so tests authenticate only
    through the explicit headers on ``E2EUser``.
    """
    return httpx.Client(base_url=API_BASE_URL, timeout=60.0)


def _register_and_authenticate_user(
    user: E2EUser,
    skip_registration: bool = False,
) -> E2EUser:
    """
    Register a user (if needed) and complete MFA setup.

    Args:
        user: User to register/authenticate
        skip_registration: If True, skip registration (user already exists)

    Returns:
        User with populated tokens
    """
    with _anonymous_client() as client:
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


def _login_user(user: E2EUser) -> E2EUser:
    """
    Login an existing user with MFA, refreshing tokens.

    Args:
        user: User to login (must have totp_secret)

    Returns:
        User with refreshed tokens
    """
    with _anonymous_client() as client:
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
    user = _register_and_authenticate_user(user)
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
    user = _register_and_authenticate_user(user, skip_registration=False)
    user.organization_id = UUID(org1["id"])

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
    user = _register_and_authenticate_user(user, skip_registration=False)
    user.organization_id = UUID(org2["id"])

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

    user = _register_and_authenticate_user(user, skip_registration=False)
    user.organization_id = UUID(org1["id"])

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

    user = _register_and_authenticate_user(user, skip_registration=False)
    user.organization_id = UUID(org1["id"])

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

    user = _register_and_authenticate_user(user, skip_registration=False)
    user.organization_id = UUID(org1["id"])

    logger.info(f"Created bob_user: {user.email}")
    return user


# Seeded provider org. Created by migration 20260107_022300_add_provider_org
# at a stable UUID so tests can reference it without recreating.
PROVIDER_ORG_ID = UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture(scope="session")
def provider_org_user(
    e2e_client: httpx.Client,
    platform_admin: E2EUser,
) -> E2EUser:
    """A regular (non-superuser) user inside the seeded provider org.

    This is the caller archetype the C2 bypass rule was added for —
    e.g. a non-admin Covi employee. ``is_superuser=False`` plus
    ``Organization.is_provider=True`` should grant the same scope-bypass
    as a platform admin: target any org, target global, list cross-org.
    """
    user = E2EUser(
        email="member@provider.gobifrost.com",
        password="ProvMember123!",
        name="Provider Member",
        organization_id=PROVIDER_ORG_ID,
    )

    response = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": user.email,
            "name": user.name,
            "organization_id": str(PROVIDER_ORG_ID),
            "is_superuser": False,
        },
    )
    assert response.status_code == 201, f"Create provider user failed: {response.text}"
    user.user_id = UUID(response.json()["id"])

    user = _register_and_authenticate_user(user, skip_registration=False)
    user.organization_id = PROVIDER_ORG_ID
    user.is_superuser = False

    logger.info(f"Created provider_org_user: {user.email}")
    return user


@pytest.fixture(scope="session")
def second_platform_admin(
    e2e_client: httpx.Client,
    platform_admin: E2EUser,
) -> E2EUser:
    """A second platform admin, for flows that need a different requester
    who still holds platform-admin write access (e.g. publish dedupe)."""
    user = E2EUser(
        email="second-admin@provider.gobifrost.com",
        password="SecondAdmin123!",
        name="Second Platform Admin",
        organization_id=PROVIDER_ORG_ID,
    )

    response = e2e_client.post(
        "/api/users",
        headers=platform_admin.headers,
        json={
            "email": user.email,
            "name": user.name,
            "organization_id": str(PROVIDER_ORG_ID),
            "is_superuser": True,
        },
    )
    assert response.status_code == 201, f"Create second admin failed: {response.text}"
    user.user_id = UUID(response.json()["id"])

    user = _register_and_authenticate_user(user, skip_registration=False)
    user.organization_id = PROVIDER_ORG_ID
    user.is_superuser = True

    logger.info(f"Created second_platform_admin: {user.email}")
    return user


# =============================================================================
# Helper Fixtures (function-scoped)
# =============================================================================


@pytest.fixture
def secrets_reader_platform_admin(
    e2e_client: httpx.Client,
    platform_admin: E2EUser,
    second_platform_admin: E2EUser,
):
    """Temporarily grant the explicit Secrets Reader role to the test admin."""
    from shared.builtin_roles import DECRYPTION_ROLE_ID

    assignments_url = f"/api/users/{platform_admin.user_id}/role-assignments"
    response = e2e_client.get(assignments_url, headers=second_platform_admin.headers)
    assert response.status_code == 200, response.text
    original = response.json()
    original_additional = [
        {
            "role_id": assignment["role_id"],
            "boundaries": [
                {
                    "kind": boundary["kind"],
                    "organization_id": boundary["organization_id"],
                }
                for boundary in assignment["boundaries"]
            ],
        }
        for assignment in original["additional"]
    ]
    already_granted = any(
        assignment["role_id"] == str(DECRYPTION_ROLE_ID)
        for assignment in original_additional
    )
    if not already_granted:
        response = e2e_client.put(
            assignments_url,
            headers=second_platform_admin.headers,
            json={
                "base_role_id": original["base_role"]["id"],
                "additional": [
                    *original_additional,
                    {"role_id": str(DECRYPTION_ROLE_ID)},
                ],
            },
        )
        assert response.status_code == 200, response.text
        assert str(DECRYPTION_ROLE_ID) in {
            assignment["role_id"] for assignment in response.json()["additional"]
        }

    try:
        yield platform_admin
    finally:
        if not already_granted:
            response = e2e_client.put(
                assignments_url,
                headers=second_platform_admin.headers,
                json={
                    "base_role_id": original["base_role"]["id"],
                    "additional": original_additional,
                },
            )
            assert response.status_code == 200, response.text


@pytest.fixture
def refresh_user_tokens():
    """
    Function fixture to refresh a user's tokens mid-test.

    Usage:
        def test_something(refresh_user_tokens, platform_admin):
            # ... do something that might invalidate tokens ...
            platform_admin = refresh_user_tokens(platform_admin)
            # ... continue with fresh tokens ...
    """

    def _refresh(user: E2EUser) -> E2EUser:
        return _login_user(user)

    return _refresh


@contextmanager
def _org_service_headers(async_session_factory, org_id: str):
    """Back an org-scoped service credential with a live, durable attempt."""
    from sqlalchemy import delete

    from src.core.security import mint_service_token
    from src.models.orm.services import ServiceAttempt, ServiceDefinition
    from src.models.orm.users import User
    from src.models.orm.workflows import Workflow

    actor_id, workflow_id, service_id, attempt_id = (uuid4() for _ in range(4))
    organization_id = UUID(org_id)

    async def create() -> None:
        async with async_session_factory() as session:
            session.add(User(
                id=actor_id,
                email=f"service-fixture-{actor_id.hex}@example.com",
                name="Org Service Fixture",
                organization_id=organization_id,
                identity_kind="custom",
                is_registered=False,
            ))
            await session.flush()
            session.add(Workflow(
                id=workflow_id,
                name=f"service_fixture_{workflow_id.hex}",
                function_name="service_fixture",
                path=f"workflows/service_fixture_{workflow_id.hex}.py",
                type="service",
                organization_id=organization_id,
                permission_mode="full",
                run_identity_id=actor_id,
            ))
            await session.flush()
            session.add(ServiceDefinition(
                id=service_id,
                workflow_id=workflow_id,
                organization_id=organization_id,
                enabled=False,
                desired_state="stopped",
                created_by="service-fixture",
            ))
            await session.flush()
            session.add(ServiceAttempt(
                id=attempt_id,
                service_id=service_id,
                run_user_id=actor_id,
                state="running",
                worker_id="service-fixture",
                lease_token=uuid4().hex,
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
            ))
            await session.commit()

    async def cleanup() -> None:
        async with async_session_factory() as session:
            await session.execute(delete(ServiceAttempt).where(ServiceAttempt.id == attempt_id))
            await session.execute(delete(ServiceDefinition).where(ServiceDefinition.id == service_id))
            await session.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await session.execute(delete(User).where(User.id == actor_id))
            await session.commit()

    asyncio.run(create())
    try:
        token, _ = mint_service_token(service_id=str(service_id), attempt_id=str(attempt_id))
        yield {"Authorization": f"Bearer {token}"}
    finally:
        asyncio.run(cleanup())


@pytest.fixture
def org1_service_headers(async_session_factory, org1: dict):
    """Org1-scoped execution credential headers."""
    with _org_service_headers(async_session_factory, org1["id"]) as headers:
        yield headers


@pytest.fixture
def org2_service_headers(async_session_factory, org2: dict):
    """Org2-scoped execution credential headers."""
    with _org_service_headers(async_session_factory, org2["id"]) as headers:
        yield headers
