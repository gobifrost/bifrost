"""
Test authentication helpers for integration tests.

Provides JWT token generation and HTTP header helpers for testing
authenticated endpoints with real HTTP requests to the FastAPI server.
"""

from datetime import datetime, timedelta, timezone

import jwt


# Test environment JWT settings (must match docker-compose.test.yml and src/config.py)
TEST_SECRET_KEY = "test-secret-key-for-e2e-testing-must-be-32-chars"
TEST_JWT_ISSUER = "bifrost-api"
TEST_JWT_AUDIENCE = "bifrost-client"
TEST_ALGORITHM = "HS256"

# Default test user UUIDs (stable for consistent testing)
DEFAULT_USER_ID = "00000000-0000-4000-8000-000000000001"
DEFAULT_ADMIN_ID = "00000000-0000-4000-8000-000000000099"


def create_test_jwt(
    user_id: str | None = None,
    email: str = "test@example.com",
    name: str = "Test User",
    is_superuser: bool = False,
    organization_id: str | None = None,
) -> str:
    """
    Create test JWT token for authentication.

    Uses the same secret key, issuer, and audience as the test environment
    configured in tests/conftest.py and src/config.py.

    Args:
        user_id: User OID (object ID) - typically a UUID
        email: User email address
        name: User display name
        is_superuser: Whether user should have superuser/platform admin privileges
        organization_id: Organization ID for ORG users (auto-generated if not provided for non-superusers)

    Returns:
        str: JWT token signed with test secret

    Example:
        >>> token = create_test_jwt(email="john@acme.com", name="John Doe")
        >>> headers = auth_headers(token)
        >>> response = requests.get("/api/organizations", headers=headers)
    """
    # Use default UUID if not provided (sub must be a valid UUID for auth middleware)
    if user_id is None:
        user_id = DEFAULT_ADMIN_ID if is_superuser else DEFAULT_USER_ID

    # For non-superusers, org_id is required by auth middleware
    # Generate a default org_id if not provided
    if not is_superuser and organization_id is None:
        organization_id = "00000000-0000-4000-8000-000000000100"  # Default test org ID

    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "name": name,
        "is_superuser": is_superuser,
        "org_id": None if (is_superuser and organization_id is None) else organization_id,
        "roles": ["authenticated"],
        "exp": now + timedelta(hours=2),
        "iat": now,
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "type": "access",
    }
    return jwt.encode(payload, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)


def create_superuser_jwt(
    user_id: str = "test-platform-admin-123",
    email: str = "admin@platform.com",
    name: str = "Platform Admin"
) -> str:
    """
    Create test JWT token for superuser/platform admin.

    Convenience function for creating superuser tokens.

    Args:
        user_id: User OID (object ID)
        email: User email address
        name: User display name

    Returns:
        str: JWT token with superuser privileges

    Example:
        >>> token = create_superuser_jwt(email="admin@platform.com")
        >>> headers = auth_headers(token)
    """
    return create_test_jwt(
        user_id=user_id,
        email=email,
        name=name,
        is_superuser=True,
    )


def auth_headers(token: str) -> dict[str, str]:
    """
    Create authorization headers with JWT token.

    Args:
        token: JWT token from create_test_jwt()

    Returns:
        dict: Headers with Authorization bearer token

    Example:
        >>> token = create_test_jwt(email="user@test.com")
        >>> headers = auth_headers(token)
        >>> response = requests.get(url, headers=headers)
    """
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
