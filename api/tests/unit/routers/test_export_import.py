"""Tests for export/import models and serialization."""

import json
from uuid import UUID

import pytest

from src.models.contracts.export_import import (
    BulkExportRequest,
    ConfigExportFile,
    ConfigExportItem,
    ImportResult,
    ImportResultItem,
    IntegrationExportFile,
    IntegrationMappingExportItem,
    KnowledgeExportFile,
    KnowledgeExportItem,
    OAuthProviderExportItem,
    TableExportFile,
    TableExportItem,
)
from src.routers.export_import import _parse_target_org


class TestKnowledgeExport:
    def test_knowledge_export_serialization(self):
        """KnowledgeExportFile serializes correctly."""
        export = KnowledgeExportFile(
            item_count=1,
            items=[{
                "namespace": "docs",
                "key": "intro",
                "content": "Hello world",
                "metadata": {"source": "manual"},
                "organization_id": None,
            }],
        )
        data = json.loads(export.model_dump_json())
        assert data["bifrost_export_version"] == "1.0"
        assert data["entity_type"] == "knowledge"
        assert data["contains_encrypted_values"] is False
        assert len(data["items"]) == 1
        assert data["items"][0]["namespace"] == "docs"
        assert data["items"][0]["content"] == "Hello world"

    def test_knowledge_export_roundtrip(self):
        """Export JSON can be parsed back into model."""
        export = KnowledgeExportFile(
            item_count=2,
            items=[
                {"namespace": "docs", "key": "a", "content": "content a", "metadata": {}},
                {"namespace": "docs", "key": "b", "content": "content b", "metadata": {"tag": "test"}},
            ],
        )
        json_str = export.model_dump_json()
        parsed = KnowledgeExportFile.model_validate_json(json_str)
        assert len(parsed.items) == 2
        assert parsed.items[1].metadata == {"tag": "test"}


class TestConfigExport:
    def test_config_export_with_secrets(self):
        """Config export marks contains_encrypted_values when secrets exist."""
        export = ConfigExportFile(
            contains_encrypted_values=True,
            item_count=2,
            items=[
                {"key": "api_url", "value": "https://api.example.com", "config_type": "string"},
                {"key": "api_key", "value": "encrypted-value-abc", "config_type": "secret"},
            ],
        )
        data = json.loads(export.model_dump_json())
        assert data["contains_encrypted_values"] is True
        assert data["items"][1]["config_type"] == "secret"

    def test_config_export_with_integration_ref(self):
        """Config items reference integration by name, not ID."""
        export = ConfigExportFile(
            item_count=1,
            items=[{
                "key": "tenant_id",
                "value": "abc-123",
                "config_type": "string",
                "integration_name": "Microsoft Partner",
            }],
        )
        data = json.loads(export.model_dump_json())
        assert data["items"][0]["integration_name"] == "Microsoft Partner"


class TestTableExport:
    def test_table_export_with_documents(self):
        """Table export includes documents."""
        export = TableExportFile(
            item_count=1,
            items=[{
                "name": "customers",
                "description": "Customer records",
                "schema": {"columns": [{"name": "email", "type": "string"}]},
                "documents": [
                    {"id": "doc-1", "data": {"email": "a@b.com", "name": "Alice"}},
                    {"id": "doc-2", "data": {"email": "c@d.com", "name": "Bob"}},
                ],
            }],
        )
        data = json.loads(export.model_dump_json())
        assert len(data["items"][0]["documents"]) == 2


class TestIntegrationExport:
    def test_integration_export_full(self):
        """Integration export includes schema, mappings, OAuth, and config."""
        export = IntegrationExportFile(
            contains_encrypted_values=True,
            item_count=1,
            items=[{
                "name": "Microsoft Partner",
                "entity_id": "tenant_id",
                "entity_id_name": "Tenant ID",
                "config_schema": [
                    {"key": "api_url", "type": "string", "required": True, "position": 0},
                    {"key": "api_key", "type": "secret", "required": True, "position": 1},
                ],
                "mappings": [
                    {"entity_id": "abc-123", "entity_name": "Contoso", "config": {"api_url": "https://api.contoso.com"}},
                ],
                "oauth_provider": {
                    "provider_name": "microsoft",
                    "client_id": "client-123",
                    "encrypted_client_secret": "encrypted-base64-value",
                    "authorization_url": "https://login.microsoft.com/authorize",
                    "token_url": "https://login.microsoft.com/token",
                    "scopes": ["openid", "profile"],
                },
                "default_config": {"api_url": "https://default-api.example.com"},
            }],
        )
        data = json.loads(export.model_dump_json())
        assert data["items"][0]["name"] == "Microsoft Partner"
        assert len(data["items"][0]["config_schema"]) == 2
        assert data["items"][0]["oauth_provider"]["client_id"] == "client-123"


class TestBulkExport:
    def test_bulk_export_request_model(self):
        """BulkExportRequest accepts optional ID lists."""
        req = BulkExportRequest(
            knowledge_ids=["id1", "id2"],
            config_ids=["id3"],
        )
        assert len(req.knowledge_ids) == 2
        assert len(req.table_ids) == 0
        assert len(req.config_ids) == 1


