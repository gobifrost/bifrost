"""
Integrations Repository

Database operations for integration management and mapping.
Handles CRUD operations for Integration and IntegrationMapping tables.
"""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.core.security import decrypt_secret
from src.models.contracts.integrations import (
    IntegrationCreate,
    IntegrationUpdate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
)
from src.models.enums import ConfigType
from src.models.orm.config import Config
from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
from src.repositories.base import BaseRepository
from src.repositories.org_scoped import OrgScopedRepository

logger = logging.getLogger(__name__)


class IntegrationMappingRepository(OrgScopedRepository[IntegrationMapping]):
    """Repository for integration mapping operations with cascade scoping.

    Cascade scoping: org-specific mappings > global mappings

    This repository provides lookup methods that automatically cascade from
    org-specific mappings to global (organization_id IS NULL) mappings when
    no org-specific mapping exists.

    Note: This is SDK-only, no RBAC required (role_table = None).
    """

    model = IntegrationMapping
    role_table = None  # SDK-only, no RBAC

    async def get_by_integration_name(self, name: str) -> IntegrationMapping | None:
        """Get mapping by integration name with cascade scoping.

        Lookup flow:
        1. Find the Integration by name
        2. Use cascade scoping to find mapping:
           - First try org-specific mapping (if org_id is set)
           - Fall back to global mapping (organization_id IS NULL)

        Args:
            name: Integration name to look up

        Returns:
            IntegrationMapping if found with cascade scoping, None otherwise
        """
        # First get integration by name
        integration_result = await self.session.execute(
            select(Integration).where(Integration.name == name)
        )
        integration = integration_result.scalar_one_or_none()
        if not integration:
            return None

        # Then use cascade scoping to find mapping
        # The inherited get() method handles cascade: org-specific first, then global
        return await self.get(integration_id=integration.id)


