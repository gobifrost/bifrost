"""Pydantic models for export/import operations."""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


class ExportMetadata(BaseModel):
    """Header metadata for all export files."""
    bifrost_export_version: str = "1.0"
    entity_type: str = ""
    exported_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    contains_encrypted_values: bool = False
    item_count: int = 0


# --- Knowledge ---

class KnowledgeExportItem(BaseModel):
    namespace: str
    key: str | None = None
    content: str
    metadata: dict = Field(default_factory=dict)
    organization_id: str | None = None
    organization_name: str | None = None


class KnowledgeExportFile(ExportMetadata):
    entity_type: str = "knowledge"
    items: list[KnowledgeExportItem] = Field(default_factory=list)


# --- Tables ---

class DocumentExportItem(BaseModel):
    id: str
    data: dict[str, Any]


class TableExportItem(BaseModel):
    name: str
    description: str | None = None
    schema_def: dict | None = Field(None, alias="schema")
    organization_id: str | None = None
    organization_name: str | None = None
    documents: list[DocumentExportItem] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class TableExportFile(ExportMetadata):
    entity_type: str = "tables"
    items: list[TableExportItem] = Field(default_factory=list)


# --- Configs ---

class ConfigExportItem(BaseModel):
    key: str
    value: Any
    config_type: str  # STRING, INT, BOOL, JSON, SECRET
    description: str | None = None
    organization_id: str | None = None
    organization_name: str | None = None
    integration_name: str | None = None  # Reference by name, not ID


class ConfigExportFile(ExportMetadata):
    entity_type: str = "configs"
    items: list[ConfigExportItem] = Field(default_factory=list)


# --- Integrations ---

class ConfigSchemaExportItem(BaseModel):
    key: str
    type: str  # string, int, bool, json, secret
    required: bool = False
    description: str | None = None
    options: list[str] | None = None
    option_labels: dict[str, str] | None = None
    position: int = 0


class IntegrationMappingExportItem(BaseModel):
    organization_id: str | None = None
    organization_name: str | None = None
    entity_id: str
    entity_name: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class OAuthProviderExportItem(BaseModel):
    provider_name: str
    display_name: str | None = None
    oauth_flow_type: str = "authorization_code"
    client_id: str
    encrypted_client_secret: str  # Exported as base64 of encrypted bytes
    authorization_url: str | None = None
    token_url: str | None = None
    token_url_defaults: dict = Field(default_factory=dict)
    redirect_uri: str | None = None
    scopes: list[str] = Field(default_factory=list)
    organization_id: str | None = None
    organization_name: str | None = None


class IntegrationExportItem(BaseModel):
    name: str
    description: str | None = None
    notes: str | None = None
    entity_id: str | None = None
    entity_id_name: str | None = None
    default_entity_id: str | None = None
    list_entities_data_provider_name: str | None = None  # Reference by name
    config_schema: list[ConfigSchemaExportItem] = Field(default_factory=list)
    mappings: list[IntegrationMappingExportItem] = Field(default_factory=list)
    oauth_provider: OAuthProviderExportItem | None = None
    default_config: dict[str, Any] = Field(default_factory=dict)


class IntegrationExportFile(ExportMetadata):
    entity_type: str = "integrations"
    items: list[IntegrationExportItem] = Field(default_factory=list)


# --- Bulk ---

class BulkExportRequest(BaseModel):
    knowledge_ids: list[str] = Field(default_factory=list)
    table_ids: list[str] = Field(default_factory=list)
    config_ids: list[str] = Field(default_factory=list)
    integration_ids: list[str] = Field(default_factory=list)


# --- Import ---

class ImportResultItem(BaseModel):
    name: str
    status: Literal["created", "updated", "skipped", "error"]
    error: str | None = None


class ImportResult(BaseModel):
    entity_type: str
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    warnings: list[str] = Field(default_factory=list)
    details: list[ImportResultItem] = Field(default_factory=list)
