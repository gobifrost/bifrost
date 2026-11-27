"""
Contract tests for OAuth API models
Tests Pydantic validation rules for OAuth connection request/response models
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

# Import OAuth models for testing
from src.models import (
    CreateOAuthConnectionRequest,
    OAuthConnection,
    OAuthConnectionDetail,
    OAuthConnectionSummary,
)


class TestCreateOAuthConnectionRequest:
    """Test validation for CreateOAuthConnectionRequest model - T014"""

    def test_invalid_oauth_flow_type(self):
        """Test that invalid OAuth flow types are rejected"""

        with pytest.raises(ValidationError) as exc_info:
            CreateOAuthConnectionRequest(
                integration_id="550e8400-e29b-41d4-a716-446655440002",
                oauth_flow_type="invalid_flow",
                client_id="abc123",
                client_secret="secret",
                authorization_url="https://auth.com/authorize",
                token_url="https://auth.com/token"
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("oauth_flow_type",) for e in errors)

    def test_authorization_url_must_be_https(self):
        """Test that authorization URL must use HTTPS"""

        with pytest.raises(ValidationError) as exc_info:
            CreateOAuthConnectionRequest(
                integration_id="550e8400-e29b-41d4-a716-446655440003",
                oauth_flow_type="authorization_code",
                client_id="abc123",
                client_secret="secret",
                authorization_url="http://insecure.com/authorize",  # HTTP not allowed
                token_url="https://auth.com/token"
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("authorization_url",) for e in errors)

    def test_token_url_must_be_https(self):
        """Test that token URL must use HTTPS"""

        with pytest.raises(ValidationError) as exc_info:
            CreateOAuthConnectionRequest(
                integration_id="550e8400-e29b-41d4-a716-446655440004",
                oauth_flow_type="authorization_code",
                client_id="abc123",
                client_secret="secret",
                authorization_url="https://auth.com/authorize",
                token_url="http://insecure.com/token"  # HTTP not allowed
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("token_url",) for e in errors)

    def test_missing_required_fields(self):
        """Test that all required fields must be present"""

        with pytest.raises(ValidationError) as exc_info:
            CreateOAuthConnectionRequest(
                # Missing: integration_id and oauth_flow_type. The selected flow
                # determines whether client_id and token_url are required.
            )

        errors = exc_info.value.errors()
        required_fields = {"integration_id", "oauth_flow_type"}
        missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}
        assert required_fields.issubset(missing_fields)
        # client_secret is optional (for PKCE flow)
        # authorization_url is optional (not needed for client_credentials)

    def test_scopes_optional_defaults_to_empty(self):
        """Test that scopes field is optional and defaults to empty string"""

        request = CreateOAuthConnectionRequest(
            integration_id="550e8400-e29b-41d4-a716-446655440005",
            oauth_flow_type="client_credentials",
            client_id="abc",
            client_secret="secret",
            token_url="https://auth.com/token"
        )

        assert request.scopes == ""

    def test_client_credentials_requires_client_secret(self):
        """Test that client_credentials flow requires client_secret"""

        with pytest.raises(ValidationError) as exc_info:
            CreateOAuthConnectionRequest(
                integration_id="550e8400-e29b-41d4-a716-446655440006",
                oauth_flow_type="client_credentials",
                client_id="abc123",
                token_url="https://auth.com/token"
                # Missing client_secret
            )

        errors = exc_info.value.errors()
        assert any("client_secret is required" in str(e["ctx"]) for e in errors if "ctx" in e)

    def test_client_credentials_does_not_require_authorization_url(self):
        """Test that client_credentials flow does not require authorization_url"""

        request = CreateOAuthConnectionRequest(
            integration_id="550e8400-e29b-41d4-a716-446655440007",
            oauth_flow_type="client_credentials",
            client_id="abc123",
            client_secret="secret",
            token_url="https://auth.com/token"
            # No authorization_url
        )

        assert request.oauth_flow_type == "client_credentials"
        assert request.authorization_url is None

    def test_authorization_code_requires_authorization_url(self):
        """Test that authorization_code flow requires authorization_url"""

        with pytest.raises(ValidationError) as exc_info:
            CreateOAuthConnectionRequest(
                integration_id="550e8400-e29b-41d4-a716-446655440008",
                oauth_flow_type="authorization_code",
                client_id="abc123",
                token_url="https://auth.com/token"
                # Missing authorization_url
            )

        errors = exc_info.value.errors()
        assert any("authorization_url is required" in str(e["ctx"]) for e in errors if "ctx" in e)

    def test_client_credentials_with_empty_string_authorization_url(self):
        """Test that client_credentials flow accepts empty string for authorization_url"""

        request = CreateOAuthConnectionRequest(
            integration_id="550e8400-e29b-41d4-a716-446655440009",
            oauth_flow_type="client_credentials",
            client_id="abc123",
            client_secret="secret",
            authorization_url="",  # Empty string should be converted to None
            token_url="https://auth.com/token"
        )

        assert request.oauth_flow_type == "client_credentials"
        assert request.authorization_url is None  # Empty string converted to None

    def test_workload_identity_accepts_optional_identity_configuration(self):
        """Host-managed credentials need no OAuth endpoints or secret."""
        request = CreateOAuthConnectionRequest(
            integration_id="550e8400-e29b-41d4-a716-446655440010",
            oauth_flow_type="workload_identity",
            tenant_id="tenant-123",
        )

        assert request.client_id == ""
        assert request.tenant_id == "tenant-123"
        assert request.authorization_url is None
        assert request.token_url is None


class TestOAuthConnectionSummary:
    """Test OAuthConnectionSummary response model - T015"""

    def test_valid_status_values(self):
        """Test that all valid status values are accepted"""

        valid_statuses = ["not_connected", "waiting_callback", "testing", "completed", "failed"]

        for status in valid_statuses:
            summary = OAuthConnectionSummary(
                connection_name="test",
                oauth_flow_type="authorization_code",
                status=status,
                created_at=datetime.now(timezone.utc)
            )
            assert summary.status == status

    def test_invalid_status_rejected(self):
        """Test that invalid status values are rejected"""

        with pytest.raises(ValidationError) as exc_info:
            OAuthConnectionSummary(
                connection_name="test",
                oauth_flow_type="authorization_code",
                status="invalid_status",
                created_at=datetime.now(timezone.utc)
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("status",) for e in errors)


class TestOAuthConnectionDetail:
    """Test OAuthConnectionDetail response model - T016"""

    def test_detail_does_not_expose_secrets(self):
        """Test that detail response does not expose sensitive fields"""

        detail = OAuthConnectionDetail(
            connection_name="test",
            oauth_flow_type="client_credentials",
            client_id="abc",
            authorization_url="https://auth.com/authorize",
            token_url="https://auth.com/token",
            scopes="",
            redirect_uri="/api/oauth/callback/test",
            status="completed",
            created_at=datetime.now(timezone.utc),
            created_by="user",
            updated_at=datetime.now(timezone.utc)
        )

        detail_dict = detail.model_dump()

        # These fields should NOT be in the response
        assert "client_secret" not in detail_dict
        assert "access_token" not in detail_dict
        assert "refresh_token" not in detail_dict

    def test_detail_serialization_to_json(self):
        """Test that detail can be serialized to JSON mode"""

        detail = OAuthConnectionDetail(
            connection_name="test",
            oauth_flow_type="client_credentials",
            client_id="abc",
            authorization_url="https://auth.com/authorize",
            token_url="https://auth.com/token",
            scopes="",
            redirect_uri="/api/oauth/callback/test",
            status="completed",
            created_at=datetime.now(timezone.utc),
            created_by="user",
            updated_at=datetime.now(timezone.utc)
        )

        detail_dict = detail.model_dump(mode="json")
        assert isinstance(detail_dict["created_at"], str)  # datetime -> ISO string
        assert isinstance(detail_dict["updated_at"], str)


class TestOAuthConnection:
    """Test internal OAuthConnection model - storage representation"""

    def test_is_expired_method(self):
        """Test is_expired() method returns True when expires_at is past"""
        from datetime import timedelta

        # Token expired 1 hour ago
        expired_connection = OAuthConnection(
            org_id="GLOBAL",
            connection_name="test",
            oauth_flow_type="client_credentials",
            client_id="abc",
            client_secret_config_key="test_client_secret",
            oauth_response_config_key="test_oauth_response",
            token_url="https://auth.com/token",
            redirect_uri="/api/oauth/callback/test",
            status="completed",
            created_by="user",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1)
        )

        assert expired_connection.is_expired()

    def test_expires_soon_method(self):
        """Test expires_soon() method with custom hours threshold"""
        from datetime import timedelta

        # Token expires in 3 hours
        connection = OAuthConnection(
            org_id="GLOBAL",
            connection_name="test",
            oauth_flow_type="client_credentials",
            client_id="abc",
            client_secret_config_key="test_client_secret",
            oauth_response_config_key="test_oauth_response",
            token_url="https://auth.com/token",
            redirect_uri="/api/oauth/callback/test",
            status="completed",
            created_by="user",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=3)
        )

        # With 4 hour threshold, should return True (expires within 4 hours)
        assert connection.expires_soon(hours=4)

        # With 2 hour threshold, should return False (does not expire within 2 hours)
        assert not connection.expires_soon(hours=2)
