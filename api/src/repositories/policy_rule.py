"""PolicyRule cascade repository."""
from src.models.orm.policy_rule import PolicyRule
from src.repositories.org_scoped import OrgScopedRepository


class PolicyRuleRepository(OrgScopedRepository[PolicyRule]):
    """Cascade org→global resolution for named policy rules."""

    model = PolicyRule
    role_table = None
