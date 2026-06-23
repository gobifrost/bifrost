import pytest
from pydantic import ValidationError
from src.models.contracts.policies import FilePolicies, TablePolicies, PolicyRuleRef


def test_ref_parses_via_dollar_alias():
    assert PolicyRuleRef.model_validate({"$ref": "ab"}).ref == "ab"


def test_mixed_ref_plus_inline_is_rejected():
    # F7: an entry carrying BOTH $ref and inline fields must NOT silently parse as inline.
    with pytest.raises(ValidationError):
        FilePolicies.model_validate({"policies": [
            {"$ref": "ab", "name": "r", "actions": ["read"], "when": None}]})


def test_ref_with_extra_key_is_rejected():
    with pytest.raises(ValidationError):
        FilePolicies.model_validate({"policies": [{"$ref": "ab", "actions": ["read"]}]})


def test_clean_mixed_list_ok():
    doc = FilePolicies.model_validate({"policies": [
        {"$ref": "ab"}, {"name": "r", "actions": ["read"], "when": None}]})
    assert isinstance(doc.policies[0], PolicyRuleRef)
    assert doc.policies[1].name == "r"


def test_table_ref_ok():
    assert isinstance(TablePolicies.model_validate({"policies": [{"$ref": "x"}]}).policies[0], PolicyRuleRef)
