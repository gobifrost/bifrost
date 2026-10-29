"""Shared workspace export/import with current named authority and data policies."""

import base64
import io
import zipfile
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, with_loader_criteria

from shared.policies.probe import make_seed_admin_bypass
from shared.claims.preresolve import preresolve_for_policies
from shared.table_batch_writes import BatchWriteRow, write_table_batch
from shared.table_documents import query_table_documents
from src.core.security import decrypt_with_key, encrypt_secret, derive_fernet_key
from src.models.enums import ConfigType
from src.models.orm.config import Config
from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
from src.models.orm.knowledge import KnowledgeStore
from src.models.orm.oauth import OAuthProvider
from src.models.orm.organizations import Organization
from src.models.orm.tables import Table
from src.models.contracts.tables import DocumentQuery
from src.models.contracts.export_import import (
    ConfigExportFile, ConfigExportItem, ConfigSchemaExportItem, DocumentExportItem,
    ImportResult, ImportResultItem, IntegrationExportFile, IntegrationExportItem,
    IntegrationMappingExportItem, KnowledgeExportFile, KnowledgeExportItem,
    OAuthProviderExportItem, TableExportFile, TableExportItem,
)
from src.services.authorization.enforce import Caller, require_permission, user_reach, permitted_organizations
from src.services.access_check_policies import enforced_policy_principal
from src.services.table_policy_loader import load_resolved_table_policies
from src.core.pubsub import publish_table_invalidated


async def _resolve_org_names(db: AsyncSession, org_ids: set[UUID]) -> dict[UUID, str]:
    """Batch-resolve organization UUIDs to names."""
    if not org_ids:
        return {}
    result = await db.execute(
        select(Organization.id, Organization.name).where(Organization.id.in_(org_ids))
    )
    return {row.id: row.name for row in result.all()}



async def _resolve_org_id(
    db: AsyncSession,
    caller: Caller,
    permission: str,
    item_org_id: str | None,
    item_org_name: str | None,
    target_org_override: UUID | None,
    force_global: bool,
    warnings: list[str],
    item_label: str,
) -> UUID | None:
    """Resolve an organization ID for import using override, name, or UUID fallback.

    Resolution priority:
    1. force_global=True → None (global scope)
    2. target_org_override → use that UUID directly
    3. item_org_name match → look up by name
    4. item_org_id UUID match → verify it exists
    5. None + warning if org info was present but unresolvable
    """
    if force_global:
        require_permission(caller, permission, None)
        return None
    if target_org_override is not None:
        require_permission(caller, permission, target_org_override)
        return target_org_override

    # Try name-based resolution
    if item_org_name:
        result = await db.execute(
            select(Organization.id).where(
                Organization.name == item_org_name,
                Organization.is_active == True,  # noqa: E712
            )
        )
        org_id = result.scalar_one_or_none()
        if org_id:
            require_permission(caller, permission, org_id)
            return org_id

    # Fall back to UUID match
    if item_org_id:
        try:
            uuid_val = UUID(item_org_id)
        except ValueError:
            raise HTTPException(400, "Invalid organization ID") from None

        result = await db.execute(
            select(Organization.id).where(Organization.id == uuid_val)
        )
        if result.scalar_one_or_none():
            require_permission(caller, permission, uuid_val)
            return uuid_val

        raise HTTPException(404, "Organization not found")

    if item_org_name:
        raise HTTPException(404, "Organization not found")
    require_permission(caller, permission, None)
    return None



def _parse_target_org(target_organization_id: str | None) -> tuple[UUID | None, bool]:
    """Parse the target_organization_id form field.

    Returns (override_uuid, force_global):
    - None/absent → (None, False) — resolve from file
    - "" → (None, True) — force global
    - UUID string → (UUID, False) — use that org
    """
    if target_organization_id is None:
        return None, False
    if target_organization_id == "":
        return None, True
    return UUID(target_organization_id), False



async def _build_knowledge_export(
    db: AsyncSession, caller: Caller, ids: list[str] | None = None
) -> KnowledgeExportFile:
    query = _scoped_query(select(KnowledgeStore), KnowledgeStore, caller, "knowledge.read")
    if ids:
        uuids = [UUID(id_str) for id_str in ids]
        query = query.where(KnowledgeStore.id.in_(uuids))

    result = await db.execute(query)
    docs = result.scalars().all()
    for doc in docs:
        require_permission(caller, "knowledge.read", doc.organization_id)

    org_ids = {doc.organization_id for doc in docs if doc.organization_id}
    org_names = await _resolve_org_names(db, org_ids)

    items = [
        KnowledgeExportItem(
            namespace=doc.namespace,
            key=doc.key,
            content=doc.content,
            metadata=doc.doc_metadata or {},
            organization_id=str(doc.organization_id) if doc.organization_id else None,
            organization_name=org_names.get(doc.organization_id) if doc.organization_id else None,
        )
        for doc in docs
    ]

    return KnowledgeExportFile(item_count=len(items), items=items)



