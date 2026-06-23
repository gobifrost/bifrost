"""E2E tests for PolicyRuleService — CRUD, Core-update rename cascade, delete guard, built-ins."""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.models.contracts.policy_rule import PolicyRuleCreate, PolicyRuleUpdate
from src.models.orm.file_metadata import FilePolicy
from src.models.orm.organizations import Organization
from src.models.orm.policy_rule import PolicyRule
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
async def test_rename_cascades_via_core_update_under_guard(db_session, seed_org, admin_actor):
    install_solution_write_guard()  # prod-faithful: guard active
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
    await svc.update(
        "ops", "file", PolicyRuleUpdate(name="operations"), org_id=seed_org, actor=admin_actor
    )
    fp = (await db_session.execute(select(FilePolicy))).scalar_one()
    assert fp.policies["policies"] == [{"$ref": "operations"}]


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
