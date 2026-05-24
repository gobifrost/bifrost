"""
Unit tests for IntegrationsRepository.

Tests the database operations for integration management and mapping.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.models.contracts.integrations import (
    ConfigSchemaItem,
    IntegrationCreate,
    IntegrationUpdate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
)
from src.repositories.integrations import IntegrationsRepository


class TestIntegrationsRepository:
    """Tests for IntegrationsRepository methods."""

    @pytest.fixture
    def mock_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.flush = AsyncMock()
        session.refresh = AsyncMock()
        return session

    @pytest.fixture
    def repository(self, mock_session):
        """Create repository with mock session."""
        return IntegrationsRepository(mock_session)

    @pytest.fixture
    def mock_integration(self):
        """Create a mock integration object."""
        integration = MagicMock()
        integration.id = uuid4()
        integration.name = "Test Integration"
        integration.oauth_provider_id = uuid4()
        integration.list_entities_data_provider_id = uuid4()

        # config_schema should be a list of objects with .key attribute, not dicts
        mock_schema_item = MagicMock()
        mock_schema_item.key = "api_key"
        mock_schema_item.type = "secret"
        mock_schema_item.required = True
        mock_schema_item.description = "API Key"
        integration.config_schema = [mock_schema_item]

        integration.is_deleted = False
        integration.created_at = MagicMock()
        integration.updated_at = MagicMock()
        integration.oauth_provider = None
        integration.mappings = []
        return integration

    @pytest.fixture
    def mock_mapping(self):
        """Create a mock integration mapping object."""
        mapping = MagicMock()
        mapping.id = uuid4()
        mapping.integration_id = uuid4()
        mapping.organization_id = uuid4()
        mapping.entity_id = "tenant-123"
        mapping.entity_name = "Test Tenant"
        mapping.oauth_token_id = uuid4()
        mapping.created_at = MagicMock()
        mapping.updated_at = MagicMock()
        mapping.integration = MagicMock()
        mapping.organization = MagicMock()
        mapping.oauth_token = None
        return mapping

    # =========================================================================
    # Integration CRUD Tests
    # =========================================================================

    async def test_create_integration(self, repository, mock_session, mock_integration):
        """Test creating an integration with all fields."""
        # Mock the create method (inherited from BaseRepository)
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()
        repository.create = AsyncMock(return_value=mock_integration)

        data = IntegrationCreate(
            name="Test Integration",
            oauth_provider_id=uuid4(),
            list_entities_data_provider_id=uuid4(),
            config_schema=[
                ConfigSchemaItem(
                    key="api_key",
                    type="secret",
                    required=True,
                    description="API Key",
                )
            ],
        )

        result = await repository.create_integration(data)

        assert result == mock_integration
        assert result.name == "Test Integration"
        repository.create.assert_called_once()

    async def test_create_integration_minimal(
        self, repository, mock_session, mock_integration
    ):
        """Test creating an integration with only required fields."""
        mock_integration.oauth_provider_id = None
        mock_integration.name = "Minimal Integration"
        mock_integration.list_entities_data_provider_id = None
        mock_integration.config_schema = None

        repository.create = AsyncMock(return_value=mock_integration)

        data = IntegrationCreate(name="Minimal Integration")

        result = await repository.create_integration(data)

        assert result == mock_integration
        assert result.name == "Minimal Integration"
        assert result.oauth_provider_id is None
        assert result.config_schema is None

    async def test_get_integration(self, repository, mock_session, mock_integration):
        """Test getting an integration by ID."""
        mock_result = MagicMock()
        mock_result.unique.return_value = mock_result
        mock_result.scalar_one_or_none.return_value = mock_integration
        mock_session.execute.return_value = mock_result

        result = await repository.get_integration(mock_integration.id)

        assert result == mock_integration
        assert result.id == mock_integration.id
        mock_session.execute.assert_called_once()

    async def test_get_integration_not_found(self, repository, mock_session):
        """Test getting an integration that doesn't exist."""
        mock_result = MagicMock()
        mock_result.unique.return_value = mock_result
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repository.get_integration(uuid4())

        assert result is None
        mock_session.execute.assert_called_once()

    async def test_get_integration_by_name(self, repository, mock_session, mock_integration):
        """Test getting an integration by name."""
        mock_result = MagicMock()
        mock_result.unique.return_value = mock_result
        mock_result.scalar_one_or_none.return_value = mock_integration
        mock_session.execute.return_value = mock_result

        result = await repository.get_integration_by_name("Test Integration")

        assert result == mock_integration
        assert result.name == "Test Integration"
        mock_session.execute.assert_called_once()

    async def test_get_config_for_mapping_skips_default_secrets(
        self, repository, mock_session
    ):
        """Org mapping config must not receive decrypted global secret defaults."""
        from src.models.enums import ConfigType

        integration_id = uuid4()
        org_id = uuid4()

        default_secret = MagicMock()
        default_secret.key = "api_key"
        default_secret.value = {"value": "encrypted-global-secret"}
        default_secret.config_type = ConfigType.SECRET
        default_secret.organization_id = None

        default_plain = MagicMock()
        default_plain.key = "base_url"
        default_plain.value = {"value": "https://example.test"}
        default_plain.config_type = ConfigType.STRING
        default_plain.organization_id = None

        org_secret = MagicMock()
        org_secret.key = "org_api_key"
        org_secret.value = {"value": "encrypted-org-secret"}
        org_secret.config_type = ConfigType.SECRET
        org_secret.organization_id = org_id

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [default_secret, default_plain, org_secret]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        with patch("src.repositories.integrations.decrypt_secret", return_value="org-secret"):
            result = await repository.get_config_for_mapping(integration_id, org_id)

        assert "api_key" not in result
        assert result["base_url"] == "https://example.test"
        assert result["org_api_key"] == "org-secret"

    async def test_get_provider_org_token_filters_by_organization(
        self, repository, mock_session
    ):
        """Org-owned token fallback must not match a user-null token from another org."""
        provider_id = uuid4()
        org_id = uuid4()
        expected_token = MagicMock()

        mock_scalars = MagicMock()
        mock_scalars.first.return_value = expected_token
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        result = await repository.get_provider_org_token(provider_id, org_id)

        assert result == expected_token
        statement = mock_session.execute.call_args.args[0]
        compiled = statement.compile()
        assert org_id in compiled.params.values()

    async def test_list_integrations(self, repository, mock_session, mock_integration):
        """Test listing all non-deleted integrations."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_integration]
        mock_result.scalars.return_value = mock_scalars
        mock_result.unique.return_value = mock_result
        mock_session.execute.return_value = mock_result

        result = await repository.list_integrations()

        assert len(result) == 1
        assert result[0] == mock_integration
        assert result[0].is_deleted is False

    async def test_list_integrations_include_deleted(
        self, repository, mock_session, mock_integration
    ):
        """Test listing integrations including deleted ones."""
        deleted_integration = MagicMock()
        deleted_integration.is_deleted = True
        deleted_integration.name = "Deleted Integration"

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_integration, deleted_integration]
        mock_result.scalars.return_value = mock_scalars
        mock_result.unique.return_value = mock_result
        mock_session.execute.return_value = mock_result

        result = await repository.list_integrations(include_deleted=True)

        assert len(result) == 2
        assert any(i.is_deleted for i in result)

    async def test_update_integration(self, repository, mock_session, mock_integration):
        """Test updating an integration with partial data."""
        repository.get_integration = AsyncMock(return_value=mock_integration)
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()

        new_name = "Updated Integration"
        data = IntegrationUpdate(name=new_name)

        result = await repository.update_integration(mock_integration.id, data)

        assert result == mock_integration
        assert result.name == new_name
        # get_integration is called twice: once to fetch, once to reload with relationships
        assert repository.get_integration.call_count == 2

    async def test_update_integration_config_schema(
        self, repository, mock_session, mock_integration
    ):
        """Test updating integration config schema."""
        repository.get_integration = AsyncMock(return_value=mock_integration)
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()

        new_schema = [
            ConfigSchemaItem(
                key="new_key",
                type="string",
                required=False,
                description="New config",
            )
        ]
        data = IntegrationUpdate(config_schema=new_schema)

        result = await repository.update_integration(mock_integration.id, data)

        assert result == mock_integration
        mock_session.flush.assert_called_once()

    async def test_update_integration_not_found(self, repository, mock_session):
        """Test updating a non-existent integration."""
        repository.get_integration = AsyncMock(return_value=None)

        data = IntegrationUpdate(name="New Name")
        result = await repository.update_integration(uuid4(), data)

        assert result is None
        repository.get_integration.assert_called_once()

    async def test_delete_integration(self, repository, mock_session, mock_integration):
        """Test soft deleting an integration."""
        repository.get_integration = AsyncMock(return_value=mock_integration)
        mock_session.flush = AsyncMock()

        result = await repository.delete_integration(mock_integration.id)

        assert result is True
        assert mock_integration.is_deleted is True
        mock_session.flush.assert_called_once()

    async def test_delete_integration_not_found(self, repository, mock_session):
        """Test deleting a non-existent integration."""
        repository.get_integration = AsyncMock(return_value=None)

        result = await repository.delete_integration(uuid4())

        assert result is False
        repository.get_integration.assert_called_once()

    # =========================================================================
    # Integration Mapping CRUD Tests
    # =========================================================================

    async def test_create_mapping(self, repository, mock_session, mock_mapping):
        """Test creating an integration mapping."""
        repository.create = AsyncMock(return_value=mock_mapping)
        repository.get_mapping = AsyncMock(return_value=mock_mapping)

        data = IntegrationMappingCreate(
            organization_id=uuid4(),
            entity_id="tenant-123",
            entity_name="Test Tenant",
            oauth_token_id=uuid4(),
        )

        result = await repository.create_mapping(uuid4(), data)

        assert result == mock_mapping
        assert result.entity_id == "tenant-123"
        assert result.entity_name == "Test Tenant"

    async def test_get_mapping(self, repository, mock_session, mock_mapping):
        """Test getting a mapping by ID."""
        mock_result = MagicMock()
        mock_result.unique.return_value = mock_result
        mock_result.scalar_one_or_none.return_value = mock_mapping
        mock_session.execute.return_value = mock_result

        result = await repository.get_mapping(mock_mapping.id)

        assert result == mock_mapping
        assert result.id == mock_mapping.id
        mock_session.execute.assert_called_once()

    async def test_get_mapping_not_found(self, repository, mock_session):
        """Test getting a mapping that doesn't exist."""
        mock_result = MagicMock()
        mock_result.unique.return_value = mock_result
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repository.get_mapping(uuid4())

        assert result is None

    async def test_get_mapping_by_org(
        self, repository, mock_session, mock_mapping
    ):
        """Test getting a mapping by integration and organization IDs."""
        mock_result = MagicMock()
        mock_result.unique.return_value = mock_result
        mock_result.scalar_one_or_none.return_value = mock_mapping
        mock_session.execute.return_value = mock_result

        result = await repository.get_mapping_by_org(
            mock_mapping.integration_id, mock_mapping.organization_id
        )

        assert result == mock_mapping
        mock_session.execute.assert_called_once()

    async def test_get_mapping_by_org_not_found(self, repository, mock_session):
        """Test getting a mapping that doesn't exist for an org."""
        mock_result = MagicMock()
        mock_result.unique.return_value = mock_result
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        result = await repository.get_mapping_by_org(uuid4(), uuid4())

        assert result is None

    async def test_list_mappings(self, repository, mock_session, mock_mapping):
        """Test listing all mappings for an integration."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_mapping]
        mock_result.scalars.return_value = mock_scalars
        mock_result.unique.return_value = mock_result
        mock_session.execute.return_value = mock_result

        result = await repository.list_mappings(mock_mapping.integration_id)

        assert len(result) == 1
        assert result[0] == mock_mapping

    async def test_list_mappings_empty(self, repository, mock_session):
        """Test listing mappings when none exist."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_result.unique.return_value = mock_result
        mock_session.execute.return_value = mock_result

        result = await repository.list_mappings(uuid4())

        assert len(result) == 0

    async def test_update_mapping(self, repository, mock_session, mock_mapping):
        """Test updating a mapping with partial data."""
        repository.get_mapping = AsyncMock(return_value=mock_mapping)
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()

        new_entity_name = "Updated Tenant"
        data = IntegrationMappingUpdate(entity_name=new_entity_name)

        result = await repository.update_mapping(mock_mapping.id, data)

        assert result == mock_mapping
        assert result.entity_name == new_entity_name
        repository.get_mapping.assert_called()

    async def test_update_mapping_oauth_token(self, repository, mock_session, mock_mapping):
        """Test updating a mapping's OAuth token."""
        repository.get_mapping = AsyncMock(return_value=mock_mapping)
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()

        new_token_id = uuid4()
        data = IntegrationMappingUpdate(oauth_token_id=new_token_id)

        result = await repository.update_mapping(mock_mapping.id, data)

        assert result == mock_mapping
        assert result.oauth_token_id == new_token_id

    async def test_update_mapping_not_found(self, repository, mock_session):
        """Test updating a non-existent mapping."""
        repository.get_mapping = AsyncMock(return_value=None)

        data = IntegrationMappingUpdate(entity_name="New Name")
        result = await repository.update_mapping(uuid4(), data)

        assert result is None

    async def test_delete_mapping(self, repository, mock_session, mock_mapping):
        """Test deleting a mapping (hard delete)."""
        repository.get_mapping = AsyncMock(return_value=mock_mapping)
        repository.delete = AsyncMock()

        result = await repository.delete_mapping(mock_mapping.id)

        assert result is True
        repository.delete.assert_called_once_with(mock_mapping)

    async def test_delete_mapping_not_found(self, repository, mock_session):
        """Test deleting a non-existent mapping."""
        repository.get_mapping = AsyncMock(return_value=None)

        result = await repository.delete_mapping(uuid4())

        assert result is False

    # =========================================================================
    # Convenience Methods Tests
    # =========================================================================

    async def test_get_integration_for_org(
        self, repository, mock_session, mock_integration, mock_mapping
    ):
        """Test convenience method to get integration mapping by name and org."""
        repository.get_integration_by_name = AsyncMock(return_value=mock_integration)
        repository.get_mapping_by_org = AsyncMock(return_value=mock_mapping)

        result = await repository.get_integration_for_org(
            "Test Integration", mock_mapping.organization_id
        )

        assert result == mock_mapping
        repository.get_integration_by_name.assert_called_once_with("Test Integration")
        repository.get_mapping_by_org.assert_called_once_with(
            mock_integration.id, mock_mapping.organization_id
        )

    async def test_get_integration_for_org_not_found_integration(
        self, repository, mock_session
    ):
        """Test get_integration_for_org when integration doesn't exist."""
        repository.get_integration_by_name = AsyncMock(return_value=None)

        result = await repository.get_integration_for_org("Nonexistent", uuid4())

        assert result is None
        repository.get_integration_by_name.assert_called_once_with("Nonexistent")

    async def test_get_integration_for_org_not_found_mapping(
        self, repository, mock_session, mock_integration
    ):
        """Test get_integration_for_org when mapping doesn't exist."""
        repository.get_integration_by_name = AsyncMock(return_value=mock_integration)
        repository.get_mapping_by_org = AsyncMock(return_value=None)

        org_id = uuid4()
        result = await repository.get_integration_for_org("Test Integration", org_id)

        assert result is None
        repository.get_mapping_by_org.assert_called_once_with(mock_integration.id, org_id)