async def _build_tables_export(db: AsyncSession, caller: Caller, ids: list[str] | None = None) -> TableExportFile:
    query = _scoped_query(select(Table).where(Table.solution_id.is_(None)), Table, caller, "tables.read")
    if ids:
        query = query.where(Table.id.in_([UUID(value) for value in ids]))
    tables = (await db.execute(query)).scalars().all()
    org_names = await _resolve_org_names(db, {table.organization_id for table in tables if table.organization_id})
    items = []
    for table in tables:
        require_permission(caller, "tables.read", table.organization_id)
        documents = []
        offset = 0
        while True:
            page = await query_table_documents(db, table, DocumentQuery(limit=1000, offset=offset), caller.principal)
            documents.extend(DocumentExportItem(id=row.id, data=row.data) for row in page.documents)
            offset += len(page.documents)
            if not page.documents or offset >= page.total:
                break
        items.append(TableExportItem(name=table.name, description=table.description, schema=table.schema,
                                     organization_id=str(table.organization_id) if table.organization_id else None,
                                     organization_name=org_names.get(table.organization_id), documents=documents))
    return TableExportFile(item_count=len(items), items=items)



async def _build_configs_export(
    db: AsyncSession, caller: Caller, ids: list[str] | None = None
) -> ConfigExportFile:
    query = _scoped_query(select(Config), Config, caller, "configs.read")
    if ids:
        uuids = [UUID(id_str) for id_str in ids]
        query = query.where(Config.id.in_(uuids))

    configs = await _config_rows(db, caller, query, "configs.read")

    org_ids = {cfg.organization_id for cfg in configs if cfg.organization_id}
    org_names = await _resolve_org_names(db, org_ids)

    has_secrets = False
    items = []
    for cfg in configs:
        integration_name = None
        if cfg.integration_id:
            int_result = await db.execute(
                select(Integration.name).where(Integration.id == cfg.integration_id)
            )
            integration_name = int_result.scalar_one_or_none()

        raw_value = cfg.value.get("value") if cfg.value else None
        if cfg.config_type == ConfigType.SECRET:
            has_secrets = True

        items.append(ConfigExportItem(
            key=cfg.key,
            value=raw_value,
            config_type=cfg.config_type.value if hasattr(cfg.config_type, "value") else str(cfg.config_type),
            description=cfg.description,
            organization_id=str(cfg.organization_id) if cfg.organization_id else None,
            organization_name=org_names.get(cfg.organization_id) if cfg.organization_id else None,
            integration_name=integration_name,
        ))

    return ConfigExportFile(
        contains_encrypted_values=has_secrets,
        item_count=len(items),
        items=items,
    )



