"""Tests for manifest parser."""
import pytest
from uuid import uuid4

import yaml


@pytest.fixture
def sample_manifest():
    """A valid manifest dict."""
    org_id = str(uuid4())
    role_id = str(uuid4())
    wf_id = str(uuid4())
    form_id = str(uuid4())
    return {
        "organizations": [{"id": org_id, "name": "TestOrg"}],
        "roles": [{"id": role_id, "name": "admin", "organization_id": org_id}],
        "workflows": {
            wf_id: {
                "id": wf_id,
                "name": "my_workflow",
                "path": "workflows/my_workflow.py",
                "function_name": "my_workflow",
                "type": "workflow",
                "organization_id": org_id,
                "roles": [role_id],
                "access_level": "role_based",
                "endpoint_enabled": False,
                "timeout_seconds": 1800,
            },
        },
        "forms": {
            form_id: {
                "id": form_id,
                "name": "my_form",
                "path": "forms/my_form.form.yaml",
                "organization_id": org_id,
                "roles": [role_id],
                "access_level": "role_based",
            },
        },
        "agents": {},
        "apps": {},
        "_wf_id": wf_id,
        "_form_id": form_id,
    }


def test_parse_manifest_from_yaml(sample_manifest):
    """Parse a YAML string into a Manifest object."""
    from bifrost.manifest import parse_manifest

    yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
    manifest = parse_manifest(yaml_str)
    wf_id = sample_manifest["_wf_id"]
    assert wf_id in manifest.workflows
    assert manifest.workflows[wf_id].name == "my_workflow"
    assert manifest.workflows[wf_id].path == "workflows/my_workflow.py"
    assert manifest.workflows[wf_id].function_name == "my_workflow"
    assert manifest.workflows[wf_id].type == "workflow"


def test_serialize_manifest(sample_manifest):
    """Serialize a Manifest back to YAML string."""
    from bifrost.manifest import parse_manifest, serialize_manifest

    yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
    manifest = parse_manifest(yaml_str)
    output = serialize_manifest(manifest)
    # Should be valid YAML
    reparsed = yaml.safe_load(output)
    assert "workflows" in reparsed
    wf_id = sample_manifest["_wf_id"]
    assert wf_id in reparsed["workflows"]


def test_serialize_manifest_round_trip_stability(sample_manifest):
    """Serialize → parse → serialize should produce identical output (no false conflicts)."""
    from bifrost.manifest import parse_manifest, serialize_manifest

    yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
    manifest = parse_manifest(yaml_str)
    output1 = serialize_manifest(manifest)
    manifest2 = parse_manifest(output1)
    output2 = serialize_manifest(manifest2)
    assert output1 == output2, "Round-trip serialization must be stable"


def test_serialize_manifest_excludes_defaults():
    """Default-valued fields should be omitted from serialized YAML."""
    from bifrost.manifest import parse_manifest, serialize_manifest

    yaml_str = """
workflows:
  wf1:
    id: "11111111-1111-1111-1111-111111111111"
    path: workflows/wf1.py
    function_name: wf1
"""
    manifest = parse_manifest(yaml_str)
    output = serialize_manifest(manifest)
    data = yaml.safe_load(output)
    wf = data["workflows"]["wf1"]
    # Required fields present
    assert wf["id"] == "11111111-1111-1111-1111-111111111111"
    assert wf["path"] == "workflows/wf1.py"
    assert wf["function_name"] == "wf1"
    # Default-valued fields should be absent
    assert "type" not in wf  # default is "workflow"
    assert "access_level" not in wf  # default is "role_based"
    assert "endpoint_enabled" not in wf  # default is False
    assert "timeout_seconds" not in wf  # default is 1800
    assert "roles" not in wf  # default is []
    assert "tags" not in wf  # default is []
    assert "organization_id" not in wf  # default is None


def test_validate_manifest_broken_ref(sample_manifest):
    """Detect broken cross-references."""
    from bifrost.manifest import parse_manifest, validate_manifest

    # Form references a workflow UUID that exists — should be fine
    yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
    manifest = parse_manifest(yaml_str)
    errors = validate_manifest(manifest)
    assert len(errors) == 0


def test_validate_manifest_missing_org(sample_manifest):
    """Detect reference to non-existent organization."""
    from bifrost.manifest import parse_manifest, validate_manifest

    wf_id = sample_manifest["_wf_id"]
    sample_manifest["workflows"][wf_id]["organization_id"] = str(uuid4())
    yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
    manifest = parse_manifest(yaml_str)
    errors = validate_manifest(manifest)
    assert any("organization" in e.lower() for e in errors)


def test_validate_manifest_missing_role(sample_manifest):
    """Detect reference to non-existent role."""
    from bifrost.manifest import parse_manifest, validate_manifest

    wf_id = sample_manifest["_wf_id"]
    sample_manifest["workflows"][wf_id]["roles"] = [str(uuid4())]
    yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
    manifest = parse_manifest(yaml_str)
    errors = validate_manifest(manifest)
    assert any("role" in e.lower() for e in errors)


def test_empty_manifest():
    """Empty manifest should parse without error."""
    from bifrost.manifest import parse_manifest

    manifest = parse_manifest("")
    assert len(manifest.workflows) == 0
    assert len(manifest.forms) == 0


def test_get_entity_ids():
    """Get all entity UUIDs from manifest."""
    from bifrost.manifest import parse_manifest, get_all_entity_ids

    yaml_str = """
workflows:
  wf1:
    id: "11111111-1111-1111-1111-111111111111"
    path: workflows/wf1.py
    function_name: wf1
    type: workflow
forms:
  form1:
    id: "22222222-2222-2222-2222-222222222222"
    path: forms/form1.form.yaml
"""
    manifest = parse_manifest(yaml_str)
    ids = get_all_entity_ids(manifest)
    assert "11111111-1111-1111-1111-111111111111" in ids
    assert "22222222-2222-2222-2222-222222222222" in ids


def test_get_paths():
    """Get all file paths from manifest."""
    from bifrost.manifest import parse_manifest, get_all_paths

    yaml_str = """
workflows:
  wf1:
    id: "11111111-1111-1111-1111-111111111111"
    path: workflows/wf1.py
    function_name: wf1
    type: workflow
forms:
  form1:
    id: "22222222-2222-2222-2222-222222222222"
    path: forms/form1.form.yaml
"""
    manifest = parse_manifest(yaml_str)
    paths = get_all_paths(manifest)
    assert "workflows/wf1.py" in paths
    assert "forms/form1.form.yaml" in paths


# =============================================================================
# Split manifest (per-entity-type files) tests
# =============================================================================


class TestSerializeManifestDir:
    def test_produces_correct_files(self, sample_manifest):
        """serialize_manifest_dir produces one file per non-empty entity type."""
        from bifrost.manifest import parse_manifest, serialize_manifest_dir

        yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        files = serialize_manifest_dir(manifest)

        assert "organizations.yaml" in files
        assert "roles.yaml" in files
        assert "workflows.yaml" in files
        assert "forms.yaml" in files

        # Verify YAML content is correct
        wf_data = yaml.safe_load(files["workflows.yaml"])
        assert "workflows" in wf_data
        wf_id = sample_manifest["_wf_id"]
        assert wf_id in wf_data["workflows"]

        org_data = yaml.safe_load(files["organizations.yaml"])
        assert "organizations" in org_data
        assert len(org_data["organizations"]) == 1

    def test_skips_empty_entity_types(self):
        """Empty entity types should not produce files."""
        from bifrost.manifest import Manifest, serialize_manifest_dir, ManifestWorkflow

        manifest = Manifest(
            workflows={
                "wf1": ManifestWorkflow(
                    id="11111111-1111-1111-1111-111111111111",
                    path="workflows/wf1.py",
                    function_name="wf1",
                )
            }
        )
        files = serialize_manifest_dir(manifest)

        assert "workflows.yaml" in files
        assert "forms.yaml" not in files
        assert "agents.yaml" not in files
        assert "apps.yaml" not in files
        assert "organizations.yaml" not in files
        assert "roles.yaml" not in files


