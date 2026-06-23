"""E2E tests for resolve_policy_refs: domain-checked, hard-fail + own-solution→org→global arm."""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio

from src.models.orm.organizations import Organization
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.solutions import Solution
from src.models.contracts.policies import FilePolicies, FilePolicyRule, TablePolicies
from src.repositories.policy_rule import PolicyRuleRepository
from shared.policy_rules import resolve_policy_refs, PolicyRuleNotFound, PolicyRuleDomainMismatch

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def seed_org(db_session):
    org = Organization(id=uuid4(), name=f"seed-{uuid4().hex[:6]}", created_by="t")
    db_session.add(org)
    await db_session.flush()
    return org.id


# ---------------------------------------------------------------------------
# Core resolver tests (solution_id=None → org→global cascade)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolves_inline(db_session, seed_org):
    db_session.add(PolicyRule(
        name="ab", domain="file", organization_id=seed_org,
        body={"actions": ["read", "write", "delete", "list"], "when": {"user": "is_platform_admin"}},
    ))
    await db_session.flush()
    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    doc = FilePolicies.model_validate({"policies": [{"$ref": "ab"}, {"name": "x", "actions": ["read"], "when": None}]})
    await resolve_policy_refs(doc, repo=repo, action_domain="file")
    assert all(isinstance(p, FilePolicyRule) for p in doc.policies)
    assert doc.policies[0].name == "ab" and doc.policies[1].name == "x"  # order preserved


@pytest.mark.asyncio
async def test_missing_raises(db_session, seed_org):
    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    doc = FilePolicies.model_validate({"policies": [{"$ref": "nope"}]})
    with pytest.raises(PolicyRuleNotFound):
        await resolve_policy_refs(doc, repo=repo, action_domain="file")


@pytest.mark.asyncio
async def test_cross_domain_raises(db_session, seed_org):
    db_session.add(PolicyRule(
        name="t", domain="table", organization_id=seed_org,
        body={"actions": ["create"], "when": None},
    ))
    await db_session.flush()
    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    doc = FilePolicies.model_validate({"policies": [{"$ref": "t"}]})  # table rule in a file policy
    with pytest.raises(PolicyRuleDomainMismatch):
        await resolve_policy_refs(doc, repo=repo, action_domain="file")


# ---------------------------------------------------------------------------
# Solution-arm tests (solution_id set → own-solution → org → global)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def seed_solution(db_session, seed_org):
    sol = Solution(
        id=uuid4(),
        slug=f"test-sol-{uuid4().hex[:8]}",
        name="Test Solution",
        organization_id=seed_org,
    )
    db_session.add(sol)
    await db_session.flush()
    return sol.id


@pytest.mark.asyncio
async def test_solution_rule_wins_over_org_rule(db_session, seed_org, seed_solution):
    """Own-solution rule takes priority over an org rule of the same (name, domain)."""
    # Org-level rule for "ab" — should be shadowed
    db_session.add(PolicyRule(
        name="ab", domain="file", organization_id=seed_org,
        body={"actions": ["read"], "when": None},
    ))
    # Solution-scoped rule for "ab" — should WIN
    db_session.add(PolicyRule(
        name="ab", domain="file", organization_id=seed_org,
        solution_id=seed_solution,
        body={"actions": ["write"], "when": None},
    ))
    await db_session.flush()

    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    doc = FilePolicies.model_validate({"policies": [{"$ref": "ab"}]})
    await resolve_policy_refs(doc, repo=repo, action_domain="file", solution_id=seed_solution)
    assert isinstance(doc.policies[0], FilePolicyRule)
    # Must have resolved to the SOLUTION rule's body (write), not the org rule (read)
    assert doc.policies[0].actions == ["write"]


@pytest.mark.asyncio
async def test_solution_falls_back_to_org_global(db_session, seed_org, seed_solution):
    """When the solution has no own rule, falls back to org→global cascade."""
    # Only an org-level rule — no solution rule
    db_session.add(PolicyRule(
        name="fallback", domain="file", organization_id=seed_org,
        body={"actions": ["list"], "when": None},
    ))
    await db_session.flush()

    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    doc = FilePolicies.model_validate({"policies": [{"$ref": "fallback"}]})
    await resolve_policy_refs(doc, repo=repo, action_domain="file", solution_id=seed_solution)
    assert isinstance(doc.policies[0], FilePolicyRule)
    assert doc.policies[0].actions == ["list"]
