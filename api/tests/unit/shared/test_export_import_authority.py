"""Workspace transfer preserves current scope, Secrets, and row-policy gates."""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from cryptography.fernet import Fernet
from sqlalchemy import delete, select

from shared import export_import as service
from shared.builtin_roles import USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.contracts.export_import import ConfigExportFile, ConfigExportItem, TableExportFile, TableExportItem, DocumentExportItem
from src.models.enums import ConfigType
from src.models.orm.config import Config
from src.models.orm.integrations import Integration, IntegrationMapping
from src.models.orm.organizations import Organization
from src.models.orm.tables import Document, Table
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.routers import export_import as router
from src.services.authorization.enforce import load_caller


async def _caller(db, *permissions):
    home, foreign = [Organization(name=name, created_by="test") for name in ("Contoso", "Fabrikam")]
    db.add_all([home, foreign])
    await db.flush()
    user = User(email=f"transfer-{uuid4().hex}@contoso.example", organization_id=home.id, base_role_id=USER_ROLE_ID)
    role = Role(name=f"Contoso Transfer {uuid4().hex}", created_by="test")
    db.add_all([user, role])
    await db.flush()
    db.add_all([
        UserRole(user_id=user.id, role_id=role.id, assigned_by="test"),
        UserRoleBoundary(user_id=user.id, role_id=role.id, kind="organization", organization_id=home.id),
        UserRoleBoundary(user_id=user.id, role_id=role.id, kind="platform"),
        *(RolePermission(role_id=role.id, permission=permission) for permission in permissions),
    ])
    await db.flush()
    principal = UserPrincipal(user_id=user.id, email=user.email, organization_id=home.id, is_superuser=True)
    return await load_caller(db, principal), home, foreign


@pytest_asyncio.fixture
async def authority(db_session, async_session_factory):
    owned = []

    async def make(*permissions):
        caller, home, foreign = await _caller(db_session, *permissions)
        owned.append((caller.principal.user_id, [grant.role_id for grant in caller.ctx.role_grants], [home.id, foreign.id]))
        return caller, home, foreign

    try:
        yield make
    finally:
        await db_session.rollback()
        async with async_session_factory() as cleanup:
            for user_id, role_ids, org_ids in owned:
                # Import owns its commit; use exact IDs in an independent session.
                await cleanup.execute(delete(Config).where(Config.organization_id.in_(org_ids)))
                await cleanup.execute(delete(Table).where(Table.organization_id.in_(org_ids)))
                await cleanup.execute(delete(User).where(User.id == user_id))
                await cleanup.execute(delete(Role).where(Role.id.in_(role_ids)))
                await cleanup.execute(delete(Organization).where(Organization.id.in_(org_ids)))
            await cleanup.commit()


async def test_transfer_route_refuses_actor_flag_before_reading_upload(db_session, authority):
    caller, _home, _foreign = await authority()
    upload = SimpleNamespace(read=Mock(side_effect=AssertionError("upload read before named authorization")))
    with pytest.raises(HTTPException) as error:
        await router.import_knowledge(db_session, caller.principal, upload, True, None)
    assert error.value.status_code == 403
    upload.read.assert_not_called()


async def test_secret_config_export_refuses_before_selecting_value_columns(db_session, authority, monkeypatch):
    caller, home, _foreign = await authority( "configs.read")
    db_session.add(Config(key="credential", config_type=ConfigType.SECRET, organization_id=home.id,
                          value={"value": "opaque secret payload"}, updated_by="test"))
    await db_session.flush()
    execute = db_session.execute
    statements = []

    async def observe(statement, *args, **kwargs):
        statements.append(str(statement))
        return await execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", observe)
    with pytest.raises(HTTPException) as error:
        await service._build_configs_export(db_session, caller)
    assert error.value.status_code == 403
    assert not any("configs.value" in sql for sql in statements)


async def test_table_export_applies_row_policy_and_original_reach(db_session, authority):
    caller, home, foreign = await authority( "tables.read")
    policy = {"policies": [{"name": "published", "actions": ["read"], "when": {"eq": [{"row": "published"}, True]}}]}
    own = Table(name=f"contoso_{uuid4().hex}", organization_id=home.id, access=policy, created_by="test")
    hidden = Table(name=f"fabrikam_{uuid4().hex}", organization_id=foreign.id, access=policy, created_by="test")
    db_session.add_all([own, hidden])
    await db_session.flush()
    db_session.add_all([Document(table_id=own.id, id="visible", data={"published": True}),
                        Document(table_id=own.id, id="hidden", data={"published": False}),
                        Document(table_id=hidden.id, id="foreign", data={"published": True})])
    await db_session.flush()
    result = await service._build_tables_export(db_session, caller, [str(own.id), str(hidden.id)])
    assert [item.name for item in result.items] == [own.name]
    assert [row.id for row in result.items[0].documents] == ["visible"]


