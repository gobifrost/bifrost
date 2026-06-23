"""E2E tests for PolicyRuleRepository cascade + override-aware where-used."""
from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio

from src.models.orm.file_metadata import FilePolicy
from src.models.orm.organizations import Organization
from src.models.orm.policy_rule import PolicyRule
from src.models.orm.tables import Table
from src.repositories.policy_rule import PolicyRuleRepository
from shared.policy_rules import find_policy_rule_usages

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def seed_org(db_session):
    org = Organization(id=uuid4(), name=f"seed-{uuid4().hex[:6]}", created_by="t")
    db_session.add(org)
    await db_session.flush()
    return org.id


@pytest_asyncio.fixture
async def other_org(db_session):
    org = Organization(id=uuid4(), name=f"other-{uuid4().hex[:6]}", created_by="t")
    db_session.add(org)
    await db_session.flush()
    return org.id


@pytest.mark.asyncio
async def test_get_cascades_org_over_global(db_session, seed_org):
    db_session.add(PolicyRule(name="r", domain="file", body={"actions": ["read"], "when": None}))
    db_session.add(PolicyRule(name="r", domain="file", organization_id=seed_org, body={"actions": ["write"], "when": None}))
    await db_session.flush()
    repo = PolicyRuleRepository(db_session, org_id=seed_org, is_superuser=True)
    result = await repo.get(name="r", domain="file")
    assert result is not None
    assert result.body["actions"] == ["write"]


@pytest.mark.asyncio
async def test_where_used_for_global_skips_org_with_override(db_session, seed_org, other_org):
    # global rule "ops" + an org override of "ops" in seed_org.
    db_session.add(PolicyRule(name="ops", domain="file", body={"actions": ["read"], "when": None}))
    db_session.add(PolicyRule(name="ops", domain="file", organization_id=seed_org, body={"actions": ["read"], "when": None}))
    # seed_org policy references "ops" → resolves to the OVERRIDE, not the global.
    db_session.add(FilePolicy(organization_id=seed_org, location="shared", path="a/", policies={"policies": [{"$ref": "ops"}]}))
    # other_org policy references "ops" → resolves to the GLOBAL.
    db_session.add(FilePolicy(organization_id=other_org, location="shared", path="b/", policies={"policies": [{"$ref": "ops"}]}))
    await db_session.flush()
    u = await find_policy_rule_usages(db_session, "ops", "file", org_id=None)  # global rule
    locs = {f["location"] + f["path"] for f in u.file_policies}
    assert "shareda/" not in locs  # seed_org overrides → NOT a usage of the global
    assert "sharedb/" in locs  # other_org → genuine usage of the global


@pytest.mark.asyncio
async def test_domain_filter_excludes_cross_domain(db_session, seed_org):
    """Domain filter (.where(false())) must exclude cross-domain entities.

    A named rule referenced as {"$ref": "shared_name"} in BOTH a Table.access
    and a FilePolicy.policies must be hidden from the wrong domain search.
    """
    ref_json = [{"$ref": "shared_name"}]
    # Seed a Table whose access references "shared_name" in seed_org.
    db_session.add(
        Table(
            name=f"t-{uuid4().hex[:6]}",
            organization_id=seed_org,
            access={"policies": ref_json},
        )
    )
    # Seed a FilePolicy that also references "shared_name" in seed_org.
    db_session.add(
        FilePolicy(
            organization_id=seed_org,
            location="shared",
            path="x/",
            policies={"policies": ref_json},
        )
    )
    await db_session.flush()

    # domain="file": table must be excluded (false() filter), file policy must appear.
    u_file = await find_policy_rule_usages(db_session, "shared_name", "file", org_id=seed_org)
    assert u_file.tables == [], "domain=file should exclude tables via WHERE false()"
    assert len(u_file.file_policies) == 1, "domain=file should include the matching file policy"

    # domain="table": file policy must be excluded, table must appear.
    u_table = await find_policy_rule_usages(db_session, "shared_name", "table", org_id=seed_org)
    assert u_table.file_policies == [], "domain=table should exclude file policies via WHERE false()"
    assert len(u_table.tables) == 1, "domain=table should include the matching table"
