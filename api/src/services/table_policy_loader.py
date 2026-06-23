"""Single resolving loader for table policy evaluation.

All table-policy eval paths MUST call `load_resolved_table_policies` rather
than validate `table.access` directly.  A `PolicyRuleRef` that reaches
`preresolve_for_policies` / `compile_read_filter` has no `.when` attribute
and will raise AttributeError — this loader inlines refs before callers
hand the doc to those functions.
"""
from __future__ import annotations

import logging

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.policy_rules import (
    PolicyRuleDomainMismatch,
    PolicyRuleNotFound,
    resolve_policy_refs,
)
from src.models.contracts.policies import TablePolicies
from src.models.orm.tables import Table
from src.repositories.policy_rule import PolicyRuleRepository

logger = logging.getLogger(__name__)


async def load_resolved_table_policies(table: Table, db: AsyncSession) -> TablePolicies:
    """Load and resolve table policies for evaluation.

    Validates ``table.access``, then inlines every ``{"$ref": name}`` entry by
    fetching the named rule from the DB (own-solution → org → global cascade).
    Returns an empty ``TablePolicies()`` (default-deny) when:

    - ``table.access`` is None / empty
    - the JSONB is malformed (validation error)
    - any ref cannot be resolved (PolicyRuleNotFound / PolicyRuleDomainMismatch)

    The last case logs a warning so operators can spot stale or misconfigured
    rule references without silently allowing access.
    """
    if not table.access:
        return TablePolicies()

    try:
        policies = TablePolicies.model_validate(table.access)
    except ValidationError as exc:
        logger.warning(
            "malformed table policies for %s; denying: %s",
            table.id,
            exc,
        )
        return TablePolicies()

    repo = PolicyRuleRepository(db, org_id=table.organization_id, is_superuser=True)
    try:
        await resolve_policy_refs(
            policies,
            repo=repo,
            action_domain="table",
            solution_id=table.solution_id,
        )
    except (PolicyRuleNotFound, PolicyRuleDomainMismatch) as exc:
        logger.warning(
            "unresolvable policy ref on table %s; denying: %s",
            table.id,
            exc,
        )
        return TablePolicies()

    return policies