async def test_integration_export_filters_mapping_org_before_serialization(db_session, authority):
    caller, home, foreign = await authority( "integrations.read")
    integration = Integration(name=f"Contoso {uuid4().hex}")
    db_session.add(integration)
    await db_session.flush()
    db_session.add_all([IntegrationMapping(integration_id=integration.id, organization_id=home.id, entity_id="contoso"),
                        IntegrationMapping(integration_id=integration.id, organization_id=foreign.id, entity_id="fabrikam")])
    await db_session.flush()
    result = await service._build_integrations_export(db_session, caller, [str(integration.id)])
    assert [item.entity_id for item in result.items[0].mappings] == ["contoso"]


async def test_secret_import_refuses_before_decryption_even_without_archive_flag(db_session, authority, monkeypatch):
    caller, home, _foreign = await authority( "configs.readwrite")
    content = ConfigExportFile(items=[ConfigExportItem(key="credential", config_type="secret", value="opaque ciphertext",
                                                       organization_id=str(home.id))]).model_dump_json().encode()
    decrypt = Mock(side_effect=AssertionError("crypto before Secrets authorization"))
    monkeypatch.setattr(service, "decrypt_with_key", decrypt)
    with pytest.raises(HTTPException) as error:
        await service.import_configs(db_session, caller, content, source_secret_key="test source key")
    assert error.value.status_code == 403
    decrypt.assert_not_called()


async def test_secret_config_import_requires_source_key_and_only_persists_reencrypted_value(
    db_session, authority
):
    """Config imports match integration/OAuth's fail-closed source-key contract."""
    from src.core.security import decrypt_secret, derive_fernet_key

    caller, home, _foreign = await authority("configs.readwrite", "secrets.read")
    source_key = "source-instance-key"
    ciphertext = base64.urlsafe_b64encode(
        Fernet(derive_fernet_key(source_key)).encrypt(b"portable credential")
    ).decode()
    content = ConfigExportFile(items=[ConfigExportItem(
        key="credential", config_type="secret", value=ciphertext,
        organization_id=str(home.id),
    )]).model_dump_json().encode()

    with pytest.raises(HTTPException, match="source_secret_key") as missing_key:
        await service.import_configs(db_session, caller, content)
    assert missing_key.value.status_code == 400
    assert (await db_session.execute(select(Config).where(Config.key == "credential"))).scalar_one_or_none() is None

    wrong_key = await service.import_configs(
        db_session, caller, content, source_secret_key="wrong-source-key"
    )
    assert wrong_key.errors == 1
    assert wrong_key.created == wrong_key.updated == 0
    assert (await db_session.execute(select(Config).where(Config.key == "credential"))).scalar_one_or_none() is None

    imported = await service.import_configs(
        db_session, caller, content, source_secret_key=source_key
    )
    assert imported.created == 1
    stored = (await db_session.execute(select(Config).where(Config.key == "credential"))).scalar_one()
    assert stored.value["value"] != ciphertext
    assert decrypt_secret(stored.value["value"]) == "portable credential"


async def test_table_import_row_refusal_rolls_back_metadata_and_data(db_session, authority):
    caller, home, _foreign = await authority( "tables.readwrite")
    table = Table(name=f"contoso_{uuid4().hex}", organization_id=home.id, description="Original", access={"policies": []}, created_by="test")
    db_session.add(table)
    await db_session.flush()
    table_id, table_name = table.id, table.name
    content = TableExportFile(items=[TableExportItem(name=table_name, description="Changed", organization_id=str(home.id),
                            documents=[DocumentExportItem(id="denied", data={"value": "private"})])]).model_dump_json().encode()
    result = await service.import_tables(db_session, caller, content)
    assert result.errors == 1
    assert result.created == result.updated == 0
    await db_session.refresh(table)
    assert table.description == "Original"
    assert (await db_session.execute(select(Document).where(Document.table_id == table_id))).scalars().all() == []


