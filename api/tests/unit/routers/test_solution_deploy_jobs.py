from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from src.jobs.consumers.solution_deploy import SolutionDeployConsumer
from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.solutions import Solution
from src.services.solutions.deploy_jobs import (
    create_staged_deploy_job,
    execute_deploy_job,
    recover_deploy_jobs,
)


@pytest.mark.asyncio
async def test_recovery_requeues_expired_claim_and_keeps_terminal_jobs(db_session):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    solution = Solution(slug="demo", name="Demo")
    db_session.add(solution)
    await db_session.flush()
    queued = SolutionDeployJob(
        install_id=solution.id,
        status="queued",
        created_at=now,
        updated_at=now,
    )
    already_published = SolutionDeployJob(
        install_id=solution.id,
        status="queued",
        published_at=now,
        created_at=now,
        updated_at=now,
    )
    stale = SolutionDeployJob(
        install_id=solution.id,
        status="running",
        claim_token=None,
        lease_expires_at=now - timedelta(seconds=1),
        created_at=now,
        updated_at=now,
    )
    live = SolutionDeployJob(
        install_id=solution.id,
        status="running",
        lease_expires_at=now + timedelta(minutes=1),
        created_at=now,
        updated_at=now,
    )
    succeeded = SolutionDeployJob(
        install_id=solution.id,
        status="succeeded",
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([queued, already_published, stale, live, succeeded])
    await db_session.flush()

    recovered = await recover_deploy_jobs(db_session, now=now)

    assert set(recovered) == {queued.id, stale.id}
    assert stale.status == "queued"
    assert stale.lease_expires_at is None
    assert stale.result == {"phase": "requeued after an interrupted worker"}
    assert live.status == "running"
    assert already_published.status == "queued"
    assert succeeded.status == "succeeded"


@pytest.mark.asyncio
async def test_create_job_stages_before_persisting_and_publishes_only_id(
    db_session, tmp_path, monkeypatch
):
    input_path = tmp_path / "input.zip"
    input_path.write_bytes(b"validated")
    write_path = AsyncMock(return_value=("a" * 64, len(b"validated")))
    publish = AsyncMock()
    monkeypatch.setattr(
        "src.services.solutions.deploy_jobs.SolutionDeployJobStorage.write_path",
        write_path,
    )
    monkeypatch.setattr(
        "src.services.solutions.deploy_jobs.publish_message",
        publish,
    )

    job = await create_staged_deploy_job(
        db_session,
        kind="install",
        install_id=None,
        options={"password": "never-plaintext", "config_values": {"key": "secret"}},
        input_path=input_path,
    )

    write_path.assert_awaited_once_with(input_path)
    publish.assert_awaited_once_with(
        "solution-deploys",
        {"job_id": str(job.id)},
    )
    assert job.kind == "install"
    assert job.input_key == f"_solution_deploy_jobs/{job.id}/input.zip"
    assert job.input_sha256 == "a" * 64
    assert job.published_at is not None
    assert "never-plaintext" not in (job.encrypted_options or "")
    assert "secret" not in (job.encrypted_options or "")


@pytest.mark.asyncio
async def test_terminal_job_is_not_executed_again(
    db_session,
    async_session_factory,
    monkeypatch,
):
    job = SolutionDeployJob(status="succeeded", kind="deploy")
    db_session.add(job)
    await db_session.commit()

    @asynccontextmanager
    async def test_db_context():
        async with async_session_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    monkeypatch.setattr(
        "src.services.solutions.deploy_jobs.get_db_context",
        test_db_context,
    )
    execute = AsyncMock()
    monkeypatch.setattr(
        "src.services.solutions.deploy_jobs._run_claimed_job",
        execute,
    )

    await execute_deploy_job(job.id)

    execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_consumer_rejects_payload_with_job_parameters(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(
        "src.jobs.consumers.solution_deploy.execute_deploy_job",
        execute,
    )
    consumer = SolutionDeployConsumer()

    with pytest.raises(ValueError, match="only job_id"):
        await consumer.process_message(
            {"job_id": "8c416226-329d-4f0b-8980-8c004cf5ed3b", "force": True}
        )

    execute.assert_not_awaited()
