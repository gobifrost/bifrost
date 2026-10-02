"""
E2E tests for OAuth 2.0 Device Authorization Flow.

Tests the complete flow from device code request → user authorization → token exchange.
"""

import pytest
import httpx


@pytest.mark.e2e
def test_device_flow_complete_success(
    e2e_client: httpx.Client,
    org1_user,
):
    """
    Test complete device authorization flow: request code → authorize → get token.
    """
    # Step 1: CLI requests device code (no auth required)
    response = e2e_client.post("/auth/device/code")
    assert response.status_code == 200

    data = response.json()
    assert "device_code" in data
    assert "user_code" in data
    assert "verification_url" in data
    assert "expires_in" in data
    assert "interval" in data

    device_code = data["device_code"]
    user_code = data["user_code"]

    # Verify user_code format (XXXX-YYYY)
    assert len(user_code) == 9
    assert user_code[4] == "-"

    # Step 2: Poll before authorization (should be pending)
    response = e2e_client.post(
        "/auth/device/token",
        json={"device_code": device_code}
    )
    assert response.status_code == 200
    token_data = response.json()
    assert "error" in token_data
    assert token_data["error"] == "authorization_pending"

    # Step 3: User authorizes device (requires auth)
    response = e2e_client.post(
        "/auth/device/authorize",
        json={"user_code": user_code},
        headers=org1_user.headers
    )
    assert response.status_code == 200
    auth_data = response.json()
    assert auth_data["success"] is True

    # Step 4: Poll again after authorization (should get tokens)
    response = e2e_client.post(
        "/auth/device/token",
        json={"device_code": device_code}
    )
    assert response.status_code == 200
    token_data = response.json()

    # Should have tokens now
    assert "access_token" in token_data
    assert "refresh_token" in token_data
    assert "token_type" in token_data
    assert "expires_in" in token_data
    assert token_data["token_type"] == "bearer"

    # Verify access token works
    response = e2e_client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token_data['access_token']}"}
    )
    assert response.status_code == 200
    user_data = response.json()
    assert "email" in user_data


@pytest.mark.e2e
def test_device_flow_expired_code(
    e2e_client: httpx.Client,
):
    """
    Test that expired device codes return error.
    """
    # Try to exchange a non-existent device code
    fake_device_code = "00000000-0000-0000-0000-000000000000"

    response = e2e_client.post(
        "/auth/device/token",
        json={"device_code": fake_device_code}
    )
    assert response.status_code == 200
    data = response.json()
    assert "error" in data
    assert data["error"] == "expired_token"


@pytest.mark.e2e
def test_device_flow_invalid_user_code(
    e2e_client: httpx.Client,
    org1_user,
):
    """
    Test that invalid user codes are rejected during authorization.
    """
    response = e2e_client.post(
        "/auth/device/authorize",
        json={"user_code": "INVALID-CODE"},
        headers=org1_user.headers
    )
    assert response.status_code == 404
    data = response.json()
    assert "detail" in data
    assert "invalid or expired" in data["detail"].lower()


@pytest.mark.e2e
def test_device_flow_one_time_use(
    e2e_client: httpx.Client,
    org1_user,
):
    """
    Test that device codes can only be used once.
    """
    # Request device code
    response = e2e_client.post("/auth/device/code")
    assert response.status_code == 200
    data = response.json()
    device_code = data["device_code"]
    user_code = data["user_code"]

    # Authorize
    response = e2e_client.post(
        "/auth/device/authorize",
        json={"user_code": user_code},
        headers=org1_user.headers
    )
    assert response.status_code == 200

    # First token exchange should succeed
    response = e2e_client.post(
        "/auth/device/token",
        json={"device_code": device_code}
    )
    assert response.status_code == 200
    token_data = response.json()
    assert "access_token" in token_data

    # Second attempt should fail (code already used)
    response = e2e_client.post(
        "/auth/device/token",
        json={"device_code": device_code}
    )
    assert response.status_code == 200
    error_data = response.json()
    assert "error" in error_data
    assert error_data["error"] == "expired_token"


@pytest.mark.e2e
def test_device_flow_authorization_requires_auth(
    e2e_client: httpx.Client,
):
    """
    Test that device authorization requires authentication.
    """
    # Request device code
    response = e2e_client.post("/auth/device/code")
    assert response.status_code == 200
    data = response.json()
    user_code = data["user_code"]

    # Try to authorize without token
    response = e2e_client.post(
        "/auth/device/authorize",
        json={"user_code": user_code}
    )
    assert response.status_code == 401


@pytest.mark.e2e
def test_device_code_user_code_format(
    e2e_client: httpx.Client,
):
    """
    Test that user codes follow the expected format and avoid ambiguous characters.
    """
    # Request multiple codes to test randomness and format
    for _ in range(5):
        response = e2e_client.post("/auth/device/code")
        assert response.status_code == 200
        data = response.json()
        user_code = data["user_code"]

        # Format: XXXX-YYYY (8 chars + hyphen)
        assert len(user_code) == 9
        assert user_code[4] == "-"

        # Should only contain uppercase letters and digits
        code_chars = user_code.replace("-", "")
        assert code_chars.isupper() or code_chars.isdigit()

        # Should NOT contain ambiguous characters
        ambiguous = set("O0I1S5Z2")
        for char in code_chars:
            assert char not in ambiguous, f"Found ambiguous character {char} in {user_code}"


@pytest.mark.e2e
def test_device_flow_refresh_token_works(
    private_client: httpx.Client,
    org1_user,
):
    """
    Test that refresh tokens obtained via device flow can be used to get new access tokens.
    """
    # Complete device flow
    response = private_client.post("/auth/device/code")
    assert response.status_code == 200
    data = response.json()
    device_code = data["device_code"]
    user_code = data["user_code"]

    # Authorize
    response = private_client.post(
        "/auth/device/authorize",
        json={"user_code": user_code},
        headers=org1_user.headers
    )
    assert response.status_code == 200

    # Get tokens
    response = private_client.post(
        "/auth/device/token",
        json={"device_code": device_code}
    )
    assert response.status_code == 200
    token_data = response.json()
    refresh_token = token_data["refresh_token"]

    # Use refresh token to get new access token
    response = private_client.post(
        "/auth/refresh",
        json={"refresh_token": refresh_token}
    )
    assert response.status_code == 200
    new_tokens = response.json()
    assert "access_token" in new_tokens
    assert "refresh_token" in new_tokens

    # New access token should work
    response = private_client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {new_tokens['access_token']}"}
    )
    assert response.status_code == 200
