"""E2E tests for PolicyRuleService — CRUD, delete guard, built-ins."""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

from src.models.contracts.policy_rule import PolicyRuleCreate, PolicyRuleUpdate
from src.models.orm.file_metadata import FilePolicy
from src.models.orm.organizations import Organization
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.solutions import Solution
from src.services.audit_context import ActorContext
from src.services.policy_rule_service import (
    PolicyRuleInUse,
    PolicyRuleReadOnly,
    PolicyRuleService,
)
from src.services.solutions.guard import install_solution_write_guard

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def seed_org(db_session):
    org = Organization(id=uuid4(), name=f"seed-{uuid4().hex[:6]}", created_by="t")
    db_session.add(org)
    await db_session.flush()
    return org.id


@pytest.fixture
def admin_actor():
    return ActorContext(user_id=uuid4(), organization_id=None, source="test")


@pytest.mark.asyncio
async def test_delete_blocked_while_referenced(db_session, seed_org, admin_actor):
    svc = PolicyRuleService(db_session)
    await svc.create(
        PolicyRuleCreate(
            name="ops",
            domain="file",
            organization_id=seed_org,
            body={"actions": ["read"], "when": None},
        ),
        actor=admin_actor,
    )
    db_session.add(
        FilePolicy(
            organization_id=seed_org,
            location="shared",
            path="d/",
            policies={"policies": [{"$ref": "ops"}]},
        )
    )
    await db_session.flush()
    with pytest.raises(PolicyRuleInUse):
        await svc.delete("ops", "file", org_id=seed_org, actor=admin_actor)


@pytest.mark.asyncio
async def test_seeds_both_domains_idempotent_and_readonly(db_session, admin_actor):
    svc = PolicyRuleService(db_session)
    await svc.seed_builtin_admin_bypass()
    await svc.seed_builtin_admin_bypass()
    rows = (
        await db_session.execute(select(PolicyRule).where(PolicyRule.name == "admin_bypass"))
    ).scalars().all()
    assert {r.domain for r in rows} == {"file", "table"}
    assert len(rows) == 2
    with pytest.raises(PolicyRuleReadOnly):
        await svc.update(
            "admin_bypass", "file", PolicyRuleUpdate(description="x"), org_id=None, actor=admin_actor
        )


@pytest.mark.asyncio
async def test_solution_managed_rule_update_and_delete_raise_409(db_session, seed_org, admin_actor):
    """Solution-managed PolicyRule must 409 on update and delete, not 500."""
    install_solution_write_guard()
    sol = Solution(
        id=uuid4(),
        slug=f"test-sol-{uuid4().hex[:8]}",
        name="Test Solution",
        organization_id=seed_org,
    )
    db_session.add(sol)
    await db_session.flush()

    svc = PolicyRuleService(db_session)
    row = PolicyRule(
        organization_id=seed_org,
        name="sol-rule",
        domain="file",
        description="solution-owned",
        body={"actions": ["read"], "when": None},
        solution_id=sol.id,
    )
    db_session.add(row)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await svc.update(
            "sol-rule", "file", PolicyRuleUpdate(description="hacked"), org_id=seed_org, actor=admin_actor
        )
    assert exc.value.status_code == 409

    with pytest.raises(HTTPException) as exc:
        await svc.delete("sol-rule", "file", org_id=seed_org, actor=admin_actor)
    assert exc.value.status_code == 409
