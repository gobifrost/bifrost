"""README round-trips repo->DB (Task 6).

A Solution's README is markdown sourced from a ``README.md`` at the repo root,
pulled onto ``Solution.readme`` by deploy. README is deploy-owned and
full-replaces: a bundle carrying a readme sets it, an absent readme CLEARS it
(mirrors the logo plumbing). Exercised here at the ``_apply_readme`` seam so a
new field can't create a deploy gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

import pytest

from src.models.orm.solutions import Solution
from src.services.solutions.deploy import SolutionDeployer


@dataclass
class _ReadmeCarrier:
    """A minimal bundle stand-in carrying only ``.readme`` (what _apply_readme
    reads), so the test is decoupled from the full SolutionBundle constructor."""

    readme: str | None = None


@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    """Drop the loop-bound Redis singleton between tests (see
    test_solution_deploy_version) so event-loop teardown can't poison it."""
    import src.core.redis_client as rc

    rc._redis_client = None
    yield
    rc._redis_client = None


@pytest.mark.e2e
class TestApplyReadme:
    async def test_deploy_writes_readme(self, db_session) -> None:
        sol = Solution(id=uuid4(), slug="r", name="R", organization_id=None)
        db_session.add(sol)
        await db_session.flush()
        dep = SolutionDeployer(db_session)
        dep._apply_readme(sol, _ReadmeCarrier(readme="# Setup\nDo the thing."))
        assert sol.readme == "# Setup\nDo the thing."

    async def test_deploy_clears_readme_when_absent(self, db_session) -> None:
        sol = Solution(
            id=uuid4(), slug="r2", name="R2", organization_id=None, readme="old"
        )
        db_session.add(sol)
        await db_session.flush()
        dep = SolutionDeployer(db_session)
        dep._apply_readme(sol, _ReadmeCarrier(readme=None))
        assert sol.readme is None

    async def test_apply_readme_defaults_to_none_for_carrier_without_field(
        self, db_session
    ) -> None:
        """A bundle object with no ``readme`` attribute at all clears the column
        (getattr default), so an older bundle shape never leaves it stale."""
        sol = Solution(
            id=uuid4(), slug="r3", name="R3", organization_id=None, readme="old"
        )
        db_session.add(sol)
        await db_session.flush()
        dep = SolutionDeployer(db_session)
        dep._apply_readme(sol, object())
        assert sol.readme is None