class TestImportModels:
    def test_import_result_model(self):
        """ImportResult tracks created/updated/skipped/errors."""
        result = ImportResult(
            entity_type="knowledge",
            created=5,
            updated=2,
            skipped=1,
            details=[
                ImportResultItem(name="docs/intro", status="created"),
                ImportResultItem(name="docs/faq", status="error", error="Invalid content"),
            ],
        )
        assert result.created == 5
        assert result.details[1].error == "Invalid content"


class TestOrganizationNameBackwardsCompat:
    """Verify old exports (without organization_name) parse correctly."""

    def test_knowledge_without_org_name(self):
        """Old knowledge export without organization_name field parses with None."""
        old_json = json.dumps({
            "bifrost_export_version": "1.0",
            "entity_type": "knowledge",
            "item_count": 1,
            "items": [{
                "namespace": "docs",
                "key": "intro",
                "content": "Hello",
                "metadata": {},
                "organization_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            }],
        })
        parsed = KnowledgeExportFile.model_validate_json(old_json)
        assert len(parsed.items) == 1
        assert parsed.items[0].organization_name is None
        assert parsed.items[0].organization_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_table_without_org_name(self):
        """Old table export without organization_name parses with None."""
        old_json = json.dumps({
            "bifrost_export_version": "1.0",
            "entity_type": "tables",
            "item_count": 1,
            "items": [{"name": "t1", "organization_id": "abc-123", "documents": []}],
        })
        parsed = TableExportFile.model_validate_json(old_json)
        assert parsed.items[0].organization_name is None

    def test_config_without_org_name(self):
        """Old config export without organization_name parses with None."""
        old_json = json.dumps({
            "bifrost_export_version": "1.0",
            "entity_type": "configs",
            "item_count": 1,
            "items": [{"key": "k", "value": "v", "config_type": "string"}],
        })
        parsed = ConfigExportFile.model_validate_json(old_json)
        assert parsed.items[0].organization_name is None

    def test_integration_mapping_without_org_name(self):
        """Old integration mapping without organization_name parses with None."""
        old_json = json.dumps({
            "bifrost_export_version": "1.0",
            "entity_type": "integrations",
            "item_count": 1,
            "items": [{
                "name": "Test",
                "config_schema": [],
                "mappings": [{"entity_id": "e1", "organization_id": "org-1", "config": {}}],
                "default_config": {},
            }],
        })
        parsed = IntegrationExportFile.model_validate_json(old_json)
        assert parsed.items[0].mappings[0].organization_name is None


class TestOrganizationNameSerialization:
    """Verify organization_name appears in export JSON when set."""

    def test_knowledge_with_org_name(self):
        """Knowledge export item includes organization_name in JSON."""
        item = KnowledgeExportItem(
            namespace="docs",
            content="Hello",
            organization_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            organization_name="Contoso",
        )
        data = json.loads(item.model_dump_json())
        assert data["organization_name"] == "Contoso"

    def test_table_with_org_name(self):
        """Table export item includes organization_name in JSON."""
        item = TableExportItem(name="t1", organization_name="Acme Corp")
        data = json.loads(item.model_dump_json())
        assert data["organization_name"] == "Acme Corp"

    def test_config_with_org_name(self):
        """Config export item includes organization_name in JSON."""
        item = ConfigExportItem(
            key="k", value="v", config_type="string",
            organization_name="Contoso",
        )
        data = json.loads(item.model_dump_json())
        assert data["organization_name"] == "Contoso"

    def test_mapping_with_org_name(self):
        """Integration mapping export item includes organization_name in JSON."""
        item = IntegrationMappingExportItem(
            entity_id="e1",
            organization_id="org-1",
            organization_name="Contoso",
        )
        data = json.loads(item.model_dump_json())
        assert data["organization_name"] == "Contoso"

    def test_oauth_provider_with_org_name(self):
        """OAuth provider export item includes organization_name in JSON."""
        item = OAuthProviderExportItem(
            provider_name="ms",
            client_id="c1",
            encrypted_client_secret="secret",
            organization_name="Contoso",
        )
        data = json.loads(item.model_dump_json())
        assert data["organization_name"] == "Contoso"

    def test_knowledge_roundtrip_with_org_name(self):
        """Knowledge export with org_name survives serialization roundtrip."""
        export = KnowledgeExportFile(
            item_count=1,
            items=[{
                "namespace": "docs",
                "content": "Hello",
                "organization_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "organization_name": "Contoso",
            }],
        )
        json_str = export.model_dump_json()
        parsed = KnowledgeExportFile.model_validate_json(json_str)
        assert parsed.items[0].organization_name == "Contoso"


class TestParseTargetOrg:
    """Unit tests for _parse_target_org helper."""

    def test_none_returns_resolve_from_file(self):
        """None input means resolve from file."""
        override, force_global = _parse_target_org(None)
        assert override is None
        assert force_global is False

    def test_empty_string_returns_force_global(self):
        """Empty string means force global scope."""
        override, force_global = _parse_target_org("")
        assert override is None
        assert force_global is True

    def test_global_string_returns_force_global(self):
        """Global sentinel means force global scope over multipart forms."""
        override, force_global = _parse_target_org("global")
        assert override is None
        assert force_global is True

    def test_uuid_string_returns_override(self):
        """UUID string returns the parsed UUID."""
        test_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        override, force_global = _parse_target_org(test_uuid)
        assert override == UUID(test_uuid)
        assert force_global is False

    def test_invalid_uuid_raises(self):
        """Invalid UUID string raises ValueError."""
        with pytest.raises(ValueError):
            _parse_target_org("not-a-uuid")