async def test_successful_table_import_invalidates_only_after_rows_commit(db_session, async_session_factory, authority, monkeypatch):
    caller, home, _foreign = await authority("tables.readwrite")
    table = Table(name=f"contoso_{uuid4().hex}", organization_id=home.id,
                  access={"policies": [{"name": "write", "actions": ["create", "update"]}]}, created_by="test")
    db_session.add(table)
    await db_session.flush()
    table_id = table.id

    async def observe_commit(published_table_id):
        assert published_table_id == str(table_id)
        async with async_session_factory() as reader:
            rows = (await reader.execute(select(Document.id).where(Document.table_id == table_id))).scalars().all()
            assert rows == ["imported"]

    invalidation = AsyncMock(side_effect=observe_commit)
    monkeypatch.setattr(service, "publish_table_invalidated", invalidation)
    content = TableExportFile(items=[TableExportItem(name=table.name, organization_id=str(home.id),
                            documents=[DocumentExportItem(id="imported", data={"value": "Contoso"})])]).model_dump_json().encode()
    result = await service.import_tables(db_session, caller, content)
    assert result.updated == 1
    assert result.errors == 0
    invalidation.assert_awaited_once_with(str(table_id))


async def test_unresolved_import_org_refuses_instead_of_writing_global(db_session, authority):
    caller, _home, _foreign = await authority( "configs.readwrite")
    content = ConfigExportFile(items=[ConfigExportItem(key="endpoint", config_type="string", value="https://contoso.example",
                                                       organization_name="Missing Contoso Organization")]).model_dump_json().encode()
    with pytest.raises(HTTPException) as error:
        await service.import_configs(db_session, caller, content)
    assert error.value.status_code == 404
    assert (await db_session.execute(select(Config).where(Config.key == "endpoint"))).scalars().all() == []


async def test_integration_archive_roundtrips_named_instance_and_shared_schema(db_session, authority):
    """The UI export keeps connections grouped without cloning their schema."""
    from src.models.orm.integrations import IntegrationConfigSchema
    from src.repositories.integrations import IntegrationsRepository

    caller, home, _foreign = await authority(
        "integrations.read", "integrations.readwrite", "configs.read", "configs.readwrite"
    )
    name = f"Instance Archive {uuid4().hex}"
    copied_name = f"{name} Copy"
    definition = Integration(name=name, config_schema=[IntegrationConfigSchema(key="region", type="string")])
    db_session.add(definition)
    await db_session.flush()
    child = Integration(name=f"{definition.id}::pbxs", parent_integration_id=definition.id,
                        instance_key="pbxs", instance_name="PBXs")
    db_session.add(child)
    await db_session.flush()
    repo = IntegrationsRepository(db_session)
    await repo.save_config(definition.id, None, {"region": "eu-west-1"}, "test")
    await repo.save_config(child.id, None, {"region": "us-east-1"}, "test")
    db_session.add(IntegrationMapping(integration_id=child.id, organization_id=home.id, entity_id="pbx-account"))
    await db_session.flush()
    try:
        archive = await service._build_integrations_export(db_session, caller, [str(definition.id)])
        assert len(archive.items) == 2
        named = next(item for item in archive.items if item.parent_integration_name)
        assert named.parent_integration_name == name
        assert named.instance_key == "pbxs"
        assert named.name == "PBXs"
        assert named.config_schema == []
        for item in archive.items:
            if item.parent_integration_name:
                item.parent_integration_name = copied_name
            else:
                item.name = copied_name
        archive.items.reverse()  # Import must order the definition before children.
        restored = await service.import_integrations(db_session, caller, archive.model_dump_json().encode())
        assert restored.errors == 0
        assert restored.created == 2
        copied = await repo.get_integration_by_name(copied_name)
        connection = await repo.get_instance_by_key(copied, "pbxs")
        assert (await repo.get_integration_defaults(copied.id, external=False, reveal_secrets=False))["region"] == "eu-west-1"
        assert (await repo.get_integration_defaults(connection.id, external=False, reveal_secrets=False))["region"] == "us-east-1"
        mapping = await repo.get_mapping_by_org(connection.id, home.id)
        assert mapping.entity_id == "pbx-account"
        config = (await db_session.execute(select(Config).where(Config.integration_id == connection.id))).scalar_one()
        assert config.config_schema_id == copied.config_schema[0].id
        assert connection.config_schema == []
    finally:
        await db_session.rollback()
        roots = select(Integration.id).where(Integration.name.in_([name, copied_name]))
        await db_session.execute(delete(Integration).where(
            Integration.parent_integration_id.in_(roots) | Integration.name.in_([name, copied_name])
        ))
        await db_session.commit()


