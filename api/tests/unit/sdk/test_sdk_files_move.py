# api/tests/unit/sdk/test_sdk_files_move.py
"""SDK files.move / move_many: enqueue over the engine transport, then poll the job."""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest


def _response(body: dict) -> MagicMock:
    response = MagicMock()
    response.json.return_value = body
    return response


def _module(monkeypatch, *responses):
    module = importlib.import_module("bifrost.files")
    client = MagicMock()
    client.engine_request = AsyncMock(side_effect=[_response(r) for r in responses])
    monkeypatch.setattr(module, "get_client", lambda: client)
    monkeypatch.setattr(module, "raise_for_status_with_detail", MagicMock())
    monkeypatch.setattr(module, "resolve_scope", lambda scope: scope)
    monkeypatch.setattr(module, "_solution_query", lambda *_a: "")
    return module, client


ITEM = {"source": "a/r.pdf", "destination": "b/r.pdf", "path": None, "renamed": False,
        "adopted": False, "status_code": 200, "error": None}


async def test_move_many_enqueues_then_polls_to_results(monkeypatch) -> None:
    module, client = _module(
        monkeypatch,
        {"job_id": "job-1", "status": "queued"},
        {"status": "running"},
        {"status": "succeeded", "result": {"results": [ITEM], "moved": 1, "failed": 0}},
    )
    results = await module.files.move_many(
        [{"source": "a/r.pdf", "destination": "b/r.pdf"}],
        location="docs",
        on_conflict="rename",
        poll_interval_seconds=0.001,
    )
    assert results == [ITEM]
    calls = client.engine_request.await_args_list
    assert (calls[0].args, calls[0].kwargs["json"]) == (
        ("POST", "/api/files/move-many"),
        {"items": [{"source": "a/r.pdf", "destination": "b/r.pdf"}], "location": "docs",
         "scope": None, "on_conflict": "rename"},
    )
    assert calls[1].args == ("GET", "/api/platform-jobs/job-1")


async def test_move_returns_final_path_or_raises_item_error(monkeypatch) -> None:
    renamed = {**ITEM, "path": "b/r (1).pdf", "renamed": True}
    module, client = _module(
        monkeypatch,
        {"job_id": "job-1"},
        {"status": "succeeded", "result": {"results": [renamed], "moved": 1, "failed": 0}},
    )
    assert await module.files.move("a/r.pdf", "b/r.pdf", location="docs", poll_interval_seconds=0.001) == "b/r (1).pdf"
    assert client.engine_request.await_args_list[0].args == ("POST", "/api/files/move")

    conflict = {**ITEM, "status_code": 409, "error": "file_exists"}
    module, _ = _module(
        monkeypatch,
        {"job_id": "job-2"},
        {"status": "succeeded", "result": {"results": [conflict], "moved": 0, "failed": 1}},
    )
    with pytest.raises(module.FileMoveError) as caught:
        await module.files.move("a/r.pdf", "b/r.pdf", location="docs", poll_interval_seconds=0.001)
    assert (caught.value.status_code, caught.value.error, caught.value.job_id) == (409, "file_exists", "job-2")


async def test_failed_job_raises_with_results_code_and_job_id(monkeypatch) -> None:
    items = [ITEM, {**ITEM, "source": "c"}, {**ITEM, "status_code": 500, "error": "not_attempted"}]
    module, _ = _module(
        monkeypatch,
        {"job_id": "job-3"},
        {
            "status": "failed",
            "error": {"code": "storage_unavailable", "message": "Object storage is unavailable"},
            "result": {"results": items, "moved": 2, "failed": 1},
        },
    )
    with pytest.raises(module.FileMoveJobError, match="job-3.*Object storage is unavailable") as caught:
        await module.files.move_many(
            [{"source": "a", "destination": "b"}], location="docs", poll_interval_seconds=0.001
        )
    assert (caught.value.job_id, caught.value.code, caught.value.results) == (
        "job-3", "storage_unavailable", items
    )
    assert isinstance(caught.value, RuntimeError)


async def test_cancelled_job_without_result_has_no_results(monkeypatch) -> None:
    module, _ = _module(monkeypatch, {"job_id": "job-5"}, {"status": "cancelled", "error": None})
    with pytest.raises(module.FileMoveJobError) as caught:
        await module.files.move("a", "b", location="docs", poll_interval_seconds=0.001)
    assert caught.value.results is None
    assert caught.value.code is None
    assert caught.value.job_id == "job-5"


async def test_job_finishing_during_last_sleep_is_not_a_timeout(monkeypatch) -> None:
    module, client = _module(
        monkeypatch,
        {"job_id": "job-6"},
        {"status": "running"},
        {"status": "succeeded", "result": {"results": [ITEM], "moved": 1, "failed": 0}},
    )
    real_sleep = module.asyncio.sleep

    async def slow_sleep(delay):
        await real_sleep(0.05)

    monkeypatch.setattr(module.asyncio, "sleep", slow_sleep)
    results = await module.files.move_many(
        [{"source": "a", "destination": "b"}], location="docs",
        timeout_seconds=0.02, poll_interval_seconds=0.01,
    )
    assert results == [ITEM]


async def test_deadline_raises_timeout_naming_the_job(monkeypatch) -> None:
    module, _ = _module(monkeypatch, {"job_id": "job-4"}, *([{"status": "running"}] * 50))
    with pytest.raises(TimeoutError, match="job-4.*may still be running"):
        await module.files.move_many(
            [{"source": "a", "destination": "b"}], location="docs",
            timeout_seconds=0.01, poll_interval_seconds=0.005,
        )


def test_default_wait_matches_the_job_timeout() -> None:
    import inspect

    module = importlib.import_module("bifrost.files")
    for method in (module.files.move, module.files.move_many):
        assert inspect.signature(method).parameters["timeout_seconds"].default == 14400
        assert "mode" not in inspect.signature(method).parameters
