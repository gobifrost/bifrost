"""Shared Solution/workspace manifest acceptance contracts."""
from __future__ import annotations

import pytest


def _solution_workspace(tmp_path, manifest_name: str, content: str) -> None:
    (tmp_path / "bifrost.solution.yaml").write_text("slug: demo\nname: Demo\nscope: tenant\n")
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / manifest_name).write_text(content)


def test_solution_and_workspace_reject_the_same_unknown_workflow_field(tmp_path) -> None:
    from bifrost.definition_validation import DefinitionValidationError, read_workspace_manifest
    from bifrost.solution_definition import read_solution_definition

    content = """workflows:
  workflow-id:
    id: workflow-id
    name: daily
    path: workflows/daily.py
    function_name: run
    timeout_second: 30
"""
    with pytest.raises(DefinitionValidationError) as workspace_error:
        read_workspace_manifest("workflows.yaml", content)

    _solution_workspace(tmp_path, "workflows.yaml", content)
    with pytest.raises(DefinitionValidationError) as solution_error:
        read_solution_definition(tmp_path)

    assert workspace_error.value.issues[0].location == solution_error.value.issues[0].location
    assert solution_error.value.issues[0].path == ".bifrost/workflows.yaml"


def test_solution_reader_preserves_map_key_defaults_and_table_schema_alias(tmp_path) -> None:
    from bifrost.solution_definition import read_solution_definition

    _solution_workspace(
        tmp_path,
        "tables.yaml",
        """tables:
  inventory:
    table_schema:
      serial: {type: string}
    policies:
      - ref: inventory_readers
""",
    )

    definition = read_solution_definition(tmp_path)
    assert definition.entries("tables") == [{
        "id": "inventory",
        "name": "inventory",
        "schema": {"serial": {"type": "string"}},
        "policies": [{"$ref": "inventory_readers"}],
    }]


def test_solution_reader_keeps_typed_package_app_transport(tmp_path) -> None:
    from bifrost.solution_definition import read_solution_definition

    _solution_workspace(
        tmp_path,
        "apps.yaml",
        """apps:
  dashboard:
    path: apps/dashboard
    app_model: standalone_v2
    dist_files: {index.html: '<main></main>'}
    bin_dist_files: {logo.png: iVBORw0KGgo=}
    prebuilt_sdk_metadata:
      package_version: 1.0.0
      fingerprint: abc
      contract_version: 1
""",
    )

    app = read_solution_definition(tmp_path).entries("apps")[0]
    assert app["id"] == "dashboard"
    assert app["path"] == "apps/dashboard"
    assert app["dist_files"] == {"index.html": "<main></main>"}
    assert app["bin_dist_files"] == {"logo.png": "iVBORw0KGgo="}
    assert app["prebuilt_sdk_metadata"]["contract_version"] == 1


def test_solution_reader_keeps_app_repo_path_transport(tmp_path) -> None:
    from bifrost.solution_definition import read_solution_definition

    _solution_workspace(
        tmp_path,
        "apps.yaml",
        """apps:
  dashboard:
    repo_path: apps/dashboard
    app_model: standalone_v2
""",
    )

    app = read_solution_definition(tmp_path).entries("apps")[0]
    assert app["id"] == "dashboard"
    assert app["repo_path"] == "apps/dashboard"

    install_app = read_solution_definition(tmp_path).install_entries("apps")[0]
    assert "path" not in install_app
    assert install_app["repo_path"] == "apps/dashboard"


def test_solution_reader_keeps_workflow_source_transport(tmp_path) -> None:
    from bifrost.solution_definition import read_solution_definition

    _solution_workspace(
        tmp_path,
        "workflows.yaml",
        """workflows:
  daily:
    path: workflows/daily.py
    function_name: run
    source: 'def run(): return 1'
""",
    )

    workflow = read_solution_definition(tmp_path).entries("workflows")[0]
    assert workflow["id"] == "daily"
    assert workflow["name"] == "daily"
    assert workflow["source"] == "def run(): return 1"


def test_solution_reader_keeps_form_and_agent_transport(tmp_path) -> None:
    from bifrost.solution_definition import read_solution_definition

    _solution_workspace(
        tmp_path,
        "forms.yaml",
        """forms:
  intake:
    workflow_path: workflows/intake.py
    workflow_function_name: submit
    form_schema: {fields: []}
""",
    )
    (tmp_path / ".bifrost" / "agents.yaml").write_text(
        """agents:
  helper:
    system_prompt: help
    max_run_timeout: 120
"""
    )

    definition = read_solution_definition(tmp_path)
    assert definition.entries("forms")[0]["workflow_path"] == "workflows/intake.py"
    assert definition.entries("forms")[0]["workflow_function_name"] == "submit"
    assert definition.entries("agents")[0]["max_run_timeout"] == 120


