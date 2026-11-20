"""
Contract tests for Permissions API models
Tests Pydantic validation rules for request/response models
"""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from shared.builtin_roles import PLATFORM_OPERATOR_PERMISSIONS, USER_BASE_PERMISSIONS
from src.models import GrantPermissionsRequest, PermissionsData, UserPermission
from src.models.contracts.permissions import (
    PRIVILEGED_PERMISSIONS,
    WILDCARD_EXCLUDED_PERMISSIONS,
    parse_permission,
    permission_display_name,
)
from src.models.contracts.users import User


# Note: Models use snake_case (e.g., user_id, org_id, can_execute_workflows)
# This matches the OpenAPI/TypeScript schema


class TestGrantPermissionsRequest:
    """Test validation for GrantPermissionsRequest model"""

    @pytest.mark.parametrize("missing_field", ["user_id", "org_id", "permissions"])
    def test_missing_required_field(self, missing_field):
        """Test that each required field is enforced"""
        fields = {
            "user_id": "user-123",
            "org_id": "org-456",
            "permissions": PermissionsData(
                can_execute_workflows=True,
                can_manage_config=True,
                can_manage_forms=True,
                can_view_history=True
            ),
        }
        del fields[missing_field]
        with pytest.raises(ValidationError) as exc_info:
            GrantPermissionsRequest(**fields)

        errors = exc_info.value.errors()
        assert any(e["loc"] == (missing_field,) and e["type"] == "missing" for e in errors)


class TestPermissionsData:
    """Test validation for PermissionsData model"""

    @pytest.mark.parametrize("missing_field", [
        "can_execute_workflows",
        "can_manage_config",
        "can_manage_forms",
        "can_view_history",
    ])
    def test_missing_required_field(self, missing_field):
        """Test that each required permission flag is enforced"""
        fields = {
            "can_execute_workflows": True,
            "can_manage_config": True,
            "can_manage_forms": True,
            "can_view_history": True,
        }
        del fields[missing_field]
        with pytest.raises(ValidationError) as exc_info:
            PermissionsData(**fields)

        errors = exc_info.value.errors()
        assert any(e["loc"] == (missing_field,) and e["type"] == "missing" for e in errors)


class TestUserPermissionResponse:
    """Test UserPermission response model structure"""

    def test_user_permission_defaults_to_false(self):
        """Test that permission flags default to False"""
        permission = UserPermission(
            user_id="user-123",
            org_id="org-456",
            granted_by="admin-user",
            granted_at=datetime.now(timezone.utc)
        )
        assert permission.can_execute_workflows is False
        assert permission.can_manage_config is False
        assert permission.can_manage_forms is False
        assert permission.can_view_history is False

    def test_user_permission_missing_required_fields(self):
        """Test that all required fields must be present"""
        with pytest.raises(ValidationError) as exc_info:
            UserPermission(
                user_id="user-123",
                org_id="org-456"
                # Missing: granted_by, granted_at
            )

        errors = exc_info.value.errors()
        required_fields = {"granted_by", "granted_at"}
        missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}
        assert required_fields.issubset(missing_fields)



class TestUserResponse:
    """Test User response model structure"""

    def test_user_is_active_defaults_to_true(self):
        """Test that is_active defaults to True"""
        user = User(
            id="user-123",
            email="user@example.com",
            display_name="Test User",
            created_at=datetime.now(timezone.utc)
        )
        assert user.is_active is True

    def test_user_missing_required_fields(self):
        """Test that all required fields must be present"""
        with pytest.raises(ValidationError) as exc_info:
            User(
                id="user-123",
                email="user@example.com"
                # Missing: display_name, created_at
            )

        errors = exc_info.value.errors()
        required_fields = {"display_name", "created_at"}
        missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}
        assert required_fields.issubset(missing_fields)


class TestExecuteCodePermission:
    def test_execute_code_is_a_privileged_platform_wildcard_permission(self):
        parsed = parse_permission("code.execute")

        assert (parsed.domain, parsed.action, parsed.extended) == (
            "code",
            "execute",
            False,
        )
        assert permission_display_name("code.execute") == "Execute Code"
        assert "code.execute" in PRIVILEGED_PERMISSIONS
        assert "code.execute" not in WILDCARD_EXCLUDED_PERMISSIONS
        assert "code.execute" not in USER_BASE_PERMISSIONS
        assert "code.execute" not in PLATFORM_OPERATOR_PERMISSIONS