async def _build_integrations_export(
    db: AsyncSession, caller: Caller, ids: list[str] | None = None
) -> IntegrationExportFile:
    require_permission(caller, "integrations.read", None)
    mapping_filter = _scoped_query(select(IntegrationMapping.id), IntegrationMapping, caller, "integrations.read")
    query = (
        select(Integration)
        .options(
            selectinload(Integration.config_schema),
            selectinload(Integration.mappings),
            selectinload(Integration.oauth_provider).defer(OAuthProvider.encrypted_client_secret),
            with_loader_criteria(IntegrationMapping, IntegrationMapping.id.in_(mapping_filter)),
        )
        .execution_options(populate_existing=True)
        .where(Integration.is_deleted == False)  # noqa: E712
    )
    if ids:
        uuids = [UUID(id_str) for id_str in ids]
        query = query.where(Integration.id.in_(uuids))

    result = await db.execute(query)
    integrations = result.scalars().unique().all()

    # Collect all org IDs from mappings and OAuth providers
    all_org_ids: set[UUID] = set()
    for integ in integrations:
        for mapping in integ.mappings:
            if mapping.organization_id:
                all_org_ids.add(mapping.organization_id)
        if integ.oauth_provider and user_reach(caller).covers(integ.oauth_provider.organization_id) and integ.oauth_provider.organization_id:
            all_org_ids.add(integ.oauth_provider.organization_id)
    org_names = await _resolve_org_names(db, all_org_ids)

    has_secrets = False
    items = []
    for integ in integrations:
        # Get data provider name if referenced
        dp_name = None
        if integ.list_entities_data_provider_id:
            from src.models.orm.workflows import Workflow
            dp_result = await db.execute(_scoped_query(
                select(Workflow.name).where(Workflow.id == integ.list_entities_data_provider_id),
                Workflow, caller, "workflows.read",
            ))
            dp_name = dp_result.scalar_one_or_none()

        # Config schema
        schema_items = [
            ConfigSchemaExportItem(
                key=cs.key,
                type=cs.type,
                required=cs.required,
                description=cs.description,
                options=cs.options,
                position=cs.position,
            )
            for cs in sorted(integ.config_schema, key=lambda x: x.position)
        ]

        # Mappings with their config
        mapping_items = []
        for mapping in integ.mappings:
            require_permission(caller, "integrations.read", mapping.organization_id)
            configs = await _config_rows(db, caller,
                select(Config).where(
                    Config.integration_id == integ.id,
                    Config.organization_id == mapping.organization_id
                    if mapping.organization_id
                    else Config.organization_id.is_(None),
                ), "integrations.read"
            )
            config_dict = {}
            for cfg in configs:
                raw_value = cfg.value.get("value") if cfg.value else None
                config_dict[cfg.key] = raw_value
                if cfg.config_type == ConfigType.SECRET:
                    has_secrets = True

            mapping_items.append(IntegrationMappingExportItem(
                organization_id=str(mapping.organization_id) if mapping.organization_id else None,
                organization_name=org_names.get(mapping.organization_id) if mapping.organization_id else None,
                entity_id=mapping.entity_id,
                entity_name=mapping.entity_name,
                config=config_dict,
            ))

        # Default config (integration-level, org_id IS NULL)
        default_configs = await _config_rows(db, caller,
            select(Config).where(
                Config.integration_id == integ.id,
                Config.organization_id.is_(None),
            ), "integrations.read"
        )
        default_config = {}
        for cfg in default_configs:
            raw_value = cfg.value.get("value") if cfg.value else None
            default_config[cfg.key] = raw_value
            if cfg.config_type == ConfigType.SECRET:
                has_secrets = True

        # OAuth provider
        oauth_item = None
        if integ.oauth_provider and user_reach(caller).covers(integ.oauth_provider.organization_id):
            op = integ.oauth_provider
            require_permission(caller, "integrations.read", op.organization_id)
            require_permission(caller, "secrets.read", op.organization_id)
            await db.refresh(op, ["encrypted_client_secret"])
            encrypted_secret_b64 = (
                base64.b64encode(op.encrypted_client_secret).decode()
                if op.encrypted_client_secret
                else ""
            )
            has_secrets = True
            oauth_item = OAuthProviderExportItem(
                provider_name=op.provider_name,
                display_name=op.display_name,
                oauth_flow_type=op.oauth_flow_type,
                client_id=op.client_id,
                encrypted_client_secret=encrypted_secret_b64,
                authorization_url=op.authorization_url,
                token_url=op.token_url,
                token_url_defaults=op.token_url_defaults or {},
                redirect_uri=op.redirect_uri,
                scopes=op.scopes or [],
                organization_id=str(op.organization_id) if op.organization_id else None,
                organization_name=org_names.get(op.organization_id) if op.organization_id else None,
            )

        items.append(IntegrationExportItem(
            name=integ.name,
            description=integ.description,
            entity_id=integ.entity_id,
            entity_id_name=integ.entity_id_name,
            default_entity_id=integ.default_entity_id,
            list_entities_data_provider_name=dp_name,
            config_schema=schema_items,
            mappings=mapping_items,
            oauth_provider=oauth_item,
            default_config=default_config,
        ))

    return IntegrationExportFile(
        contains_encrypted_values=has_secrets,
        item_count=len(items),
        items=items,
    )