class TestParseManifestDir:
    def test_round_trip(self, sample_manifest):
        """serialize_manifest_dir → parse_manifest_dir produces equivalent manifest."""
        from bifrost.manifest import parse_manifest, serialize_manifest_dir, parse_manifest_dir

        yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
        original = parse_manifest(yaml_str)
        files = serialize_manifest_dir(original)
        restored = parse_manifest_dir(files)

        assert len(restored.workflows) == len(original.workflows)
        assert len(restored.forms) == len(original.forms)
        assert len(restored.organizations) == len(original.organizations)
        assert len(restored.roles) == len(original.roles)

        for name in original.workflows:
            assert name in restored.workflows
            assert restored.workflows[name].id == original.workflows[name].id
            assert restored.workflows[name].path == original.workflows[name].path

    def test_missing_files(self):
        """Partial set of files should still work (missing = empty)."""
        from bifrost.manifest import parse_manifest_dir

        files = {
            "workflows.yaml": """
workflows:
  wf1:
    id: "11111111-1111-1111-1111-111111111111"
    path: workflows/wf1.py
    function_name: wf1
"""
        }
        manifest = parse_manifest_dir(files)
        assert len(manifest.workflows) == 1
        assert len(manifest.forms) == 0
        assert len(manifest.agents) == 0
        assert len(manifest.apps) == 0


class TestReadWriteManifestDir:
    def test_write_and_read_split(self, tmp_path, sample_manifest):
        """write_manifest_to_dir → read_manifest_from_dir round-trip."""
        from bifrost.manifest import (
            parse_manifest,
            write_manifest_to_dir,
            read_manifest_from_dir,
        )

        yaml_str = yaml.dump(sample_manifest, default_flow_style=False)
        original = parse_manifest(yaml_str)

        bifrost_dir = tmp_path / ".bifrost"
        write_manifest_to_dir(original, bifrost_dir)

        # Verify files on disk
        assert (bifrost_dir / "workflows.yaml").exists()
        assert (bifrost_dir / "forms.yaml").exists()
        assert (bifrost_dir / "organizations.yaml").exists()
        assert (bifrost_dir / "roles.yaml").exists()
        assert not (bifrost_dir / "metadata.yaml").exists()

        restored = read_manifest_from_dir(bifrost_dir)
        assert len(restored.workflows) == len(original.workflows)
        assert len(restored.forms) == len(original.forms)

    def test_write_cleans_legacy_file(self, tmp_path):
        """write_manifest_to_dir removes legacy metadata.yaml if present."""
        from bifrost.manifest import Manifest, write_manifest_to_dir

        bifrost_dir = tmp_path / ".bifrost"
        bifrost_dir.mkdir()
        legacy = bifrost_dir / "metadata.yaml"
        legacy.write_text("workflows: {}")

        write_manifest_to_dir(Manifest(), bifrost_dir)

        assert not legacy.exists()

    def test_write_removes_stale_split_files(self, tmp_path):
        """write_manifest_to_dir removes split files for now-empty entity types."""
        from bifrost.manifest import Manifest, ManifestWorkflow, write_manifest_to_dir

        bifrost_dir = tmp_path / ".bifrost"

        # Write with workflows
        manifest_with_wf = Manifest(
            workflows={
                "wf1": ManifestWorkflow(
                    id="11111111-1111-1111-1111-111111111111",
                    path="workflows/wf1.py",
                    function_name="wf1",
                )
            }
        )
        write_manifest_to_dir(manifest_with_wf, bifrost_dir)
        assert (bifrost_dir / "workflows.yaml").exists()

        # Write empty manifest — workflows.yaml should be removed
        write_manifest_to_dir(Manifest(), bifrost_dir)
        assert not (bifrost_dir / "workflows.yaml").exists()

    def test_read_split_format(self, tmp_path):
        """read_manifest_from_dir detects and reads split files."""
        from bifrost.manifest import read_manifest_from_dir

        bifrost_dir = tmp_path / ".bifrost"
        bifrost_dir.mkdir()
        (bifrost_dir / "workflows.yaml").write_text("""
workflows:
  wf1:
    id: "11111111-1111-1111-1111-111111111111"
    path: workflows/wf1.py
    function_name: wf1
""")
        manifest = read_manifest_from_dir(bifrost_dir)
        assert "wf1" in manifest.workflows

    def test_read_legacy_format(self, tmp_path):
        """read_manifest_from_dir falls back to legacy metadata.yaml."""
        from bifrost.manifest import read_manifest_from_dir

        bifrost_dir = tmp_path / ".bifrost"
        bifrost_dir.mkdir()
        (bifrost_dir / "metadata.yaml").write_text("""
workflows:
  wf1:
    id: "11111111-1111-1111-1111-111111111111"
    path: workflows/wf1.py
    function_name: wf1
""")
        manifest = read_manifest_from_dir(bifrost_dir)
        assert "wf1" in manifest.workflows

    def test_read_empty_directory(self, tmp_path):
        """Empty directory returns empty Manifest."""
        from bifrost.manifest import read_manifest_from_dir

        bifrost_dir = tmp_path / ".bifrost"
        bifrost_dir.mkdir()
        manifest = read_manifest_from_dir(bifrost_dir)
        assert len(manifest.workflows) == 0
        assert len(manifest.forms) == 0

    def test_read_missing_directory(self, tmp_path):
        """Missing directory returns empty Manifest."""
        from bifrost.manifest import read_manifest_from_dir

        manifest = read_manifest_from_dir(tmp_path / "nonexistent")
        assert len(manifest.workflows) == 0


# =============================================================================
# New entity types: Integrations, Configs, Tables, Knowledge, Events
# =============================================================================


@pytest.fixture
def full_manifest_data():
    """Manifest data with all entity types populated."""
    org_id = str(uuid4())
    role_id = str(uuid4())
    wf_id = str(uuid4())
    dp_wf_id = str(uuid4())  # data provider workflow
    form_id = str(uuid4())
    agent_id = str(uuid4())
    app_id = str(uuid4())
    integ_id = str(uuid4())
    config_id = str(uuid4())
    secret_config_id = str(uuid4())
    table_id = str(uuid4())
    oauth_token_id = str(uuid4())
    event_source_id = str(uuid4())
    event_sub_id = str(uuid4())

    return {
        "org_id": org_id,
        "role_id": role_id,
        "wf_id": wf_id,
        "dp_wf_id": dp_wf_id,
        "form_id": form_id,
        "agent_id": agent_id,
        "app_id": app_id,
        "integ_id": integ_id,
        "config_id": config_id,
        "secret_config_id": secret_config_id,
        "oauth_token_id": oauth_token_id,
        "table_id": table_id,
        "event_source_id": event_source_id,
        "event_sub_id": event_sub_id,
        "manifest": {
            "organizations": [{"id": org_id, "name": "TestOrg"}],
            "roles": [{"id": role_id, "name": "admin"}],
            "workflows": {
                wf_id: {
                    "id": wf_id,
                    "name": "my_workflow",
                    "path": "workflows/my_workflow.py",
                    "function_name": "my_workflow",
                },
                dp_wf_id: {
                    "id": dp_wf_id,
                    "name": "list_entities_dp",
                    "path": "workflows/list_entities_dp.py",
                    "function_name": "list_entities_dp",
                    "type": "data_provider",
                },
            },
            "integrations": {
                integ_id: {
                    "id": integ_id,
                    "name": "HaloPSA",
                    "entity_id": "tenant_id",
                    "entity_id_name": "Tenant",
                    "default_entity_id": "default-tenant",
                    "list_entities_data_provider_id": dp_wf_id,
                    "config_schema": [
                        {
                            "key": "api_url",
                            "type": "string",
                            "required": True,
                            "description": "HaloPSA API URL",
                            "position": 0,
                        },
                        {
                            "key": "api_key",
                            "type": "secret",
                            "required": True,
                            "description": "API Key",
                            "position": 1,
                        },
                    ],
                    "oauth_provider": {
                        "provider_name": "halopsa",
                        "display_name": "HaloPSA OAuth",
                        "oauth_flow_type": "client_credentials",
                        "client_id": "__NEEDS_SETUP__",
                        "authorization_url": "https://halo.example.com/auth",
                        "token_url": "https://halo.example.com/token",
                        "scopes": ["all"],
                    },
                    "mappings": [
                        {
                            "organization_id": org_id,
                            "entity_id": "tenant-123",
                            "entity_name": "My Tenant",
                            "oauth_token_id": oauth_token_id,
                        },
                    ],
                },
            },
            "configs": {
                config_id: {
                    "id": config_id,
                    "integration_id": integ_id,
                    "key": "halopsa/api_url",
                    "config_type": "string",
                    "description": "HaloPSA API URL",
                    "organization_id": org_id,
                    "value": "https://api.halopsa.com",
                },
                secret_config_id: {
                    "id": secret_config_id,
                    "integration_id": integ_id,
                    "key": "halopsa/api_key",
                    "config_type": "secret",
                    "description": "API Key",
                    "organization_id": org_id,
                    "value": None,
                },
            },
            "tables": {
                table_id: {
                    "id": table_id,
                    "name": "ticket_cache",
                    "description": "Cached ticket data",
                    "organization_id": org_id,
                    "application_id": app_id,
                    "schema": {
                        "columns": [
                            {"name": "ticket_id", "type": "string"},
                            {"name": "subject", "type": "string"},
                        ]
                    },
                },
            },
            "events": {
                event_source_id: {
                    "id": event_source_id,
                    "name": "Ticket Webhook",
                    "source_type": "webhook",
                    "organization_id": org_id,
                    "is_active": True,
                    "adapter_name": "halopsa",
                    "webhook_integration_id": integ_id,
                    "webhook_config": {"verify_ssl": True},
                    "subscriptions": [
                        {
                            "id": event_sub_id,
                            "workflow_id": wf_id,
                            "event_type": "ticket.created",
                            "input_mapping": {"ticket_id": "$.data.id"},
                            "is_active": True,
                        },
                    ],
                },
            },
            "forms": {
                form_id: {
                    "id": form_id,
                    "name": "my_form",
                    "path": "forms/my_form.form.yaml",
                    "organization_id": org_id,
                    "roles": [role_id],
                },
            },
            "agents": {
                agent_id: {
                    "id": agent_id,
                    "name": "my_agent",
                    "path": "agents/my_agent.agent.yaml",
                    "organization_id": org_id,
                    "roles": [role_id],
                },
            },
            "apps": {
                app_id: {
                    "id": app_id,
                    "path": "apps/my-app",
                    "name": "My App",
                    "description": "Test app",
                    "dependencies": {"recharts": "2.12"},
                    "organization_id": org_id,
                    "roles": [role_id],
                },
            },
        },
    }


