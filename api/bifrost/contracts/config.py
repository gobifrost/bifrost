"""Minimal CLI-side mirror of config create/update DTOs."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from bifrost.contracts.enums import ConfigType


class ConfigCreate(BaseModel):
    """Input for creating a config (CLI mirror).

    ``value`` is a string for every :class:`ConfigType`, matching the server's
    ``SetConfigRequest`` wire contract. Non-string types travel serialized and
    are coerced on read using ``config_type``: ``int``/``bool`` are cast and
    ``json`` is parsed.
    """

    key: str = Field(max_length=255)
    value: str
    config_type: ConfigType = Field(default=ConfigType.STRING)
    description: str | None = Field(default=None)
    organization_id: UUID | None = None


class ConfigUpdate(BaseModel):
    """Input for updating a config (CLI mirror).

    ``value`` is a string for every type — see :class:`ConfigCreate`.
    """

    value: str | None = None
    config_type: ConfigType | None = None
    description: str | None = None
