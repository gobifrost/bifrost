"""Unit tests for ``src.services.config_values``, the one config row writer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.security import decrypt_secret, encrypt_secret
from src.models.enums import ConfigType
from src.models.orm.config import Config
from src.services.config_values import (
    KEEP,
    ConfigValueError,
    new_config_row,
    stored_config_value,
    stored_manifest_value,
    validate_schema_value,
    write_config_value,
)


def _session(schema_type: str | None = None) -> AsyncMock:
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=schema_type)
    session.add = MagicMock()
    return session


class TestStoredConfigValue:
    def test_a_new_secret_is_encrypted(self) -> None:
        stored = stored_config_value("k", config_type=ConfigType.SECRET, value="s3cret")
        assert stored["value"] != "s3cret"
        assert decrypt_secret(stored["value"]) == "s3cret"

    def test_a_secret_must_be_text(self) -> None:
        with pytest.raises(ConfigValueError):
            stored_config_value("k", config_type=ConfigType.SECRET, value=123)

    def test_an_empty_secret_is_stored_empty(self) -> None:
        assert stored_config_value("k", config_type=ConfigType.SECRET, value="") == {"value": ""}

    def test_a_plain_value_is_wrapped(self) -> None:
        assert stored_config_value("k", config_type=ConfigType.INT, value=5) == {"value": 5}

    def test_keep_encrypts_plaintext_that_becomes_secret(self) -> None:
        stored = stored_config_value(
            "k",
            config_type=ConfigType.SECRET,
            current={"value": "plain"},
            current_type=ConfigType.STRING,
        )
        assert decrypt_secret(stored["value"]) == "plain"

    def test_keep_leaves_a_stored_secret_unchanged(self) -> None:
        current = {"value": encrypt_secret("s")}
        assert (
            stored_config_value(
                "k", config_type=ConfigType.SECRET, current=current, current_type=ConfigType.SECRET
            )
            == current
        )

    def test_keep_refuses_to_reveal_a_secret(self) -> None:
        with pytest.raises(ConfigValueError):
            stored_config_value(
                "k",
                config_type=ConfigType.STRING,
                current={"value": encrypt_secret("s")},
                current_type=ConfigType.SECRET,
            )

    def test_a_new_value_may_replace_a_secret_with_plain_text(self) -> None:
        assert stored_config_value(
            "k", config_type=ConfigType.STRING, value="public"
        ) == {"value": "public"}

    def test_keep_on_a_new_row_is_empty(self) -> None:
        assert stored_config_value("k", config_type=ConfigType.SECRET, value=KEEP) == {}


class TestStoredManifestValue:
    def test_a_plain_value_is_kept_verbatim(self) -> None:
        assert stored_manifest_value("k", config_type=ConfigType.INT, manifest_value=3) == 3
        assert stored_manifest_value("k", config_type=ConfigType.STRING, manifest_value=None) == {}

    def test_a_secret_with_a_value_is_encrypted(self) -> None:
        stored = stored_manifest_value(
            "k", config_type=ConfigType.SECRET, manifest_value={"value": "plain"}
        )
        assert decrypt_secret(stored["value"]) == "plain"

    def test_a_secret_without_a_value_encrypts_stored_plain_text(self) -> None:
        stored = stored_manifest_value(
            "k",
            config_type=ConfigType.SECRET,
            manifest_value=None,
            current={"value": "was-plain"},
            current_type=ConfigType.STRING,
        )
        assert decrypt_secret(stored["value"]) == "was-plain"

    def test_a_secret_without_a_value_keeps_a_stored_secret(self) -> None:
        current = {"value": encrypt_secret("s")}
        assert (
            stored_manifest_value(
                "k",
                config_type=ConfigType.SECRET,
                manifest_value=None,
                current=current,
                current_type=ConfigType.SECRET,
            )
            == current
        )


class TestWriteConfigValue:
    async def test_an_integration_row_takes_its_schema_type(self) -> None:
        row = Config(key="api_key", value={"value": "x"}, config_type=ConfigType.SECRET, integration_id=uuid4())

        with pytest.raises(ConfigValueError, match="integration field"):
            await write_config_value(_session("secret"), row, config_type=ConfigType.STRING, value="y")
        assert row.config_type == ConfigType.SECRET

    async def test_a_plain_row_has_no_schema_constraint(self) -> None:
        session = _session()
        row = Config(key="k", value={"value": "x"}, config_type=ConfigType.STRING)

        await write_config_value(session, row, config_type=ConfigType.SECRET)

        assert row.config_type == ConfigType.SECRET
        assert decrypt_secret(row.value["value"]) == "x"
        session.scalar.assert_not_awaited()

    async def test_new_config_row_is_encoded_and_added(self) -> None:
        session = _session("secret")

        row = await new_config_row(
            session,
            key="api_key",
            config_type=ConfigType.SECRET,
            value="s",
            integration_id=uuid4(),
            updated_by="a@example.test",
        )

        session.add.assert_called_once_with(row)
        assert decrypt_secret(row.value["value"]) == "s"


@pytest.mark.parametrize(
    ("schema_type", "value"),
    [("int", "abc"), ("int", 1.5), ("bool", "true"), ("json", "{bad"), ("secret", 123)],
)
def test_validate_schema_value_rejects(schema_type: str, value) -> None:
    with pytest.raises(ConfigValueError):
        validate_schema_value("k", value, schema_type)


@pytest.mark.parametrize(
    ("schema_type", "value"),
    [("int", 5), ("int", "5"), ("bool", False), ("json", "{}"), ("secret", "s"), ("secret", None)],
)
def test_validate_schema_value_accepts(schema_type: str, value) -> None:
    validate_schema_value("k", value, schema_type)
