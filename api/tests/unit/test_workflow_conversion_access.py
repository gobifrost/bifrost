from uuid import UUID

from shared.access_checks import ALL_ORGS
from shared.workflow_conversion_access import new_identity_requirements
from src.services.recommended_access import CheckedAction


HOME = UUID("00000000-0000-0000-0000-000000000011")
OTHER = UUID("00000000-0000-0000-0000-000000000022")


def test_a_new_identity_does_not_inherit_observed_foreign_or_all_org_reach() -> None:
    result = new_identity_requirements([
        CheckedAction("scope_switch", HOME, ()),
        CheckedAction("scope_switch", OTHER, ()),
        CheckedAction("child_run", ALL_ORGS, ()),
    ], HOME)
    assert len(result) == 2
    assert any(str(OTHER) in note for note in result)
    assert any("all organizations" in note for note in result)


def test_policy_roles_and_personal_claims_are_requirements_not_automatic_grants() -> None:
    result = new_identity_requirements([
        CheckedAction("policy", HOME, ("role:Operators", "claim:region")),
        CheckedAction("policy", HOME, ("role:Operators",)),
    ], HOME)
    assert len(result) == 2
    assert any("additional roles" in note for note in result)
    assert any("personal claim" in note for note in result)
