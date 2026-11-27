"""
Integrations Repository

Database operations for integrations, their organization mappings and their
config values. Every surface (REST, SDK, worker-local engine) saves
integration config through ``IntegrationsRepository.save_config``.
"""

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, union, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer, joinedload, selectinload

from src.core.log_safety import log_safe
from src.core.security import SECRET_PLACEHOLDER, decrypt_secret
from src.models.contracts.integrations import (
    IntegrationCreate,
    IntegrationInstanceCreate,
    IntegrationInstanceUpdate,
    IntegrationUpdate,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
)
from src.models.enums import ConfigType
from src.models.orm.config import Config
from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
from src.models.orm.oauth import OAuthProvider, OAuthToken
from src.repositories.base import BaseRepository
from src.repositories.org_scoped import OrgScopedRepository
from src.services.authorization.reach import OrgReach
from src.services.config_values import (
    ConfigValueError,
    config_type_for_schema_type,
    new_config_row,
    unwrap,
    validate_schema_value,
    write_config_value,
)

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
            select(Integration).where(
                Integration.name == name,
                Integration.is_deleted.is_(False),
            )
        )
        integration = integration_result.scalar_one_or_none()
        if not integration:
            return None

        # Then use cascade scoping to find mapping
        # The inherited get() method handles cascade: org-specific first, then global
        return await self.get(integration_id=integration.id)


async def retype_integration_config_values(
    session: AsyncSession,
    integration_id: UUID,
    key: str,
    new_schema_type: str,
) -> None:
    """Convert every stored value of one schema key to the field's new type.

    Call this after setting a config-schema item's new ``type``; the change
    is flushed first so each row is checked against it. Conversion follows
    ``src.services.config_values``: becoming ``secret`` encrypts stored
    values; leaving ``secret`` is refused while a value is stored.
    """
    await session.flush()
    target = config_type_for_schema_type(new_schema_type)
    rows = (
        await session.execute(
            select(Config).where(
                Config.integration_id.in_(
                    select(Integration.id).where(
                        (Integration.id == integration_id)
                        | (Integration.parent_integration_id == integration_id)
                    )
                ),
                Config.key == key,
            )
        )
    ).scalars().all()
    for row in rows:
        await write_config_value(session, row, config_type=target)
    await session.flush()


