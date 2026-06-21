"""Deploy must not mutate S3 until the DB transaction is durable (Codex P1-c).

The deployer writes Python source to ``_solutions/{id}/`` and builds app
``dist/``. If those S3 writes happen *before* the router commits and the commit
then fails, running solution code is changed while the DB metadata rolls back.

Guarantee under test: ``deploy()`` performs ONLY DB work and returns a
``DeployResult`` carrying a deferred ``finalize_s3`` coroutine factory. No S3
write (no Python source, no app build) happens until the caller — after a
successful ``commit()`` — awaits ``finalize_s3()``.
"""
from __future__ import annotations

import uuid

import pytest

from src.models.orm.solutions import Solution
from src.services.solutions.deploy import SolutionBundle, SolutionDeployer
from src.services.solutions.source_artifact import SolutionSourceArtifactStorage
from src.services.solutions.storage import SolutionStorage
from src.services.solutions.zip_install import deploy_zip_to_solution


@pytest.fixture(autouse=True)
def _guard():
    from src.services.solutions.guard import install_solution_write_guard

    install_solution_write_guard()
    yield


async def _install(db) -> Solution:
    sol = Solution(
        id=uuid.uuid4(),
        slug=f"s3def-{uuid.uuid4().hex[:8]}",
        name="S3DEF",
        organization_id=None,
    )
    db.add(sol)
    await db.flush()
    return sol


@pytest.mark.e2e
class TestS3AfterCommit:
    async def test_deploy_defers_python_write_until_finalize(self, db_session) -> None:
        db = db_session
        sol = await _install(db)
        storage = SolutionStorage(sol.id)

        result = await SolutionDeployer(db).deploy(
            SolutionBundle(
                solution=sol,
                python_files={"workflows/w1.py": "def run():\n    return 1\n"},
            )
        )
        await db.flush()

        # DB phase done, but NOTHING written to S3 yet — a commit failure here
        # would leave running code untouched.
        assert await storage.list("") == []

        # Caller runs this only after a durable commit.
        await result.finalize_s3()
        assert "workflows/w1.py" in set(await storage.list(""))

    async def test_finalize_is_idempotent_callable(self, db_session) -> None:
        db = db_session
        sol = await _install(db)
        result = await SolutionDeployer(db).deploy(
            SolutionBundle(solution=sol, python_files={})
        )
        # finalize_s3 must be present and awaitable even for an empty bundle.
        await result.finalize_s3()

    async def test_deploy_defers_source_artifact_until_finalize(self, db_session) -> None:
        db = db_session
        sol = await _install(db)
        artifact = SolutionSourceArtifactStorage(sol.id)

        result = await SolutionDeployer(db).deploy(
            SolutionBundle(
                solution=sol,
                python_files={"workflows/w1.py": "def run():\n    return 1\n"},
                workflows=[
                    {
                        "id": str(uuid.uuid4()),
                        "name": "run",
                        "function_name": "run",
                        "path": "workflows/w1.py",
                    }
                ],
            )
        )
        await db.flush()

        assert await artifact.read() is None

        await result.finalize_s3()
        source_zip = await artifact.read()
        assert source_zip is not None
        assert b"workflows/w1.py" in source_zip

    async def test_deploy_zip_to_solution_defers_artifact_until_finalize(
        self,
        db_session,
        tmp_path,
    ) -> None:
        db = db_session
        sol = await _install(db)
        wf_id = uuid.uuid4()
        (tmp_path / "bifrost.solution.yaml").write_text(
            f"slug: {sol.slug}\nname: S3DEF\nversion: 0.1.0\n"
        )
        (tmp_path / "workflows").mkdir()
        (tmp_path / "workflows" / "w.py").write_text("def run():\n    return 1\n")
        (tmp_path / ".bifrost").mkdir()
        (tmp_path / ".bifrost" / "workflows.yaml").write_text(
            "workflows:\n"
            f"  {wf_id}:\n"
            f"    id: {wf_id}\n"
            "    name: run\n"
            "    function_name: run\n"
            "    path: workflows/w.py\n"
        )

        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(tmp_path.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(tmp_path).as_posix())

        result = await deploy_zip_to_solution(db, sol, buf.getvalue())
        await db.flush()
        artifact = SolutionSourceArtifactStorage(sol.id)
        assert await artifact.read() is None

        await result.finalize_s3()
        source_zip = await artifact.read()
        assert source_zip is not None
        assert b"workflows/w.py" in source_zip
