from shared.file_policies_seed import make_seed_admin_bypass_file
from src.models.contracts.policies import FilePolicies, PolicyRuleRef


def test_seed_is_valid_file_policies_with_admin_ref():
    seed = make_seed_admin_bypass_file()
    parsed = FilePolicies.model_validate(seed)
    assert len(parsed.policies) == 1
    rule = parsed.policies[0]
    assert isinstance(rule, PolicyRuleRef)
    assert rule.ref == "admin_bypass"
