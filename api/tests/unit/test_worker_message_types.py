"""Worker handles summarize message types.

Validates the summary message handler that delegates to
``run_summarizer``, and the failure-swallowing path. Summary dispatch
(claiming, backfill fan-out) is covered in
``tests/unit/services/execution/test_summary_dispatch.py``.
"""
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from src.models.orm.agent_runs import AgentRun


@pytest.mark.asyncio
async def test_summarize_message_calls_summarizer():
    """handle_summarize_message claims the summary, then delegates to summarize_run."""
    from src.jobs.summarize_worker import handle_summarize_message

    run_id = uuid4()
    with (
        patch(
            "src.services.execution.summary_dispatch.claim_summary",
            new=AsyncMock(return_value=True),
        ),
        patch("src.jobs.summarize_worker.summarize_run", new=AsyncMock()) as mock,
    ):
        await handle_summarize_message({"run_id": str(run_id), "dispatch_id": str(uuid4())})
        mock.assert_awaited_once()
        called_run_id = mock.await_args.args[0]
        assert isinstance(called_run_id, UUID)
        assert called_run_id == run_id


@pytest.mark.asyncio
async def test_lazy_summarize_wrapper_forwards_to_real_service(monkeypatch):
    """Patch-compatible worker wrapper imports and forwards on first use."""
    from src.jobs.summarize_worker import summarize_run

    run_id = uuid4()
    session_factory = object()
    fake_module = ModuleType("src.services.execution.run_summarizer")
    fake_module.summarize_run = AsyncMock()  # type: ignore[attr-defined]
    monkeypatch.setitem(
        sys.modules,
        "src.services.execution.run_summarizer",
        fake_module,
    )

    await summarize_run(run_id, session_factory)  # type: ignore[arg-type]

    fake_module.summarize_run.assert_awaited_once_with(  # type: ignore[attr-defined]
        run_id,
        session_factory,
    )


@pytest.mark.asyncio
async def test_summarize_failure_marks_run_failed(
    db_session, async_session_factory, seed_agent
):
    """A failure inside summarize_run is caught, run.summary_status = 'failed', error stored.

    The handler must NOT re-raise or retry what is almost always a
    deterministic failure (bad LLM output / missing config). The UI exposes
    a regenerate button for recovery instead.
    """
    from src.jobs.summarize_worker import handle_summarize_message

    run = AgentRun(
        id=uuid4(),
        agent_id=seed_agent.id,
        trigger_type="test",
        status="completed",
        iterations_used=1,
        tokens_used=1,
        summary_status="pending",
    )
    db_session.add(run)
    # Use commit so the handler's separate session sees the row.
    # Cleanup happens at the end of the test below.
    await db_session.commit()

    try:
        with (
            patch(
                "src.services.execution.summary_dispatch.claim_summary",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "src.jobs.summarize_worker.summarize_run",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            # Should not raise — errors are swallowed to prevent retry loops.
            await handle_summarize_message(
                {"run_id": str(run.id), "dispatch_id": str(uuid4())},
                session_factory=async_session_factory,
            )

        # Verify in a fresh session so we see the handler's commit.
        async with async_session_factory() as verify:
            reloaded = (
                await verify.execute(
                    select(AgentRun).where(AgentRun.id == run.id)
                )
            ).scalar_one()
            assert reloaded.summary_status == "failed"
            assert reloaded.summary_error is not None
            assert "boom" in reloaded.summary_error
    finally:
        # Manual cleanup since we committed past the db_session rollback boundary.
        async with async_session_factory() as cleanup:
            await cleanup.execute(
                AgentRun.__table__.delete().where(AgentRun.id == run.id)
            )
            await cleanup.commit()


@pytest.mark.asyncio
async def test_backfill_endpoint_enqueues_durable_platform_job(async_session_factory):
    """The request path persists orchestration and hands work to the scheduler."""
    from unittest.mock import patch
    from uuid import uuid4

    from shared.builtin_roles import USER_ROLE_ID
    from src.core.principal import UserPrincipal
    from src.models.contracts.agent_runs import BackfillSummariesRequest
    from src.models.orm.agent_runs import AgentRun
    from src.models.orm.agents import Agent
    from src.models.orm.organizations import Organization
    from src.models.orm.summary_backfill_job import SummaryBackfillJob
    from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
    from src.routers.agent_runs import backfill_summaries

    agent_id = uuid4()
    run_id = uuid4()
    created_backfill_job_id: UUID | None = None
    user_id = uuid4()
    role_id = uuid4()
    organization_id = uuid4()

    # Seed an agent + a pending run so the endpoint has something to enqueue.
    async with async_session_factory() as db:
        organization = Organization(id=organization_id, name=f"Backfill {organization_id}", created_by="test")
        user = User(
            id=user_id,
            email=f"backfill-{user_id}@test.local",
            organization_id=organization.id,
            base_role_id=USER_ROLE_ID,
        )
        role = Role(id=role_id, name=f"Backfill writer {role_id}", created_by="test")
        db.add_all([organization, user, role])
        await db.flush()
        db.add_all([
            UserRole(user_id=user.id, role_id=role.id, assigned_by="test"),
            UserRoleBoundary(user_id=user.id, role_id=role.id, kind="platform"),
            RolePermission(role_id=role.id, permission="agentruns.readwrite.all"),
        ])
        db.add(
            Agent(
                id=agent_id,
                name=f"Backfill router unit {uuid4().hex[:8]}",
                description="backfill router unit test",
                system_prompt="test",
                access_level="authenticated",
                created_by="test",
            )
        )
        db.add(
            AgentRun(
                id=run_id,
                agent_id=agent_id,
                trigger_type="test",
                status="completed",
                iterations_used=1,
                tokens_used=10,
                summary_status="pending",
            )
        )
        await db.commit()

    admin = UserPrincipal(
        user_id=user_id,
        email="admin@test.local",
        organization_id=organization_id,
        name="Test Admin",
        is_superuser=False,
    )

    try:
        async with async_session_factory() as db:
            with patch(
                "src.services.platform_jobs.enqueue_platform_job",
                new=AsyncMock(return_value=(object(), True)),
            ) as enqueue, patch(
                "src.services.platform_jobs.publish_platform_job_update",
                new=AsyncMock(),
            ):
                result = await backfill_summaries(
                    request=BackfillSummariesRequest(
                        agent_id=agent_id,
                        statuses=["pending"],
                        limit=100,
                        dry_run=False,
                    ),
                    db=db,
                    user=admin,
                )

            assert result.queued == 1
            created_backfill_job_id = result.job_id
            enqueue.assert_awaited_once()
            definition = enqueue.await_args.args[1]
            payload = enqueue.await_args.args[2]
            assert definition.job_type == "agent.summary_backfill"
            assert payload.run_ids == [run_id]
            assert payload.backfill_job_id == result.job_id
            run = await db.get(AgentRun, run_id)
            assert run is not None
            assert run.summary_backfill_job_id == result.job_id
    finally:
        async with async_session_factory() as db:
            if created_backfill_job_id is not None:
                await db.execute(
                    SummaryBackfillJob.__table__.delete().where(
                        SummaryBackfillJob.id == created_backfill_job_id
                    )
                )
            await db.execute(
                AgentRun.__table__.delete().where(AgentRun.id == run_id)
            )
            await db.execute(Agent.__table__.delete().where(Agent.id == agent_id))
            await db.execute(delete(User).where(User.id == user_id))
            await db.execute(delete(Role).where(Role.id == role_id))
            await db.execute(delete(Organization).where(Organization.id == organization_id))
            await db.commit()
