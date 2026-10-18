"""Prospective policy analysis stays conservative without resource data."""

from uuid import UUID

from shared.workflow_conversion_policy import (
    analyze_file_policies,
    analyze_table_policies,
)
from src.core.principal import UserPrincipal
from src.models.contracts.policies import FilePolicies, TablePolicies


def _principal(**changes: object) -> UserPrincipal:
    values: dict[str, object] = {
        "user_id": UUID("00000000-0000-0000-0000-000000000001"),
        "email": "synthetic@example.invalid",
        "organization_id": UUID("00000000-0000-0000-0000-000000000002"),
        "policy_authorities": frozenset({"platform_admin"}),
        "is_superuser": True,
    }
    values.update(changes)
    return UserPrincipal(**values)  # type: ignore[arg-type]


def _table_policy(action: str, when: dict) -> TablePolicies:
    return TablePolicies.model_validate({"policies": [{
        "name": "synthetic", "actions": [action], "when": when,
    }]})


def test_unused_unknown_role_branch_does_not_make_true_or_conditional() -> None:
    result = analyze_table_policies(
        _table_policy("read", {"or": [True, {"call": "has_role", "args": ["missing"]}]}),
        "read",
        _principal(),
    )

    assert result.kind == "allowed"
    assert result.reasons == ()


def test_wrong_action_is_ignored_and_default_deny_is_not_conditional() -> None:
    result = analyze_table_policies(
        _table_policy("create", {"row": "owner_id"}),
        "read",
        _principal(),
    )

    assert result.kind == "denied"
    assert result.reasons == ()


def test_synthetic_principal_has_no_full_authority() -> None:
    result = analyze_table_policies(
        _table_policy("read", {"authority": "platform_admin"}),
        "read",
        _principal(),
        known_user_fields=frozenset({"is_platform_admin"}),
    )

    assert result.kind == "denied"


def test_false_and_branch_overrides_unknown_row_reference() -> None:
    result = analyze_table_policies(
        _table_policy("read", {"and": [{"row": "owner_id"}, False]}),
        "read",
        _principal(),
    )

    assert result.kind == "denied"
    assert result.reasons == ()


def test_true_or_branch_overrides_unknown_row_reference() -> None:
    result = analyze_table_policies(
        _table_policy("read", {"or": [{"row": "owner_id"}, True]}),
        "read",
        _principal(),
    )

    assert result.kind == "allowed"
    assert result.reasons == ()


def test_unsupplied_claims_remain_conditional() -> None:
    result = analyze_table_policies(
        _table_policy("read", {"in": ["member", {"claims": "groups"}]}),
        "read",
        _principal(),
    )

    assert result.kind == "conditional"
    assert [reason.kind for reason in result.reasons] == ["claim"]


def test_synthetic_identity_fields_are_unknown_until_explicitly_supplied() -> None:
    result = analyze_table_policies(
        _table_policy("read", {"neq": [{"user": "email"}, "other@example.invalid"]}),
        "read",
        _principal(),
    )

    assert result.kind == "conditional"
    assert [reason.kind for reason in result.reasons] == ["user"]


def test_fixed_file_path_can_prove_a_file_policy() -> None:
    policies = FilePolicies.model_validate({"policies": [{
        "name": "synthetic", "actions": ["read"],
        "when": {"eq": [{"file": "path"}, "reports"]},
    }]})

    result = analyze_file_policies(
        policies,
        "read",
        _principal(),
        known_file_fields={"location": "workspace", "path": "reports"},
    )

    assert result.kind == "allowed"


def test_unknown_file_metadata_remains_conditional() -> None:
    policies = FilePolicies.model_validate({"policies": [{
        "name": "synthetic", "actions": ["read"],
        "when": {"eq": [{"file": "created_by"}, "synthetic"]},
    }]})

    result = analyze_file_policies(
        policies,
        "read",
        _principal(),
        known_file_fields={"location": "workspace", "path": "reports"},
    )

    assert result.kind == "conditional"
    assert [reason.kind for reason in result.reasons] == ["file"]
