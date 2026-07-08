"""E2E: SolutionDeployer upserts + reconciles solution-tier file policies (Task A3).

Deploy pipeline: file locations are reconciled first (seeding a root admin_bypass
per declared location), then ``_upsert_file_policies`` upserts the bundle's
solution-tier policies by natural key ``(solution_id, location, path)``.

Asserts:
  - a bundle referencing a solution-scoped policy installs cleanly (no 409),
  - the policy row lands solution-scoped (``solution_id`` == install id),
  - a redeploy WITHOUT the policy deletes the stale row,
  - the seeded root admin_bypass (``path=""``) survives across redeploys.

The read-only guard is installed (as in prod) so any accidental ORM write to a
solution_id-bearing row would fail — deploy must use Core statements only.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.models.orm.file_metadata import FilePolicy
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.policy_rule_service import PolicyRuleService
from src.services.solutions.deploy import SolutionBundle, SolutionDeployer

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _guard():
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    yield


@pytest_asyncio.fixture
async def org(db_session):
    o = Organization(
        id=uuid.uuid4(), name=f"fp-deploy-{uuid.uuid4().hex[:6]}", created_by="t"
    )
    db_session.add(o)
    await db_session.flush()
    return o


@pytest_asyncio.fixture
async def install(db_session, org):
    s = Solution(
        id=uuid.uuid4(),
        slug=f"fp-deploy-{uuid.uuid4().hex[:6]}",
        name="File Policy Deploy Test",
        organization_id=org.id,
    )
    db_session.add(s)
    await db_session.flush()
    # The seeded root admin_bypass references the file-domain built-in rule.
    await PolicyRuleService(db_session).seed_builtin_admin_bypass()
    await db_session.flush()
    return s


def _prefix_policy(policy_id: str, path: str) -> dict:
    """A portable (INSTALL-view-shaped) file policy carrying an inline rule."""
    return {
        "id": policy_id,
        "location": "shared",
        "path": path,
        "policies": [
            {
                "name": "readers",
                "actions": ["read"],
                "when": {"user": "is_platform_admin"},
            }
        ],
    }


async def _deploy(db_session, bundle: SolutionBundle) -> None:
    deployer = SolutionDeployer(db_session)
    result = await deployer.deploy(bundle)
    await db_session.commit()
    await result.finalize_s3()


async def _policies(db_session, install_id) -> dict[str, dict]:
    rows = (
        await db_session.execute(
            select(FilePolicy).where(FilePolicy.solution_id == install_id)
        )
    ).scalars().all()
    return {r.path: r for r in rows}


@pytest.mark.asyncio
async def test_deploy_lands_solution_scoped_policy(db_session, install):
    """A bundle with a solution-scoped policy installs cleanly + lands scoped."""
    pid = str(uuid.uuid4())
    bundle = SolutionBundle(
        solution=install,
        file_locations=["shared"],
        file_policies=[_prefix_policy(pid, "finance")],
    )

    await _deploy(db_session, bundle)  # must NOT raise (no 409)

    by_path = await _policies(db_session, install.id)
    # Root seed (path="") + the finance prefix are both present, solution-scoped.
    assert "" in by_path, "seeded root admin_bypass missing"
    assert "finance" in by_path, "solution-scoped prefix policy did not land"
    finance = by_path["finance"]
    assert finance.solution_id == install.id
    assert finance.organization_id == install.organization_id
    assert finance.policies == {
        "policies": [
            {"name": "readers", "actions": ["read"], "when": {"user": "is_platform_admin"}}
        ]
    }


@pytest.mark.asyncio
async def test_redeploy_drops_stale_policy_root_survives(db_session, install):
    """Redeploy without a policy deletes the stale row; root seed survives."""
    keep_id = str(uuid.uuid4())
    drop_id = str(uuid.uuid4())
    bundle1 = SolutionBundle(
        solution=install,
        file_locations=["shared"],
        file_policies=[
            _prefix_policy(keep_id, "keep"),
            _prefix_policy(drop_id, "drop"),
        ],
    )
    await _deploy(db_session, bundle1)

    after_first = await _policies(db_session, install.id)
    assert {"", "keep", "drop"} <= set(after_first)
    root_id_first = after_first[""].id

    # Redeploy WITHOUT the "drop" prefix.
    bundle2 = SolutionBundle(
        solution=install,
        file_locations=["shared"],
        file_policies=[_prefix_policy(keep_id, "keep")],
    )
    await _deploy(db_session, bundle2)

    after_second = await _policies(db_session, install.id)
    assert "drop" not in after_second, "stale policy was not swept on redeploy"
    assert "keep" in after_second, "surviving policy was wrongly deleted"
    # Seeded root admin_bypass survives (same row, never double-inserted).
    assert "" in after_second, "root admin_bypass was deleted (regression)"
    assert after_second[""].id == root_id_first, "root seed row was replaced"


@pytest.mark.asyncio
async def test_redeploy_customized_root_updates_seed_in_place(db_session, install):
    """A bundle carrying a path="" policy UPDATES the seed, never double-inserts."""
    # First deploy seeds the root via file-location reconcile.
    await _deploy(
        db_session,
        SolutionBundle(solution=install, file_locations=["shared"], file_policies=[]),
    )
    seed = (await _policies(db_session, install.id))[""]
    seed_id = seed.id

    # Redeploy with a customized root policy (path="").
    custom_root = _prefix_policy(str(uuid.uuid4()), "")
    await _deploy(
        db_session,
        SolutionBundle(
            solution=install,
            file_locations=["shared"],
            file_policies=[custom_root],
        ),
    )

    after = await _policies(db_session, install.id)
    # Exactly one root row (no double-insert) and it carries the customized doc.
    root_rows = [p for p in after.values() if p.path == ""]
    assert len(root_rows) == 1, "root policy was double-inserted"
    assert root_rows[0].id == seed_id, "root upsert replaced the seed row id"
    assert root_rows[0].policies == {
        "policies": [
            {"name": "readers", "actions": ["read"], "when": {"user": "is_platform_admin"}}
        ]
    }
