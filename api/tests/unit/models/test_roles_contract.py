"""
Contract tests for Roles API
Tests request/response validation for role management endpoints
"""

import pytest
from pydantic import ValidationError

from src.models import (
    AssignFormsToRoleRequest,
    AssignUsersToRoleRequest,
    RoleCreate,
    RoleUpdate,
)


# Note: Models use snake_case (e.g., is_active, user_ids, form_ids)
# This matches the OpenAPI/TypeScript schema


class TestRoleCreate:
    """Test RoleCreate validation"""

    def test_invalid_empty_name(self):
        """Empty name should fail validation"""
        with pytest.raises(ValidationError) as exc_info:
            RoleCreate(name="")

        errors = exc_info.value.errors()
        assert any(error["type"] == "string_too_short" for error in errors)

    def test_invalid_name_too_long(self):
        """Name exceeding 100 characters should fail"""
        with pytest.raises(ValidationError):
            RoleCreate(name="A" * 101)

    def test_invalid_missing_name(self):
        """Missing name should fail validation"""
        with pytest.raises(ValidationError) as exc_info:
            RoleCreate()

        errors = exc_info.value.errors()
        assert any(error["loc"] == ("name",) for error in errors)


class TestRoleUpdate:
    """Test RoleUpdate validation"""

    def test_invalid_empty_name(self):
        """Empty name should fail validation"""
        with pytest.raises(ValidationError):
            RoleUpdate(name="")

    def test_invalid_name_too_long(self):
        """Name exceeding 100 characters should fail"""
        with pytest.raises(ValidationError):
            RoleUpdate(name="A" * 101)


class TestAssignUsersToRoleRequest:
    """Test AssignUsersToRoleRequest validation"""

    def test_invalid_empty_list(self):
        """Empty user list should fail validation"""
        with pytest.raises(ValidationError) as exc_info:
            AssignUsersToRoleRequest(user_ids=[])

        errors = exc_info.value.errors()
        assert any(error["type"] == "too_short" for error in errors)

    def test_invalid_missing_user_ids(self):
        """Missing user_ids should fail validation"""
        with pytest.raises(ValidationError) as exc_info:
            AssignUsersToRoleRequest()

        errors = exc_info.value.errors()
        assert any(error["loc"] == ("user_ids",) for error in errors)


class TestAssignFormsToRoleRequest:
    """Test AssignFormsToRoleRequest validation"""

    def test_invalid_empty_list(self):
        """Empty form list should fail validation"""
        with pytest.raises(ValidationError) as exc_info:
            AssignFormsToRoleRequest(form_ids=[])

        errors = exc_info.value.errors()
        assert any(error["type"] == "too_short" for error in errors)

    def test_invalid_missing_form_ids(self):
        """Missing form_ids should fail validation"""
        with pytest.raises(ValidationError) as exc_info:
            AssignFormsToRoleRequest()

        errors = exc_info.value.errors()
        assert any(error["loc"] == ("form_ids",) for error in errors)