class IntegrationsRepository(BaseRepository[Integration]):
    """Repository for integration operations."""

    model = Integration

    def __init__(self, session: AsyncSession):
        """
        Initialize repository with database session.

        Args:
            session: SQLAlchemy async session
        """
        super().__init__(session)

    # =========================================================================
    # Integration CRUD Operations
    # =========================================================================

    async def create_integration(
        self, data: IntegrationCreate
    ) -> Integration:
        """
        Create a new integration.

        Args:
            data: Integration creation data

        Returns:
            Created integration
        """
        integration = Integration(
            name=data.name,
            entity_id=data.entity_id,
            entity_id_name=data.entity_id_name,
        )

        # Add config schema items if provided
        if data.config_schema:
            for idx, item in enumerate(data.config_schema):
                schema_item = IntegrationConfigSchema(
                    key=item.key,
                    type=item.type,
                    required=item.required,
                    description=item.description,
                    options=item.options,
                    position=idx,
                )
                integration.config_schema.append(schema_item)

        return await self.create(integration)

    async def get_integration(self, id: UUID) -> Integration | None:
        """
        Get integration by ID with relationships loaded.

        Args:
            id: Integration UUID

        Returns:
            Integration or None if not found
        """
        result = await self.session.execute(
            select(Integration)
            .where(Integration.id == id)
            .options(
                joinedload(Integration.oauth_provider),
                selectinload(Integration.mappings),
                selectinload(Integration.config_schema),
            )
        )
        return result.unique().scalar_one_or_none()

    async def get_integration_by_name(self, name: str) -> Integration | None:
        """
        Get integration by name with relationships loaded.

        Args:
            name: Integration name

        Returns:
            Integration or None if not found
        """
        result = await self.session.execute(
            select(Integration)
            .where(Integration.name == name)
            .options(
                joinedload(Integration.oauth_provider),
                selectinload(Integration.mappings),
                selectinload(Integration.config_schema),
            )
        )
        return result.unique().scalar_one_or_none()

    async def list_integrations(
        self, include_deleted: bool = False
    ) -> list[Integration]:
        """
        List all integrations.

        Args:
            include_deleted: If True, include soft-deleted integrations

        Returns:
            List of integrations
        """
        query = select(Integration).options(
            joinedload(Integration.oauth_provider),
            selectinload(Integration.mappings),
            selectinload(Integration.config_schema),
        )

        if not include_deleted:
            query = query.where(Integration.is_deleted.is_(False))

        query = query.order_by(Integration.name)
        result = await self.session.execute(query)
        return list(result.unique().scalars().all())

    async def update_integration(
        self, id: UUID, data: IntegrationUpdate
    ) -> Integration | None:
        """
        Update an integration.

        Handles config_schema updates by:
        1. Removing schema items not in the new list (cascade deletes related configs)
        2. Updating existing schema items by key
        3. Adding new schema items

        Args:
            id: Integration UUID
            data: Update data (partial)

        Returns:
            Updated integration or None if not found
        """
        integration = await self.get_integration(id)
        if not integration:
            return None

        # Update only provided fields
        if data.name is not None:
            integration.name = data.name
        if data.list_entities_data_provider_id is not None:
            integration.list_entities_data_provider_id = (
                data.list_entities_data_provider_id
            )
        if data.entity_id is not None:
            integration.entity_id = data.entity_id
        if data.entity_id_name is not None:
            integration.entity_id_name = data.entity_id_name

        # Handle config_schema updates (normalized table)
        if data.config_schema is not None:
            # Build lookup of existing schema items by key
            existing_by_key = {item.key: item for item in integration.config_schema}
            new_keys = {item.key for item in data.config_schema}

            # Remove schema items that are not in the new list
            # (cascade delete will remove related configs)
            for key in list(existing_by_key.keys()):
                if key not in new_keys:
                    item = existing_by_key[key]
                    integration.config_schema.remove(item)
                    await self.session.delete(item)

            # Update existing or add new schema items
            for idx, item_data in enumerate(data.config_schema):
                if item_data.key in existing_by_key:
                    # Update existing
                    existing = existing_by_key[item_data.key]
                    existing.type = item_data.type
                    existing.required = item_data.required
                    existing.description = item_data.description
                    existing.options = item_data.options
                    existing.position = idx
                else:
                    # Add new
                    new_item = IntegrationConfigSchema(
                        integration_id=integration.id,
                        key=item_data.key,
                        type=item_data.type,
                        required=item_data.required,
                        description=item_data.description,
                        options=item_data.options,
                        position=idx,
                    )
                    integration.config_schema.append(new_item)

        await self.session.flush()
        await self.session.refresh(integration)
        # Reload with relationships
        return await self.get_integration(id)

    async def delete_integration(self, id: UUID) -> bool:
        """
        Soft delete an integration.

        Args:
            id: Integration UUID

        Returns:
            True if deleted, False if not found
        """
        integration = await self.get_integration(id)
        if not integration:
            return False

        integration.is_deleted = True
        await self.session.flush()
        return True

    # =========================================================================
    # Integration Mapping CRUD Operations
    # =========================================================================

    async def create_mapping(
        self, integration_id: UUID, data: IntegrationMappingCreate, updated_by: str = "system"
    ) -> IntegrationMapping:
        """
        Create a new integration mapping.

        Args:
            integration_id: Integration UUID
            data: Mapping creation data
            updated_by: User identifier for config audit trail

        Returns:
            Created mapping with relationships loaded
        """
        mapping = IntegrationMapping(
            integration_id=integration_id,
            organization_id=data.organization_id,
            entity_id=data.entity_id,
            entity_name=data.entity_name,
            oauth_token_id=data.oauth_token_id,
        )
        created = await self.create(mapping)

        # Persist config to configs table if provided
        if data.config is not None:
            await self._save_config(
                integration_id=integration_id,
                organization_id=data.organization_id,
                config=data.config,
                updated_by=updated_by,
            )

        # Reload with relationships
        result = await self.get_mapping(created.id)
        assert result is not None, "Created mapping should be retrievable"
        return result

    async def get_mapping(self, id: UUID) -> IntegrationMapping | None:
        """
        Get integration mapping by ID with relationships loaded.

        Args:
            id: Mapping UUID

        Returns:
            IntegrationMapping or None if not found
        """
        result = await self.session.execute(
            select(IntegrationMapping)
            .where(IntegrationMapping.id == id)
            .options(
                joinedload(IntegrationMapping.integration)
                .options(joinedload(Integration.oauth_provider)),
                joinedload(IntegrationMapping.organization),
                joinedload(IntegrationMapping.oauth_token),
            )
        )
        return result.unique().scalar_one_or_none()

    async def get_mapping_by_org(
        self, integration_id: UUID, organization_id: UUID
    ) -> IntegrationMapping | None:
        """
        Get integration mapping for a specific organization.

        Args:
            integration_id: Integration UUID
            organization_id: Organization UUID

        Returns:
            IntegrationMapping or None if not found
        """
        result = await self.session.execute(
            select(IntegrationMapping)
            .where(IntegrationMapping.integration_id == integration_id)
            .where(IntegrationMapping.organization_id == organization_id)
            .options(
                joinedload(IntegrationMapping.integration)
                .options(joinedload(Integration.oauth_provider)),
                joinedload(IntegrationMapping.organization),
                joinedload(IntegrationMapping.oauth_token),
            )
        )
        return result.unique().scalar_one_or_none()

    async def list_mappings(
        self, integration_id: UUID
    ) -> list[IntegrationMapping]:
        """
        List all mappings for an integration.

        Args:
            integration_id: Integration UUID

        Returns:
            List of mappings for the integration
        """
        result = await self.session.execute(
            select(IntegrationMapping)
            .where(IntegrationMapping.integration_id == integration_id)
            .options(
                joinedload(IntegrationMapping.integration)
                .options(joinedload(Integration.oauth_provider)),
                joinedload(IntegrationMapping.organization),
                joinedload(IntegrationMapping.oauth_token),
            )
            .order_by(IntegrationMapping.created_at)
        )
        return list(result.unique().scalars().all())

    async def _save_config(
        self,
        integration_id: UUID,
        organization_id: UUID | None,
        config: dict[str, Any],
        updated_by: str = "system",
    ) -> None:
        """
        Persist config values to the configs table.

        For each key-value pair:
        - If value is None or empty string, delete the entry (fall back to default)
        - Otherwise, upsert the entry

        Uses explicit SELECT + INSERT/UPDATE pattern because PostgreSQL's
        ON CONFLICT doesn't work with functional indexes (COALESCE for NULL handling).

        Args:
            integration_id: Integration UUID
            organization_id: Organization UUID (None for integration-level defaults)
            config: Config key-value pairs to save
            updated_by: User identifier for audit trail
        """
        # Look up schema items for this integration to get config_schema_id
        schema_result = await self.session.execute(
            select(IntegrationConfigSchema)
            .where(IntegrationConfigSchema.integration_id == integration_id)
        )
        schema_items = {item.key: item for item in schema_result.scalars().all()}

        for key, value in config.items():
            # Get the schema item for this key (for FK reference)
            schema_item = schema_items.get(key)

            # Build the WHERE clause for matching existing config
            # Handle NULL comparison properly with IS NULL
            if organization_id is None:
                where_clause = and_(
                    Config.integration_id == integration_id,
                    Config.organization_id.is_(None),
                    Config.key == key,
                )
            else:
                where_clause = and_(
                    Config.integration_id == integration_id,
                    Config.organization_id == organization_id,
                    Config.key == key,
                )

            if value is None or value == "":
                # Delete override (fall back to default)
                await self.session.execute(delete(Config).where(where_clause))
            else:
                # Check if record exists
                result = await self.session.execute(
                    select(Config.id).where(where_clause)
                )
                existing = result.scalar_one_or_none()

                if existing:
                    # Update existing record
                    await self.session.execute(
                        update(Config)
                        .where(Config.id == existing)
                        .values(
                            value={"value": value},
                            updated_by=updated_by,
                            config_schema_id=schema_item.id if schema_item else None,
                        )
                    )
                else:
                    # Insert new record
                    new_config = Config(
                        integration_id=integration_id,
                        organization_id=organization_id,
                        key=key,
                        value={"value": value},
                        updated_by=updated_by,
                        config_schema_id=schema_item.id if schema_item else None,
                    )
                    self.session.add(new_config)

    async def update_mapping(
        self, id: UUID, data: IntegrationMappingUpdate, updated_by: str = "system"
    ) -> IntegrationMapping | None:
        """
        Update an integration mapping.

        Args:
            id: Mapping UUID
            data: Update data (partial)
            updated_by: User identifier for config audit trail

        Returns:
            Updated mapping or None if not found
        """
        mapping = await self.get_mapping(id)
        if not mapping:
            return None

        # Update only provided fields
        if data.entity_id is not None:
            mapping.entity_id = data.entity_id
        if data.entity_name is not None:
            mapping.entity_name = data.entity_name
        if data.oauth_token_id is not None:
            mapping.oauth_token_id = data.oauth_token_id

        # Persist config to configs table
        if data.config is not None:
            await self._save_config(
                integration_id=mapping.integration_id,
                organization_id=mapping.organization_id,
                config=data.config,
                updated_by=updated_by,
            )

        await self.session.flush()
        await self.session.refresh(mapping)
        # Reload with relationships
        return await self.get_mapping(id)

    async def delete_mapping(self, id: UUID) -> bool:
        """
        Delete an integration mapping (hard delete).

        Args:
            id: Mapping UUID

        Returns:
            True if deleted, False if not found
        """
        mapping = await self.get_mapping(id)
        if not mapping:
            return False

        await self.delete(mapping)
        return True

    # =========================================================================
    # Convenience Methods
    # =========================================================================

    async def get_integration_for_org(
        self, integration_name: str, org_id: UUID
    ) -> IntegrationMapping | None:
        """
        Get integration mapping for a specific organization.

        Convenience method that finds the integration by name and gets the
        mapping for the given organization. Returns None if either the
        integration or mapping doesn't exist.

        Used by SDK's `integrations.get()` method.

        Args:
            integration_name: Integration name to look up
            org_id: Organization UUID

        Returns:
            IntegrationMapping with integration and oauth data loaded,
            or None if integration or mapping not found
        """
        # First, find the integration by name
        integration = await self.get_integration_by_name(integration_name)
        if not integration:
            return None

        # Then get the mapping for this org
        return await self.get_mapping_by_org(integration.id, org_id)

    async def get_org_config_overrides(
        self, integration_id: UUID, org_id: UUID
    ) -> dict[str, Any]:
        """
        Get ONLY org-specific config overrides (not merged with defaults).

        Used for admin UI where we only want to show what the org has explicitly set,
        not the default values. This prevents users from accidentally saving defaults
        back to the org config.

        Args:
            integration_id: Integration UUID
            org_id: Organization UUID

        Returns:
            dict: Only org-specific config overrides
        """
        from src.models.orm import Config as ConfigModel

        config_query = select(ConfigModel).where(
            and_(
                ConfigModel.organization_id == org_id,
                ConfigModel.integration_id == integration_id,
            )
        )
        result = await self.session.execute(config_query)
        config_entries = result.scalars().all()

        config: dict[str, Any] = {}
        for entry in config_entries:
            value = entry.value
            if isinstance(value, dict) and "value" in value:
                config[entry.key] = value["value"]
            else:
                config[entry.key] = value

        return config

    async def get_config_for_mapping(
        self,
        integration_id: UUID,
        org_id: UUID,
        *,
        include_default_secrets: bool = False,
    ) -> dict:
        """
        Get merged configuration for an integration mapping.

        Gets integration-level defaults (org_id=NULL) and merges
        organization-specific overrides on top.

        Args:
            integration_id: Integration UUID
            org_id: Organization UUID
            include_default_secrets: Include integration-default secret
                fallback values. SDK mapping reads leave this false so global
                defaults do not leak as decrypted org config.

        Returns:
            dict: Merged configuration (integration defaults + org overrides)
        """
        from src.models.orm import Config as ConfigModel
        from sqlalchemy import or_

        # Query for both integration defaults (org_id=NULL) and org-specific overrides
        config_query = select(ConfigModel).where(
            and_(
                ConfigModel.integration_id == integration_id,
                or_(
                    ConfigModel.organization_id.is_(None),
                    ConfigModel.organization_id == org_id,
                ),
            )
        )
        result = await self.session.execute(config_query)
        config_entries = result.scalars().all()

        # Separate defaults from org overrides
        defaults: dict[str, Any] = {}
        overrides: dict[str, Any] = {}

        for entry in config_entries:
            value = entry.value
            if isinstance(value, dict) and "value" in value:
                val = value["value"]
            else:
                val = value

            is_secret = entry.config_type == ConfigType.SECRET
            if (
                is_secret
                and entry.organization_id is None
                and not include_default_secrets
            ):
                continue

            if is_secret and isinstance(val, str):
                try:
                    val = decrypt_secret(val)
                except Exception:
                    logger.warning(f"Failed to decrypt config '{entry.key}'")
                    val = None

            if entry.organization_id is None:
                defaults[entry.key] = val
            else:
                overrides[entry.key] = val

        # Merge: start with defaults, apply org overrides
        config = {**defaults, **overrides}

        return config

    async def get_integration_defaults(
        self,
        integration_id: UUID,
        *,
        include_secrets: bool = False,
    ) -> dict[str, Any]:
        """
        Get integration-level config defaults (org_id=NULL).

        Used when no org mapping exists but we want integration defaults.

        Args:
            integration_id: Integration UUID
            include_secrets: Include decrypted default secret values. Leave
                false when serving org-scoped fallback responses.

        Returns:
            dict: Integration-level config defaults
        """
        from src.models.orm import Config as ConfigModel

        config_query = select(ConfigModel).where(
            and_(
                ConfigModel.integration_id == integration_id,
                ConfigModel.organization_id.is_(None),
            )
        )
        result = await self.session.execute(config_query)
        config_entries = result.scalars().all()

        config: dict[str, Any] = {}
        for entry in config_entries:
            value = entry.value
            if isinstance(value, dict) and "value" in value:
                val = value["value"]
            else:
                val = value

            if entry.config_type == ConfigType.SECRET and not include_secrets:
                continue

            if entry.config_type == ConfigType.SECRET and isinstance(val, str):
                try:
                    val = decrypt_secret(val)
                except Exception:
                    logger.warning(f"Failed to decrypt config '{entry.key}'")
                    val = None

            config[entry.key] = val

        return config

    async def get_provider_org_token(
        self,
        provider_id: UUID,
        organization_id: UUID | None,
    ) -> Any:
        """
        Get org-level OAuth token for a provider (user_id=NULL).

        Used when getting integration defaults - returns the org-level
        token that's not tied to a specific user.

        Args:
            provider_id: OAuthProvider UUID
            organization_id: Owning organization, or None for global tokens

        Returns:
            OAuthToken or None if not found
        """
        from src.models.orm.oauth import OAuthToken

        stmt = select(OAuthToken).where(
            OAuthToken.provider_id == provider_id,
            OAuthToken.user_id.is_(None),
        )
        if organization_id is None:
            stmt = stmt.where(OAuthToken.organization_id.is_(None))
        else:
            stmt = stmt.where(OAuthToken.organization_id == organization_id)

        result = await self.session.execute(stmt)
        return result.scalars().first()
