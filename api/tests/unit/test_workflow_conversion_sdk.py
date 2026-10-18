"""SDK candidates come from real SDK transports and canonical access entries."""

from shared.workflow_conversion_sdk import permissions_for_sdk_calls, sdk_permission_fingerprint


def test_sdk_method_names_map_to_actual_transport_permissions() -> None:
    permissions, unknowns = permissions_for_sdk_calls([
        "bifrost.organizations.create", "bifrost.organizations.list", "bifrost.ai.complete",
    ])
    assert permissions == ["ai.execute", "organizations.read", "organizations.readwrite"]
    assert unknowns == []


def test_file_and_document_policies_require_review_instead_of_invented_grants() -> None:
    permissions, unknowns = permissions_for_sdk_calls(["bifrost.files.read", "bifrost.tables.query"])
    assert permissions == []
    assert len(unknowns) == 2
    assert all("Resource policy" in item for item in unknowns)


def test_secret_and_personal_authority_decisions_remain_visible() -> None:
    permissions, unknowns = permissions_for_sdk_calls(["bifrost.config.get", "bifrost.artifacts.list"])
    assert "secrets.read" in permissions
    assert not any(permission.endswith(".all") for permission in permissions)
    assert any("secret" in item for item in unknowns)
    assert any("ownership" in item for item in unknowns)


def test_unknown_call_is_never_treated_as_no_permissions_needed() -> None:
    permissions, unknowns = permissions_for_sdk_calls(["bifrost.future.method"])
    assert permissions == []
    assert len(unknowns) == 1


def test_catalog_and_sdk_version_participate_in_proposal_versioning() -> None:
    assert len(sdk_permission_fingerprint()) == 64