async def import_knowledge(
    db: AsyncSession,
    caller: Caller,
    content: bytes,
    replace_existing: bool = True,
    target_organization_id: str | None = None,
) -> ImportResult:
    """Import knowledge documents from JSON file."""
    try:
        export_data = KnowledgeExportFile.model_validate_json(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid export file: {e}")

    target_override, force_global = _parse_target_org(target_organization_id)
    result = ImportResult(entity_type="knowledge")

    for item in export_data.items:
        item_name = f"{item.namespace}/{item.key or 'unnamed'}"
        previous_counts = (result.created, result.updated, result.skipped, len(result.details))
        try:
            async with db.begin_nested():
                org_id = await _resolve_org_id(
                    db, caller, "knowledge.readwrite", item.organization_id, item.organization_name,
                    target_override, force_global, result.warnings, item_name,
                )

                existing_query = select(KnowledgeStore).where(
                    KnowledgeStore.namespace == item.namespace,
                )
                if item.key:
                    existing_query = existing_query.where(KnowledgeStore.key == item.key)
                if org_id:
                    existing_query = existing_query.where(KnowledgeStore.organization_id == org_id)
                else:
                    existing_query = existing_query.where(KnowledgeStore.organization_id.is_(None))

                existing_result = await db.execute(existing_query)
                existing = existing_result.scalar_one_or_none()

                if existing:
                    if replace_existing:
                        existing.content = item.content
                        existing.doc_metadata = item.metadata
                        result.updated += 1
                        result.details.append(ImportResultItem(name=item_name, status="updated"))
                    else:
                        result.skipped += 1
                        result.details.append(ImportResultItem(name=item_name, status="skipped"))
                else:
                    new_doc = KnowledgeStore(
                        namespace=item.namespace,
                        key=item.key,
                        content=item.content,
                        doc_metadata=item.metadata,
                        organization_id=org_id,
                        created_by=caller.principal.user_id,
                        embedding=[0.0] * 1536,  # Placeholder — reindex to generate real embeddings
                    )
                    db.add(new_doc)
                    result.created += 1
                    result.details.append(ImportResultItem(name=item_name, status="created"))
        except HTTPException:
            raise
        except Exception as e:
            result.created, result.updated, result.skipped = previous_counts[:3]
            del result.details[previous_counts[3]:]
            result.errors += 1
            result.details.append(ImportResultItem(name=item_name, status="error", error=str(e)))

    await db.commit()

    if result.created > 0:
        result.warnings.append(
            "Imported knowledge documents have placeholder embeddings. "
            "Run 'Reindex Workspace' from Maintenance to generate real embeddings."
        )

    return result



async def import_tables(
    db: AsyncSession,
    caller: Caller,
    content: bytes,
    replace_existing: bool = True,
    target_organization_id: str | None = None,
) -> ImportResult:
    """Import tables with documents from JSON file."""
    try:
        export_data = TableExportFile.model_validate_json(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid export file: {e}")

    target_override, force_global = _parse_target_org(target_organization_id)
    result = ImportResult(entity_type="tables")
    invalidated_tables: set[UUID] = set()

    for item in export_data.items:
        previous_counts = (result.created, result.updated, result.skipped, len(result.details))
        changed_table_id = None
        try:
            async with db.begin_nested():
                org_id = await _resolve_org_id(
                    db, caller, "tables.readwrite", item.organization_id, item.organization_name,
                    target_override, force_global, result.warnings, item.name,
                )

                existing_query = select(Table).where(Table.name == item.name, Table.solution_id.is_(None))
                if org_id:
                    existing_query = existing_query.where(Table.organization_id == org_id)
                else:
                    existing_query = existing_query.where(Table.organization_id.is_(None))

                existing_result = await db.execute(existing_query)
                existing_table = existing_result.scalar_one_or_none()

                if existing_table:
                    if replace_existing:
                        existing_table.description = item.description
                        existing_table.schema = item.schema_def

                        await _write_imported_documents(db, caller, existing_table, item.documents)
                        if item.documents:
                            changed_table_id = existing_table.id

                        result.updated += 1
                        result.details.append(ImportResultItem(name=item.name, status="updated"))
                    else:
                        result.skipped += 1
                        result.details.append(ImportResultItem(name=item.name, status="skipped"))
                else:
                    # Imported definitions use the existing role-ID policy seed.
                    new_table = Table(
                        name=item.name,
                        description=item.description,
                        schema=item.schema_def,
                        organization_id=org_id,
                        access=make_seed_admin_bypass(),
                        created_by=str(caller.principal.user_id),
                    )
                    db.add(new_table)
                    await db.flush()

                    await _write_imported_documents(db, caller, new_table, item.documents)
                    if item.documents:
                        changed_table_id = new_table.id

                    result.created += 1
                    result.details.append(ImportResultItem(name=item.name, status="created"))
            if changed_table_id is not None:
                invalidated_tables.add(changed_table_id)
        except HTTPException:
            raise
        except Exception as e:
            result.created, result.updated, result.skipped = previous_counts[:3]
            del result.details[previous_counts[3]:]
            result.errors += 1
            result.details.append(ImportResultItem(name=item.name, status="error", error=str(e)))

    await db.commit()
    for table_id in sorted(invalidated_tables, key=str):
        await publish_table_invalidated(str(table_id))
    return result



async def import_configs(
    db: AsyncSession,
    caller: Caller,
    content: bytes,
    replace_existing: bool = True,
    source_secret_key: str | None = None,
    target_organization_id: str | None = None,
) -> ImportResult:
    """Import configs from JSON file with optional secret re-encryption."""
    try:
        export_data = ConfigExportFile.model_validate_json(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid export file: {e}")

    target_override, force_global = _parse_target_org(target_organization_id)
    result = ImportResult(entity_type="configs")

    for item in export_data.items:
        previous_counts = (result.created, result.updated, result.skipped, len(result.details))
        try:
            async with db.begin_nested():
                org_id = await _resolve_org_id(
                    db, caller, "configs.readwrite", item.organization_id, item.organization_name,
                    target_override, force_global, result.warnings, item.key,
                )
                await _require_config_secret_write(db, caller, org_id, item.key, item.config_type == "secret")
                value = item.value

                # Re-encrypt secrets
                if item.config_type == "secret" and value and source_secret_key:
                    try:
                        plaintext = decrypt_with_key(value, source_secret_key)
                        value = encrypt_secret(plaintext)
                    except Exception as e:
                        result.errors += 1
                        result.details.append(ImportResultItem(
                            name=item.key, status="error",
                            error=f"Failed to re-encrypt secret: {e}",
                        ))
                        continue

                # Resolve integration_id from name
                integration_id = None
                if item.integration_name:
                    int_result = await db.execute(
                        select(Integration.id).where(
                            Integration.name == item.integration_name,
                            Integration.is_deleted == False,  # noqa: E712
                        )
                    )
                    integration_id = int_result.scalar_one_or_none()
                    if not integration_id:
                        result.warnings.append(
                            f"Integration '{item.integration_name}' not found for config '{item.key}'"
                        )

                # Check existing
                existing_query = select(Config).where(Config.key == item.key)
                if org_id:
                    existing_query = existing_query.where(Config.organization_id == org_id)
                else:
                    existing_query = existing_query.where(Config.organization_id.is_(None))
                if integration_id:
                    existing_query = existing_query.where(Config.integration_id == integration_id)

                existing_result = await db.execute(existing_query)
                existing = existing_result.scalar_one_or_none()

                valid_types = [e.value for e in ConfigType]
                config_type_enum = ConfigType(item.config_type) if item.config_type in valid_types else ConfigType.STRING

                if existing:
                    if replace_existing:
                        existing.value = {"value": value}
                        existing.config_type = config_type_enum
                        existing.description = item.description
                        existing.updated_by = str(caller.principal.user_id)
                        result.updated += 1
                        result.details.append(ImportResultItem(name=item.key, status="updated"))
                    else:
                        result.skipped += 1
                        result.details.append(ImportResultItem(name=item.key, status="skipped"))
                else:
                    db.add(Config(
                        key=item.key,
                        value={"value": value},
                        config_type=config_type_enum,
                        description=item.description,
                        organization_id=org_id,
                        integration_id=integration_id,
                        updated_by=str(caller.principal.user_id),
                    ))
                    result.created += 1
                    result.details.append(ImportResultItem(name=item.key, status="created"))
        except HTTPException:
            raise
        except Exception as e:
            result.created, result.updated, result.skipped = previous_counts[:3]
            del result.details[previous_counts[3]:]
            result.errors += 1
            result.details.append(ImportResultItem(name=item.key, status="error", error=str(e)))

    await db.commit()
    return result



async def import_integrations(
    db: AsyncSession,
    caller: Caller,
    content: bytes,
    replace_existing: bool = True,
    source_secret_key: str | None = None,
    target_organization_id: str | None = None,
) -> ImportResult:
    """Import integrations from JSON file with optional secret re-encryption."""
    try:
        export_data = IntegrationExportFile.model_validate_json(content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid export file: {e}")

    target_override, force_global = _parse_target_org(target_organization_id)
    result = ImportResult(entity_type="integrations")

    for item in export_data.items:
        previous_counts = (result.created, result.updated, result.skipped, len(result.details))
        try:
            async with db.begin_nested():
                await _preflight_integration(db, caller, item, target_override, force_global)
                # Check for existing integration
                existing_result = await db.execute(
                    select(Integration)
                    .options(
                        selectinload(Integration.config_schema),
                        selectinload(Integration.mappings),
                        selectinload(Integration.oauth_provider).defer(OAuthProvider.encrypted_client_secret),
                    )
                    .where(Integration.name == item.name, Integration.is_deleted == False)  # noqa: E712
                )
                existing = existing_result.scalar_one_or_none()

                if existing:
                    if not replace_existing:
                        result.skipped += 1
                        result.details.append(ImportResultItem(name=item.name, status="skipped"))
                        continue

                    # Update basic fields
                    existing.description = item.description
                    existing.entity_id = item.entity_id
                    existing.entity_id_name = item.entity_id_name
                    existing.default_entity_id = item.default_entity_id

                    # Resolve data provider
                    if item.list_entities_data_provider_name:
                        dp_id = await _data_provider_id(db, caller, item.list_entities_data_provider_name)
                        if dp_id:
                            existing.list_entities_data_provider_id = dp_id

                    # Sync config schema
                    await _sync_config_schema(db, existing, item.config_schema)

                    result.updated += 1
                    result.details.append(ImportResultItem(name=item.name, status="updated"))
                    integ = existing
                else:
                    # Create new integration
                    integ = Integration(
                        name=item.name,
                        description=item.description,
                        entity_id=item.entity_id,
                        entity_id_name=item.entity_id_name,
                        default_entity_id=item.default_entity_id,
                    )

                    # Resolve data provider
                    if item.list_entities_data_provider_name:
                        dp_id = await _data_provider_id(db, caller, item.list_entities_data_provider_name)
                        if dp_id:
                            integ.list_entities_data_provider_id = dp_id

                    db.add(integ)
                    await db.flush()

                    # Add config schema
                    for cs_item in item.config_schema:
                        db.add(IntegrationConfigSchema(
                            integration_id=integ.id,
                            key=cs_item.key,
                            type=cs_item.type,
                            required=cs_item.required,
                            description=cs_item.description,
                            options=cs_item.options,
                            position=cs_item.position,
                        ))

                    result.created += 1
                    result.details.append(ImportResultItem(name=item.name, status="created"))

                await db.flush()

                # Import mappings with their config
                for mapping_item in item.mappings:
                    org_id = await _resolve_org_id(
                        db, caller, "integrations.readwrite", mapping_item.organization_id, mapping_item.organization_name,
                        target_override, force_global, result.warnings,
                        f"{item.name}/mapping/{mapping_item.entity_id}",
                    )

                    # Check for existing mapping
                    mapping_query = select(IntegrationMapping).where(
                        IntegrationMapping.integration_id == integ.id,
                    )
                    if org_id:
                        mapping_query = mapping_query.where(IntegrationMapping.organization_id == org_id)
                    else:
                        mapping_query = mapping_query.where(IntegrationMapping.organization_id.is_(None))

                    mapping_result = await db.execute(mapping_query)
                    existing_mapping = mapping_result.scalar_one_or_none()

                    if existing_mapping:
                        existing_mapping.entity_id = mapping_item.entity_id
                        existing_mapping.entity_name = mapping_item.entity_name
                    else:
                        db.add(IntegrationMapping(
                            integration_id=integ.id,
                            organization_id=org_id,
                            entity_id=mapping_item.entity_id,
                            entity_name=mapping_item.entity_name,
                        ))

                    # Import config for this mapping
                    for cfg_key, cfg_value in mapping_item.config.items():
                        await _import_config_value(
                            db, caller, integ.id, org_id, cfg_key, cfg_value,
                            source_secret_key,
                            str(caller.principal.user_id), result,
                        )

                # Import default config (org_id = NULL)
                for cfg_key, cfg_value in item.default_config.items():
                    await _import_config_value(
                        db, caller, integ.id, None, cfg_key, cfg_value,
                        source_secret_key,
                        str(caller.principal.user_id), result,
                    )

                # Import OAuth provider
                if item.oauth_provider:
                    await _import_oauth_provider(
                        db, caller, integ, item.oauth_provider,
                        source_secret_key, result,
                        target_override, force_global,
                    )

        except HTTPException:
            raise
        except Exception as e:
            result.created, result.updated, result.skipped = previous_counts[:3]
            del result.details[previous_counts[3]:]
            result.errors += 1
            result.details.append(ImportResultItem(
                name=item.name, status="error", error=str(e),
            ))

    await db.commit()
    return result



async def import_all(
    db: AsyncSession,
    caller: Caller,
    content: bytes,
    replace_existing: bool = True,
    source_secret_key: str | None = None,
    target_organization_id: str | None = None,
) -> dict:
    """Import all entities from a ZIP file."""
    results: list[ImportResult] = []

    try:
        with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
            for filename, entity_type in [
                ("knowledge.json", "knowledge"),
                ("tables.json", "tables"),
                ("configs.json", "configs"),
                ("integrations.json", "integrations"),
            ]:
                if filename not in zf.namelist():
                    continue

                file_content = zf.read(filename)

                if entity_type == "knowledge":
                    r = await import_knowledge(
                        db, caller, file_content, replace_existing,
                        target_organization_id,
                    )
                elif entity_type == "tables":
                    r = await import_tables(
                        db, caller, file_content, replace_existing,
                        target_organization_id,
                    )
                elif entity_type == "configs":
                    r = await import_configs(
                        db, caller, file_content, replace_existing,
                        source_secret_key,
                        target_organization_id,
                    )
                elif entity_type == "integrations":
                    r = await import_integrations(
                        db, caller, file_content, replace_existing,
                        source_secret_key,
                        target_organization_id,
                    )
                else:
                    continue

                results.append(r)

    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid ZIP file")

    return {"results": [r.model_dump() for r in results]}



async def _sync_config_schema(
    db: AsyncSession,
    integration: Integration,
    schema_items: list[ConfigSchemaExportItem],
) -> None:
    """Sync config schema items for an existing integration."""
    existing_keys = {cs.key: cs for cs in integration.config_schema}
    import_keys = {cs.key for cs in schema_items}

    # Update existing or add new
    for cs_item in schema_items:
        if cs_item.key in existing_keys:
            existing_cs = existing_keys[cs_item.key]
            existing_cs.type = cs_item.type
            existing_cs.required = cs_item.required
            existing_cs.description = cs_item.description
            existing_cs.options = cs_item.options
            existing_cs.position = cs_item.position
        else:
            db.add(IntegrationConfigSchema(
                integration_id=integration.id,
                key=cs_item.key,
                type=cs_item.type,
                required=cs_item.required,
                description=cs_item.description,
                options=cs_item.options,
                position=cs_item.position,
            ))

    # Remove schema items not in import (optional: skip if we want to preserve)
    for key, cs in existing_keys.items():
        if key not in import_keys:
            await db.delete(cs)



async def _import_config_value(
    db: AsyncSession,
    caller: Caller,
    integration_id: UUID,
    org_id: UUID | None,
    key: str,
    value: str | None,
    source_secret_key: str | None,
    user_id: str,
    result: ImportResult,
) -> None:
    """Import a single config value, handling secret re-encryption."""
    require_permission(caller, "integrations.readwrite", org_id)
    # Determine config type from schema
    schema_result = await db.execute(
        select(IntegrationConfigSchema).where(
            IntegrationConfigSchema.integration_id == integration_id,
            IntegrationConfigSchema.key == key,
        )
    )
    schema_item = schema_result.scalar_one_or_none()
    config_type = ConfigType.STRING
    config_schema_id = None
    if schema_item:
        config_schema_id = schema_item.id
        try:
            config_type = ConfigType(schema_item.type)
        except ValueError:
            config_type = ConfigType.STRING

    await _require_config_secret_write(db, caller, org_id, key, config_type == ConfigType.SECRET)
    # Re-encrypt secrets only after target authorization.
    if config_type == ConfigType.SECRET and value and not source_secret_key:
        raise HTTPException(400, "Provide source_secret_key to re-encrypt imported secrets")
    if config_type == ConfigType.SECRET and value and source_secret_key:
        try:
            plaintext = decrypt_with_key(value, source_secret_key)
            value = encrypt_secret(plaintext)
        except Exception as exc:
            raise ValueError("Could not re-encrypt the imported secret") from exc

    # Upsert config
    existing_query = select(Config).where(
        Config.integration_id == integration_id,
        Config.key == key,
    )
    if org_id:
        existing_query = existing_query.where(Config.organization_id == org_id)
    else:
        existing_query = existing_query.where(Config.organization_id.is_(None))

    existing_result = await db.execute(existing_query)
    existing = existing_result.scalar_one_or_none()

    if existing:
        existing.value = {"value": value}
        existing.config_type = config_type
        existing.updated_by = user_id
    else:
        db.add(Config(
            key=key,
            value={"value": value},
            config_type=config_type,
            organization_id=org_id,
            integration_id=integration_id,
            config_schema_id=config_schema_id,
            updated_by=user_id,
        ))



async def _import_oauth_provider(
    db: AsyncSession,
    caller: Caller,
    integration: Integration,
    oauth_item: OAuthProviderExportItem,
    source_secret_key: str | None,
    result: ImportResult,
    target_override: UUID | None = None,
    force_global: bool = False,
) -> None:
    """Import OAuth provider for an integration."""
    org_id = await _resolve_org_id(
        db, caller, "integrations.readwrite", oauth_item.organization_id, oauth_item.organization_name,
        target_override, force_global, result.warnings,
        f"{integration.name}/oauth/{oauth_item.provider_name}",
    )

    if integration.oauth_provider is not None:
        require_permission(caller, "integrations.readwrite", integration.oauth_provider.organization_id)
        require_permission(caller, "secrets.read", integration.oauth_provider.organization_id)
    require_permission(caller, "secrets.read", org_id)
    # Decode encrypted secret
    encrypted_secret_bytes = base64.b64decode(oauth_item.encrypted_client_secret) if oauth_item.encrypted_client_secret else b""

    if encrypted_secret_bytes and not source_secret_key:
        raise HTTPException(400, "Provide source_secret_key to re-encrypt the imported OAuth client secret")
    if source_secret_key and encrypted_secret_bytes:
        try:
            # The encrypted_client_secret in OAuthProvider is raw Fernet encrypted bytes (not base64-wrapped)
            # We need to decrypt with source key and re-encrypt with destination key
            from cryptography.fernet import Fernet

            source_key = derive_fernet_key(source_secret_key)
            f = Fernet(source_key)
            plaintext = f.decrypt(encrypted_secret_bytes).decode()

            # Re-encrypt with current instance key
            dest_key = _get_current_fernet_key()
            f2 = Fernet(dest_key)
            encrypted_secret_bytes = f2.encrypt(plaintext.encode())
        except Exception as exc:
            raise ValueError("Could not re-encrypt the imported OAuth client secret") from exc

    if integration.oauth_provider:
        # Update existing
        op = integration.oauth_provider
        op.provider_name = oauth_item.provider_name
        op.display_name = oauth_item.display_name
        op.oauth_flow_type = oauth_item.oauth_flow_type
        op.client_id = oauth_item.client_id
        op.encrypted_client_secret = encrypted_secret_bytes
        op.authorization_url = oauth_item.authorization_url
        op.token_url = oauth_item.token_url
        op.token_url_defaults = oauth_item.token_url_defaults
        op.redirect_uri = oauth_item.redirect_uri
        op.scopes = oauth_item.scopes
    else:
        # Create new
        db.add(OAuthProvider(
            provider_name=oauth_item.provider_name,
            display_name=oauth_item.display_name,
            oauth_flow_type=oauth_item.oauth_flow_type,
            client_id=oauth_item.client_id,
            encrypted_client_secret=encrypted_secret_bytes,
            authorization_url=oauth_item.authorization_url,
            token_url=oauth_item.token_url,
            token_url_defaults=oauth_item.token_url_defaults,
            redirect_uri=oauth_item.redirect_uri,
            scopes=oauth_item.scopes,
            organization_id=org_id,
            integration_id=integration.id,
        ))




def _get_current_fernet_key() -> bytes:
    """Get current instance's Fernet key."""
    from src.core.security import _get_fernet_key
    return _get_fernet_key()



def _scoped_query(query, model, caller: Caller, permission: str):
    for reach in (user_reach(caller), permitted_organizations(caller, permission)):
        predicate = reach.where(model.organization_id)
        if predicate is not None:
            query = query.where(predicate)
    return query


async def _config_rows(db: AsyncSession, caller: Caller, query, permission: str):
    # Classify and authorize before selecting secret-bearing value columns.
    rows = (await db.execute(query.with_only_columns(Config.id, Config.organization_id, Config.config_type))).all()
    for row in rows:
        require_permission(caller, permission, row.organization_id)
        if row.config_type == ConfigType.SECRET:
            require_permission(caller, "secrets.read", row.organization_id)
    if not rows:
        return []
    return (await db.execute(select(Config).where(Config.id.in_([row.id for row in rows])))).scalars().all()


async def _require_config_secret_write(db, caller: Caller, org_id, key: str, incoming_secret: bool) -> None:
    existing_types = (await db.execute(select(Config.config_type).where(Config.key == key, Config.organization_id == org_id))).scalars().all()
    if incoming_secret or ConfigType.SECRET in existing_types:
        require_permission(caller, "secrets.read", org_id)


async def _preflight_integration(db, caller: Caller, item: IntegrationExportItem, target_override, force_global) -> None:
    require_permission(caller, "integrations.readwrite", None)
    types = {schema.key: schema.type for schema in item.config_schema}
    # Existing schema still classifies values if the archive changes its type.
    old_types = (await db.execute(select(IntegrationConfigSchema.key, IntegrationConfigSchema.type)
                    .join(Integration, Integration.id == IntegrationConfigSchema.integration_id)
                    .where(Integration.name == item.name, Integration.is_deleted.is_(False)))).all()
    secret_keys = {key for key, kind in old_types if kind == "secret"} | {key for key, kind in types.items() if kind == "secret"}
    for mapping in item.mappings:
        org_id = await _resolve_org_id(db, caller, "integrations.readwrite", mapping.organization_id, mapping.organization_name,
                                      target_override, force_global, [], item.name)
        for key in mapping.config:
            await _require_config_secret_write(db, caller, org_id, key, key in secret_keys)
    for key in item.default_config:
        await _require_config_secret_write(db, caller, None, key, key in secret_keys)
    if item.oauth_provider is not None:
        oauth = item.oauth_provider
        org_id = await _resolve_org_id(db, caller, "integrations.readwrite", oauth.organization_id, oauth.organization_name,
                                      target_override, force_global, [], item.name)
        require_permission(caller, "secrets.read", org_id)
        existing_scope = (await db.execute(select(OAuthProvider.organization_id).join(Integration, Integration.id == OAuthProvider.integration_id)
                            .where(Integration.name == item.name, Integration.is_deleted.is_(False)))).one_or_none()
        if existing_scope is not None:
            require_permission(caller, "integrations.readwrite", existing_scope.organization_id)
            require_permission(caller, "secrets.read", existing_scope.organization_id)


async def _write_imported_documents(db, caller: Caller, table: Table, documents: list[DocumentExportItem]) -> None:
    principal = await enforced_policy_principal(db, caller.principal)
    policies = await load_resolved_table_policies(table, db)
    await preresolve_for_policies(principal, policies, db, table.organization_id, table.solution_id)
    actor = str(caller.principal.user_id)
    await write_table_batch(db, table, [BatchWriteRow(i, item.id, item.data, actor, actor) for i, item in enumerate(documents)],
                           mode="replace_upsert", policies=policies, user=principal)


async def _data_provider_id(db, caller: Caller, name: str) -> UUID:
    from dataclasses import replace
    from src.repositories.workflows import WorkflowRepository

    original = Caller(replace(caller.principal, runtime_authority=None), caller.ctx)
    workflow = await WorkflowRepository(db, org_id=caller.ctx.home_organization_id if caller.ctx else None,
                                        caller=original).get(name=name)
    if workflow is None or not workflow.is_active or workflow.type != "data_provider":
        raise HTTPException(404, "Data provider not found")
    return workflow.id
