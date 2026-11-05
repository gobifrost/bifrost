"""Unit coverage for strict workspace-manifest decoding."""

from __future__ import annotations

import pytest


def test_unknown_nested_manifest_field_is_rejected_before_model_projection() -> None:
    from bifrost.definition_validation import DefinitionValidationError
    from bifrost.manifest import parse_manifest

    with pytest.raises(DefinitionValidationError) as error:
        parse_manifest(
            """events:
  event-1:
    id: event-1
    source_type: topic
    subscriptions:
      - id: sub-1
        criteria: {priority: high}
"""
        )

    assert error.value.issues[0].path == ".bifrost/metadata.yaml"
    assert error.value.issues[0].location == (
        "events", "event-1", "subscriptions", 0, "criteria"
    )
    assert error.value.issues[0].code == "extra_forbidden"


@pytest.mark.parametrize("source", ["false", "[]", "plain scalar", "null"])
def test_nonempty_invalid_document_is_not_treated_as_an_empty_manifest(source: str) -> None:
    from bifrost.definition_validation import DefinitionValidationError
    from bifrost.manifest import parse_manifest

    with pytest.raises(DefinitionValidationError) as error:
        parse_manifest(source)

    assert error.value.issues[0].code == "mapping_required"
    assert error.value.issues[0].path == ".bifrost/metadata.yaml"


def test_comment_only_document_is_empty_but_explicit_null_is_rejected() -> None:
    from bifrost.definition_validation import DefinitionValidationError
    from bifrost.manifest import parse_manifest

    assert parse_manifest("# intentionally empty\n").workflows == {}

    with pytest.raises(DefinitionValidationError) as error:
        parse_manifest("null\n")

    assert error.value.issues[0].code == "mapping_required"


def test_split_manifest_validates_wrapper_before_projecting_the_section() -> None:
    from bifrost.definition_validation import DefinitionValidationError
    from bifrost.manifest import parse_manifest_dir

    with pytest.raises(DefinitionValidationError) as error:
        parse_manifest_dir({"events.yaml": "events: {}\nz_section: {}\na_section: {}\n"})

    assert [(issue.location, issue.code) for issue in error.value.issues] == [
        (("a_section",), "unknown_section"),
        (("z_section",), "unknown_section"),
    ]
    assert all(issue.path == ".bifrost/events.yaml" for issue in error.value.issues)


def test_files_yaml_is_a_direct_strict_mapping() -> None:
    from bifrost.definition_validation import DefinitionValidationError
    from bifrost.manifest import parse_manifest_dir

    manifest = parse_manifest_dir({"files.yaml": "locations: [reports]\n"})
    assert manifest.files.locations == ["reports"]

    with pytest.raises(DefinitionValidationError) as error:
        parse_manifest_dir({"files.yaml": "locatoins: [reports]\n"})

    assert error.value.issues[0].location == ("files", "locatoins")
    assert error.value.issues[0].code == "extra_forbidden"


def test_open_data_dictionaries_remain_open_at_the_strict_boundary() -> None:
    from bifrost.manifest import parse_manifest

    manifest = parse_manifest(
        """events:
  event-1:
    id: event-1
    source_type: topic
    webhook_config: {adapter_owned_key: 42}
    subscriptions:
      - id: sub-1
        input_mapping: {customer_defined_parameter: '{{ payload }}'}
"""
    )

    event = manifest.events["event-1"]
    assert event.webhook_config == {"adapter_owned_key": 42}
    assert event.subscriptions[0].input_mapping == {
        "customer_defined_parameter": "{{ payload }}"
    }


def test_strict_workspace_validation_does_not_change_direct_model_parsing() -> None:
    from bifrost.definition_validation import DefinitionValidationError
    from bifrost.manifest import ManifestEventSource, parse_manifest

    direct = ManifestEventSource.model_validate({
        "id": "event-1",
        "source_type": "topic",
        "unsupported": True,
    })

    with pytest.raises(DefinitionValidationError):
        parse_manifest(
            """events:
  event-1:
    id: event-1
    source_type: topic
    unsupported: true
"""
        )

    assert direct.id == "event-1"


@pytest.mark.parametrize(
    "source",
    [
        "true: first\nTrue: second\n",
        "1: first\n01: second\n",
        "1: first\ntrue: second\n",
    ],
)
def test_duplicate_yaml_keys_are_rejected_using_constructed_key_equality(source: str) -> None:
    from bifrost.definition_validation import DefinitionValidationError, read_yaml_mapping

    with pytest.raises(DefinitionValidationError) as error:
        read_yaml_mapping(source, path=".bifrost/events.yaml")

    issue = error.value.issues[0]
    assert issue.code == "duplicate_key"
    assert issue.line == 2
    assert "line 2" in str(error.value)


def test_yaml_merge_override_remains_valid() -> None:
    from bifrost.definition_validation import read_yaml_mapping

    data = read_yaml_mapping(
        """defaults: &defaults
  is_active: true
entry:
  <<: *defaults
  is_active: false
""",
        path=".bifrost/events.yaml",
    )

    assert data["entry"] == {"is_active": False}


def test_yaml_parse_errors_do_not_echo_the_rejected_input() -> None:
    from bifrost.definition_validation import DefinitionValidationError, read_yaml_mapping

    with pytest.raises(DefinitionValidationError) as error:
        read_yaml_mapping("secret: *missing-secret\n", path=".bifrost/events.yaml")

    assert error.value.issues[0].code == "invalid_yaml"
    assert "missing-secret" not in str(error.value)


def test_directory_reader_uses_the_same_strict_split_decoder(tmp_path) -> None:
    from bifrost.definition_validation import DefinitionValidationError
    from bifrost.manifest import read_manifest_from_dir

    (tmp_path / "events.yaml").write_text("events: {}\nevnts: {}\n")

    with pytest.raises(DefinitionValidationError) as error:
        read_manifest_from_dir(tmp_path)

    assert error.value.issues[0].path == ".bifrost/events.yaml"
