"""tool_description must survive a Solutions deploy (the §8 live-repro bug:
the field was dropped end-to-end across capture/manifest/deploy)."""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.solutions.deploy import SolutionBundle, SolutionDeployer, solution_entity_id


pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _guard():
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    yield


async def _make_solution(db) -> Solution:
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"tool-desc-{uuid.uuid4().hex[:8]}",
        name="Tool Description Test",
        organization_id=None,
    )
    db.add(sol)
    await db.flush()
    return sol


@pytest.mark.asyncio
async def test_deploy_carries_tool_description(db_session):
    sol = await _make_solution(db_session)
    wid = str(uuid.uuid4())
    bundle = SolutionBundle(
        solution=sol,
        workflows=[{
            "id": wid,
            "name": "hello",
            "function_name": "main",
            "path": "functions/hello.py",
            "type": "tool",
            "tool_description": "CURATED-TOOLDESC-DO-NOT-LOSE",
        }],
    )
    await SolutionDeployer(db_session).deploy(bundle, force=True)
    expected_id = solution_entity_id(sol.id, uuid.UUID(wid))
    row = await db_session.get(Workflow, expected_id)
    assert row is not None, "workflow was not deployed"
    assert row.tool_description == "CURATED-TOOLDESC-DO-NOT-LOSE"