class IntegrationsRepository(BaseRepository[Integration]):
    """The one data-access path for integrations, mappings and their config.

    Integration config values are written by :meth:`save_config`, which
    stores rows through ``src.services.config_values`` (secret schema values
    encrypted with ``config_type=SECRET``).
    Every config read masks or decrypts by the field's schema type, not only
    by the stored row's ``config_type``.
    """

    model = Integration

    # =========================================================================
    # Integration CRUD
    # =========================================================================

    async def list_integrations(self) -> list[Integration]:
        """List integration definitions, not their connection instances."""
        query = (
            select(Integration)
            .where(Integration.is_deleted.is_(False))
            .where(Integration.parent_integration_id.is_(None))
            .options(
                selectinload(Integration.config_schema),
                defer(Integration.logo_data),
                defer(Integration.logo_thumbnail_data),
            )
            .order_by(Integration.name)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_list_connection_summaries(
        self,
        reach: OrgReach,
    ) -> dict[UUID, tuple[int, int, dict[str, int]]]:
        """Aggregate mapping counts and distinct OAuth connection health."""
        mapping_query = select(
            IntegrationMapping.integration_id,
            func.count(IntegrationMapping.id),
        )
        predicate = reach.where(IntegrationMapping.organization_id)
        if predicate is not None:
            mapping_query = mapping_query.where(predicate)
        mapping_result = await self.session.execute(
            mapping_query.group_by(IntegrationMapping.integration_id)
        )
        summaries: dict[UUID, tuple[int, int, dict[str, int]]] = {
            integration_id: (int(count or 0), 0, {})
            for integration_id, count in mapping_result.all()
        }

        ranked_defaults = (
            select(
                OAuthProvider.integration_id.label("integration_id"),
                OAuthToken.id.label("token_id"),
                OAuthToken.status.label("token_status"),
                func.row_number()
                .over(
                    partition_by=OAuthProvider.integration_id,
                    order_by=(OAuthToken.created_at.desc(), OAuthToken.id.desc()),
                )
                .label("token_rank"),
            )
            .join(OAuthToken, OAuthToken.provider_id == OAuthProvider.id)
            .where(
                OAuthProvider.integration_id.isnot(None),
                OAuthToken.organization_id.is_(None),
            )
            .subquery()
        )
        default_connections = select(
            ranked_defaults.c.integration_id,
            ranked_defaults.c.token_id,
            ranked_defaults.c.token_status,
        ).where(ranked_defaults.c.token_rank == 1)
        override_connections = select(
            IntegrationMapping.integration_id.label("integration_id"),
            OAuthToken.id.label("token_id"),
            OAuthToken.status.label("token_status"),
        ).join(OAuthToken, OAuthToken.id == IntegrationMapping.oauth_token_id)
        if predicate is not None:
            override_connections = override_connections.where(predicate)
        connections = (
            union(default_connections, override_connections)
            if reach.covers(None)
            else override_connections
        ).subquery()
        status_result = await self.session.execute(
            select(
                connections.c.integration_id,
                connections.c.token_status,
                func.count(connections.c.token_id),
            ).group_by(connections.c.integration_id, connections.c.token_status)
        )
        for integration_id, token_status, count in status_result.all():
            mapping_count, connected_count, status_counts = summaries.get(
                integration_id, (0, 0, {})
            )
            count_int = int(count or 0)
            status_counts[token_status] = status_counts.get(token_status, 0) + count_int
            if token_status in {"completed", "connected"}:
                connected_count += count_int
            summaries[integration_id] = (mapping_count, connected_count, status_counts)
        return summaries

    async def get_integration_by_id(self, integration_id: UUID) -> Integration | None:
        """Get a non-deleted integration by ID."""
        result = await self.session.execute(
            select(Integration)
            .where(
                Integration.id == integration_id,
                Integration.is_deleted.is_(False),
            )
            .options(selectinload(Integration.config_schema))
        )
        return result.scalar_one_or_none()

    async def get_integration_detail_by_id(self, integration_id: UUID) -> Integration | None:
        """Get a non-deleted integration with OAuth tokens, mappings and schema."""
        result = await self.session.execute(
            select(Integration)
            .where(
                Integration.id == integration_id,
                Integration.is_deleted.is_(False),
            )
            .options(
                joinedload(Integration.oauth_provider).selectinload(OAuthProvider.tokens),
                selectinload(Integration.mappings),
                selectinload(Integration.config_schema),
            )
        )
        return result.unique().scalar_one_or_none()

    async def get_integration_by_name(self, name: str) -> Integration | None:
        """Get a public definition name's default connection (the root row)."""
        result = await self.session.execute(
            select(Integration)
            .where(
                Integration.name == name,
                Integration.parent_integration_id.is_(None),
                Integration.is_deleted.is_(False),
            )
            .options(
                joinedload(Integration.oauth_provider),
                selectinload(Integration.mappings),
                selectinload(Integration.config_schema),
            )
        )
        return result.unique().scalar_one_or_none()

    async def get_definition_by_id(self, integration_id: UUID) -> Integration | None:
        """Return the canonical definition for a root or child connection ID."""
        integration = await self.get_integration_by_id(integration_id)
        if integration is None:
            return None
        if integration.parent_integration_id is None:
            return integration
        return await self.get_integration_by_id(integration.parent_integration_id)

    async def get_instance(
        self, definition_id: UUID, instance_id: UUID
    ) -> Integration | None:
        """Get one live connection instance belonging to ``definition_id``."""
        result = await self.session.execute(
            select(Integration)
            .where(
                Integration.id == instance_id,
                Integration.is_deleted.is_(False),
                (
                    (Integration.id == definition_id)
                    | (Integration.parent_integration_id == definition_id)
                ),
            )
            .options(joinedload(Integration.oauth_provider), selectinload(Integration.mappings))
        )
        return result.unique().scalar_one_or_none()

    async def get_instance_by_key(
        self, definition: Integration, key: str
    ) -> Integration | None:
        """Resolve the default/root or a named instance by its stable key."""
        if key == "default":
            return definition
        result = await self.session.execute(
            select(Integration)
            .where(
                Integration.parent_integration_id == definition.id,
                Integration.instance_key == key,
                Integration.is_deleted.is_(False),
            )
            .options(joinedload(Integration.oauth_provider), selectinload(Integration.mappings))
        )
        return result.unique().scalar_one_or_none()

    async def list_instances(self, definition_id: UUID) -> list[Integration]:
        """List the root default and every active named child instance."""
        result = await self.session.execute(
            select(Integration)
            .where(
                Integration.is_deleted.is_(False),
                (
                    (Integration.id == definition_id)
                    | (Integration.parent_integration_id == definition_id)
                ),
            )
            .options(joinedload(Integration.oauth_provider))
            .order_by(Integration.parent_integration_id.nullsfirst(), Integration.instance_name)
        )
        return list(result.unique().scalars().all())

    async def create_instance(
        self, definition: Integration, request: IntegrationInstanceCreate
    ) -> Integration:
        """Create a child connection without copying shared definition metadata."""
        if request.key == "default":
            raise ValueError("'default' is reserved for the original connection")
        existing = await self.get_instance_by_key(definition, request.key)
        if existing is not None:
            raise ValueError(f"Integration instance key '{request.key}' already exists")
        instance = Integration(
            name=f"{definition.id}::{request.key}",
            parent_integration_id=definition.id,
            instance_key=request.key,
            instance_name=request.name,
            authentication_method=request.authentication_method,
            oauth_provider=None,
            is_deleted=False,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(instance)
                await self.session.flush()
        except IntegrityError as exc:
            raise ValueError(
                f"Integration instance key '{request.key}' is already reserved"
            ) from exc
        return instance

    @staticmethod
    def _set_authentication_method(instance: Integration, method: str) -> None:
        if method == "config" and instance.oauth_provider:
            raise ConfigValueError("Disconnect OAuth before changing authentication method to config")
        instance.authentication_method = method

    async def update_instance(
        self, instance: Integration, request: IntegrationInstanceUpdate
    ) -> Integration:
        """Update only instance-owned metadata; shared metadata lives on root."""
        if request.name is not None:
            instance.instance_name = request.name
        if request.authentication_method is not None:
            self._set_authentication_method(instance, request.authentication_method)
        instance.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return instance

    async def create_integration(self, request: IntegrationCreate) -> Integration:
        """Create a new integration with normalized config schema."""
        integration = Integration(
            name=request.name,
            instance_key="default",
            instance_name=request.name,
            authentication_method=request.authentication_method,
            description=request.description,
            notes=request.notes,
            workflow_links=[link.model_dump(mode="json") for link in request.workflow_links],
            entity_id=request.entity_id,
            entity_id_name=request.entity_id_name,
            default_entity_id=request.default_entity_id,
            is_deleted=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        if request.config_schema:
            for idx, item in enumerate(request.config_schema):
                integration.config_schema.append(
                    IntegrationConfigSchema(
                        key=item.key,
                        type=item.type,
                        required=item.required,
                        description=item.description,
                        options=item.options,
                        option_labels=item.option_labels,
                        position=idx,
                    )
                )

        self.session.add(integration)
        await self.session.flush()
        await self.session.refresh(integration)
        return integration

    async def update_integration(
        self, integration_id: UUID, request: IntegrationUpdate
    ) -> Integration | None:
        """Update an integration, upserting its config schema by key.

        A schema item whose ``type`` changes has its stored values converted
        (see :func:`retype_integration_config_values`).
        """
        integration = await self.get_integration_by_id(integration_id)
        if not integration:
            return None

        if request.authentication_method is not None:
            self._set_authentication_method(integration, request.authentication_method)
        if request.workflow_links is not None:
            integration.workflow_links = [link.model_dump(mode="json") for link in request.workflow_links]
        if request.name is not None:
            integration.name = request.name
        if "description" in request.model_fields_set:
            integration.description = request.description
        if "notes" in request.model_fields_set:
            integration.notes = request.notes
        if "list_entities_data_provider_id" in request.model_fields_set:
            integration.list_entities_data_provider_id = (
                request.list_entities_data_provider_id
            )
        if request.entity_id is not None:
            integration.entity_id = request.entity_id
        if request.entity_id_name is not None:
            integration.entity_id_name = request.entity_id_name
        if "default_entity_id" in request.model_fields_set:
            # An empty string clears the value.
            integration.default_entity_id = request.default_entity_id or None

        if request.config_schema is not None:
            existing_by_key = {item.key: item for item in integration.config_schema}
            new_keys = {item.key for item in request.config_schema}

            # Removing a schema item cascades to its stored config values.
            for key in list(existing_by_key.keys()):
                if key not in new_keys:
                    item = existing_by_key[key]
                    integration.config_schema.remove(item)
                    await self.session.delete(item)

            for idx, item_data in enumerate(request.config_schema):
                if item_data.key in existing_by_key:
                    existing = existing_by_key[item_data.key]
                    retyped = existing.type != item_data.type
                    existing.type = item_data.type
                    if retyped:
                        await retype_integration_config_values(
                            self.session, integration.id, item_data.key, item_data.type
                        )
                    existing.required = item_data.required
                    existing.description = item_data.description
                    existing.options = item_data.options
                    existing.option_labels = item_data.option_labels
                    existing.position = idx
                else:
                    integration.config_schema.append(
                        IntegrationConfigSchema(
                            integration_id=integration.id,
                            key=item_data.key,
                            type=item_data.type,
                            required=item_data.required,
                            description=item_data.description,
                            options=item_data.options,
                            option_labels=item_data.option_labels,
                            position=idx,
                        )
                    )

        integration.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self.session.refresh(integration)
        return await self.get_integration_by_id(integration_id)

    async def delete_integration(self, integration_id: UUID) -> bool:
        """Soft delete an integration and remove its OAuth credentials.

        The integration row is kept, but its OAuth provider and tokens are
        deleted: a soft-deleted integration can't be managed from the UI,
        so leaving them would keep the refresh scheduler refreshing (and
        alerting on) a connection nobody can disconnect.
        """
        integration = await self.get_integration_by_id(integration_id)
        if not integration:
            return False

        integration_ids = select(Integration.id).where(
            (Integration.id == integration_id)
            | (Integration.parent_integration_id == integration_id)
        )
        now = datetime.now(timezone.utc)
        await self.session.execute(
            update(Integration)
            .where(Integration.id.in_(integration_ids))
            .values(is_deleted=True, updated_at=now)
        )

        provider_ids = select(OAuthProvider.id).where(
            OAuthProvider.integration_id.in_(integration_ids)
        )
        token_ids = select(OAuthToken.id).where(OAuthToken.provider_id.in_(provider_ids))
        await self.session.execute(
            update(IntegrationMapping)
            .where(IntegrationMapping.oauth_token_id.in_(token_ids))
            .values(oauth_token_id=None)
        )
        await self.session.execute(
            delete(OAuthToken).where(OAuthToken.provider_id.in_(provider_ids))
        )
        await self.session.execute(
            delete(OAuthProvider).where(OAuthProvider.integration_id.in_(integration_ids))
        )
        await self.session.flush()
        return True

    async def get_oauth_provider(self, integration_id: UUID) -> OAuthProvider | None:
        """Get the OAuth provider associated with an integration."""
        integration = await self.get_integration_by_id(integration_id)
        if not integration:
            return None
        return integration.oauth_provider

    # =========================================================================
    # Integration mappings
    # =========================================================================

    async def list_mappings(
        self,
        integration_id: UUID,
        *,
        reach: OrgReach,
    ) -> list[IntegrationMapping]:
        """List an integration's mappings whose organization is in ``reach``.

        Callers pass the caller's permitted reach to enumerate, or a
        single-organization reach for an exact scope.
        """
        stmt = select(IntegrationMapping).where(
            IntegrationMapping.integration_id == integration_id
        )
        predicate = reach.where(IntegrationMapping.organization_id)
        if predicate is not None:
            stmt = stmt.where(predicate)
        stmt = stmt.options(
            joinedload(IntegrationMapping.integration).options(
                joinedload(Integration.oauth_provider)
            ),
            joinedload(IntegrationMapping.organization),
            joinedload(IntegrationMapping.oauth_token),
        ).order_by(IntegrationMapping.created_at)
        result = await self.session.execute(stmt)
        return list(result.unique().scalars().all())

    async def get_mapping_by_id(
        self, integration_id: UUID, mapping_id: UUID
    ) -> IntegrationMapping | None:
        """Get one mapping of one integration."""
        result = await self.session.execute(
            select(IntegrationMapping).where(
                IntegrationMapping.id == mapping_id,
                IntegrationMapping.integration_id == integration_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_mapping_by_org(
        self, integration_id: UUID, organization_id: UUID
    ) -> IntegrationMapping | None:
        """Get an integration's mapping for exactly one organization."""
        result = await self.session.execute(
            select(IntegrationMapping)
            .where(IntegrationMapping.integration_id == integration_id)
            .where(IntegrationMapping.organization_id == organization_id)
            .options(
                joinedload(IntegrationMapping.integration).options(
                    joinedload(Integration.oauth_provider)
                ),
                joinedload(IntegrationMapping.organization),
                joinedload(IntegrationMapping.oauth_token),
            )
        )
        return result.unique().scalar_one_or_none()

    async def get_integration_for_org(
        self, integration_name: str, org_id: UUID
    ) -> IntegrationMapping | None:
        """Get the named integration's mapping for one organization (SDK ``get``)."""
        integration = await self.get_integration_by_name(integration_name)
        if not integration:
            return None
        return await self.get_mapping_by_org(integration.id, org_id)

    async def create_mapping(
        self,
        integration_id: UUID,
        request: IntegrationMappingCreate,
        updated_by: str = "system",
    ) -> IntegrationMapping:
        """Create a mapping and save its config through :meth:`save_config`."""
        mapping = IntegrationMapping(
            integration_id=integration_id,
            organization_id=request.organization_id,
            entity_id=request.entity_id,
            entity_name=request.entity_name,
            oauth_token_id=request.oauth_token_id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.session.add(mapping)
        await self.session.flush()
        await self.session.refresh(mapping)

        if request.config is not None:
            await self.save_config(
                integration_id=integration_id,
                organization_id=request.organization_id,
                config=request.config,
                updated_by=updated_by,
            )

        return mapping

    async def update_mapping(
        self,
        integration_id: UUID,
        mapping_id: UUID,
        request: IntegrationMappingUpdate,
        updated_by: str = "system",
    ) -> IntegrationMapping | None:
        """Update a mapping and save its config through :meth:`save_config`."""
        mapping = await self.get_mapping_by_id(integration_id, mapping_id)
        if not mapping:
            return None

        if request.entity_id is not None:
            mapping.entity_id = request.entity_id
        if request.entity_name is not None:
            mapping.entity_name = request.entity_name
        if request.oauth_token_id is not None:
            mapping.oauth_token_id = request.oauth_token_id

        if request.config is not None:
            await self.save_config(
                integration_id=integration_id,
                organization_id=mapping.organization_id,
                config=request.config,
                updated_by=updated_by,
            )

        mapping.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        await self.session.refresh(mapping)
        return mapping

    async def delete_mapping(self, integration_id: UUID, mapping_id: UUID) -> bool:
        """Delete a mapping (hard delete)."""
        mapping = await self.get_mapping_by_id(integration_id, mapping_id)
        if not mapping:
            return False

        await self.session.delete(mapping)
        await self.session.flush()
        return True

    # =========================================================================
    # Integration config values
    # =========================================================================

    async def save_config(
        self,
        integration_id: UUID,
        organization_id: UUID | None,
        config: dict[str, Any],
        updated_by: str = "system",
    ) -> None:
        """Persist integration config values; the only integration config writer.

        For each key-value pair:
        - Validate the value against the key's schema type
          (``ConfigValueError`` on mismatch)
        - ``None`` or ``""`` deletes the stored value (fall back to the default)
        - ``SECRET_PLACEHOLDER`` for a secret key keeps the stored value
          (admin reads return the placeholder and the UI saves back what it read)
        - Otherwise upsert through ``src.services.config_values``, with
          ``config_type`` from the schema (secret values encrypted)

        Uses explicit SELECT then update/insert because PostgreSQL's ON
        CONFLICT doesn't work with the COALESCE functional index.
        """
        schema_items = await self._schema_items(integration_id)

        for key, value in config.items():
            schema_item = schema_items.get(key)
            if schema_item:
                validate_schema_value(key, value, schema_item.type)
            config_type = config_type_for_schema_type(
                schema_item.type if schema_item else None
            )

            if config_type == ConfigType.SECRET and value == SECRET_PLACEHOLDER:
                continue

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
                await self.session.execute(delete(Config).where(where_clause))
                continue

            existing = (
                await self.session.execute(select(Config).where(where_clause))
            ).scalar_one_or_none()
            schema_id = schema_item.id if schema_item else None
            if existing:
                await write_config_value(
                    self.session, existing, config_type=config_type, value=value
                )
                existing.updated_by = updated_by
                existing.config_schema_id = schema_id
            else:
                await new_config_row(
                    self.session,
                    key=key,
                    config_type=config_type,
                    value=value,
                    integration_id=integration_id,
                    organization_id=organization_id,
                    updated_by=updated_by,
                    config_schema_id=schema_id,
                )

        # Flush so the writes are visible to subsequent queries.
        await self.session.flush()

    async def _schema_items(self, integration_id: UUID) -> dict[str, IntegrationConfigSchema]:
        definition_id = select(Integration.parent_integration_id).where(
            Integration.id == integration_id
        ).scalar_subquery()
        result = await self.session.execute(
            select(IntegrationConfigSchema).where(
                IntegrationConfigSchema.integration_id == func.coalesce(
                    definition_id, integration_id
                )
            )
        )
        return {item.key: item for item in result.scalars().all()}

    async def _secret_keys(self, integration_id: UUID) -> set[str]:
        definition_id = select(Integration.parent_integration_id).where(
            Integration.id == integration_id
        ).scalar_subquery()
        result = await self.session.execute(
            select(IntegrationConfigSchema.key).where(
                IntegrationConfigSchema.integration_id == func.coalesce(
                    definition_id, integration_id
                ),
                IntegrationConfigSchema.type == "secret",
            )
        )
        return set(result.scalars().all())

    @staticmethod
    def _read_value(entry: Config, secret_keys: set[str], *, reveal_secrets: bool) -> Any:
        """One stored value for a reader: masked or decrypted when secret.

        A value is secret when its schema field is ``secret`` or its row is
        stored as ``SECRET``. Revealed secrets are decrypted; a value that
        cannot be decrypted reads as ``None``.
        """
        value = unwrap(entry.value)
        if entry.key not in secret_keys and entry.config_type != ConfigType.SECRET:
            return value
        if not reveal_secrets:
            return SECRET_PLACEHOLDER
        try:
            return decrypt_secret(value)
        except Exception:
            logger.warning(f"Failed to decrypt config '{log_safe(entry.key)}'")
            return None

    async def _read_configs(
        self, integration_id: UUID, where: Any, *, reveal_secrets: bool
    ) -> list[tuple[Config, Any]]:
        entries = (
            await self.session.execute(
                select(Config).where(Config.integration_id == integration_id, where)
            )
        ).scalars().all()
        if not entries:
            return []
        secret_keys = await self._secret_keys(integration_id)
        return [
            (entry, self._read_value(entry, secret_keys, reveal_secrets=reveal_secrets))
            for entry in entries
        ]

    async def get_config_for_mapping(
        self, integration_id: UUID, org_id: UUID, *, external: bool, reveal_secrets: bool
    ) -> dict[str, Any]:
        """Merged configuration for one organization's mapping.

        Integration-level defaults (org_id=NULL) with the organization's
        overrides on top.

        ``external`` is REQUIRED (no default — EXT-1 OPEN-E / NEW-G). An
        EXTERNAL portal caller passes True and gets the organization's own
        values only, so a decrypted global SECRET is never returned;
        engine/sentinel/admin callers pass False explicitly. No default so a
        new call site can't silently re-open the global tier.
        """
        if external:
            where = Config.organization_id == org_id
        else:
            where = or_(Config.organization_id.is_(None), Config.organization_id == org_id)

        defaults: dict[str, Any] = {}
        overrides: dict[str, Any] = {}
        for entry, value in await self._read_configs(
            integration_id, where, reveal_secrets=reveal_secrets
        ):
            if entry.organization_id is None:
                defaults[entry.key] = value
            else:
                overrides[entry.key] = value
        return {**defaults, **overrides}

    async def get_integration_defaults(
        self, integration_id: UUID, *, external: bool, reveal_secrets: bool
    ) -> dict[str, Any]:
        """Integration-level config defaults (org_id=NULL).

        ``external`` is REQUIRED (no default — EXT-1 OPEN-E / NEW-G). These
        defaults ARE the global tier and may carry SECRETs: an EXTERNAL portal
        caller passes True and gets ``{}`` without a query.
        """
        if external:
            return {}
        return {
            entry.key: value
            for entry, value in await self._read_configs(
                integration_id,
                Config.organization_id.is_(None),
                reveal_secrets=reveal_secrets,
            )
        }

    async def get_org_config_overrides(
        self, integration_id: UUID, org_id: UUID
    ) -> dict[str, Any]:
        """One organization's own config values (not merged), secrets masked.

        Admin responses show only what the organization set explicitly, so a
        save does not copy defaults into the organization's config.
        """
        return {
            entry.key: value
            for entry, value in await self._read_configs(
                integration_id, Config.organization_id == org_id, reveal_secrets=False
            )
        }

    async def get_all_org_config_overrides(
        self, integration_id: UUID, reach: OrgReach
    ) -> dict[UUID, dict[str, Any]]:
        """Each in-reach organization's own config values, secrets masked."""
        where = Config.organization_id.isnot(None)
        predicate = reach.where(Config.organization_id)
        if predicate is not None:
            where = and_(where, predicate)

        configs_by_org: dict[UUID, dict[str, Any]] = {}
        for entry, value in await self._read_configs(
            integration_id, where, reveal_secrets=False
        ):
            configs_by_org.setdefault(entry.organization_id, {})[entry.key] = value
        return configs_by_org


# Deleted (2026-05): get_provider_org_token had no organization_id filter
# and could return any org's user_id=NULL token. The cross-tenant token
# leak is fixed by routing all OAuth token reads through
# OAuthTokenRepository.get_org_level_for_provider in
# api/src/repositories/oauth.py, which applies the standard cascade
# (org-specific preferred, falls back to global) and never silently
# returns another org's token.
