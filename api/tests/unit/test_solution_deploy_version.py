"""Solutions versioning (Task 20): version bookkeeping + the downgrade gate.

Upgrade is an explicit verb, replace is the semantics — the existing
full-replace reconcile IS the upgrade. Deploy records the bundle's version on
the install (``version`` + ``upgraded_from_version``) and refuses a downgrade
(PEP 440-ordered older version) unless forced. Unparseable or absent versions
are unordered — they never block, and are recorded verbatim.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.models.orm.solutions import Solution
from src.services.solutions.deploy import (
    SolutionBundle,
    SolutionDeployer,
    SolutionDowngradeBlocked,
    _is_downgrade,
)

def _wf_entry(wf_id: str, name: str) -> dict:
    """A minimal manifest-shaped workflow entry."""
    return {
        "id": wf_id,
        "name": name,
        "function_name": "run",
        "path": f"workflows/{name}.py",
        "type": "workflow",
    }

class TestIsDowngrade:
    """(f) _is_downgrade: PEP 440 ordering, never block on unordered versions."""

    def test_lower_version_is_downgrade(self) -> None:
        assert _is_downgrade("0.9.0", "1.0.0") is True

    def test_higher_version_is_not_downgrade(self) -> None:
        assert _is_downgrade("1.1.0", "1.0.0") is False

    def test_equal_version_is_not_downgrade(self) -> None:
        assert _is_downgrade("1.0.0", "1.0.0") is False

    def test_absent_new_never_blocks(self) -> None:
        assert _is_downgrade(None, "1.0.0") is False
        assert _is_downgrade("", "1.0.0") is False

    def test_absent_current_never_blocks(self) -> None:
        assert _is_downgrade("1.0.0", None) is False
        assert _is_downgrade("1.0.0", "") is False

    def test_unparseable_versions_never_block(self) -> None:
        assert _is_downgrade("not-a-version", "1.0.0") is False
        assert _is_downgrade("0.9.0", "bananas") is False
        assert _is_downgrade("v-final-FINAL2", "v-final") is False

    def test_pep440_prerelease_ordering(self) -> None:
        assert _is_downgrade("1.0.0rc1", "1.0.0") is True
        assert _is_downgrade("1.0.0", "1.0.0rc1") is False

@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    """See test_solution_deploy_reconcile: drop the loop-bound Redis singleton
    so cross-test event-loop teardown can't poison the deployer's cache sync."""
    import src.core.redis_client as rc

    rc._redis_client = None
    yield
    rc._redis_client = None

@pytest.mark.e2e
class TestSolutionDeployVersion:
    async def _make_install(
        self, db, slug: str, version: str | None = None
    ) -> Solution:
        sol = Solution(
            id=uuid4(), slug=slug, name=slug.upper(), organization_id=None,
            version=version,
        )
        db.add(sol)
        await db.flush()
        return sol

    def _bundle(self, sol: Solution, version: str | None) -> SolutionBundle:
        wf = str(uuid4())
        return SolutionBundle(
            solution=sol,
            python_files={"workflows/w1.py": "def run():\n    return 1\n"},
            workflows=[_wf_entry(wf, "w1")],
            version=version,
        )

    async def test_upgrade_records_versions(self, db_session) -> None:
        """(a) deploy v1.1.0 over v1.0.0 → version recorded, upgraded_from kept."""
        db = db_session
        sol = await self._make_install(db, f"ver-{uuid4().hex[:8]}", version="1.0.0")
        await SolutionDeployer(db).deploy(self._bundle(sol, "1.1.0"))
        await db.flush()
        assert sol.version == "1.1.0"
        assert sol.upgraded_from_version == "1.0.0"

    async def test_first_set_records_version_without_from(self, db_session) -> None:
        """First deploy with a version onto a versionless install: version set,
        upgraded_from_version stays None (there was nothing to upgrade from)."""
        db = db_session
        sol = await self._make_install(db, f"ver-{uuid4().hex[:8]}", version=None)
        await SolutionDeployer(db).deploy(self._bundle(sol, "1.0.0"))
        await db.flush()
        assert sol.version == "1.0.0"
        assert sol.upgraded_from_version is None

    async def test_downgrade_blocked(self, db_session) -> None:
        """(b) v0.9.0 over v1.0.0 → SolutionDowngradeBlocked, nothing recorded."""
        db = db_session
        sol = await self._make_install(db, f"ver-{uuid4().hex[:8]}", version="1.0.0")
        with pytest.raises(SolutionDowngradeBlocked) as exc:
            await SolutionDeployer(db).deploy(self._bundle(sol, "0.9.0"))
        assert "0.9.0" in str(exc.value)
        assert "1.0.0" in str(exc.value)
        assert sol.version == "1.0.0"
        assert sol.upgraded_from_version is None

    async def test_downgrade_forced_succeeds_and_records(self, db_session) -> None:
        """(c) same downgrade with force=True → deploys + records the downgrade."""
        db = db_session
        sol = await self._make_install(db, f"ver-{uuid4().hex[:8]}", version="1.0.0")
        await SolutionDeployer(db).deploy(self._bundle(sol, "0.9.0"), force=True)
        await db.flush()
        assert sol.version == "0.9.0"
        assert sol.upgraded_from_version == "1.0.0"

    async def test_unparseable_versions_recorded_verbatim(self, db_session) -> None:
        """(d) unordered (non-PEP 440) versions never block and are recorded."""
        db = db_session
        sol = await self._make_install(
            db, f"ver-{uuid4().hex[:8]}", version="v-final"
        )
        await SolutionDeployer(db).deploy(self._bundle(sol, "v-final-FINAL2"))
        await db.flush()
        assert sol.version == "v-final-FINAL2"
        assert sol.upgraded_from_version == "v-final"

    async def test_versionless_bundle_leaves_versions_untouched(self, db_session) -> None:
        """(e) bundle.version None → install's version fields untouched."""
        db = db_session
        sol = await self._make_install(db, f"ver-{uuid4().hex[:8]}", version="1.0.0")
        await SolutionDeployer(db).deploy(self._bundle(sol, None))
        await db.flush()
        assert sol.version == "1.0.0"
        assert sol.upgraded_from_version is None

    async def test_same_version_redeploy_keeps_upgraded_from(self, db_session) -> None:
        """Redeploying the SAME version is not an upgrade: upgraded_from_version
        is not clobbered with the current version."""
        db = db_session
        sol = await self._make_install(db, f"ver-{uuid4().hex[:8]}", version="1.0.0")
        await SolutionDeployer(db).deploy(self._bundle(sol, "1.0.0"))
        await db.flush()
        assert sol.version == "1.0.0"
        assert sol.upgraded_from_version is None