def test_solution_reader_retains_declaration_defaults(tmp_path) -> None:
    from bifrost.solution_definition import read_solution_definition

    _solution_workspace(
        tmp_path,
        "configs.yaml",
        "configs:\n  API_KEY: {}\n",
    )
    (tmp_path / ".bifrost" / "connections.yaml").write_text(
        "connections:\n  crm: {}\n"
    )

    definition = read_solution_definition(tmp_path)
    assert definition.config_entries() == [{
        "id": "API_KEY",
        "key": "API_KEY",
        "type": "string",
        "required": False,
        "description": None,
        "default": None,
        "position": 0,
    }]
    assert definition.connection_entries() == [{
        "integration_name": "crm",
        "template": {},
        "position": 0,
    }]


def test_solution_reader_normalizes_documented_empty_map_key_defaults(tmp_path) -> None:
    from bifrost.solution_definition import read_solution_definition

    _solution_workspace(
        tmp_path,
        "workflows.yaml",
        """workflows:
  daily:
    name: ''
    path: workflows/daily.py
    function_name: run
""",
    )
    (tmp_path / ".bifrost" / "tables.yaml").write_text(
        "tables:\n  inventory:\n    name: ''\n    schema: {}\n"
    )
    (tmp_path / ".bifrost" / "apps.yaml").write_text(
        "apps:\n  dashboard:\n    path: apps/dashboard\n    slug: ''\n    name: ''\n"
    )
    (tmp_path / ".bifrost" / "claims.yaml").write_text(
        """claims:
  allowed_ids:
    name: ''
    query: {table: memberships, select: id}
"""
    )

    definition = read_solution_definition(tmp_path)
    assert definition.entries("workflows")[0]["name"] == "daily"
    assert definition.entries("tables")[0]["name"] == "inventory"
    assert definition.entries("apps")[0]["slug"] == "dashboard"
    assert definition.entries("apps")[0]["name"] == "dashboard"
    assert definition.entries("claims")[0]["name"] == "allowed_ids"


def test_solution_collectors_use_the_canonical_install_view(tmp_path) -> None:
    from bifrost.commands.solution import (
        _collect_claims,
        _collect_file_policies,
        _collect_tables,
    )
    from bifrost.solution_definition import read_solution_definition

    _solution_workspace(
        tmp_path,
        "tables.yaml",
        """tables:
  inventory:
    organization_id: source-org
    description: Inventory
    schema: {serial: {type: string}}
    policies: [{name: readers, actions: [read]}]
""",
    )
    (tmp_path / ".bifrost" / "claims.yaml").write_text(
        """claims:
  allowed_ids:
    organization_id: source-org
    description: Allowed IDs
    query: {table: memberships, select: id}
"""
    )
    (tmp_path / ".bifrost" / "file-policies.yaml").write_text(
        """file_policies:
  reports-root:
    organization_id: source-org
    location: reports
    path: ''
    policies: [{$ref: admin_bypass}]
"""
    )

    definition = read_solution_definition(tmp_path)
    assert _collect_tables(tmp_path, definition=definition) == definition.install_entries("tables")
    assert _collect_claims(tmp_path, definition=definition) == definition.install_entries("claims")
    assert _collect_file_policies(tmp_path, definition=definition) == definition.install_entries("file_policies")
    for section in ("tables", "claims", "file_policies"):
        assert "organization_id" not in definition.install_entries(section)[0]


def test_solution_reader_rejects_malformed_entity_instead_of_skipping_it(tmp_path) -> None:
    from bifrost.definition_validation import DefinitionValidationError
    from bifrost.solution_definition import read_solution_definition

    _solution_workspace(tmp_path, "forms.yaml", "forms:\n  broken: [not, a, mapping]\n")

    with pytest.raises(DefinitionValidationError) as error:
        read_solution_definition(tmp_path)

    assert error.value.issues[0].path == ".bifrost/forms.yaml"


def test_solution_reader_normalizes_legacy_workflow_content_transport(tmp_path) -> None:
    from bifrost.solution_definition import read_solution_definition

    _solution_workspace(
        tmp_path,
        "workflows.yaml",
        """workflows:
  daily:
    path: workflows/daily.py
    function_name: run
    content: 'def run(): return 1'
""",
    )

    workflow = read_solution_definition(tmp_path).entries("workflows")[0]
    assert workflow["source"] == "def run(): return 1"
    assert "content" not in workflow


def test_solution_reader_normalizes_legacy_event_schedule(tmp_path) -> None:
    from bifrost.solution_definition import read_solution_definition

    _solution_workspace(
        tmp_path,
        "events.yaml",
        """events:
  nightly:
    source_type: schedule
    schedule:
      cron: '0 0 * * *'
      timezone: UTC
    subscriptions: []
""",
    )

    event = read_solution_definition(tmp_path).entries("events")[0]
    assert event["cron_expression"] == "0 0 * * *"
    assert event["timezone"] == "UTC"
    assert event["schedule_enabled"] is True
    assert event["overlap_policy"] == "skip"
    assert "schedule" not in event
