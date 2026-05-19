"""
Unit tests for Integrations contract models.

Tests Pydantic validation for request/response models.
"""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.models.contracts.integrations import (
    ConfigSchemaItem,
    IntegrationCreate,
    IntegrationUpdate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
    IntegrationSDKResponse,
)


class TestConfigSchemaItem:
    """Tests for ConfigSchemaItem model."""

    def test_config_schema_item_invalid_type(self):
        """Test config schema item with invalid type."""
        with pytest.raises(ValidationError) as exc_info:
            ConfigSchemaItem(key="test", type="invalid")  # type: ignore
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("type",) for e in errors)

    def test_config_schema_item_invalid_key_empty(self):
        """Test config schema item with empty key."""
        with pytest.raises(ValidationError) as exc_info:
            ConfigSchemaItem(key="", type="string")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("key",) for e in errors)

    def test_config_schema_item_invalid_key_characters(self):
        """Test config schema item with invalid characters in key."""
        with pytest.raises(ValidationError) as exc_info:
            ConfigSchemaItem(key="api-key", type="string")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("key",) for e in errors)

    def test_config_schema_item_invalid_key_too_long(self):
        """Test config schema item with key exceeding max length."""
        with pytest.raises(ValidationError) as exc_info:
            ConfigSchemaItem(key="a" * 256, type="string")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("key",) for e in errors)

    def test_config_schema_item_description_too_long(self):
        """Test config schema item with description exceeding max length."""
        with pytest.raises(ValidationError) as exc_info:
            ConfigSchemaItem(key="test", type="string", description="a" * 501)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("description",) for e in errors)


class TestIntegrationCreate:
    """Tests for IntegrationCreate model."""

    def test_integration_create_invalid_name_empty(self):
        """Test creating integration with empty name."""
        with pytest.raises(ValidationError) as exc_info:
            IntegrationCreate(name="")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("name",) for e in errors)

    def test_integration_create_invalid_name_too_long(self):
        """Test creating integration with name exceeding max length."""
        with pytest.raises(ValidationError) as exc_info:
            IntegrationCreate(name="a" * 256)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("name",) for e in errors)

    def test_integration_create_missing_name(self):
        """Test creating integration without required name."""
        with pytest.raises(ValidationError) as exc_info:
            IntegrationCreate()  # type: ignore
        errors = exc_info.value.errors()
        assert any(e["type"] == "missing" and e["loc"] == ("name",) for e in errors)

class TestIntegrationUpdate:
    """Tests for IntegrationUpdate model."""

    def test_integration_update_invalid_name_empty(self):
        """Test update with empty name fails validation."""
        with pytest.raises(ValidationError) as exc_info:
            IntegrationUpdate(name="")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("name",) for e in errors)


class TestIntegrationMappingCreate:
    """Tests for IntegrationMappingCreate model."""

    def test_mapping_create_missing_organization_id(self):
        """Test creating mapping without organization_id."""
        with pytest.raises(ValidationError) as exc_info:
            IntegrationMappingCreate(entity_id="tenant-123")  # type: ignore
        errors = exc_info.value.errors()
        assert any(
            e["type"] == "missing" and e["loc"] == ("organization_id",)
            for e in errors
        )

    def test_mapping_create_missing_entity_id(self):
        """Test creating mapping without entity_id."""
        with pytest.raises(ValidationError) as exc_info:
            IntegrationMappingCreate(organization_id=uuid4())  # type: ignore
        errors = exc_info.value.errors()
        assert any(
            e["type"] == "missing" and e["loc"] == ("entity_id",)
            for e in errors
        )

    def test_mapping_create_allows_empty_entity_id(self):
        """Empty entity_id is allowed — mapping can be created before OAuth
        captures the tenant identifier. See commit 030b4c9f."""
        mapping = IntegrationMappingCreate(
            organization_id=uuid4(),
            entity_id="",
        )
        assert mapping.entity_id == ""

    def test_mapping_create_invalid_entity_id_too_long(self):
        """Test creating mapping with entity_id exceeding max length."""
        with pytest.raises(ValidationError) as exc_info:
            IntegrationMappingCreate(
                organization_id=uuid4(),
                entity_id="a" * 256,
            )
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("entity_id",) for e in errors)

    def test_mapping_create_invalid_entity_name_too_long(self):
        """Test creating mapping with entity_name exceeding max length."""
        with pytest.raises(ValidationError) as exc_info:
            IntegrationMappingCreate(
                organization_id=uuid4(),
                entity_id="tenant-123",
                entity_name="a" * 256,
            )
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("entity_name",) for e in errors)


class TestIntegrationMappingUpdate:
    """Tests for IntegrationMappingUpdate model."""

    def test_mapping_update_allows_empty_entity_id(self):
        """Empty entity_id clears the field — see commit 030b4c9f."""
        update = IntegrationMappingUpdate(entity_id="")
        assert update.entity_id == ""


class TestIntegrationSDKResponse:
    """Tests for IntegrationSDKResponse model (SDK response)."""

    def test_integration_data_missing_integration_id(self):
        """Test that integration_id is required and cannot be None."""
        with pytest.raises(ValidationError) as exc_info:
            IntegrationSDKResponse(integration_id=None, entity_id="entity-1")  # type: ignore
        errors = exc_info.value.errors()
        # Passing None to a UUID field raises uuid_type error
        assert any(
            e["type"] == "uuid_type" and e["loc"] == ("integration_id",)
            for e in errors
        )

    def test_integration_data_missing_entity_id(self):
        """Test that entity_id is required and cannot be None."""
        with pytest.raises(ValidationError) as exc_info:
            IntegrationSDKResponse(integration_id=uuid4(), entity_id=None)  # type: ignore
        errors = exc_info.value.errors()
        # Passing None to a str field raises string_type error
        assert any(
            e["type"] == "string_type" and e["loc"] == ("entity_id",)
            for e in errors
        )

