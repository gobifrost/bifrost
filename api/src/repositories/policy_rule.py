"""PolicyRule cascade repository."""
from uuid import UUID

from sqlalchemy import or_, select

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
        # Cross-domain detection: direct org-cascade query, name only, no domain filter.
        # The unique index ensures at most one non-solution row per (org, name, domain),
        # but same-name rules CAN exist for different domains in the same org. We use
        # .limit(1) + .first() instead of scalar_one_or_none() to avoid
        # MultipleResultsFound in that edge case — the caller only needs to know that
        # A rule of this name exists (to raise PolicyRuleDomainMismatch vs PolicyRuleNotFound).
        scope_filter = (
            or_(
                PolicyRule.organization_id == self.org_id,
                PolicyRule.organization_id.is_(None),
            )
            if self.org_id is not None
            else PolicyRule.organization_id.is_(None)
        )
        cross_q = (
            select(PolicyRule)
            .where(
                PolicyRule.name == name,
                PolicyRule.solution_id.is_(None),
                scope_filter,
            )
            .limit(1)
        )
        result = await self.session.execute(cross_q)
        return result.scalars().first()
