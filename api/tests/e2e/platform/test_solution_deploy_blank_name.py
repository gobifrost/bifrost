"""A solution bundle with a blank-name agent must fail the deploy loudly,
not silently swallow the agent while reporting success (the §8 live-repro bug)."""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.solutions import Solution
from src.services.solutions.deploy import SolutionBundle, SolutionDeployer, SolutionDeployConflict


pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _guard():
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    yield


async def _make_solution(db) -> Solution:
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"blank-name-{uuid.uuid4().hex[:8]}",
        name="Blank Name Test",
        organization_id=None,
    )
    db.add(sol)
    await db.flush()
    return sol


@pytest.mark.asyncio
async def test_deploy_blank_name_agent_raises(db_session):
    sol = await _make_solution(db_session)
    bundle = SolutionBundle(
        solution=sol,
        agents=[{
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "",  # blank — must not silently vanish
            "system_prompt": "You are a test agent.",
        }],
    )
    with pytest.raises(SolutionDeployConflict):
        await SolutionDeployer(db_session).deploy(bundle, force=True)
