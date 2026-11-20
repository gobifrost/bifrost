"""The one place a ``configs`` row's stored value and type are decided.

Every writer of ``Config`` rows (the config API, the SDK, integration
mappings and defaults, schema type changes, export/import and git-sync)
goes through this module, so the storage rules hold everywhere:

- a ``secret`` value is stored encrypted with ``encrypt_secret``;
- changing a row's type converts its stored value: becoming ``secret``
  encrypts it, and leaving ``secret`` is refused while a value is stored,
  because decrypting it would reveal the secret to whoever may change the
  type, without ``secrets.read`` (send a new value instead);
- an integration row (one with ``integration_id``) has exactly the type of
  its integration config-schema field.

``tests/unit/test_config_value_writers.py`` fails if code outside this module
builds a ``Config`` row or assigns its value or type. Git-sync writes rows as
``Upsert`` values, computed here by :func:`stored_manifest_value`.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.security import encrypt_secret
from src.models.enums import ConfigType
from src.models.orm.config import Config
from src.models.orm.integrations import IntegrationConfigSchema


class ConfigValueError(ValueError):
    """A config value cannot be stored as its type requires."""


class _Keep:
    def __repr__(self) -> str:
        return "KEEP"


KEEP: Any = _Keep()
"""Keep the stored value, converting it if the row's type changes."""


_CONFIG_TYPE_BY_SCHEMA_TYPE: dict[str, ConfigType] = {
    "string": ConfigType.STRING,
    "int": ConfigType.INT,
    "bool": ConfigType.BOOL,
    "json": ConfigType.JSON,
    "secret": ConfigType.SECRET,
}


def config_type_for_schema_type(schema_type: str | None) -> ConfigType:
    """The stored ``ConfigType`` for an integration config-schema type."""
    return _CONFIG_TYPE_BY_SCHEMA_TYPE.get(schema_type or "", ConfigType.STRING)


def validate_schema_value(key: str, value: Any, schema_type: str) -> None:
    """Raise ``ConfigValueError`` when ``value`` does not fit an integration
    schema type. ``None`` and ``""`` always pass: they clear the value."""
    if value is None or value == "":
        return

    if schema_type == "int":
        if isinstance(value, str):
            try:
                int(value)
            except ValueError:
                raise ConfigValueError(f"Config key '{key}' expects integer, got invalid value")
        elif not isinstance(value, int):
            raise ConfigValueError(
                f"Config key '{key}' expects integer, got {type(value).__name__}"
            )
    elif schema_type == "bool":
        if not isinstance(value, bool):
            raise ConfigValueError(
                f"Config key '{key}' expects boolean, got {type(value).__name__}"
            )
    elif schema_type == "json":
        if isinstance(value, str):
            try:
                json.loads(value)
            except json.JSONDecodeError:
                raise ConfigValueError(f"Config key '{key}' contains invalid JSON")
    elif schema_type == "secret":
        if not isinstance(value, str):
            raise ConfigValueError(
                f"Config key '{key}' expects a text secret, got {type(value).__name__}"
            )


def unwrap(stored: Any) -> Any:
    """The value inside the ``{"value": X}`` storage envelope."""
    if isinstance(stored, dict) and "value" in stored:
        return stored["value"]
    return stored


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def encode_config_value(key: str, config_type: ConfigType, value: Any) -> dict[str, Any]:
    """The stored envelope for a new ``value`` of ``config_type``.

    A secret is encrypted and must be text; an empty value is stored as is.
    """
    if config_type == ConfigType.SECRET and not _is_empty(value):
        if not isinstance(value, str):
            raise ConfigValueError(
                f"Config key '{key}' expects a text secret, got {type(value).__name__}"
            )
        return {"value": encrypt_secret(value)}
    return {"value": value}


def convert_config_value(
    key: str, stored: Any, from_type: ConfigType | None, to_type: ConfigType
) -> Any:
    """The stored envelope after a type change that keeps the value."""
    from_type = from_type or ConfigType.STRING
    raw = unwrap(stored)
    # ``{}`` is the empty envelope git-sync stores for a value-less entry.
    if from_type == to_type or stored in (None, {}) or _is_empty(raw):
        return stored
    if to_type == ConfigType.SECRET:
        if not isinstance(raw, str):
            raise ConfigValueError(
                f"Config key '{key}' has a stored {type(raw).__name__} value that "
                "cannot become a secret; clear it first"
            )
        return {"value": encrypt_secret(raw)}
    if from_type == ConfigType.SECRET:
        raise ConfigValueError(
            f"Config key '{key}' stores a secret; set a new value to change its "
            "type from secret"
        )
    return stored


def stored_config_value(
    key: str,
    *,
    config_type: ConfigType,
    value: Any = KEEP,
    current: Any = None,
    current_type: ConfigType | None = None,
) -> Any:
    """The stored envelope for a row of ``config_type``.

    ``value=KEEP`` keeps ``current`` (the stored envelope, ``None`` for a new
    row), converting it from ``current_type``; any other value is encoded.
    """
    if value is KEEP:
        return convert_config_value(
            key, {} if current is None else current, current_type, config_type
        )
    return encode_config_value(key, config_type, value)


def stored_manifest_value(
    key: str,
    *,
    config_type: ConfigType,
    manifest_value: Any,
    current: Any = None,
    current_type: ConfigType | None = None,
) -> Any:
    """The stored value git-sync writes for a manifest config entry.

    A manifest carries a non-secret value in its stored form, which is kept
    verbatim (``{}`` when absent). Manifests never carry secrets: a secret
    entry without a value keeps the stored one (``current``, ``None`` for a
    new row), encrypting it if it was plain text; a secret entry with a value
    is encrypted.
    """
    if config_type != ConfigType.SECRET:
        return {} if manifest_value is None else manifest_value
    raw = unwrap(manifest_value)
    if not _is_empty(raw) and manifest_value != {}:
        return encode_config_value(key, ConfigType.SECRET, raw)
    if current is None:
        return {} if manifest_value is None else manifest_value
    return convert_config_value(key, current, current_type, ConfigType.SECRET)


async def required_config_type(
    session: AsyncSession, integration_id: UUID | None, key: str
) -> ConfigType | None:
    """The type an integration row must have, or ``None`` when unconstrained."""
    if integration_id is None:
        return None
    schema_type = await session.scalar(
        select(IntegrationConfigSchema.type).where(
            IntegrationConfigSchema.integration_id == integration_id,
            IntegrationConfigSchema.key == key,
        )
    )
    return None if schema_type is None else config_type_for_schema_type(schema_type)


async def write_config_value(
    session: AsyncSession,
    row: Config,
    *,
    config_type: ConfigType,
    value: Any = KEEP,
) -> None:
    """Set ``row``'s type and value: the only way a ``Config`` row changes.

    ``value=KEEP`` keeps the stored value, converting it to ``config_type``.
    """
    required = await required_config_type(session, row.integration_id, row.key)
    if required is not None and config_type != required:
        raise ConfigValueError(
            f"Config key '{row.key}' belongs to an integration field of type "
            f"'{required.value}'; change the field type on the integration instead"
        )
    row.value = stored_config_value(
        row.key,
        config_type=config_type,
        value=value,
        current=row.value,
        current_type=row.config_type,
    )
    row.config_type = config_type


async def new_config_row(
    session: AsyncSession,
    *,
    key: str,
    config_type: ConfigType,
    value: Any,
    **columns: Any,
) -> Config:
    """Build, validate and add a new ``Config`` row (not yet flushed)."""
    row = Config(key=key, value={}, **columns)
    await write_config_value(session, row, config_type=config_type, value=value)
    session.add(row)
    return row