class TestIntegrationManifest:
    """Tests for integration manifest models."""

    def test_parse_integration(self, full_manifest_data):
        """Parse integration with config_schema, oauth, and mappings."""
        from bifrost.manifest import parse_manifest

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        manifest = parse_manifest(yaml_str)

        integ_id = full_manifest_data["integ_id"]
        assert integ_id in manifest.integrations
        integ = manifest.integrations[integ_id]
        assert integ.name == "HaloPSA"
        assert integ.id == full_manifest_data["integ_id"]
        assert integ.entity_id == "tenant_id"
        assert integ.entity_id_name == "Tenant"
        assert integ.default_entity_id == "default-tenant"
        assert integ.list_entities_data_provider_id == full_manifest_data["dp_wf_id"]

        # Config schema
        assert len(integ.config_schema) == 2
        assert integ.config_schema[0].key == "api_url"
        assert integ.config_schema[0].type == "string"
        assert integ.config_schema[0].required is True
        assert integ.config_schema[1].key == "api_key"
        assert integ.config_schema[1].type == "secret"

        # OAuth provider
        assert integ.oauth_provider is not None
        assert integ.oauth_provider.provider_name == "halopsa"
        assert integ.oauth_provider.oauth_flow_type == "client_credentials"
        assert integ.oauth_provider.client_id == "__NEEDS_SETUP__"
        assert integ.oauth_provider.scopes == ["all"]

        # Mappings
        assert len(integ.mappings) == 1
        assert integ.mappings[0].entity_id == "tenant-123"
        assert integ.mappings[0].organization_id == full_manifest_data["org_id"]
        assert integ.mappings[0].oauth_token_id == full_manifest_data["oauth_token_id"]

    def test_integration_round_trip(self, full_manifest_data):
        """Integration survives serialize → parse round-trip."""
        from bifrost.manifest import parse_manifest, serialize_manifest

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        original = parse_manifest(yaml_str)
        output = serialize_manifest(original)
        restored = parse_manifest(output)

        integ_id = full_manifest_data["integ_id"]
        integ_orig = original.integrations[integ_id]
        integ_rest = restored.integrations[integ_id]
        assert integ_rest.id == integ_orig.id
        assert integ_rest.entity_id == integ_orig.entity_id
        assert len(integ_rest.config_schema) == len(integ_orig.config_schema)
        assert integ_rest.oauth_provider is not None
        assert integ_orig.oauth_provider is not None
        assert integ_rest.oauth_provider.provider_name == integ_orig.oauth_provider.provider_name
        assert len(integ_rest.mappings) == len(integ_orig.mappings)

    def test_integration_defaults_omitted(self):
        """Integration with defaults only serializes non-default fields."""
        from bifrost.manifest import Manifest, ManifestIntegration, serialize_manifest

        manifest = Manifest(
            integrations={
                "bare": ManifestIntegration(id="11111111-1111-1111-1111-111111111111")
            }
        )
        output = serialize_manifest(manifest)
        data = yaml.safe_load(output)
        integ = data["integrations"]["bare"]
        assert integ["id"] == "11111111-1111-1111-1111-111111111111"
        assert "config_schema" not in integ
        assert "oauth_provider" not in integ
        assert "mappings" not in integ
        assert "entity_id" not in integ

    def test_mapping_oauth_token_id_round_trip(self):
        """Mapping oauth_token_id survives serialize → parse round-trip."""
        from bifrost.manifest import (
            Manifest, ManifestIntegration, ManifestIntegrationMapping,
            serialize_manifest, parse_manifest,
        )

        token_id = str(uuid4())
        manifest = Manifest(
            integrations={
                "TestInteg": ManifestIntegration(
                    id=str(uuid4()),
                    mappings=[
                        ManifestIntegrationMapping(
                            entity_id="tenant-1",
                            oauth_token_id=token_id,
                        ),
                    ],
                ),
            },
        )
        output = serialize_manifest(manifest)
        restored = parse_manifest(output)
        assert restored.integrations["TestInteg"].mappings[0].oauth_token_id == token_id

    def test_integration_split_file(self, full_manifest_data):
        """Integrations serialize to integrations.yaml in split format."""
        from bifrost.manifest import parse_manifest, serialize_manifest_dir, parse_manifest_dir

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        files = serialize_manifest_dir(manifest)

        assert "integrations.yaml" in files
        integ_data = yaml.safe_load(files["integrations.yaml"])
        assert "integrations" in integ_data
        integ_id = full_manifest_data["integ_id"]
        assert integ_id in integ_data["integrations"]

        # Round-trip through split format
        restored = parse_manifest_dir(files)
        assert integ_id in restored.integrations
        assert restored.integrations[integ_id].id == integ_id


class TestConfigManifest:
    """Tests for config manifest models."""

    def test_parse_config(self, full_manifest_data):
        """Parse config entries including secret redaction."""
        from bifrost.manifest import parse_manifest

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        manifest = parse_manifest(yaml_str)

        config_id = full_manifest_data["config_id"]
        secret_config_id = full_manifest_data["secret_config_id"]

        assert config_id in manifest.configs
        cfg = manifest.configs[config_id]
        assert cfg.id == config_id
        assert cfg.config_type == "string"
        assert cfg.value == "https://api.halopsa.com"
        assert cfg.integration_id == full_manifest_data["integ_id"]
        assert cfg.organization_id == full_manifest_data["org_id"]

        # Secret config has null value
        secret_cfg = manifest.configs[secret_config_id]
        assert secret_cfg.config_type == "secret"
        assert secret_cfg.value is None

    def test_config_round_trip(self, full_manifest_data):
        """Configs survive serialize → parse round-trip."""
        from bifrost.manifest import parse_manifest, serialize_manifest

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        original = parse_manifest(yaml_str)
        output = serialize_manifest(original)
        restored = parse_manifest(output)

        assert len(restored.configs) == len(original.configs)
        for key in original.configs:
            assert key in restored.configs
            assert restored.configs[key].id == original.configs[key].id
            assert restored.configs[key].config_type == original.configs[key].config_type

    def test_config_split_file(self, full_manifest_data):
        """Configs serialize to configs.yaml in split format."""
        from bifrost.manifest import parse_manifest, serialize_manifest_dir

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        files = serialize_manifest_dir(manifest)

        assert "configs.yaml" in files
        cfg_data = yaml.safe_load(files["configs.yaml"])
        assert "configs" in cfg_data
        assert full_manifest_data["config_id"] in cfg_data["configs"]


