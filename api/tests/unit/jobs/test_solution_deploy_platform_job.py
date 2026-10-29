from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, call
from uuid import uuid4

import pytest

from src.jobs.platform.base import PlatformJobFailure
from src.jobs.platform.solution_deploy import (
    SolutionDeployPayload,
    run_solution_deploy,
)
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.services.solutions.deploy import (
    CompiledSolutionAppDeployment,
    SolutionDeployInstrumentation,
    SolutionDeployer,
    solution_deploy_instrumentation,
)


@pytest.mark.asyncio
async def test_platform_job_reports_deploy_phases_beyond_staged_input(
    monkeypatch,
):
    """The platform job must expose work after its staged-input copy.

    The legacy deploy projection may retain its compatibility phase strings, but
    the canonical PlatformJob is what browser notifications consume.  This
    guards against the wrapper appearing stuck at 2% for the entire deploy.
    """
    deploy_job_id = uuid4()
    install_id = uuid4()
    projection = SolutionDeployJob(
        id=deploy_job_id,
        install_id=install_id,
        status="succeeded",
        result={"solution_id": str(install_id)},
    )

    class FakeDB:
        async def get(self, model, row_id):  # noqa: ANN001, ANN201
            assert model is SolutionDeployJob
            assert row_id == deploy_job_id
            return projection

    @asynccontextmanager
    async def fake_db_context():
        yield FakeDB()

    context = AsyncMock()
    monkeypatch.setattr(
        "src.jobs.platform.solution_deploy.SolutionDeployJobStorage.copy_to_path",
        AsyncMock(return_value=1),
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_deploy.SolutionDeployJobStorage.delete",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_deploy.get_db_context", fake_db_context,
    )

    async def complete_deploy(*_args, progress_callback, **_kwargs):
        await progress_callback("Reconciling Solution resources", 20)
        await progress_callback("Finalizing Solution artifacts", 90)

    monkeypatch.setattr("src.routers.solutions._run_deploy_job", complete_deploy)

    await run_solution_deploy(
        context,
        SolutionDeployPayload(
            deploy_job_id=deploy_job_id,
            kind="deploy",
            install_id=install_id,
            input_sha256="a" * 64,
            options={},
        ),
    )

    assert context.report.await_args_list == [
        call("Loading staged Solution input", percent=2),
        call("Preparing Solution deployment", percent=8),
        call("Reconciling Solution resources", percent=20),
        call("Finalizing Solution artifacts", percent=90),
        call("Solution deploy complete", percent=100),
    ]


@pytest.mark.asyncio
async def test_app_artifact_and_activation_timings_are_distinct(monkeypatch):
    """App finalization must not be folded into its S3 artifact upload timing."""
    timings: list[tuple[str, int]] = []
    phases: list[tuple[str, float]] = []

    async def record_timing(category: str, elapsed_ms: int) -> None:
        timings.append((category, elapsed_ms))

    async def report_phase(phase: str, percent: float) -> None:
        phases.append((phase, percent))

    instrumentation = SolutionDeployInstrumentation(
        progress_callback=report_phase,
        timing_callback=record_timing,
    )
    with solution_deploy_instrumentation(instrumentation):
        deployer = SolutionDeployer(db=None)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "src.services.solutions.app_build.SolutionAppBuilder.upload_deployment",
        AsyncMock(),
    )
    monkeypatch.setattr(deployer, "_activate_compiled_dists", AsyncMock())
    item = CompiledSolutionAppDeployment(
        app_id=uuid4(),
        solution_id=uuid4(),
        deployment_id=uuid4(),
        expected_old_deployment_id=None,
        superseded_deployment_id=None,
        dist={"index.html": b"ok"},
    )

    await deployer._upload_compiled_dists([item])

    assert phases == [("Activating Solution applications", 95)]
    assert [category for category, _ in timings] == [
        "app_artifacts_s3",
        "app_finalization_db",
    ]
    assert all(elapsed_ms >= 0 for _, elapsed_ms in timings)


@pytest.mark.asyncio
async def test_failed_repo_install_raises_job_failure_without_touching_install(
    monkeypatch,
):
    """The brand-new install row for a failed from-repo first deploy is now
    deleted atomically by ``_run_deploy_job`` itself, in the same transaction
    as its ``failed`` status write (see ``_run_deploy_job``'s docstring and
    ``tests/unit/routers/test_solution_deploy_jobs.py``). ``run_solution_deploy``
    only needs to read the terminal job status back and surface its error as a
    ``PlatformJobFailure`` — it must not also reach for the install row itself.
    """
    deploy_job_id = uuid4()
    install_id = uuid4()
    projection = SolutionDeployJob(
        id=deploy_job_id,
        install_id=None,  # already cleared by _run_deploy_job's own failure path
        status="failed",
        error="manifest invalid",
    )
    orphan = Solution(id=install_id, slug="failed-install", name="Failed install")

    class FakeDB:
        def __init__(self) -> None:
            self.flush = AsyncMock()
            self.delete = AsyncMock()

        async def get(self, model, row_id):  # noqa: ANN001, ANN201
            if model is SolutionDeployJob and row_id == deploy_job_id:
                return projection
            if model is Solution and row_id == install_id:
                return orphan
            return None

    db = FakeDB()
    transaction_committed = False

    @asynccontextmanager
    async def fake_db_context():
        nonlocal transaction_committed
        yield db
        transaction_committed = True

    context = AsyncMock()
    monkeypatch.setattr(
        "src.jobs.platform.solution_deploy.SolutionDeployJobStorage.copy_to_path",
        AsyncMock(return_value=1),
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_deploy.SolutionDeployJobStorage.delete",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "src.jobs.platform.solution_deploy.get_db_context", fake_db_context
    )
    monkeypatch.setattr("src.routers.solutions._run_deploy_job", AsyncMock())

    with pytest.raises(PlatformJobFailure, match="manifest invalid"):
        await run_solution_deploy(
            context,
            SolutionDeployPayload(
                deploy_job_id=deploy_job_id,
                kind="install_from_repo",
                install_id=install_id,
                input_sha256="a" * 64,
                options={},
            ),
        )

    assert transaction_committed is True
    # No install-cleanup reach-in from this layer anymore: the orphan lookup
    # (db.get(Solution, ...)) never happens, and neither does a flush/delete.
    db.flush.assert_not_awaited()
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_reporter_bounds_progress_and_diagnostic_failure_does_not_abort():
    from src.jobs.platform.solution_deploy import _SolutionDeployReporter

    context = AsyncMock()
    reporter = _SolutionDeployReporter(context, last_percent=2)
    await reporter.progress("Publishing source", 75)
    await reporter.progress("Repeated source phase", 70)
    await reporter.progress("Activating applications", 150)
    assert context.report.await_args_list == [
        call("Publishing source", percent=75),
        call("Activating applications", percent=99),
    ]
    context.log.side_effect = RuntimeError("diagnostic unavailable")
    await reporter.timing("redis_module_cache", 10)