async def test_named_instance_archive_import_uses_parent_secret_classification(db_session, authority):
    """A child cannot import encrypted secret text as an ordinary string."""
    from src.models.contracts.export_import import ImportResult
    from src.models.orm.integrations import IntegrationConfigSchema

    caller, _home, _foreign = await authority("integrations.readwrite", "secrets.read")
    definition = Integration(name=f"Secret Parent {uuid4().hex}", config_schema=[IntegrationConfigSchema(key="key", type="secret")])
    db_session.add(definition)
    await db_session.flush()
    child = Integration(name=f"{definition.id}::pbxs", parent_integration_id=definition.id,
                        instance_key="pbxs", instance_name="PBXs")
    db_session.add(child)
    await db_session.flush()
    with pytest.raises(HTTPException) as error:
        await service._import_config_value(db_session, caller, child.id, None, "key",
                                           "encrypted-source-value", None, "test", ImportResult(entity_type="integrations"))
    assert error.value.status_code == 400
    assert "source_secret_key" in error.value.detail


async def test_config_archive_selects_named_instance_without_overwriting_other_values(db_session, authority):
    from src.models.orm.integrations import IntegrationConfigSchema
    from src.repositories.integrations import IntegrationsRepository
    from src.services.config_values import new_config_row

    caller, _home, _foreign = await authority("configs.read", "configs.readwrite")
    name = f"Config Archive {uuid4().hex}"
    key = f"region_{uuid4().hex}"
    definition = Integration(name=name, config_schema=[IntegrationConfigSchema(key=key, type="string")])
    db_session.add(definition)
    await db_session.flush()
    child = Integration(name=f"{definition.id}::pbxs", parent_integration_id=definition.id,
                        instance_key="pbxs", instance_name="PBXs")
    db_session.add(child)
    await db_session.flush()
    repo = IntegrationsRepository(db_session)
    await repo.save_config(definition.id, None, {key: "eu-west-1"}, "test")
    await repo.save_config(child.id, None, {key: "us-east-1"}, "test")
    standalone = await new_config_row(db_session, key=key, value="global-value", config_type=ConfigType.STRING,
                                      organization_id=None, updated_by="test")
    await db_session.flush()
    try:
        config = (await db_session.execute(select(Config).where(Config.integration_id == child.id))).scalar_one()
        exported = await service._build_configs_export(db_session, caller, [str(config.id)])
        assert exported.items[0].integration_name == name
        assert exported.items[0].instance_key == "pbxs"
        exported.items[0].value = "ap-south-1"
        result = await service.import_configs(db_session, caller, exported.model_dump_json().encode())
        assert result.errors == 0
        assert (await repo.get_integration_defaults(child.id, external=False, reveal_secrets=False))[key] == "ap-south-1"
        assert (await repo.get_integration_defaults(definition.id, external=False, reveal_secrets=False))[key] == "eu-west-1"
        await db_session.refresh(standalone)
        assert standalone.value == {"value": "global-value"}
        assert config.config_schema_id == definition.config_schema[0].id
        exported.items[0].instance_key = "missing"
        with pytest.raises(HTTPException) as error:
            await service.import_configs(db_session, caller, exported.model_dump_json().encode())
        assert error.value.status_code == 400
    finally:
        await db_session.rollback()
        await db_session.execute(delete(Config).where(Config.key == key))
        roots = select(Integration.id).where(Integration.name == name)
        await db_session.execute(delete(Integration).where(
            Integration.parent_integration_id.in_(roots) | (Integration.name == name)
        ))
        await db_session.commit()


async def test_archive_rejects_named_instance_without_parent_before_updating_root(db_session, authority):
    import json

    caller, _home, _foreign = await authority("integrations.readwrite")
    root = Integration(name=f"Archive Root {uuid4().hex}", description="keep this")
    db_session.add(root)
    await db_session.flush()
    archive = {"items": [{"name": root.name, "instance_key": "pbxs", "description": "wrong target"}]}
    with pytest.raises(HTTPException) as error:
        await service.import_integrations(db_session, caller, json.dumps(archive).encode())
    assert error.value.status_code == 400
    assert "parent integration name" in error.value.detail
    await db_session.refresh(root)
    assert root.description == "keep this"