class TestTableManifest:
    """Tests for table manifest models with schema alias."""

    def test_parse_table_with_schema_alias(self, full_manifest_data):
        """Parse table with 'schema' alias → table_schema field."""
        from bifrost.manifest import parse_manifest

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        manifest = parse_manifest(yaml_str)

        table_id = full_manifest_data["table_id"]
        assert table_id in manifest.tables
        table = manifest.tables[table_id]
        assert table.id == table_id
        assert table.name == "ticket_cache"
        assert table.description == "Cached ticket data"
        assert table.organization_id == full_manifest_data["org_id"]
        assert table.application_id == full_manifest_data["app_id"]
        assert table.table_schema is not None
        assert "columns" in table.table_schema
        assert len(table.table_schema["columns"]) == 2

    def test_table_serializes_as_schema(self, full_manifest_data):
        """Table serializes table_schema as 'schema' in YAML (via alias)."""
        from bifrost.manifest import parse_manifest, serialize_manifest

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        output = serialize_manifest(manifest)
        data = yaml.safe_load(output)

        table_id = full_manifest_data["table_id"]
        table_data = data["tables"][table_id]
        assert "schema" in table_data
        assert "table_schema" not in table_data

    def test_table_round_trip(self, full_manifest_data):
        """Tables survive serialize → parse round-trip (alias preserved)."""
        from bifrost.manifest import parse_manifest, serialize_manifest

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        original = parse_manifest(yaml_str)
        output = serialize_manifest(original)
        restored = parse_manifest(output)

        table_id = full_manifest_data["table_id"]
        assert table_id in restored.tables
        assert restored.tables[table_id].table_schema == original.tables[table_id].table_schema

    def test_table_split_file(self, full_manifest_data):
        """Tables serialize to tables.yaml in split format."""
        from bifrost.manifest import parse_manifest, serialize_manifest_dir

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        files = serialize_manifest_dir(manifest)

        assert "tables.yaml" in files
        table_data = yaml.safe_load(files["tables.yaml"])
        assert "tables" in table_data
        table_id = full_manifest_data["table_id"]
        assert table_id in table_data["tables"]
        # Alias should appear in YAML
        assert "schema" in table_data["tables"][table_id]


class TestEventManifest:
    """Tests for event source + subscription manifest models."""

    def test_parse_event_source(self, full_manifest_data):
        """Parse event source with nested subscriptions."""
        from bifrost.manifest import parse_manifest

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        manifest = parse_manifest(yaml_str)

        es_id = full_manifest_data["event_source_id"]
        assert es_id in manifest.events
        evt = manifest.events[es_id]
        assert evt.id == es_id
        assert evt.name == "Ticket Webhook"
        assert evt.source_type == "webhook"
        assert evt.organization_id == full_manifest_data["org_id"]
        assert evt.adapter_name == "halopsa"
        assert evt.webhook_integration_id == full_manifest_data["integ_id"]
        assert evt.webhook_config == {"verify_ssl": True}

        # Subscriptions
        assert len(evt.subscriptions) == 1
        sub = evt.subscriptions[0]
        assert sub.id == full_manifest_data["event_sub_id"]
        assert sub.workflow_id == full_manifest_data["wf_id"]
        assert sub.event_type == "ticket.created"
        assert sub.input_mapping == {"ticket_id": "$.data.id"}
        assert sub.is_active is True

    def test_schedule_event_source(self):
        """Parse a schedule-type event source."""
        from bifrost.manifest import parse_manifest

        wf_id = str(uuid4())
        sub_id = str(uuid4())
        es_id = str(uuid4())
        yaml_str = yaml.dump({
            "workflows": {
                wf_id: {
                    "id": wf_id,
                    "name": "sync_job",
                    "path": "workflows/sync_job.py",
                    "function_name": "sync_job",
                },
            },
            "events": {
                es_id: {
                    "id": es_id,
                    "name": "Daily Sync",
                    "source_type": "schedule",
                    "cron_expression": "0 6 * * *",
                    "timezone": "America/New_York",
                    "schedule_enabled": True,
                    "subscriptions": [
                        {
                            "id": sub_id,
                            "workflow_id": wf_id,
                        },
                    ],
                },
            },
        }, default_flow_style=False)
        manifest = parse_manifest(yaml_str)

        evt = manifest.events[es_id]
        assert evt.source_type == "schedule"
        assert evt.cron_expression == "0 6 * * *"
        assert evt.timezone == "America/New_York"
        assert evt.schedule_enabled is True
        assert len(evt.subscriptions) == 1

    def test_event_round_trip(self, full_manifest_data):
        """Events survive serialize → parse round-trip."""
        from bifrost.manifest import parse_manifest, serialize_manifest

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        original = parse_manifest(yaml_str)
        output = serialize_manifest(original)
        restored = parse_manifest(output)

        es_id = full_manifest_data["event_source_id"]
        assert es_id in restored.events
        evt_orig = original.events[es_id]
        evt_rest = restored.events[es_id]
        assert evt_rest.id == evt_orig.id
        assert evt_rest.source_type == evt_orig.source_type
        assert len(evt_rest.subscriptions) == len(evt_orig.subscriptions)
        assert evt_rest.subscriptions[0].workflow_id == evt_orig.subscriptions[0].workflow_id

    def test_event_split_file(self, full_manifest_data):
        """Events serialize to events.yaml in split format."""
        from bifrost.manifest import parse_manifest, serialize_manifest_dir, parse_manifest_dir

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        files = serialize_manifest_dir(manifest)

        assert "events.yaml" in files
        evt_data = yaml.safe_load(files["events.yaml"])
        assert "events" in evt_data
        es_id = full_manifest_data["event_source_id"]
        assert es_id in evt_data["events"]

        # Round-trip split
        restored = parse_manifest_dir(files)
        assert es_id in restored.events
        assert len(restored.events[es_id].subscriptions) == 1


class TestFullManifestSplitRoundTrip:
    """Test full manifest with all entity types through split format."""

    def test_all_entity_types_round_trip(self, full_manifest_data):
        """All entity types survive write_manifest_to_dir → read_manifest_from_dir."""
        from bifrost.manifest import (
            parse_manifest,
            write_manifest_to_dir,
            read_manifest_from_dir,
        )

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        original = parse_manifest(yaml_str)

        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmpdir:
            bifrost_dir = Path(tmpdir) / ".bifrost"
            write_manifest_to_dir(original, bifrost_dir)

            # Verify all expected files exist
            assert (bifrost_dir / "organizations.yaml").exists()
            assert (bifrost_dir / "roles.yaml").exists()
            assert (bifrost_dir / "workflows.yaml").exists()
            assert (bifrost_dir / "integrations.yaml").exists()
            assert (bifrost_dir / "configs.yaml").exists()
            assert (bifrost_dir / "tables.yaml").exists()
            assert (bifrost_dir / "events.yaml").exists()
            assert (bifrost_dir / "forms.yaml").exists()
            assert (bifrost_dir / "agents.yaml").exists()
            assert (bifrost_dir / "apps.yaml").exists()

            restored = read_manifest_from_dir(bifrost_dir)

        assert len(restored.organizations) == len(original.organizations)
        assert len(restored.roles) == len(original.roles)
        assert len(restored.workflows) == len(original.workflows)
        assert len(restored.integrations) == len(original.integrations)
        assert len(restored.configs) == len(original.configs)
        assert len(restored.tables) == len(original.tables)
        assert len(restored.events) == len(original.events)
        assert len(restored.forms) == len(original.forms)
        assert len(restored.agents) == len(original.agents)
        assert len(restored.apps) == len(original.apps)


