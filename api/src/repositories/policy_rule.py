"""PolicyRule cascade repository."""
from uuid import UUID

from sqlalchemy import select

from src.models.orm.policy_rule import PolicyRule
from src.repositories.org_scoped import OrgScopedRepository


class PolicyRuleRepository(OrgScopedRepository[PolicyRule]):
    """Cascade org→global resolution for named policy rules."""

    model = PolicyRule
    role_table = None

    async def get_for_ref(
        self, *, name: str, domain: str, solution_id: UUID | None = None
    ) -> PolicyRule | None:
        """Resolve a policy rule by (name, domain) with optional solution-first arm.

        Returns the best-matching row for the ref so the caller can check domain
        and raise PolicyRuleDomainMismatch (not PolicyRuleNotFound) when a rule
        with the right name exists but has the wrong domain.

        Resolution order:
          1. If solution_id is set: own-solution row (solution_id, name, domain) exact match.
          2. Exact (name, domain) cascade via OrgScopedRepository.get(name=, domain=).
          3. Cross-domain detection: direct query for (name, solution_id IS NULL) in the org
             cascade scope — returns a wrong-domain row so the caller can raise
             PolicyRuleDomainMismatch instead of PolicyRuleNotFound.
        """
        if solution_id is not None:
            stmt = select(PolicyRule).where(
                PolicyRule.solution_id == solution_id,
                PolicyRule.name == name,
                PolicyRule.domain == domain,
            )
            result = await self.session.execute(stmt)
            own = result.scalar_one_or_none()
            if own is not None:
                return own
        # Try exact domain match first (happy path)
        row = await self.get(name=name, domain=domain)
        if row is not None:
            return row
        # Cross-domain detection via the canonical cascade (no inline org filter):
        # list() applies _apply_cascade_scope (org→global). The solution_id IS NULL
        # exclusion lives in get(), not list(), so we pass it explicitly here so a
        # different solution's rule can't leak into cross-domain detection.
        rows = await self.list(name=name, solution_id=None)
        return rows[0] if rows else None
