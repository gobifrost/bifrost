"""Database integration checks for Solution deploy validation gates.

The async deploy endpoint and job result mapping have their own live tests.
These cases exercise the deployer and persisted state without starting a job
for each rejected or accepted manifest detail.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.models.orm.solutions import Solution
from src.models.orm.tables import Table
from src.models.orm.workflows import Workflow
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployConflict,
    SolutionDeployer,
    SolutionWorkflowNameMismatch,
    solution_entity_id,
)

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    import src.core.redis_client as rc

    rc._redis_client = None
    yield
    rc._redis_client = None


async def _install(db_session) -> Solution:
    solution = Solution(
        id=uuid4(),
        slug=f"validation-{uuid4().hex[:8]}",
        name="Validation",
        organization_id=None,
    )
    db_session.add(solution)
    await db_session.flush()
    return solution


async def test_missing_workflow_function_refuses_without_writing(db_session):
    solution = await _install(db_session)
    manifest_id = uuid4()
    source = "def other():\n    return 1\n"
    bundle = SolutionBundle(
        solution=solution,
        python_files={"workflows/snap.py": source},
        workflows=[{
            "id": str(manifest_id),
            "name": "hello",
            "function_name": "main",
            "path": "workflows/snap.py",
            "type": "workflow",
            "source": source,
        }],
    )

    with pytest.raises(SolutionWorkflowNameMismatch, match="workflows/snap.py::main"):
        await SolutionDeployer(db_session).deploy(bundle)

    assert await db_session.get(Workflow, solution_entity_id(solution.id, manifest_id)) is None


async def test_unknown_table_policy_ref_refuses_without_writing(db_session):
    solution = await _install(db_session)
    manifest_id = uuid4()
    bundle = SolutionBundle(
        solution=solution,
        tables=[{
            "id": str(manifest_id),
            "name": "policy_ref_missing",
            "schema": {"columns": [{"name": "data"}]},
            "policies": [{"$ref": "does_not_exist_xyz_abc"}],
        }],
    )

    with pytest.raises(SolutionDeployConflict, match="policy ref unresolvable"):
        await SolutionDeployer(db_session).deploy(bundle)

    assert await db_session.get(Table, solution_entity_id(solution.id, manifest_id)) is None


async def test_builtin_table_policy_ref_is_preserved(db_session):
    solution = await _install(db_session)
    manifest_id = uuid4()
    bundle = SolutionBundle(
        solution=solution,
        tables=[{
            "id": str(manifest_id),
            "name": "policy_ref_builtin",
            "schema": {"columns": [{"name": "data"}]},
            "policies": [{"$ref": "admin_bypass"}],
        }],
    )

    result = await SolutionDeployer(db_session).deploy(bundle)
    assert result.tables_upserted == 1
    table = await db_session.get(Table, solution_entity_id(solution.id, manifest_id))
    assert table is not None
    assert table.access == {"policies": [{"$ref": "admin_bypass"}]}