class TestValidateManifestNewTypes:
    """Validation tests for cross-references in new entity types."""

    def test_valid_full_manifest(self, full_manifest_data):
        """Full manifest with correct references passes validation."""
        from bifrost.manifest import parse_manifest, validate_manifest

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        errors = validate_manifest(manifest)
        assert errors == []

    def test_integration_bad_data_provider_ref(self, full_manifest_data):
        """Integration referencing unknown data provider workflow is caught."""
        from bifrost.manifest import parse_manifest, validate_manifest

        data = full_manifest_data["manifest"]
        data["integrations"][full_manifest_data["integ_id"]]["list_entities_data_provider_id"] = str(uuid4())
        yaml_str = yaml.dump(data, default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        errors = validate_manifest(manifest)
        assert any("data provider" in e.lower() for e in errors)

    def test_integration_bad_mapping_org_ref(self, full_manifest_data):
        """Integration mapping referencing unknown org is caught."""
        from bifrost.manifest import parse_manifest, validate_manifest

        data = full_manifest_data["manifest"]
        data["integrations"][full_manifest_data["integ_id"]]["mappings"][0]["organization_id"] = str(uuid4())
        yaml_str = yaml.dump(data, default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        errors = validate_manifest(manifest)
        assert any("organization" in e.lower() for e in errors)

    def test_config_bad_integration_ref(self, full_manifest_data):
        """Config referencing unknown integration is caught."""
        from bifrost.manifest import parse_manifest, validate_manifest

        data = full_manifest_data["manifest"]
        config_id = full_manifest_data["config_id"]
        data["configs"][config_id]["integration_id"] = str(uuid4())
        yaml_str = yaml.dump(data, default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        errors = validate_manifest(manifest)
        assert any("integration" in e.lower() for e in errors)

    def test_config_bad_org_ref(self, full_manifest_data):
        """Config referencing unknown organization is caught."""
        from bifrost.manifest import parse_manifest, validate_manifest

        data = full_manifest_data["manifest"]
        config_id = full_manifest_data["config_id"]
        data["configs"][config_id]["organization_id"] = str(uuid4())
        yaml_str = yaml.dump(data, default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        errors = validate_manifest(manifest)
        assert any("organization" in e.lower() for e in errors)

    def test_table_bad_org_ref(self, full_manifest_data):
        """Table referencing unknown org is caught."""
        from bifrost.manifest import parse_manifest, validate_manifest

        data = full_manifest_data["manifest"]
        data["tables"][full_manifest_data["table_id"]]["organization_id"] = str(uuid4())
        yaml_str = yaml.dump(data, default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        errors = validate_manifest(manifest)
        assert any("organization" in e.lower() for e in errors)

    def test_table_bad_app_ref(self, full_manifest_data):
        """Table referencing unknown application is caught."""
        from bifrost.manifest import parse_manifest, validate_manifest

        data = full_manifest_data["manifest"]
        data["tables"][full_manifest_data["table_id"]]["application_id"] = str(uuid4())
        yaml_str = yaml.dump(data, default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        errors = validate_manifest(manifest)
        assert any("application" in e.lower() for e in errors)

    def test_event_bad_org_ref(self, full_manifest_data):
        """Event source referencing unknown org is caught."""
        from bifrost.manifest import parse_manifest, validate_manifest

        data = full_manifest_data["manifest"]
        data["events"][full_manifest_data["event_source_id"]]["organization_id"] = str(uuid4())
        yaml_str = yaml.dump(data, default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        errors = validate_manifest(manifest)
        assert any("organization" in e.lower() for e in errors)

    def test_event_bad_webhook_integration_ref(self, full_manifest_data):
        """Event source referencing unknown webhook integration is caught."""
        from bifrost.manifest import parse_manifest, validate_manifest

        data = full_manifest_data["manifest"]
        data["events"][full_manifest_data["event_source_id"]]["webhook_integration_id"] = str(uuid4())
        yaml_str = yaml.dump(data, default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        errors = validate_manifest(manifest)
        assert any("integration" in e.lower() for e in errors)

    def test_event_sub_bad_workflow_ref(self, full_manifest_data):
        """Event subscription referencing unknown workflow is caught."""
        from bifrost.manifest import parse_manifest, validate_manifest

        data = full_manifest_data["manifest"]
        data["events"][full_manifest_data["event_source_id"]]["subscriptions"][0]["workflow_id"] = str(uuid4())
        yaml_str = yaml.dump(data, default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        errors = validate_manifest(manifest)
        assert any("workflow" in e.lower() for e in errors)


class TestConfigDictKeyCollision:
    """Verify configs with same key name but different scopes don't collide."""

    def test_configs_with_same_key_different_orgs_survive_round_trip(self):
        """Two configs with same key but different org_ids both survive serialization."""
        from bifrost.manifest import Manifest, ManifestConfig, serialize_manifest, parse_manifest

        config_id_1 = str(uuid4())
        config_id_2 = str(uuid4())
        org_id_1 = str(uuid4())
        org_id_2 = str(uuid4())
        integ_id = str(uuid4())

        manifest = Manifest(
            configs={
                config_id_1: ManifestConfig(
                    id=config_id_1,
                    integration_id=integ_id,
                    key="api_url",
                    config_type="string",
                    organization_id=org_id_1,
                    value="https://org1.example.com",
                ),
                config_id_2: ManifestConfig(
                    id=config_id_2,
                    integration_id=integ_id,
                    key="api_url",
                    config_type="string",
                    organization_id=org_id_2,
                    value="https://org2.example.com",
                ),
            },
        )
        output = serialize_manifest(manifest)
        restored = parse_manifest(output)

        assert len(restored.configs) == 2
        assert config_id_1 in restored.configs
        assert config_id_2 in restored.configs
        assert restored.configs[config_id_1].value == "https://org1.example.com"
        assert restored.configs[config_id_2].value == "https://org2.example.com"


class TestBackwardCompatNameKeys:
    """Legacy manifests with name-based dict keys still parse correctly."""

    def test_legacy_name_keyed_manifest_parses(self):
        """Old-format YAML with name keys parses; name field defaults to empty."""
        from bifrost.manifest import parse_manifest

        yaml_str = """
workflows:
  my_workflow:
    id: "11111111-1111-1111-1111-111111111111"
    path: workflows/my_workflow.py
    function_name: my_workflow
integrations:
  HaloPSA:
    id: "22222222-2222-2222-2222-222222222222"
tables:
  ticket_cache:
    id: "33333333-3333-3333-3333-333333333333"
events:
  Daily Sync:
    id: "44444444-4444-4444-4444-444444444444"
    source_type: schedule
forms:
  my_form:
    id: "55555555-5555-5555-5555-555555555555"
    path: forms/my_form.form.yaml
agents:
  my_agent:
    id: "66666666-6666-6666-6666-666666666666"
    path: agents/my_agent.agent.yaml
"""
        manifest = parse_manifest(yaml_str)
        assert "my_workflow" in manifest.workflows
        assert manifest.workflows["my_workflow"].name == ""
        assert manifest.workflows["my_workflow"].id == "11111111-1111-1111-1111-111111111111"
        assert "HaloPSA" in manifest.integrations
        assert manifest.integrations["HaloPSA"].name == ""
        assert "ticket_cache" in manifest.tables
        assert manifest.tables["ticket_cache"].name == ""
        assert "Daily Sync" in manifest.events
        assert manifest.events["Daily Sync"].name == ""
        assert "my_form" in manifest.forms
        assert manifest.forms["my_form"].name == ""
        assert "my_agent" in manifest.agents
        assert manifest.agents["my_agent"].name == ""


class TestDuplicateNamesSurvive:
    """UUID-keyed manifests preserve entities with duplicate names."""

    def test_duplicate_workflow_names_survive_round_trip(self):
        """Two workflows with the same name but different UUIDs both survive."""
        from bifrost.manifest import (
            Manifest, ManifestWorkflow, serialize_manifest, parse_manifest,
        )

        wf_id_1 = str(uuid4())
        wf_id_2 = str(uuid4())
        org_id = str(uuid4())

        manifest = Manifest(
            workflows={
                wf_id_1: ManifestWorkflow(
                    id=wf_id_1,
                    name="onboard_user",
                    path="workflows/onboard_user.py",
                    function_name="onboard_user",
                ),
                wf_id_2: ManifestWorkflow(
                    id=wf_id_2,
                    name="onboard_user",
                    path="workflows/onboard_user_v2.py",
                    function_name="onboard_user",
                    organization_id=org_id,
                ),
            },
        )
        output = serialize_manifest(manifest)
        restored = parse_manifest(output)

        assert len(restored.workflows) == 2
        assert wf_id_1 in restored.workflows
        assert wf_id_2 in restored.workflows
        assert restored.workflows[wf_id_1].name == "onboard_user"
        assert restored.workflows[wf_id_2].name == "onboard_user"


class TestGetAllEntityIdsNewTypes:
    """Test that get_all_entity_ids includes all new entity types."""

    def test_includes_new_entity_ids(self, full_manifest_data):
        """get_all_entity_ids includes integrations, configs, tables, events, subscriptions."""
        from bifrost.manifest import parse_manifest, get_all_entity_ids

        yaml_str = yaml.dump(full_manifest_data["manifest"], default_flow_style=False)
        manifest = parse_manifest(yaml_str)
        ids = get_all_entity_ids(manifest)

        # Existing types
        assert full_manifest_data["wf_id"] in ids
        assert full_manifest_data["form_id"] in ids
        assert full_manifest_data["agent_id"] in ids
        assert full_manifest_data["app_id"] in ids

        # New types
        assert full_manifest_data["integ_id"] in ids
        assert full_manifest_data["config_id"] in ids
        assert full_manifest_data["secret_config_id"] in ids
        assert full_manifest_data["table_id"] in ids
        assert full_manifest_data["event_source_id"] in ids
        assert full_manifest_data["event_sub_id"] in ids


class TestManifestSchemaCoverage:
    """Verify that all DB columns are either tracked in manifest models or explicitly ignored.

    When a developer adds a new column to an integration ORM model, this test
    forces them to decide: is this field managed by git (add to manifest model)
    or managed exclusively by the UI/runtime (add to the ignored set)?
    """

    # Columns that are intentionally NOT in the manifest — either internal
    # bookkeeping or UI-managed state.
    INTEGRATION_IGNORED = {
        "id",            # manifest uses UUID dict key; id is a field inside
        "is_deleted",    # soft-delete flag, internal
        "created_at",
        "updated_at",
    }

    CONFIG_SCHEMA_IGNORED = {
        "id",              # DB surrogate key, not in manifest
        "integration_id",  # implicit from parent integration
        "created_at",
        "updated_at",
    }

    MAPPING_IGNORED = {
        "id",              # DB surrogate key
        "integration_id",  # implicit from parent integration
        "created_at",
        "updated_at",
    }

    def test_integration_columns_tracked(self):
        """All Integration DB columns are in ManifestIntegration or explicitly ignored."""
        from sqlalchemy import inspect as sa_inspect
        from src.models.orm.integrations import Integration
        from bifrost.manifest import ManifestIntegration

        db_columns = {c.name for c in sa_inspect(Integration).columns}
        manifest_fields = set(ManifestIntegration.model_fields.keys())
        untracked = db_columns - manifest_fields - self.INTEGRATION_IGNORED
        assert not untracked, (
            f"New Integration DB columns not tracked in manifest or ignored: {untracked}. "
            "Add them to ManifestIntegration or to INTEGRATION_IGNORED in this test."
        )

    def test_config_schema_columns_tracked(self):
        """All IntegrationConfigSchema DB columns are in ManifestIntegrationConfigSchema or ignored."""
        from sqlalchemy import inspect as sa_inspect
        from src.models.orm.integrations import IntegrationConfigSchema
        from bifrost.manifest import ManifestIntegrationConfigSchema

        db_columns = {c.name for c in sa_inspect(IntegrationConfigSchema).columns}
        manifest_fields = set(ManifestIntegrationConfigSchema.model_fields.keys())
        untracked = db_columns - manifest_fields - self.CONFIG_SCHEMA_IGNORED
        assert not untracked, (
            f"New IntegrationConfigSchema DB columns not tracked in manifest or ignored: {untracked}. "
            "Add them to ManifestIntegrationConfigSchema or to CONFIG_SCHEMA_IGNORED in this test."
        )

    def test_mapping_columns_tracked(self):
        """All IntegrationMapping DB columns are in ManifestIntegrationMapping or ignored."""
        from sqlalchemy import inspect as sa_inspect
        from src.models.orm.integrations import IntegrationMapping
        from bifrost.manifest import ManifestIntegrationMapping

        db_columns = {c.name for c in sa_inspect(IntegrationMapping).columns}
        manifest_fields = set(ManifestIntegrationMapping.model_fields.keys())
        untracked = db_columns - manifest_fields - self.MAPPING_IGNORED
        assert not untracked, (
            f"New IntegrationMapping DB columns not tracked in manifest or ignored: {untracked}. "
            "Add them to ManifestIntegrationMapping or to MAPPING_IGNORED in this test."
        )


class TestAgentManifestFields:
    """Test agent budget fields round-trip through manifest."""

    def test_agent_max_iterations_round_trip(self):
        """max_iterations survives serialize -> parse -> serialize."""
        from bifrost.manifest import ManifestAgent, Manifest, serialize_manifest, parse_manifest

        agent_id = str(uuid4())
        agent = ManifestAgent(
            id=agent_id,
            name="Budget Agent",
            path="agents/test.agent.yaml",
            max_iterations=25,
            max_token_budget=50000,
        )
        manifest = Manifest(agents={agent_id: agent})
        yaml_out = serialize_manifest(manifest)
        parsed = parse_manifest(yaml_out)
        assert parsed.agents[agent_id].max_iterations == 25
        assert parsed.agents[agent_id].max_token_budget == 50000

        # Stability: second round-trip identical
        yaml_out2 = serialize_manifest(parsed)
        assert yaml_out == yaml_out2


class TestEventSubscriptionManifestFields:
    """Test event subscription agent fields round-trip."""

    def test_agent_subscription_round_trip(self):
        """target_type=agent with agent_id survives round-trip."""
        from bifrost.manifest import (
            ManifestEventSource, ManifestEventSubscription, Manifest,
            serialize_manifest, parse_manifest,
        )

        agent_id = str(uuid4())
        sub_id = str(uuid4())
        source_id = str(uuid4())
        sub = ManifestEventSubscription(
            id=sub_id,
            target_type="agent",
            agent_id=agent_id,
            workflow_id=None,
            is_active=True,
        )
        source = ManifestEventSource(
            id=source_id,
            name="Test Source",
            source_type="webhook",
            is_active=True,
            subscriptions=[sub],
        )
        manifest = Manifest(events={source_id: source})
        yaml_out = serialize_manifest(manifest)
        parsed = parse_manifest(yaml_out)

        parsed_sub = parsed.events[source_id].subscriptions[0]
        assert parsed_sub.target_type == "agent"
        assert parsed_sub.agent_id == agent_id
        assert parsed_sub.workflow_id is None

    def test_workflow_subscription_round_trip(self):
        """target_type=workflow with workflow_id survives round-trip."""
        from bifrost.manifest import (
            ManifestEventSource, ManifestEventSubscription, Manifest,
            serialize_manifest, parse_manifest,
        )

        workflow_id = str(uuid4())
        sub_id = str(uuid4())
        source_id = str(uuid4())
        sub = ManifestEventSubscription(
            id=sub_id,
            target_type="workflow",
            workflow_id=workflow_id,
            agent_id=None,
            is_active=True,
        )
        source = ManifestEventSource(
            id=source_id,
            name="Test Source",
            source_type="webhook",
            is_active=True,
            subscriptions=[sub],
        )
        manifest = Manifest(events={source_id: source})
        yaml_out = serialize_manifest(manifest)
        parsed = parse_manifest(yaml_out)

        parsed_sub = parsed.events[source_id].subscriptions[0]
        # target_type="workflow" is default, so after exclude_defaults it will be "workflow" on re-parse
        assert parsed_sub.target_type == "workflow"
        assert parsed_sub.workflow_id == workflow_id
        assert parsed_sub.agent_id is None


class TestManifestValidationAgents:
    """Test manifest validation catches agent subscription issues."""

    def test_validate_unknown_agent_in_subscription(self):
        """Subscription referencing non-existent agent_id should fail validation."""
        from bifrost.manifest import (
            Manifest, ManifestEventSource, ManifestEventSubscription,
            validate_manifest,
        )

        sub_id = str(uuid4())
        source_id = str(uuid4())
        sub = ManifestEventSubscription(
            id=sub_id,
            target_type="agent",
            agent_id=str(uuid4()),  # Unknown agent
            is_active=True,
        )
        source = ManifestEventSource(
            id=source_id,
            name="Source",
            source_type="webhook",
            is_active=True,
            subscriptions=[sub],
        )
        manifest = Manifest(events={source_id: source})

        errors = validate_manifest(manifest)
        assert any("agent" in e.lower() for e in errors), f"Expected agent validation error, got: {errors}"

    def test_validate_known_agent_in_subscription(self):
        """Subscription referencing existing agent_id should pass."""
        from bifrost.manifest import (
            Manifest, ManifestEventSource, ManifestEventSubscription,
            ManifestAgent, validate_manifest,
        )

        agent_id = str(uuid4())
        sub_id = str(uuid4())
        source_id = str(uuid4())
        agent = ManifestAgent(
            id=agent_id,
            name="Test Agent",
            path=f"agents/{agent_id}.agent.yaml",
        )
        sub = ManifestEventSubscription(
            id=sub_id,
            target_type="agent",
            agent_id=agent_id,
            is_active=True,
        )
        source = ManifestEventSource(
            id=source_id,
            name="Source",
            source_type="webhook",
            is_active=True,
            subscriptions=[sub],
        )
        manifest = Manifest(
            agents={agent_id: agent},
            events={source_id: source},
        )

        errors = validate_manifest(manifest)
        agent_errors = [e for e in errors if "agent" in e.lower()]
        assert len(agent_errors) == 0, f"Unexpected agent errors: {agent_errors}"

    def test_validate_agent_subscription_no_false_workflow_error(self):
        """Agent subscription with workflow_id=None should not produce workflow error."""
        from bifrost.manifest import (
            Manifest, ManifestEventSource, ManifestEventSubscription,
            ManifestAgent, validate_manifest,
        )

        agent_id = str(uuid4())
        sub_id = str(uuid4())
        source_id = str(uuid4())
        agent = ManifestAgent(
            id=agent_id,
            name="Test Agent",
            path=f"agents/{agent_id}.agent.yaml",
        )
        sub = ManifestEventSubscription(
            id=sub_id,
            target_type="agent",
            agent_id=agent_id,
            workflow_id=None,
            is_active=True,
        )
        source = ManifestEventSource(
            id=source_id,
            name="Source",
            source_type="webhook",
            is_active=True,
            subscriptions=[sub],
        )
        manifest = Manifest(
            agents={agent_id: agent},
            events={source_id: source},
        )

        errors = validate_manifest(manifest)
        workflow_errors = [e for e in errors if "workflow" in e.lower()]
        assert len(workflow_errors) == 0, f"Agent subscription should not produce workflow errors: {workflow_errors}"


# =============================================================================
# _diff_manifests and _collect_changed_ids tests
# =============================================================================


class TestDiffManifests:
    """Tests for _diff_manifests()."""

    def _make_manifest(self, **kwargs):
        from bifrost.manifest import Manifest
        return Manifest(**kwargs)

    def _diff(self, incoming, current):
        from src.services.manifest_import import _diff_manifests
        return _diff_manifests(incoming, current)

    def test_identical_manifests_empty_diff(self):
        org_id = str(uuid4())
        wf_id = str(uuid4())
        data = {
            "organizations": [{"id": org_id, "name": "Org"}],
            "workflows": {wf_id: {"id": wf_id, "name": "wf", "path": "w.py", "function_name": "wf"}},
        }
        m1 = self._make_manifest(**data)
        m2 = self._make_manifest(**data)
        assert self._diff(m1, m2) == []

    def test_new_entity_is_add(self):
        wf_id = str(uuid4())
        incoming = self._make_manifest(
            workflows={wf_id: {"id": wf_id, "name": "wf", "path": "w.py", "function_name": "wf"}}
        )
        current = self._make_manifest()
        changes = self._diff(incoming, current)
        assert len(changes) == 1
        assert changes[0]["action"] == "add"
        assert changes[0]["entity_type"] == "workflows"
        assert changes[0]["name"] == "wf"

    def test_removed_entity_is_delete(self):
        wf_id = str(uuid4())
        incoming = self._make_manifest()
        current = self._make_manifest(
            workflows={wf_id: {"id": wf_id, "name": "wf", "path": "w.py", "function_name": "wf"}}
        )
        changes = self._diff(incoming, current)
        assert len(changes) == 1
        assert changes[0]["action"] == "delete"

    def test_modified_entity_is_update(self):
        wf_id = str(uuid4())
        incoming = self._make_manifest(
            workflows={wf_id: {"id": wf_id, "name": "wf_v2", "path": "w.py", "function_name": "wf"}}
        )
        current = self._make_manifest(
            workflows={wf_id: {"id": wf_id, "name": "wf_v1", "path": "w.py", "function_name": "wf"}}
        )
        changes = self._diff(incoming, current)
        assert len(changes) == 1
        assert changes[0]["action"] == "update"

    def test_unchanged_entities_omitted(self):
        wf_id1 = str(uuid4())
        wf_id2 = str(uuid4())
        shared = {"id": wf_id1, "name": "same", "path": "w.py", "function_name": "wf"}
        incoming = self._make_manifest(
            workflows={
                wf_id1: shared,
                wf_id2: {"id": wf_id2, "name": "new", "path": "w2.py", "function_name": "wf2"},
            }
        )
        current = self._make_manifest(workflows={wf_id1: shared})
        changes = self._diff(incoming, current)
        assert len(changes) == 1
        assert changes[0]["name"] == "new"

    def test_config_display_name_with_integration_prefix(self):
        integ_id = str(uuid4())
        cfg_id = str(uuid4())
        incoming = self._make_manifest(
            integrations={integ_id: {"id": integ_id, "name": "MyInteg"}},
            configs={cfg_id: {"id": cfg_id, "key": "api_key", "integration_id": integ_id}},
        )
        current = self._make_manifest()
        changes = self._diff(incoming, current)
        config_changes = [c for c in changes if c["entity_type"] == "configs"]
        assert len(config_changes) == 1
        assert config_changes[0]["name"] == "MyInteg/api_key"

    def test_organization_resolution(self):
        org_id = str(uuid4())
        wf_id = str(uuid4())
        incoming = self._make_manifest(
            organizations=[{"id": org_id, "name": "TestOrg"}],
            workflows={wf_id: {"id": wf_id, "name": "wf", "path": "w.py", "function_name": "wf", "organization_id": org_id}},
        )
        current = self._make_manifest(organizations=[{"id": org_id, "name": "TestOrg"}])
        changes = self._diff(incoming, current)
        wf_changes = [c for c in changes if c["entity_type"] == "workflows"]
        assert wf_changes[0]["organization"] == "TestOrg"

    def test_sort_order(self):
        """Changes are sorted by entity_type, then action priority, then name."""
        wf_add = str(uuid4())
        wf_del = str(uuid4())
        org_id = str(uuid4())
        incoming = self._make_manifest(
            organizations=[{"id": org_id, "name": "Org"}],
            workflows={wf_add: {"id": wf_add, "name": "alpha", "path": "a.py", "function_name": "a"}},
        )
        current = self._make_manifest(
            workflows={wf_del: {"id": wf_del, "name": "beta", "path": "b.py", "function_name": "b"}},
        )
        changes = self._diff(incoming, current)
        # orgs first (add), then workflows (add alpha, delete beta)
        types = [c["entity_type"] for c in changes]
        assert types == sorted(types)  # sorted by entity_type


class TestCollectChangedIds:
    """Tests for _collect_changed_ids()."""

    def _make_manifest(self, **kwargs):
        from bifrost.manifest import Manifest
        return Manifest(**kwargs)

    def _collect(self, incoming, current):
        from src.services.manifest_import import _collect_changed_ids
        return _collect_changed_ids(incoming, current)

    def test_identical_returns_empty(self):
        wf_id = str(uuid4())
        data = {"workflows": {wf_id: {"id": wf_id, "name": "wf", "path": "w.py", "function_name": "wf"}}}
        assert self._collect(self._make_manifest(**data), self._make_manifest(**data)) == set()

    def test_new_entity_in_set(self):
        wf_id = str(uuid4())
        incoming = self._make_manifest(
            workflows={wf_id: {"id": wf_id, "name": "wf", "path": "w.py", "function_name": "wf"}}
        )
        assert wf_id in self._collect(incoming, self._make_manifest())

    def test_removed_entity_in_set(self):
        wf_id = str(uuid4())
        current = self._make_manifest(
            workflows={wf_id: {"id": wf_id, "name": "wf", "path": "w.py", "function_name": "wf"}}
        )
        assert wf_id in self._collect(self._make_manifest(), current)

    def test_modified_entity_in_set(self):
        wf_id = str(uuid4())
        incoming = self._make_manifest(
            workflows={wf_id: {"id": wf_id, "name": "v2", "path": "w.py", "function_name": "wf"}}
        )
        current = self._make_manifest(
            workflows={wf_id: {"id": wf_id, "name": "v1", "path": "w.py", "function_name": "wf"}}
        )
        assert wf_id in self._collect(incoming, current)

    def test_unchanged_not_in_set(self):
        wf_id = str(uuid4())
        new_id = str(uuid4())
        shared = {"id": wf_id, "name": "same", "path": "w.py", "function_name": "wf"}
        incoming = self._make_manifest(
            workflows={
                wf_id: shared,
                new_id: {"id": new_id, "name": "new", "path": "n.py", "function_name": "n"},
            }
        )
        current = self._make_manifest(workflows={wf_id: shared})
        ids = self._collect(incoming, current)
        assert new_id in ids
        assert wf_id not in ids

    def test_integration_change_includes_dependent_configs(self):
        integ_id = str(uuid4())
        cfg_id = str(uuid4())
        incoming = self._make_manifest(
            integrations={integ_id: {"id": integ_id, "name": "v2"}},
            configs={cfg_id: {"id": cfg_id, "key": "k", "integration_id": integ_id}},
        )
        current = self._make_manifest(
            integrations={integ_id: {"id": integ_id, "name": "v1"}},
            configs={cfg_id: {"id": cfg_id, "key": "k", "integration_id": integ_id}},
        )
        ids = self._collect(incoming, current)
        assert integ_id in ids
        assert cfg_id in ids  # dependent config included even though config itself unchanged

    def test_list_entities_organizations(self):
        org_id = str(uuid4())
        incoming = self._make_manifest(organizations=[{"id": org_id, "name": "New"}])
        current = self._make_manifest()
        assert org_id in self._collect(incoming, current)


# =============================================================================
# Inline form/agent content (Task 9: manifest carries content under UUID)
# =============================================================================


class TestInlineFormContent:
    """ManifestForm carries portable content (workflow_id, form_schema, ...) inline."""

    def test_form_inline_round_trip(self):
        """All inline content fields round-trip through serialize → parse → serialize."""
        from bifrost.manifest import (
            Manifest, ManifestForm, parse_manifest, serialize_manifest,
        )

        form_id = str(uuid4())
        wf_id = str(uuid4())
        launch_id = str(uuid4())
        form = ManifestForm(
            id=form_id,
            name="Onboarding",
            description="Onboard a new client",
            workflow_id=wf_id,
            launch_workflow_id=launch_id,
            default_launch_params={"source": "marketing"},
            allowed_query_params=["utm_source", "utm_medium"],
            form_schema={
                "fields": [
                    {"name": "email", "type": "text", "required": True, "label": "Email"},
                    {"name": "count", "type": "number", "required": False, "default_value": 5},
                ],
            },
        )
        manifest = Manifest(forms={form_id: form})
        yaml_out = serialize_manifest(manifest)
        parsed = parse_manifest(yaml_out)

        round_tripped = parsed.forms[form_id]
        assert round_tripped.description == "Onboard a new client"
        assert round_tripped.workflow_id == wf_id
        assert round_tripped.launch_workflow_id == launch_id
        assert round_tripped.default_launch_params == {"source": "marketing"}
        assert round_tripped.allowed_query_params == ["utm_source", "utm_medium"]
        assert round_tripped.form_schema is not None
        assert len(round_tripped.form_schema["fields"]) == 2
        assert round_tripped.form_schema["fields"][0]["name"] == "email"

        yaml_out2 = serialize_manifest(parsed)
        assert yaml_out == yaml_out2

    def test_form_path_optional(self):
        """ManifestForm should accept missing path (inline-content layout)."""
        from bifrost.manifest import ManifestForm

        form_id = str(uuid4())
        f = ManifestForm(id=form_id, name="No Path Form")
        assert f.path is None


class TestInlineAgentContent:
    """ManifestAgent carries portable content (system_prompt, tools, ...) inline."""

    def test_agent_inline_round_trip(self):
        """All inline content fields round-trip through serialize → parse → serialize."""
        from bifrost.manifest import (
            Manifest, ManifestAgent, parse_manifest, serialize_manifest,
        )

        agent_id = str(uuid4())
        tool_id = str(uuid4())
        delegate_id = str(uuid4())
        agent = ManifestAgent(
            id=agent_id,
            name="Triage",
            description="Triage incoming tickets",
            system_prompt="You are a triage agent. Classify tickets.",
            channels=["chat", "email"],
            tool_ids=[tool_id],
            delegated_agent_ids=[delegate_id],
            knowledge_sources=["faq", "runbooks"],
            system_tools=["execute_workflow", "search_knowledge"],
            llm_model="claude-sonnet-4",
            llm_max_tokens=8000,
            max_iterations=15,
            max_token_budget=120000,
        )
        manifest = Manifest(agents={agent_id: agent})
        yaml_out = serialize_manifest(manifest)
        parsed = parse_manifest(yaml_out)

        rt = parsed.agents[agent_id]
        assert rt.description == "Triage incoming tickets"
        assert rt.system_prompt == "You are a triage agent. Classify tickets."
        assert rt.channels == ["chat", "email"]
        assert rt.tool_ids == [tool_id]
        assert rt.delegated_agent_ids == [delegate_id]
        assert rt.knowledge_sources == ["faq", "runbooks"]
        assert rt.system_tools == ["execute_workflow", "search_knowledge"]
        assert rt.llm_model == "claude-sonnet-4"
        assert rt.llm_max_tokens == 8000
        assert rt.max_iterations == 15
        assert rt.max_token_budget == 120000

        yaml_out2 = serialize_manifest(parsed)
        assert yaml_out == yaml_out2

    def test_agent_path_optional(self):
        """ManifestAgent should accept missing path (inline-content layout)."""
        from bifrost.manifest import ManifestAgent

        agent_id = str(uuid4())
        a = ManifestAgent(id=agent_id, name="No Path Agent")
        assert a.path is None


class TestInlineContentDetection:
    """Helpers _form_has_inline_content / _agent_has_inline_content."""

    def test_form_with_inline_fields_detected(self):
        from bifrost.manifest import ManifestForm
        from src.services.manifest_import import _form_has_inline_content

        f = ManifestForm(id=str(uuid4()), name="x", workflow_id=str(uuid4()))
        assert _form_has_inline_content(f) is True

    def test_form_with_only_path_not_detected(self):
        from bifrost.manifest import ManifestForm
        from src.services.manifest_import import _form_has_inline_content

        f = ManifestForm(id=str(uuid4()), name="x", path="forms/x.form.yaml")
        assert _form_has_inline_content(f) is False

    def test_agent_with_system_prompt_detected(self):
        from bifrost.manifest import ManifestAgent
        from src.services.manifest_import import _agent_has_inline_content

        a = ManifestAgent(id=str(uuid4()), name="x", system_prompt="hi")
        assert _agent_has_inline_content(a) is True

    def test_agent_with_only_path_not_detected(self):
        from bifrost.manifest import ManifestAgent
        from src.services.manifest_import import _agent_has_inline_content

        a = ManifestAgent(id=str(uuid4()), name="x", path="agents/x.agent.yaml")
        assert _agent_has_inline_content(a) is False


class TestBackCompatSeparateFile:
    """Back-compat: manifests written before the inline rollout still import,
    falling back to companion .form.yaml / .agent.yaml files. A deprecation
    warning is logged so users know to regenerate.
    """

    async def test_form_back_compat_reads_companion_file(self, caplog):
        import logging
        from bifrost.manifest import ManifestForm
        from src.services.manifest_import import _resolve_form_content

        form_id = str(uuid4())
        mform = ManifestForm(
            id=form_id,
            name="Legacy Form",
            path=f"forms/{form_id}.form.yaml",
        )
        legacy_yaml = f"name: Legacy Form\nworkflow_id: {uuid4()}\n".encode("utf-8")

        async def read_fn(path: str) -> bytes | None:
            assert path == f"forms/{form_id}.form.yaml"
            return legacy_yaml

        with caplog.at_level(logging.WARNING, logger="src.services.manifest_import"):
            content = await _resolve_form_content(mform, read_fn)

        assert content == legacy_yaml
        assert any(
            "deprecated" in r.message.lower() and "regenerate" in r.message.lower()
            for r in caplog.records
        ), f"Expected deprecation warning, got: {[r.message for r in caplog.records]}"

    async def test_agent_back_compat_reads_companion_file(self, caplog):
        import logging
        from bifrost.manifest import ManifestAgent
        from src.services.manifest_import import _resolve_agent_content

        agent_id = str(uuid4())
        magent = ManifestAgent(
            id=agent_id,
            name="Legacy Agent",
            path=f"agents/{agent_id}.agent.yaml",
        )
        legacy_yaml = b"name: Legacy Agent\nsystem_prompt: hello\n"

        async def read_fn(path: str) -> bytes | None:
            assert path == f"agents/{agent_id}.agent.yaml"
            return legacy_yaml

        with caplog.at_level(logging.WARNING, logger="src.services.manifest_import"):
            content = await _resolve_agent_content(magent, read_fn)

        assert content == legacy_yaml
        assert any(
            "deprecated" in r.message.lower() and "regenerate" in r.message.lower()
            for r in caplog.records
        )

    async def test_form_inline_skips_file_read(self):
        """Inline content takes precedence; the file read fn must not be called."""
        from bifrost.manifest import ManifestForm
        from src.services.manifest_import import _resolve_form_content

        form_id = str(uuid4())
        mform = ManifestForm(
            id=form_id,
            name="Inline Form",
            path=f"forms/{form_id}.form.yaml",
            workflow_id=str(uuid4()),
        )

        called = False
        async def read_fn(path: str) -> bytes | None:
            nonlocal called
            called = True
            return None

        content = await _resolve_form_content(mform, read_fn)
        assert content is not None
        assert called is False

    async def test_returns_none_when_no_source(self):
        """No inline content and no path → returns None (and never logs warning)."""
        from bifrost.manifest import ManifestForm
        from src.services.manifest_import import _resolve_form_content

        mform = ManifestForm(id=str(uuid4()), name="Empty")

        async def read_fn(path: str) -> bytes | None:
            return None

        assert await _resolve_form_content(mform, read_fn) is None
