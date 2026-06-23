from src.models.orm.policy_rule import PolicyRule


def test_columns_and_defaults():
    r = PolicyRule(name="ops", domain="file", body={"actions": ["write"], "when": None})
    assert r.organization_id is None and r.is_builtin is False
    for col in ("id", "name", "domain", "description", "body", "is_builtin", "created_by", "created_at", "updated_at"):
        assert hasattr(r, col)


def test_partial_unique_indexes_declared():
    idx = {i.name: i for i in PolicyRule.__table__.indexes}
    assert "uq_policy_rules_global_name_domain" in idx
    assert "uq_policy_rules_org_name_domain" in idx
    assert idx["uq_policy_rules_global_name_domain"].unique
    assert idx["uq_policy_rules_org_name_domain"].unique
