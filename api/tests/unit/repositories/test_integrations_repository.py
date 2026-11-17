"""Unit tests for IntegrationsRepository, the one integration config path.

The session is an ``AsyncMock``: these pin what the repository writes and how
it shapes values for readers. Live-database coverage of the same contracts
(REST, SDK upsert, schema type changes) is in
``tests/e2e/api/test_integrations.py::TestIntegrationConfigSecrets``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.security import SECRET_PLACEHOLDER, decrypt_secret, encrypt_secret
from src.models.contracts.integrations import (
    ConfigSchemaItem,
    IntegrationMappingCreate,
    IntegrationMappingUpdate,
    IntegrationUpdate,
)
from src.models.enums import ConfigType
from src.models.orm.config import Config
from src.repositories.integrations import (
    IntegrationConfigError,
    IntegrationsRepository,
    retype_integration_config_values,
    validate_config_value,
)


def _scalars(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _scalar(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _schema(key: str, type_: str) -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), key=key, type=type_)


def _session(*results) -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=list(results))
    session.add = MagicMock()
    return session


def _update_values(statement) -> dict:
    """The literal SET values of an ``update(Config)`` statement."""
    return {column.key: value.value for column, value in statement._values.items()}


# =============================================================================
# save_config — the single writer
# =============================================================================


class TestSaveConfig:
    async def test_new_secret_is_inserted_encrypted_as_secret(self) -> None:
        session = _session(_scalars([_schema("api_key", "secret")]), _scalar(None))

        await IntegrationsRepository(session).save_config(
            uuid4(), uuid4(), {"api_key": "plain-key"}, updated_by="a@example.test"
        )

        row = session.add.call_args.args[0]
        assert isinstance(row, Config)
        assert row.config_type == ConfigType.SECRET
        assert row.value["value"] != "plain-key"
        assert decrypt_secret(row.value["value"]) == "plain-key"

    async def test_existing_secret_is_updated_encrypted_as_secret(self) -> None:
        """An update re-encrypts and keeps SECRET (the old mapping path wrote
        plaintext over the ciphertext while the row stayed SECRET)."""
        session = _session(
            _scalars([_schema("api_key", "secret")]), _scalar(uuid4()), MagicMock()
        )

        await IntegrationsRepository(session).save_config(
            uuid4(), uuid4(), {"api_key": "rotated"}
        )

        values = _update_values(session.execute.await_args_list[2].args[0])
        assert values["config_type"] == ConfigType.SECRET
        assert decrypt_secret(values["value"]["value"]) == "rotated"
        session.add.assert_not_called()

    async def test_placeholder_keeps_the_stored_secret(self) -> None:
        session = _session(_scalars([_schema("api_key", "secret")]))

        await IntegrationsRepository(session).save_config(
            uuid4(), uuid4(), {"api_key": SECRET_PLACEHOLDER}
        )

        assert session.execute.await_count == 1  # schema lookup only
        session.add.assert_not_called()

    async def test_non_secret_is_stored_with_its_schema_type(self) -> None:
        session = _session(_scalars([_schema("port", "int")]), _scalar(None))

        await IntegrationsRepository(session).save_config(uuid4(), None, {"port": 443})

        row = session.add.call_args.args[0]
        assert row.config_type == ConfigType.INT
        assert row.value == {"value": 443}

    async def test_mismatched_value_is_rejected_before_any_write(self) -> None:
        session = _session(_scalars([_schema("api_key", "secret")]))

        with pytest.raises(IntegrationConfigError):
            await IntegrationsRepository(session).save_config(
                uuid4(), uuid4(), {"api_key": {"nested": "x"}}
            )
        session.add.assert_not_called()


class TestMappingWritesUseSaveConfig:
    """Mapping create/update (REST and SDK ``upsert_mapping``) save config
    through ``save_config``."""

    async def test_create_mapping_saves_config(self) -> None:
        session = AsyncMock()
        session.add = MagicMock()
        repo = IntegrationsRepository(session)
        repo.save_config = AsyncMock()
        integration_id, org_id = uuid4(), uuid4()

        await repo.create_mapping(
            integration_id,
            IntegrationMappingCreate(
                organization_id=org_id, entity_id="t", config={"api_key": "k"}
            ),
            updated_by="a@example.test",
        )

        repo.save_config.assert_awaited_once_with(
            integration_id=integration_id,
            organization_id=org_id,
            config={"api_key": "k"},
            updated_by="a@example.test",
        )

    async def test_update_mapping_saves_config_at_the_mapping_org(self) -> None:
        org_id = uuid4()
        mapping = SimpleNamespace(
            organization_id=org_id, entity_id=None, entity_name=None, oauth_token_id=None
        )
        session = AsyncMock()
        repo = IntegrationsRepository(session)
        repo.get_mapping_by_id = AsyncMock(return_value=mapping)
        repo.save_config = AsyncMock()
        integration_id = uuid4()

        await repo.update_mapping(
            integration_id,
            uuid4(),
            IntegrationMappingUpdate(config={"api_key": "k"}),
            updated_by="a@example.test",
        )

        repo.save_config.assert_awaited_once_with(
            integration_id=integration_id,
            organization_id=org_id,
            config={"api_key": "k"},
            updated_by="a@example.test",
        )


# =============================================================================
# Schema type changes
# =============================================================================


def _row(value, config_type: ConfigType) -> SimpleNamespace:
    return SimpleNamespace(value={"value": value}, config_type=config_type)


class TestRetypeConfigValues:
    async def test_becoming_secret_encrypts_plaintext(self) -> None:
        row = _row("plain", ConfigType.STRING)
        session = _session(_scalars([row]))

        await retype_integration_config_values(session, uuid4(), "api_key", "secret")

        assert row.config_type == ConfigType.SECRET
        assert decrypt_secret(row.value["value"]) == "plain"

    async def test_becoming_secret_rejects_a_non_text_value(self) -> None:
        session = _session(_scalars([_row(42, ConfigType.INT)]))

        with pytest.raises(IntegrationConfigError):
            await retype_integration_config_values(session, uuid4(), "k", "secret")

    async def test_leaving_secret_is_refused_while_a_value_is_stored(self) -> None:
        ciphertext = encrypt_secret("stored")
        row = _row(ciphertext, ConfigType.SECRET)
        session = _session(_scalars([row]))

        with pytest.raises(IntegrationConfigError):
            await retype_integration_config_values(session, uuid4(), "api_key", "string")
        assert row.value["value"] == ciphertext

    async def test_leaving_secret_relabels_an_empty_value(self) -> None:
        row = _row(None, ConfigType.SECRET)
        session = _session(_scalars([row]))

        await retype_integration_config_values(session, uuid4(), "api_key", "string")

        assert row.config_type == ConfigType.STRING

    async def test_update_integration_retypes_only_changed_fields(self, monkeypatch) -> None:
        import src.repositories.integrations as module

        api_key = SimpleNamespace(key="api_key", type="string")
        region = SimpleNamespace(key="region", type="string")
        integration = MagicMock(id=uuid4(), config_schema=[api_key, region])
        session = AsyncMock()
        repo = IntegrationsRepository(session)
        repo.get_integration_by_id = AsyncMock(return_value=integration)
        retype = AsyncMock()
        monkeypatch.setattr(module, "retype_integration_config_values", retype)

        await repo.update_integration(
            integration.id,
            IntegrationUpdate(
                config_schema=[
                    ConfigSchemaItem(key="api_key", type="secret"),
                    ConfigSchemaItem(key="region", type="string"),
                ]
            ),
        )

        retype.assert_awaited_once_with(session, integration.id, "api_key", "secret")
        assert api_key.type == "secret"


# =============================================================================
# Reads mask and decrypt by schema type
# =============================================================================


def _config(key: str, value, config_type: ConfigType, org_id=None) -> SimpleNamespace:
    return SimpleNamespace(
        key=key, value={"value": value}, config_type=config_type, organization_id=org_id
    )


class TestReadsUseSchemaType:
    async def test_string_row_of_a_secret_field_is_masked(self) -> None:
        """A legacy STRING row of a secret field never reads as plaintext."""
        org_id = uuid4()
        session = _session(
            _scalars([_config("api_key", "legacy-plain", ConfigType.STRING, org_id)]),
            _scalars(["api_key"]),
        )

        config = await IntegrationsRepository(session).get_org_config_overrides(
            uuid4(), org_id
        )

        assert config == {"api_key": SECRET_PLACEHOLDER}

    async def test_sdk_read_masks_without_secrets_read(self) -> None:
        org_id = uuid4()
        session = _session(
            _scalars(
                [
                    _config("api_key", encrypt_secret("k"), ConfigType.SECRET, org_id),
                    _config("region", "eu", ConfigType.STRING, org_id),
                ]
            ),
            _scalars(["api_key"]),
        )

        config = await IntegrationsRepository(session).get_config_for_mapping(
            uuid4(), org_id, external=False, reveal_secrets=False
        )

        assert config == {"api_key": SECRET_PLACEHOLDER, "region": "eu"}

    async def test_sdk_read_decrypts_with_secrets_read(self) -> None:
        session = _session(
            _scalars([_config("api_key", encrypt_secret("k"), ConfigType.SECRET)]),
            _scalars(["api_key"]),
        )

        config = await IntegrationsRepository(session).get_integration_defaults(
            uuid4(), external=False, reveal_secrets=True
        )

        assert config == {"api_key": "k"}


@pytest.mark.parametrize(
    ("schema_type", "value"),
    [("int", "abc"), ("int", 1.5), ("bool", "true"), ("json", "{bad"), ("secret", 123)],
)
def test_validate_config_value_rejects(schema_type: str, value) -> None:
    with pytest.raises(IntegrationConfigError):
        validate_config_value("k", value, schema_type)


@pytest.mark.parametrize(
    ("schema_type", "value"),
    [("int", 5), ("int", "5"), ("bool", False), ("json", "{}"), ("secret", "s"), ("secret", None)],
)
def test_validate_config_value_accepts(schema_type: str, value) -> None:
    validate_config_value("k", value, schema_type)
