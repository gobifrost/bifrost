"""Minimal CLI-side mirror of integration create/update DTOs."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


ConfigItemType = Literal["string", "int", "bool", "json", "secret"]


class ConfigSchemaItem(BaseModel):
    """Metadata for a single configuration item (CLI mirror)."""

    model_config = ConfigDict(from_attributes=True)

    key: str = Field(min_length=1, max_length=255, pattern=r"^[a-zA-Z0-9_]+$")
    type: ConfigItemType
    required: bool = Field(default=False)
    description: str | None = Field(default=None, max_length=500)
    options: list[str] | None = Field(default=None)
    option_labels: dict[str, str] | None = Field(
        default=None, description="Display labels keyed by stored option value"
    )

    @model_validator(mode="after")
    def validate_option_labels(self):
        if self.option_labels is not None:
            options = self.options or []
            if self.type != "string" or not options:
                raise ValueError("Choice labels require string options")
            if any(not value.strip() for value in options) or len(set(options)) != len(options):
                raise ValueError("Choice values must be nonempty and unique")
            if not set(self.option_labels).issubset(options):
                raise ValueError("Choice labels must reference declared options")
            if any(not label.strip() for label in self.option_labels.values()):
                raise ValueError("Choice labels must not be empty")
        return self



class IntegrationCreate(BaseModel):
    """Request model for creating an integration (CLI mirror)."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=50000)
    config_schema: list[ConfigSchemaItem] | None = Field(default=None)
    entity_id: str | None = Field(default=None, min_length=1, max_length=255)
    entity_id_name: str | None = Field(default=None, max_length=255)
    default_entity_id: str | None = Field(default=None, max_length=255)


class IntegrationUpdate(BaseModel):
    """Request model for updating an integration (CLI mirror)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=50000)
    list_entities_data_provider_id: UUID | None = Field(default=None)
    config_schema: list[ConfigSchemaItem] | None = Field(default=None)
    entity_id: str | None = Field(default=None, min_length=1, max_length=255)
    entity_id_name: str | None = Field(default=None, max_length=255)
    default_entity_id: str | None = Field(default=None, max_length=255)


class IntegrationMappingCreate(BaseModel):
    """Request model for creating an integration mapping (CLI mirror)."""

    organization_id: UUID
    entity_id: str = Field(min_length=1, max_length=255)
    entity_name: str | None = Field(default=None, max_length=255)
    oauth_token_id: UUID | None = Field(default=None)
    config: dict[str, Any] | None = Field(default=None)


class IntegrationMappingUpdate(BaseModel):
    """Request model for updating an integration mapping (CLI mirror)."""

    entity_id: str | None = Field(default=None, min_length=1, max_length=255)
    entity_name: str | None = Field(default=None, max_length=255)
    oauth_token_id: UUID | None = Field(default=None)
    config: dict[str, Any] | None = Field(default=None)
