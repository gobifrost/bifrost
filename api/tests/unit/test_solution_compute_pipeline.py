"""Transaction ownership of the shared Solution preparation pipeline."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4, uuid5

import pytest

from src.services.solutions import compute_pipeline as pipeline
from src.services.solutions.deploy import SolutionDeployConflict


@pytest.fixture
def preparation(monkeypatch, tmp_path):
    state = SimpleNamespace(db_count=0, locked=False, planned_id=None)
    archive = SimpleNamespace(
        preview=SimpleNamespace(slug="example", name="Example"), workspace=tmp_path,
    )
    context = SimpleNamespace(
        job_id=uuid4(), file_compute=AsyncMock(), app_build_compute=AsyncMock(),
        report=AsyncMock(),
    )

    @asynccontextmanager
    async def inspect(*_args):
        yield archive

    @asynccontextmanager
    async def database():
        state.db_count += 1
        try:
            yield SimpleNamespace()
        finally:
            state.db_count -= 1

    @asynccontextmanager
    async def lock(_target):
        state.locked = True
        try:
            yield
        finally:
            state.locked = False

    async def plan(_self, bundle):
        assert state.db_count == 1
        state.planned_id = bundle.solution.id
        return "plan"

    async def compile_apps(plan, compute):
        assert plan == "plan"
        assert compute is context.app_build_compute
        assert state.db_count == 0
        assert state.locked
        return "receipts"

    monkeypatch.setattr(pipeline, "inspect_solution_archive", inspect)
    monkeypatch.setattr(pipeline, "get_db_context", database)
    monkeypatch.setattr(pipeline, "solution_write_lock", lock)
    monkeypatch.setattr(pipeline, "find_install", AsyncMock(return_value=None))
    monkeypatch.setattr(pipeline, "_build_bundle", lambda solution, *_args: SimpleNamespace(solution=solution))
    monkeypatch.setattr(pipeline.SolutionDeployer, "prepare_app_builds", plan)
    monkeypatch.setattr(pipeline, "compile_solution_app_builds", compile_apps)
    monkeypatch.setattr(pipeline, "build_workspace_zip", lambda _bundle: b"source")
    return state, context


@pytest.mark.asyncio
async def test_new_install_prepares_without_db_during_compute_and_keeps_writer_lock(preparation, tmp_path):
    state, context = preparation
    async with pipeline.prepare_solution_deployment(context, tmp_path / "input.zip", solution_id=None) as prepared:
        assert state.db_count == 0
        assert state.locked
        assert prepared.app_builds == "receipts"
        assert prepared.source_artifact == b"source"
        assert state.planned_id == uuid5(context.job_id, "solution.install")
    assert not state.locked


@pytest.mark.asyncio
async def test_target_change_under_lock_rejects_before_compiling(preparation, monkeypatch, tmp_path):
    state, context = preparation
    monkeypatch.setattr(pipeline, "find_install", AsyncMock(side_effect=[
        SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4()),
    ]))
    with pytest.raises(SolutionDeployConflict, match="target changed"):
        async with pipeline.prepare_solution_deployment(context, tmp_path / "input.zip", solution_id=None):
            pytest.fail("changed target cannot reach publication")
    assert state.planned_id is None
    assert state.db_count == 0
    assert not state.locked


@pytest.mark.asyncio
async def test_publication_observations_do_not_open_progress_transactions():
    observations = pipeline.DeferredSolutionDeployObservations()
    progress, timing = AsyncMock(), AsyncMock()
    await observations.record_progress("Applying", 30)
    await observations.record_timing("database", 12)
    progress.assert_not_awaited()
    timing.assert_not_awaited()
    await observations.flush(progress, timing)
    progress.assert_awaited_once_with("Applying", 30)
    timing.assert_awaited_once_with("database", 12)
