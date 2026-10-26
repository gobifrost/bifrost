# api/tests/unit/services/file_move/test_job.py
"""The files.move job: request-order items, compact checkpoints, systemic stop."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import httpx
from pydantic import ValidationError

from src.jobs.platform import file_move
from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure
from src.services import platform_jobs
from src.jobs.platform.file_move import (
    FILE_MOVE_DEFINITION,
    FileMovePayload,
    FileMoveWorkItem,
    RejectedFileMove,
    run_file_move,
)
from src.jobs.platform.registry import get_platform_job_definition
from src.models.contracts.cli import FileMoveResult

from .support import key_for, seed_file


@pytest.fixture
def context():
    return SimpleNamespace(save_checkpoint=AsyncMock(), log=AsyncMock())


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch, store):
    monkeypatch.setattr(file_move, "_object_store", lambda: store)


def payload(target, items, rejected=(), on_conflict="error") -> FileMovePayload:
    return FileMovePayload(
        location=target.location,
        scope=target.scope,
        solution_id=target.solution_id,
        organization_id=target.organization_id,
        on_conflict=on_conflict,
        requested_by_user_id=target.requested_by,
        policy_user_id=target.requested_by,
        total=len(items) + len(rejected),
        items=[FileMoveWorkItem(index=i, source=s, destination=d) for i, s, d in items],
        rejected=list(rejected),
    )


def test_definition_is_registered_with_the_spec_policy() -> None:
    assert get_platform_job_definition("files.move") is FILE_MOVE_DEFINITION
    policy = FILE_MOVE_DEFINITION.policy
    assert (policy.timeout_seconds, policy.max_attempts, policy.retry_on_runner_loss) == (14400, 1, False)
    assert (policy.allow_running_cancellation, policy.min_memory_headroom_mb) == (False, 128)


def test_payload_requires_every_index_exactly_once() -> None:
    with pytest.raises(ValidationError):
        FileMovePayload(
            location="docs", scope=str(uuid4()), on_conflict="error",
            requested_by_user_id=uuid4(), policy_user_id=uuid4(), total=2,
            items=[FileMoveWorkItem(index=0, source="a", destination="b")],
        )


async def _enqueue_move(db_session):
    job, reused = await platform_jobs.enqueue_platform_job(
        db_session,
        FILE_MOVE_DEFINITION,
        FileMovePayload(
            location="docs", scope=str(uuid4()), on_conflict="error",
            requested_by_user_id=uuid4(), policy_user_id=uuid4(), total=1,
            items=[FileMoveWorkItem(index=0, source="a.pdf", destination="b.pdf")],
        ),
        dedupe_key=None,
        organization_id=None,
        requested_by_user_id=uuid4(),
        requested_by_email="dev@example.com",
        requested_by_name="Dev",
        resource_type=None,
        resource_id=None,
        title="Moving 1 file",
        action_url=None,
    )
    assert reused is False
    return job


async def test_cancel_succeeds_while_queued(db_session) -> None:
    job = await _enqueue_move(db_session)
    assert job.status == "queued"

    job, accepted = await platform_jobs.request_platform_job_cancel(db_session, job)

    assert accepted is True
    assert job.status == "cancelled"


async def test_cancel_is_refused_while_running(db_session) -> None:
    job = await _enqueue_move(db_session)
    job.status = "running"
    job.lease_token = uuid4()
    job.lease_owner = "scheduler-a"
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
    await db_session.commit()

    job, accepted = await platform_jobs.request_platform_job_cancel(db_session, job)

    assert accepted is False
    assert job.status == "running"
    assert job.cancel_requested_at is None


async def test_results_in_request_order_with_rejections_and_checkpoints(store, solution_target, context) -> None:
    await seed_file(store, solution_target, "a/1.pdf")
    await seed_file(store, solution_target, "a/3.pdf")
    forbidden = RejectedFileMove(
        index=1,
        result=FileMoveResult(source="a/2.pdf", destination="b/2.pdf", status_code=403, error="forbidden"),
    )
    result = await run_file_move(
        context,
        payload(solution_target, [(0, "a/1.pdf", "b/1.pdf"), (2, "a/3.pdf", "b/3.pdf")], [forbidden]),
    )

    assert [r["status_code"] for r in result["results"]] == [200, 403, 200]
    assert (result["moved"], result["failed"]) == (2, 1)
    first, second = context.save_checkpoint.await_args_list
    assert first.args[0]["failed"] == [{"index": 1, **forbidden.result.model_dump()}]
    assert first.args[0]["in_flight"] == {"index": 0, "source": "a/1.pdf", "destination": "b/1.pdf"}
    assert (first.kwargs["current"], first.kwargs["total"], first.kwargs["phase"]) == (0, 2, "Moving 1 of 2")
    assert second.args[0]["moved"] == 1 and second.args[0]["in_flight"]["index"] == 2


async def test_item_failure_is_recorded_in_the_next_checkpoint(store, solution_target, context) -> None:
    await seed_file(store, solution_target, "a/2.pdf")
    await run_file_move(
        context,
        payload(solution_target, [(0, "a/missing.pdf", "b/1.pdf"), (1, "a/2.pdf", "b/2.pdf")]),
    )
    first, second = (c.args[0] for c in context.save_checkpoint.await_args_list)
    assert first["failed"] == []
    assert [f["index"] for f in second["failed"]] == [0]
    assert second["failed"][0]["status_code"] == 404


async def test_same_source_twice_is_already_moved_then_404(store, solution_target, context) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    result = await run_file_move(
        context,
        payload(solution_target, [
            (0, "a/r.pdf", "b/r.pdf"),
            (1, "a/r.pdf", "b/r.pdf"),
            (2, "a/r.pdf", "c/r.pdf"),
        ]),
    )
    statuses = [(r["status_code"], r["adopted"]) for r in result["results"]]
    assert statuses == [(200, False), (200, True), (404, False)]


async def test_swap_fails_cleanly_and_loses_nothing(store, solution_target, context) -> None:
    await seed_file(store, solution_target, "a.pdf", data=b"A")
    await seed_file(store, solution_target, "b.pdf", data=b"B")
    result = await run_file_move(
        context, payload(solution_target, [(0, "a.pdf", "b.pdf"), (1, "b.pdf", "a.pdf")])
    )
    assert [r["status_code"] for r in result["results"]] == [409, 409]
    assert store.objects[key_for(solution_target, "a.pdf")].data == b"A"
    assert store.objects[key_for(solution_target, "b.pdf")].data == b"B"


async def test_three_consecutive_storage_failures_stop_the_job(store, solution_target, context) -> None:
    async def unreachable(_key: str) -> None:
        raise httpx.ConnectError("s3 unavailable")

    store.before["head"] = unreachable
    items = [(i, f"a/{i}.pdf", f"b/{i}.pdf") for i in range(5)]
    with pytest.raises(PlatformJobFailure) as caught:
        await run_file_move(context, payload(solution_target, items))
    assert caught.value.code == "storage_unavailable"
    results = caught.value.result["results"]
    assert [r["error"] for r in results[3:]] == ["not_attempted", "not_attempted"]
    assert all(r["status_code"] == 500 for r in results)
    assert context.save_checkpoint.await_count == 3


async def test_non_storage_failure_resets_the_storage_run(store, solution_target, context) -> None:
    calls = 0

    async def flaky(_key: str) -> None:
        nonlocal calls
        calls += 1
        # Items 0 and 1 hit storage errors; item 2 (two clean heads) is a plain
        # 404 that resets the run; item 3 fails on storage again — no stop.
        if calls in (1, 2, 5):
            raise httpx.ConnectError("s3 unavailable")

    store.before["head"] = flaky
    items = [(i, f"a/{i}.pdf", f"b/{i}.pdf") for i in range(4)]
    result = await run_file_move(context, payload(solution_target, items))
    assert [r["status_code"] for r in result["results"]] == [500, 500, 404, 500]


async def test_lost_lease_propagates(store, solution_target, context) -> None:
    context.save_checkpoint.side_effect = PlatformJobCancelled
    with pytest.raises(PlatformJobCancelled):
        await run_file_move(context, payload(solution_target, [(0, "a/r.pdf", "b/r.pdf")]))


async def test_checkpoint_failure_becomes_structured_failure(store, solution_target, context) -> None:
    context.save_checkpoint.side_effect = RuntimeError("db down")
    with pytest.raises(PlatformJobFailure) as caught:
        await run_file_move(context, payload(solution_target, [(0, "a/r.pdf", "b/r.pdf")]))
    assert caught.value.code == "checkpoint_failed"
    assert caught.value.result["in_flight"]["index"] == 0


async def test_all_rejected_completes_without_moving(store, solution_target, context) -> None:
    rejected = RejectedFileMove(
        index=0, result=FileMoveResult(source="a", destination="a", status_code=400, error="same_path")
    )
    result = await run_file_move(context, payload(solution_target, [], [rejected]))
    assert result == {"results": [rejected.result.model_dump()], "moved": 0, "failed": 1}
    context.save_checkpoint.assert_not_awaited()


async def test_cleanup_failure_is_logged_for_operators(store, solution_target, context) -> None:
    await seed_file(store, solution_target, "a/r.pdf")
    dest_key = key_for(solution_target, "b/r.pdf")

    async def truncate(key: str) -> None:
        store.objects[key].data = b"x"

    async def cleanup_fails(key: str) -> None:
        if key == dest_key:
            raise RuntimeError("nope")

    store.after["copy"] = truncate
    store.before["delete"] = cleanup_fails
    await run_file_move(context, payload(solution_target, [(0, "a/r.pdf", "b/r.pdf")]))
    level, code, message = context.log.await_args.args
    assert (level, code) == ("warning", "file_move_cleanup_failed")
    assert dest_key in message
