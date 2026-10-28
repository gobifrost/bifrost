"""Spike evidence for a shared acceptance boundary; no production hooks yet."""

import json
from pathlib import Path

import pytest

from bifrost.definition_validation import (
    DefinitionValidationError,
    read_workspace_manifest,
    read_yaml_mapping,
    validate_decorator_keywords,
    validate_definition,
)
from bifrost.manifest import ManifestApp, ManifestEventSource, ManifestSolutionConfigSchema
from bifrost.solution_descriptor import SolutionDescriptor

GOLDEN = Path(__file__).parent / "golden" / "manifest_codec"


def test_unknown_subscription_key_is_rejected_before_it_can_be_discarded():
    source = """events:
  event-1:
    id: event-1
    source_type: topic
    subscriptions:
      - id: sub-1
        criteria: {priority: high}
"""
    with pytest.raises(DefinitionValidationError) as error:
        read_workspace_manifest("events.yaml", source)
    assert error.value.issues[0].location == (
        "events", "event-1", "subscriptions", 0, "criteria",
    )
    assert error.value.issues[0].code == "extra_forbidden"


@pytest.mark.parametrize("name", ["event_git_sync", "event_install"])
def test_workspace_and_solution_event_exports_share_the_strict_entity_schema(name):
    data = json.loads((GOLDEN / f"{name}.json").read_text())
    event = validate_definition(ManifestEventSource, data, path="events.yaml")
    assert event.model_dump(mode="json") == data


def test_acceptance_validation_does_not_change_normal_model_parsing():
    data = {"id": "event-1", "source_type": "topic", "unsupported": True}
    before = ManifestEventSource.model_validate(data)
    with pytest.raises(DefinitionValidationError):
        validate_definition(ManifestEventSource, data, path="events.yaml")
    assert ManifestEventSource.model_validate(data) == before


def test_app_install_transport_requires_its_own_schema():
    data = json.loads((GOLDEN / "app_install.json").read_text())
    with pytest.raises(DefinitionValidationError) as error:
        validate_definition(ManifestApp, data, path="apps.yaml")
    errors = {(issue.location, issue.code) for issue in error.value.issues}
    assert (("path",), "missing") in errors
    for transport_field in ("repo_path", "logo_b64", "logo_content_type"):
        assert ((transport_field,), "extra_forbidden") in errors


def test_solution_config_declarations_use_the_existing_declaration_schema():
    data = json.loads((GOLDEN / "solution_config_schema_install.json").read_text())
    declaration = validate_definition(ManifestSolutionConfigSchema, data, path="configs.yaml")
    assert declaration.key == data["key"]
    assert declaration.default == data["default"]
    with pytest.raises(DefinitionValidationError):
        read_workspace_manifest("configs.yaml", json.dumps({"configs": {"x": data}}))


def test_open_data_dictionaries_remain_open_under_recursive_strict_decoding():
    manifest = read_workspace_manifest("events.yaml", """events:
  event-1:
    id: event-1
    source_type: topic
    webhook_config: {adapter_owned_key: 42}
    subscriptions:
      - id: sub-1
        input_mapping: {customer_defined_parameter: '{{ payload }}'}
""")
    event = manifest.events["event-1"]
    assert event.webhook_config == {"adapter_owned_key": 42}
    assert event.subscriptions[0].input_mapping == {
        "customer_defined_parameter": "{{ payload }}",
    }


@pytest.mark.parametrize("source", ["events: {}\nevnts: {}", "workflows: {}"])
def test_split_file_wrapper_keys_are_checked_before_section_projection(source):
    with pytest.raises(DefinitionValidationError) as error:
        read_workspace_manifest("events.yaml", source)
    assert error.value.issues[0].code == "unknown_section"


@pytest.mark.parametrize("source", ["false", "[]", "plain scalar", "null"])
def test_nonempty_invalid_document_cannot_become_an_empty_manifest(source):
    with pytest.raises(DefinitionValidationError) as error:
        read_workspace_manifest("events.yaml", source)
    assert error.value.issues[0].code == "mapping_required"


def test_duplicate_yaml_key_is_rejected_before_loading_selects_one_value():
    with pytest.raises(DefinitionValidationError) as error:
        read_yaml_mapping("is_active: true\nis_active: false\n", path="events.yaml")
    assert error.value.issues[0].code == "duplicate_key"
    assert error.value.issues[0].line == 2


def test_yaml_merge_overrides_still_work():
    assert read_yaml_mapping("""defaults: &defaults
  is_active: true
entry:
  <<: *defaults
  is_active: false
""", path="events.yaml")["entry"] == {"is_active": False}


def test_descriptor_recognized_alias_is_distinct_from_an_unknown_key():
    descriptor = validate_definition(SolutionDescriptor, {
        "slug": "example", "name": "Example", "global_repo_access": True,
    }, path="bifrost.solution.yaml")
    assert descriptor.allow_outbound_access is True
    with pytest.raises(DefinitionValidationError) as error:
        validate_definition(SolutionDescriptor, {
            "slug": "example", "name": "Example", "allow_outbond_access": True,
        }, path="bifrost.solution.yaml")
    assert error.value.issues[0].location == ("allow_outbond_access",)


@pytest.mark.parametrize("binding,decorator", [
    ("from bifrost import workflow", "workflow"),
    ("from bifrost import workflow as wf", "wf"),
    ("import bifrost as b", "b.workflow"),
    ("import bifrost.decorators as d", "d.workflow"),
])
def test_decorator_typo_is_detected_without_importing_user_source(binding, decorator):
    issues = validate_decorator_keywords(
        f"{binding}\nraise RuntimeError('must not execute')\n"
        f"@{decorator}(naem=unknown_expression())\nasync def run(): pass\n",
        path="workflows/example.py",
    )
    assert len(issues) == 1
    assert issues[0].location == ("run", "workflow", "naem")
    assert issues[0].line == 3


@pytest.mark.parametrize("name", ["workflow", "tool", "data_provider", "service"])
def test_supported_decorator_contracts_are_read_from_the_real_signatures(name):
    assert validate_decorator_keywords(
        f"from bifrost import {name}\n@{name}(name='Example', tags=['x'])\n"
        "async def run(): pass\n", path="example.py",
    ) == []


def test_unrelated_library_decorator_is_not_treated_as_bifrost():
    assert validate_decorator_keywords(
        "from another_package import workflow\n@workflow(custom=True)\n"
        "def run(): pass\n", path="example.py",
    ) == []


def test_literal_keyword_expansion_is_checked_and_dynamic_expansion_is_refused():
    source = "from bifrost import workflow\n@workflow(**{'naem': 'Example'})\ndef run(): pass\n"
    assert validate_decorator_keywords(source, path="example.py")[0].code == "unknown_keyword"
    dynamic = source.replace("{'naem': 'Example'}", "runtime_options")
    assert validate_decorator_keywords(dynamic, path="example.py")[0].code == "unverifiable_keywords"


def test_diagnostics_do_not_include_the_rejected_value():
    with pytest.raises(DefinitionValidationError) as error:
        validate_definition(SolutionDescriptor, {
            "slug": "example", "name": "Example", "unknown": "synthetic-secret-value",
        }, path="bifrost.solution.yaml")
    assert "synthetic-secret-value" not in str(error.value)
