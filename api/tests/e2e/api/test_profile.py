"""
Profile API E2E Tests

Tests for the profile management endpoints.
"""


from tests.e2e.fixtures.users import E2EUser
from tests.e2e.fixtures.setup import _login_user


class TestProfile:
    """Tests for profile endpoints."""

    def test_get_profile(self, e2e_client, platform_admin: E2EUser):
        """Test getting the current user's profile."""
        response = e2e_client.get("/api/profile", headers=platform_admin.headers)
        assert response.status_code == 200

        data = response.json()
        assert "id" in data
        assert "email" in data
        assert data["email"] == platform_admin.email
        assert "name" in data
        assert "has_avatar" in data
        assert "is_superuser" in data
        assert data["is_superuser"] is True  # Platform admin is superuser

    def test_update_profile_name(self, e2e_client, platform_admin: E2EUser):
        """Test updating the profile name."""
        # Get current profile
        response = e2e_client.get("/api/profile", headers=platform_admin.headers)
        assert response.status_code == 200
        original_name = response.json().get("name")

        # Update name
        new_name = "Test User Updated"
        response = e2e_client.patch(
            "/api/profile",
            headers=platform_admin.headers,
            json={"name": new_name},
        )
        assert response.status_code == 200
        assert response.json()["name"] == new_name

        # Restore original name
        e2e_client.patch(
            "/api/profile",
            headers=platform_admin.headers,
            json={"name": original_name},
        )

    def test_update_profile_no_fields(self, e2e_client, platform_admin: E2EUser):
        """Test updating profile with no fields returns 400."""
        response = e2e_client.patch(
            "/api/profile",
            headers=platform_admin.headers,
            json={},
        )
        assert response.status_code == 400
        assert "No fields to update" in response.json()["detail"]

    def test_get_avatar_not_set(self, e2e_client, platform_admin: E2EUser):
        """Test getting avatar when not set returns 404."""
        # First ensure no avatar is set by deleting any existing
        e2e_client.delete("/api/profile/avatar", headers=platform_admin.headers)

        response = e2e_client.get("/api/profile/avatar", headers=platform_admin.headers)
        assert response.status_code == 404

    def test_upload_avatar(self, e2e_client, platform_admin: E2EUser):
        """Test uploading an avatar."""
        # Create a small test PNG image (1x1 pixel red)
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
            b"\x00\x05\xfe\xd4G\x00\x00\x00\x00IEND\xaeB`\x82"
        )

        # Need to only pass auth header, not content-type (let httpx handle it for multipart)
        auth_headers = {"Authorization": platform_admin.headers["Authorization"]}

        response = e2e_client.post(
            "/api/profile/avatar",
            headers=auth_headers,
            files={"file": ("test.png", png_data, "image/png")},
        )
        assert response.status_code == 200
        assert response.json()["has_avatar"] is True

        # Clean up
        e2e_client.delete("/api/profile/avatar", headers=platform_admin.headers)

    def test_upload_avatar_invalid_type(self, e2e_client, platform_admin: E2EUser):
        """Test uploading invalid file type returns 400."""
        auth_headers = {"Authorization": platform_admin.headers["Authorization"]}

        response = e2e_client.post(
            "/api/profile/avatar",
            headers=auth_headers,
            files={"file": ("test.txt", b"not an image", "text/plain")},
        )
        assert response.status_code == 400
        assert "Invalid file type" in response.json()["detail"]

    def test_delete_avatar(self, e2e_client, platform_admin: E2EUser):
        """Test deleting avatar."""
        # First upload an avatar
        png_data = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
            b"\x00\x05\xfe\xd4G\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        auth_headers = {"Authorization": platform_admin.headers["Authorization"]}
        e2e_client.post(
            "/api/profile/avatar",
            headers=auth_headers,
            files={"file": ("test.png", png_data, "image/png")},
        )

        # Delete it
        response = e2e_client.delete("/api/profile/avatar", headers=platform_admin.headers)
        assert response.status_code == 200
        assert response.json()["has_avatar"] is False


class TestPasswordChange:
    """Tests for password change endpoint."""

    def test_change_password_wrong_current(self, e2e_client, platform_admin: E2EUser):
        """Test changing password with wrong current password."""
        response = e2e_client.post(
            "/api/profile/password",
            headers=platform_admin.headers,
            json={
                "current_password": "wrongpassword",
                "new_password": "newpassword123",
            },
        )
        assert response.status_code == 400
        assert "incorrect" in response.json()["detail"].lower()

    def test_change_password_success(self, e2e_client, platform_admin: E2EUser):
        """Test successful password change."""
        original_password = platform_admin.password
        new_password = "newpassword123456"
        password_changed = False

        try:
            response = e2e_client.post(
                "/api/profile/password",
                headers=platform_admin.headers,
                json={
                    "current_password": original_password,
                    "new_password": new_password,
                },
            )
            assert response.status_code == 204
            password_changed = True
            platform_admin.password = new_password

            # Password changes revoke every human session. Get a new MFA-backed
            # session before exercising the inverse change.
            _login_user(platform_admin)
            response = e2e_client.post(
                "/api/profile/password",
                headers=platform_admin.headers,
                json={
                    "current_password": new_password,
                    "new_password": original_password,
                },
            )
            assert response.status_code == 204
            password_changed = False
            platform_admin.password = original_password
        finally:
            if password_changed:
                _login_user(platform_admin)
                response = e2e_client.post(
                    "/api/profile/password",
                    headers=platform_admin.headers,
                    json={
                        "current_password": new_password,
                        "new_password": original_password,
                    },
                )
                assert response.status_code == 204
                platform_admin.password = original_password
            _login_user(platform_admin)


class TestProfileAuth:
    """Tests for profile authentication requirements."""

    def test_get_profile_requires_auth(self, e2e_api_url):
        """Test that profile endpoints require authentication."""
        import httpx

        # Use a fresh client without any session cookies
        with httpx.Client(base_url=e2e_api_url, timeout=30.0) as client:
            response = client.get("/api/profile")
            assert response.status_code == 401
