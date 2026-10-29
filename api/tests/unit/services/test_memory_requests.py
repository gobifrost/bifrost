"""Work requests memory from an executor (#769 stage 6a)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.models.orm.workflows import Workflow
from src.services import memory_requests
from src.services.memory_requests import (
    DEFAULT_WORKFLOW_REQUEST_BYTES,
    JOB_LIMIT_STEP_BYTES,
    MIB,
    WORKFLOW_REQUEST_MARGIN_BYTES,
    job_budget_bytes,
    measure_budget,
    record_peak_growth,
    sized_job_limit_bytes,
    under_pressure,
    workflow_request_bytes,
)

GIB = 1024 * MIB


def test_a_workflow_without_history_requests_the_default() -> None:
    assert workflow_request_bytes(None) == DEFAULT_WORKFLOW_REQUEST_BYTES


def test_a_learned_workflow_requests_its_peak_plus_the_margin() -> None:
    assert workflow_request_bytes(200 * MIB) == 200 * MIB + WORKFLOW_REQUEST_MARGIN_BYTES


def test_a_declared_request_replaces_the_learned_one() -> None:
    assert workflow_request_bytes(200 * MIB, declared_mib=512) == 512 * MIB
    assert workflow_request_bytes(None, declared_mib=32) == 32 * MIB


async def _learned_after(db_session, growths: list[int | tuple[int, str | None]]) -> int | None:  # type: ignore[no-untyped-def]
    workflow = Workflow(
        id=uuid4(), name=f"learn-{uuid4().hex[:8]}", function_name="main",
        path="workflows/learn.py", type="workflow", is_active=True,
    )
    db_session.add(workflow)
    await db_session.flush()
    for run in growths:
        growth, code_hash = run if isinstance(run, tuple) else (run, "v1")
        await record_peak_growth(db_session, workflow.id, growth, code_hash)
    await db_session.refresh(workflow)
    return workflow.learned_memory_bytes


async def test_the_first_run_sets_the_learned_peak(db_session) -> None:  # type: ignore[no-untyped-def]
    assert await _learned_after(db_session, [100 * MIB]) == 100 * MIB


async def test_a_larger_run_raises_the_learned_peak_to_it(db_session) -> None:  # type: ignore[no-untyped-def]
    assert await _learned_after(db_session, [100 * MIB, 300 * MIB]) == 300 * MIB


async def test_a_smaller_run_moves_the_learned_peak_a_tenth_down(db_session) -> None:  # type: ignore[no-untyped-def]
    learned = await _learned_after(db_session, [300 * MIB, 100 * MIB])
    assert learned == 300 * MIB - (200 * MIB) // 10


async def test_the_first_run_of_changed_code_replaces_the_learned_peak(db_session) -> None:  # type: ignore[no-untyped-def]
    learned = await _learned_after(db_session, [(2048 * MIB, "buggy"), (100 * MIB, "fixed")])
    assert learned == 100 * MIB


async def test_a_run_of_unknown_code_decays_the_learned_peak(db_session) -> None:  # type: ignore[no-untyped-def]
    learned = await _learned_after(db_session, [(300 * MIB, "v1"), (100 * MIB, None)])
    assert learned == 300 * MIB - (200 * MIB) // 10


@pytest.mark.parametrize("request_bytes", [1, 900 * MIB, 3 * GIB, 7 * GIB + 1])
def test_a_sized_job_limit_is_the_smallest_step_whose_budget_fits(request_bytes: int) -> None:
    threshold, baseline = 0.85, 768 * MIB

    limit = sized_job_limit_bytes(request_bytes, threshold, baseline, 64 * GIB)

    assert limit % JOB_LIMIT_STEP_BYTES == 0
    assert job_budget_bytes(limit, threshold, baseline) >= request_bytes
    assert job_budget_bytes(limit - JOB_LIMIT_STEP_BYTES, threshold, baseline) < request_bytes


def test_a_sized_job_limit_never_exceeds_the_largest_job() -> None:
    """A maximum that is not a step multiple: the budget at the maximum
    already holds the request, so the rounded-up step is not needed."""
    threshold, baseline, largest = 0.85, 768 * MIB, 8000 * MIB
    request = job_budget_bytes(largest, threshold, baseline)

    assert sized_job_limit_bytes(request, threshold, baseline, largest) == largest


def _no_cgroup_limit(monkeypatch, current: int) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "src.services.execution.memory_monitor.get_cgroup_memory", lambda: (current, -1)
    )


def test_without_a_container_limit_the_budget_comes_from_host_memory(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _no_cgroup_limit(monkeypatch, current=1 * GIB)
    monkeypatch.setattr(
        memory_requests, "_meminfo", lambda: {"MemTotal": 16 * GIB, "MemAvailable": 8 * GIB}
    )

    budget = measure_budget(0.85)

    assert (budget.limit_bytes, budget.baseline_bytes) == (16 * GIB, 1 * GIB)
    assert budget.budget_bytes == int(16 * GIB * 0.85) - 1 * GIB


def test_without_a_container_limit_pressure_is_host_memory_in_use(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _no_cgroup_limit(monkeypatch, current=1 * GIB)

    monkeypatch.setattr(
        memory_requests, "_meminfo", lambda: {"MemTotal": 16 * GIB, "MemAvailable": 1 * GIB}
    )
    assert under_pressure(0.85)

    monkeypatch.setattr(
        memory_requests, "_meminfo", lambda: {"MemTotal": 16 * GIB, "MemAvailable": 8 * GIB}
    )
    assert not under_pressure(0.85)


def test_with_a_container_limit_pressure_is_its_working_set_ratio(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    limit = 1000 * MIB
    monkeypatch.setattr(
        "src.services.execution.memory_monitor.get_cgroup_memory", lambda: (900 * MIB, limit)
    )
    assert under_pressure(0.85)

    monkeypatch.setattr(
        "src.services.execution.memory_monitor.get_cgroup_memory", lambda: (850 * MIB, limit)
    )
    assert not under_pressure(0.85), "at the threshold is not over it"


def test_work_volunteers_to_be_the_kernels_first_out_of_memory_choice(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import builtins

    score = tmp_path / "oom_score_adj"
    real_open = builtins.open
    monkeypatch.setattr(
        builtins, "open",
        lambda path, *a, **k: real_open(score if path == "/proc/self/oom_score_adj" else path, *a, **k),
    )

    from src.services.execution.oom_priority import volunteer_for_oom_kill

    volunteer_for_oom_kill()

    assert score.read_text() == "1000"
