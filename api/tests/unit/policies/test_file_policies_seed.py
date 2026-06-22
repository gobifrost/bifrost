from shared.file_policies_seed import make_seed_admin_bypass_file
from src.models.contracts.policies import FilePolicies


def test_seed_is_valid_file_policies_with_admin_rule():
    seed = make_seed_admin_bypass_file()
    parsed = FilePolicies.model_validate(seed)
    assert len(parsed.policies) == 1
    rule = parsed.policies[0]
    assert rule.name == "admin_bypass"
    assert set(rule.actions) == {"read", "write", "delete", "list"}
    assert rule.when is not None
    assert rule.when.root == {"user": "is_platform_admin"}
