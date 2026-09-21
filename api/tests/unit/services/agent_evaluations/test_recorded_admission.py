"""Recorded evaluation admission and handler contracts."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, func, select

import shared.agent_recorded_admission as admission_module
from shared.agent_recorded_admission import (
    admit_recorded_evaluation,
    assert_recorded_evaluation_readable,
    execute_recorded_evaluation_job,
    get_recorded_results_page,
)
from shared.agent_recorded_judge import RecordedJudgeCallResult, assertion_hash
from shared.models import RecordedEvaluationCreate, RecordedApplicabilityOverride
from src.core.principal import UserPrincipal
from src.jobs.platform.base import PlatformJobCancelled, PlatformJobContext, PlatformJobFailure
from src.jobs.platform.agent_recorded_evaluation import (
    AGENT_RECORDED_SEMANTIC_EVALUATION_DEFINITION,
)
from src.models.enums import AgentAccessLevel
from src.models.orm.agent_evaluations import AgentEvaluationCase, AgentEvaluationSuite
from src.models.orm.agent_recorded_evaluations import (
    AgentRecordedEvaluation,
    AgentRecordedEvaluationResult,
)
from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.models.orm.agents import Agent
from src.models.orm.ai_usage import AIUsage, AIUsageAttempt
from src.models.orm.organizations import Organization
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.users import User
from src.services.agent_runtime import types as runtime_types
from src.services.platform_jobs import lock_running_platform_job_for_result_write
from src.routers.agent_recorded_evaluations import get_recorded_evaluation_usage

pytestmark = pytest.mark.asyncio

_CREATED_ORG_IDS: set[UUID] = set()
_CREATED_USER_IDS: set[UUID] = set()
_CREATED_AGENT_IDS: set[UUID] = set()
_CREATED_RUN_IDS: set[UUID] = set()
_CREATED_SUITE_IDS: set[UUID] = set()
_CREATED_CASE_IDS: set[UUID] = set()
_CREATED_JOB_IDS: set[UUID] = set()
_CREATED_EVALUATION_IDS: set[UUID] = set()


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_recorded_admission_rows(db_session):
    for bucket in (
        _CREATED_ORG_IDS,
        _CREATED_USER_IDS,
        _CREATED_AGENT_IDS,
        _CREATED_RUN_IDS,
        _CREATED_SUITE_IDS,
        _CREATED_CASE_IDS,
        _CREATED_JOB_IDS,
        _CREATED_EVALUATION_IDS,
    ):
        bucket.clear()
    yield
    await db_session.rollback()
    eval_ids = set(_CREATED_EVALUATION_IDS) | {
        row
        for row in (
            await db_session.execute(
                select(AgentRecordedEvaluation.id).where(
                    AgentRecordedEvaluation.org_id.in_(_CREATED_ORG_IDS)
                )
            )
        ).scalars()
    }
    if eval_ids:
        await db_session.execute(
            delete(AIUsage).where(AIUsage.quality_operation_id.in_(eval_ids))
        )
        await db_session.execute(
            delete(AIUsageAttempt).where(AIUsageAttempt.quality_operation_id.in_(eval_ids))
        )
        await db_session.execute(
            delete(AgentRecordedEvaluationResult).where(
                AgentRecordedEvaluationResult.evaluation_id.in_(eval_ids)
            )
        )
        await db_session.execute(
            delete(AgentRecordedEvaluation).where(AgentRecordedEvaluation.id.in_(eval_ids))
        )
    if _CREATED_JOB_IDS:
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id.in_(_CREATED_JOB_IDS)))
    if _CREATED_ORG_IDS:
        await db_session.execute(
            delete(PlatformJob).where(PlatformJob.organization_id.in_(_CREATED_ORG_IDS))
        )
    if _CREATED_RUN_IDS:
        await db_session.execute(
            delete(AgentRunJournalEntry).where(AgentRunJournalEntry.run_id.in_(_CREATED_RUN_IDS))
        )
        await db_session.execute(delete(AgentRun).where(AgentRun.id.in_(_CREATED_RUN_IDS)))
    if _CREATED_CASE_IDS:
        await db_session.execute(
            delete(AgentEvaluationCase).where(AgentEvaluationCase.id.in_(_CREATED_CASE_IDS))
        )
    if _CREATED_SUITE_IDS:
        await db_session.execute(
            delete(AgentEvaluationSuite).where(AgentEvaluationSuite.id.in_(_CREATED_SUITE_IDS))
        )
    if _CREATED_AGENT_IDS:
        await db_session.execute(delete(Agent).where(Agent.id.in_(_CREATED_AGENT_IDS)))
    if _CREATED_USER_IDS:
        await db_session.execute(delete(User).where(User.id.in_(_CREATED_USER_IDS)))
    if _CREATED_ORG_IDS:
        await db_session.execute(delete(Organization).where(Organization.id.in_(_CREATED_ORG_IDS)))
    await db_session.commit()


def _user(org_id: UUID | None, *, is_superuser: bool = False) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="recorded-b1@example.com",
        organization_id=org_id,
        is_superuser=is_superuser,
    )


async def _db_user(db_session, org_id: UUID | None, *, is_superuser: bool = False) -> User:
    user = User(
        id=uuid4(),
        email=f"recorded-{uuid4().hex[:12]}@example.com",
        organization_id=org_id,
        is_superuser=is_superuser,
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    await db_session.flush()
    _CREATED_USER_IDS.add(user.id)
    return user


async def _org(db_session, name: str) -> UUID:
    org = Organization(
        id=uuid4(),
        name=f"{name}-{uuid4().hex[:8]}",
        created_by="test",
    )
    db_session.add(org)
    await db_session.flush()
    _CREATED_ORG_IDS.add(org.id)
    return org.id


async def _agent(db_session, org_id: UUID | None, name: str) -> Agent:
    agent = Agent(
        id=uuid4(),
        name=f"{name}-{uuid4().hex[:8]}",
        system_prompt="Test only.",
        access_level=AgentAccessLevel.AUTHENTICATED,
        organization_id=org_id,
        created_by="test",
    )
    db_session.add(agent)
    await db_session.flush()
    _CREATED_AGENT_IDS.add(agent.id)
    return agent


async def _case(db_session, org_id: UUID, agent_id: UUID, *, assertions=None):
    suite = AgentEvaluationSuite(
        id=uuid4(),
        org_id=org_id,
        agent_id=agent_id,
        name=f"Recorded suite {uuid4().hex[:8]}",
        status="published",
        version=1,
    )
    case = AgentEvaluationCase(
        id=uuid4(),
        suite_id=suite.id,
        name="Terminal completed",
        enabled=True,
        accepted=True,
        version=1,
        assertions=assertions
        or [{"type": "terminal_status", "params": {"status": "completed"}}],
    )
    db_session.add_all([suite, case])
    await db_session.flush()
    _CREATED_SUITE_IDS.add(suite.id)
    _CREATED_CASE_IDS.add(case.id)
    return case


async def _run(
    db_session,
    org_id: UUID,
    agent_id: UUID,
    *,
    parent_id=None,
    caller_user_id: str | None = None,
    trigger_type: str | None = None,
) -> UUID:
    run_id = uuid4()
    run = AgentRun(
        id=run_id,
        org_id=org_id,
        agent_id=agent_id,
        trigger_type=trigger_type or ("api" if parent_id is None else "delegation"),
        status="completed",
        root_run_id=parent_id or run_id,
        parent_run_id=parent_id,
        output={"ok": True},
        caller_user_id=caller_user_id,
    )
    db_session.add(run)
    await db_session.flush()
    _CREATED_RUN_IDS.add(run_id)
    db_session.add(
        AgentRunJournalEntry(
            run_id=run_id,
            sequence=1,
            kind=runtime_types.JOURNAL_COMPLETION,
            data={"status": "completed"},
        )
    )
    await db_session.flush()
    return run_id


async def _job(db_session, *, org_id: UUID, status: str = "running") -> PlatformJob:
    job = PlatformJob(
        id=uuid4(),
        job_type="agent.evaluation_recorded",
        payload_version=1,
        payload={},
        organization_id=org_id,
        requested_by_user_id=str(uuid4()),
        requested_by_email="requester@example.com",
        requested_by_name="Requester",
        resource_type="agent_recorded_evaluation",
        resource_id=str(uuid4()),
        title="Recorded eval",
        status=status,
        phase="Running",
        lease_token=uuid4(),
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db_session.add(job)
    await db_session.flush()
    _CREATED_JOB_IDS.add(job.id)
    return job


async def test_admission_sorts_dedupe_and_freezes_delegated_source_identity(db_session):
    org_id = await _org(db_session, "dedupe")
    parent = await _agent(db_session, org_id, "parent")
    child = await _agent(db_session, org_id, "child")
    case = await _case(db_session, org_id, parent.id)
    first_run = await _run(db_session, org_id, parent.id)
    second_run = await _run(db_session, org_id, parent.id)
    await _run(db_session, org_id, child.id, parent_id=first_run)
    user = _user(org_id)

    first, job, reused = await admit_recorded_evaluation(
        db_session,
        user=user,
        body=RecordedEvaluationCreate(
            agent_id=parent.id,
            run_ids=[first_run, second_run],
            case_ids=[case.id],
            applicability="applicable",
        ),
    )
    assert reused is False
    await db_session.commit()

    second, reused_job, reused = await admit_recorded_evaluation(
        db_session,
        user=user,
        body=RecordedEvaluationCreate(
            agent_id=parent.id,
            run_ids=[second_run, first_run],
            case_ids=[case.id],
            applicability="applicable",
        ),
    )

    assert reused is True
    assert reused_job.id == job.id
    assert second.id == first.id
    source_runs = first.frozen_input["runs"][0]["source_runs"]
    assert any(item["agent_id"] == str(child.id) for item in source_runs)


async def test_tenant_suite_can_evaluate_globally_shared_agent(db_session):
    org_id = await _org(db_session, "global-agent")
    agent = await _agent(db_session, None, "global")
    case = await _case(db_session, org_id, agent.id)
    run_id = await _run(db_session, org_id, agent.id)

    evaluation, job, reused = await admit_recorded_evaluation(
        db_session,
        user=_user(org_id),
        body=RecordedEvaluationCreate(
            agent_id=agent.id,
            run_ids=[run_id],
            case_ids=[case.id],
            applicability="applicable",
        ),
    )

    assert reused is False
    assert evaluation.org_id == org_id
    assert job.organization_id == org_id
    assert evaluation.frozen_input["agent"]["org_id"] is None


async def test_superuser_recorded_evaluation_rejects_mixed_tenant_roots(db_session):
    first_org = await _org(db_session, "super-first")
    second_org = await _org(db_session, "super-second")
    agent = await _agent(db_session, None, "global")
    case = await _case(db_session, first_org, agent.id)
    first_run = await _run(db_session, first_org, agent.id)
    second_run = await _run(db_session, second_org, agent.id)

    with pytest.raises(HTTPException) as exc:
        await admit_recorded_evaluation(
            db_session,
            user=_user(None, is_superuser=True),
            body=RecordedEvaluationCreate(
                agent_id=agent.id,
                run_ids=[first_run, second_run],
                case_ids=[case.id],
            ),
        )

    assert exc.value.status_code == 422
    assert "one tenant" in str(exc.value.detail)


async def test_applicability_override_must_reference_selected_pair(db_session):
    org_id = await _org(db_session, "override")
    agent = await _agent(db_session, org_id, "agent")
    case = await _case(db_session, org_id, agent.id)
    run_id = await _run(db_session, org_id, agent.id)
    with pytest.raises(HTTPException) as exc:
        await admit_recorded_evaluation(
            db_session,
            user=_user(org_id),
            body=RecordedEvaluationCreate(
                agent_id=agent.id,
                run_ids=[run_id],
                case_ids=[case.id],
                applicability_overrides=[
                    RecordedApplicabilityOverride(
                        case_id=case.id,
                        run_id=uuid4(),
                        applicability="applicable",
                    )
                ],
            ),
        )
    assert exc.value.status_code == 422


async def test_requester_scoped_results_and_dedupe(db_session):
    org_id = await _org(db_session, "requester")
    agent = await _agent(db_session, org_id, "agent")
    case = await _case(db_session, org_id, agent.id)
    run_id = await _run(db_session, org_id, agent.id)
    requester = _user(org_id)
    other_requester = _user(org_id)

    first, first_job, reused = await admit_recorded_evaluation(
        db_session,
        user=requester,
        body=RecordedEvaluationCreate(
            agent_id=agent.id,
            run_ids=[run_id],
            case_ids=[case.id],
            applicability="applicable",
        ),
    )
    assert reused is False

    with pytest.raises(HTTPException) as exc:
        await assert_recorded_evaluation_readable(
            db_session, evaluation=first, user=other_requester
        )
    assert exc.value.status_code == 404

    second, second_job, reused = await admit_recorded_evaluation(
        db_session,
        user=other_requester,
        body=RecordedEvaluationCreate(
            agent_id=agent.id,
            run_ids=[run_id],
            case_ids=[case.id],
            applicability="applicable",
        ),
    )
    assert reused is False
    assert second.id != first.id
    assert second_job.id != first_job.id


async def test_private_selected_root_is_denied_at_admission(db_session):
    org_id = await _org(db_session, "private-root")
    owner_id = uuid4()
    agent = await _agent(db_session, org_id, "agent")
    case = await _case(db_session, org_id, agent.id)
    run_id = await _run(
        db_session,
        org_id,
        agent.id,
        caller_user_id=str(owner_id),
        trigger_type="delegation",
    )

    with pytest.raises(HTTPException) as exc:
        await admit_recorded_evaluation(
            db_session,
            user=_user(org_id),
            body=RecordedEvaluationCreate(
                agent_id=agent.id,
                run_ids=[run_id],
                case_ids=[case.id],
            ),
        )
    assert exc.value.status_code == 404


async def test_read_auth_rechecks_delegated_child_current_source(db_session):
    org_id = await _org(db_session, "read")
    other_org = await _org(db_session, "other")
    parent = await _agent(db_session, org_id, "parent")
    child = await _agent(db_session, org_id, "child")
    case = await _case(db_session, org_id, parent.id)
    root_id = await _run(db_session, org_id, parent.id)
    child_id = await _run(db_session, org_id, child.id, parent_id=root_id)
    user = _user(org_id)
    evaluation, _, _ = await admit_recorded_evaluation(
        db_session,
        user=user,
        body=RecordedEvaluationCreate(
            agent_id=parent.id,
            run_ids=[root_id],
            case_ids=[case.id],
        ),
    )
    await assert_recorded_evaluation_readable(
        db_session, evaluation=evaluation, user=user
    )

    child_run = await db_session.get(AgentRun, child_id)
    assert child_run is not None
    child_run.org_id = other_org
    await db_session.flush()
    with pytest.raises(HTTPException) as exc:
        await assert_recorded_evaluation_readable(
            db_session, evaluation=evaluation, user=user
        )
    assert exc.value.status_code == 404


async def test_read_auth_uses_frozen_source_authorization_not_evidence_reload(
    db_session, monkeypatch
):
    org_id = await _org(db_session, "read-no-reload")
    agent = await _agent(db_session, org_id, "agent")
    case = await _case(db_session, org_id, agent.id)
    run_id = await _run(db_session, org_id, agent.id)
    user = _user(org_id)
    evaluation, _, _ = await admit_recorded_evaluation(
        db_session,
        user=user,
        body=RecordedEvaluationCreate(
            agent_id=agent.id,
            run_ids=[run_id],
            case_ids=[case.id],
        ),
    )

    async def _fail_if_reloaded(*_args, **_kwargs):
        raise AssertionError("result read must not reload recorded evidence")

    monkeypatch.setattr(
        admission_module, "load_recorded_run_evidence", _fail_if_reloaded
    )

    await assert_recorded_evaluation_readable(
        db_session, evaluation=evaluation, user=user
    )


async def test_read_auth_fails_closed_without_frozen_source_refs(db_session):
    org_id = await _org(db_session, "missing-refs")
    agent = await _agent(db_session, org_id, "agent")
    run_id = await _run(db_session, org_id, agent.id)
    evaluation = AgentRecordedEvaluation(
        id=uuid4(),
        org_id=org_id,
        agent_id=agent.id,
        requested_by_user_id=str(uuid4()),
        requested_by_email="requester@example.com",
        platform_job_id=(await _job(db_session, org_id=org_id)).id,
        frozen_input={
            "cases": [],
            "runs": [
                {
                    "run_id": str(run_id),
                    "evidence": {},
                    "completeness": {},
                    "evidence_refs": [],
                    "limitations": [],
                }
            ],
            "applicability": {},
        },
    )
    db_session.add(evaluation)
    _CREATED_EVALUATION_IDS.add(evaluation.id)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await assert_recorded_evaluation_readable(
            db_session,
            evaluation=evaluation,
            user=UserPrincipal(
                user_id=UUID(evaluation.requested_by_user_id),
                email="requester@example.com",
                organization_id=org_id,
            ),
        )
    assert exc.value.status_code == 404


async def test_handler_persists_results_only_for_bound_running_job(db_session):
    org_id = await _org(db_session, "handler")
    agent = await _agent(db_session, org_id, "agent")
    run_id = uuid4()
    evaluation_id = uuid4()
    job = await _job(db_session, org_id=org_id)
    wrong_job = await _job(db_session, org_id=org_id)
    evaluation = AgentRecordedEvaluation(
        id=evaluation_id,
        org_id=org_id,
        agent_id=agent.id,
        requested_by_user_id=str(uuid4()),
        requested_by_email="requester@example.com",
        platform_job_id=job.id,
        frozen_input={
            "cases": [
                {
                    "case_id": str(uuid4()),
                    "suite_id": str(uuid4()),
                    "name": "terminal",
                    "version": 1,
                    "assertions": [
                        {"type": "terminal_status", "params": {"status": "completed"}}
                    ],
                }
            ],
            "runs": [
                {
                    "run_id": str(run_id),
                    "evidence": {"terminal_status": "completed"},
                    "completeness": {"terminal_status": True},
                    "evidence_refs": [],
                    "limitations": [],
                    "source_runs": [],
                }
            ],
            "applicability": {
                f"{uuid4()}:{run_id}": {
                    "value": "applicable",
                    "source": "caller_declared_default",
                }
            },
        },
    )
    case_id = evaluation.frozen_input["cases"][0]["case_id"]
    evaluation.frozen_input["applicability"] = {
        f"{case_id}:{run_id}": {
            "value": "applicable",
            "source": "caller_declared_default",
        }
    }
    db_session.add(evaluation)
    _CREATED_EVALUATION_IDS.add(evaluation.id)
    await db_session.commit()

    with pytest.raises(PlatformJobFailure):
        await execute_recorded_evaluation_job(
            PlatformJobContext(
                job_id=wrong_job.id,
                lease_token=wrong_job.lease_token,
                organization_id=org_id,
                requested_by_user_id=wrong_job.requested_by_user_id,
                requested_by_email=wrong_job.requested_by_email,
                requested_by_name=wrong_job.requested_by_name,
            ),
            evaluation_id=evaluation_id,
        )
    count = (
        await db_session.execute(
            select(AgentRecordedEvaluationResult).where(
                AgentRecordedEvaluationResult.evaluation_id == evaluation_id
            )
        )
    ).scalars().all()
    assert count == []

    result = await execute_recorded_evaluation_job(
        PlatformJobContext(
            job_id=job.id,
            lease_token=job.lease_token,
            organization_id=org_id,
            requested_by_user_id=job.requested_by_user_id,
            requested_by_email=job.requested_by_email,
            requested_by_name=job.requested_by_name,
        ),
        evaluation_id=evaluation_id,
    )
    assert result["counts"]["passed"] == 1
    rows = (
        await db_session.execute(
            select(AgentRecordedEvaluationResult).where(
                AgentRecordedEvaluationResult.evaluation_id == evaluation_id
            )
        )
    ).scalars().all()
    assert len(rows) == 1

    repeat = await execute_recorded_evaluation_job(
        PlatformJobContext(
            job_id=job.id,
            lease_token=job.lease_token,
            organization_id=org_id,
            requested_by_user_id=job.requested_by_user_id,
            requested_by_email=job.requested_by_email,
            requested_by_name=job.requested_by_name,
        ),
        evaluation_id=evaluation_id,
    )
    assert repeat["counts"]["passed"] == 1
    rows = (
        await db_session.execute(
            select(AgentRecordedEvaluationResult).where(
                AgentRecordedEvaluationResult.evaluation_id == evaluation_id
            )
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_platform_job_fence_rechecks_expiry_after_waiting_for_lock(
    db_session, async_session_factory
):
    org_id = await _org(db_session, "fence-lock")
    job = await _job(db_session, org_id=org_id)
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(milliseconds=150)
    job_id = job.id
    lease_token = job.lease_token
    await db_session.commit()

    async with async_session_factory() as locker:
        locked = (
            await locker.execute(
                select(PlatformJob)
                .where(PlatformJob.id == job_id)
                .with_for_update()
            )
        ).scalar_one()
        assert locked.id == job_id

        async def contender():
            async with async_session_factory() as contender_session:
                return await lock_running_platform_job_for_result_write(
                    contender_session, job_id=job_id, lease_token=lease_token
                )

        task = asyncio.create_task(contender())
        try:
            while True:
                now_value = await db_session.scalar(select(func.clock_timestamp()))
                assert now_value is not None
                if now_value > job.lease_expires_at:
                    break
                await asyncio.sleep(0.01)
            await locker.commit()
            assert await task is None
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

async def test_platform_job_fence_rejects_expired_and_cancelled(db_session):
    org_id = await _org(db_session, "fence")
    job = await _job(db_session, org_id=org_id)
    assert await lock_running_platform_job_for_result_write(
        db_session, job_id=job.id, lease_token=job.lease_token
    )
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.flush()
    assert await lock_running_platform_job_for_result_write(
        db_session, job_id=job.id, lease_token=job.lease_token
    ) is None
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    job.cancel_requested_at = datetime.now(timezone.utc)
    await db_session.flush()
    assert await lock_running_platform_job_for_result_write(
        db_session, job_id=job.id, lease_token=job.lease_token
    ) is None


def _semantic_assertion(*, endpoint: str | None = None, requirements=None) -> dict:
    return {
        "type": "llm_judge",
        "params": {
            "rubric": "Output is acceptable.",
            "prompt_version": "recorded-v1",
            "threshold": 0.7,
            "recorded_evidence_requirements": requirements or ["terminal_status", "output"],
            "judge_snapshot": {
                "profile_id": "00000000-0000-0000-0000-000000000001",
                "provider": "openai",
                "model": "judge-v1",
                "endpoint": endpoint,
                "openai_transport": None,
                "anthropic_prompt_cache_supported": None,
                "default_max_tokens": None,
                "extra_params": {},
                "prompt_version": "recorded-v1",
            },
        },
    }


async def _semantic_eval_fixture(db_session, *, endpoint: str | None = None, assertions=None):
    org_id = await _org(db_session, "semantic")
    requester = await _db_user(db_session, org_id, is_superuser=True)
    agent = await _agent(db_session, org_id, "agent")
    run_id = await _run(db_session, org_id, agent.id, caller_user_id=str(requester.id))
    case_id = uuid4()
    job = await _job(db_session, org_id=org_id)
    job.job_type = "agent.evaluation_recorded_semantic"
    job.requested_by_user_id = str(requester.id)
    evaluation = AgentRecordedEvaluation(
        id=uuid4(),
        org_id=org_id,
        agent_id=agent.id,
        requested_by_user_id=str(requester.id),
        requested_by_email=requester.email,
        platform_job_id=job.id,
        frozen_input={
            "judge_mode": "semantic",
            "cases": [
                {
                    "case_id": str(case_id),
                    "suite_id": str(uuid4()),
                    "name": "semantic",
                    "version": 1,
                    "assertions": assertions or [_semantic_assertion(endpoint=endpoint)],
                }
            ],
            "runs": [
                {
                    "run_id": str(run_id),
                    "evidence": {"terminal_status": "completed", "output": {"ok": True}},
                    "completeness": {"terminal_status": True, "output": True},
                    "evidence_refs": [],
                    "limitations": [],
                    "source_runs": [
                        {
                            "run_id": str(run_id),
                            "agent_id": str(agent.id),
                            "org_id": str(org_id),
                        }
                    ],
                }
            ],
            "applicability": {
                f"{case_id}:{run_id}": {
                    "value": "applicable",
                    "source": "caller_declared_default",
                }
            },
        },
    )
    db_session.add(evaluation)
    _CREATED_EVALUATION_IDS.add(evaluation.id)
    await db_session.commit()
    context = PlatformJobContext(
        job_id=job.id,
        lease_token=job.lease_token,
        organization_id=org_id,
        requested_by_user_id=job.requested_by_user_id,
        requested_by_email=job.requested_by_email,
        requested_by_name=job.requested_by_name,
    )
    return evaluation, job, context


async def test_semantic_admission_requires_admin_and_mode_affects_dedupe(db_session):
    org_id = await _org(db_session, "semantic-admit")
    agent = await _agent(db_session, org_id, "agent")
    case = await _case(db_session, org_id, agent.id, assertions=[_semantic_assertion()])
    run_id = await _run(db_session, org_id, agent.id)

    with pytest.raises(HTTPException) as exc:
        await admit_recorded_evaluation(
            db_session,
            user=_user(org_id),
            body=RecordedEvaluationCreate(
                agent_id=agent.id,
                run_ids=[run_id],
                case_ids=[case.id],
                applicability="applicable",
                judge_mode="semantic",
            ),
        )
    assert exc.value.status_code == 403

    admin = _user(org_id, is_superuser=True)
    exact, exact_job, _ = await admit_recorded_evaluation(
        db_session,
        user=admin,
        body=RecordedEvaluationCreate(
            agent_id=agent.id,
            run_ids=[run_id],
            case_ids=[case.id],
            applicability="applicable",
            judge_mode="exact",
        ),
    )
    semantic, semantic_job, _ = await admit_recorded_evaluation(
        db_session,
        user=admin,
        body=RecordedEvaluationCreate(
            agent_id=agent.id,
            run_ids=[run_id],
            case_ids=[case.id],
            applicability="applicable",
            judge_mode="semantic",
        ),
    )
    assert exact.id != semantic.id
    assert exact_job.job_type == "agent.evaluation_recorded"
    assert semantic_job.job_type == "agent.evaluation_recorded_semantic"


async def test_semantic_platform_job_policy_has_no_runner_loss_retry():
    policy = AGENT_RECORDED_SEMANTIC_EVALUATION_DEFINITION.policy
    assert policy.max_attempts == 1
    assert policy.retry_on_runner_loss is False


async def test_exact_unknown_and_not_applicable_do_not_start_semantic_attempts(db_session, monkeypatch):
    calls = 0

    async def unexpected_call(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("model must not be called")

    monkeypatch.setattr(admission_module, "execute_recorded_semantic_judge", unexpected_call)
    for applicability in ("unknown", "not_applicable"):
        evaluation, _job_row, context = await _semantic_eval_fixture(db_session)
        frozen_input = dict(evaluation.frozen_input)
        frozen_input["applicability"] = {
            key: {"value": applicability, "source": "caller_declared_default"}
            for key in frozen_input["applicability"]
        }
        evaluation.frozen_input = frozen_input
        await db_session.commit()
        await execute_recorded_evaluation_job(context, evaluation_id=evaluation.id)
    assert calls == 0


async def test_semantic_handler_records_usage_for_malformed_response_openrouter_identity(db_session, monkeypatch):
    from decimal import Decimal

    from src.models.orm.ai_usage import AIUsage

    async def fake_call(**_kwargs):
        return RecordedJudgeCallResult(
            outcome={
                "type": "llm_judge",
                "outcome": "error",
                "passed": False,
                "reason": "judge_invalid_response",
                "detail": "semantic judge returned malformed or invalid structured output",
            },
            usage={
                "input_tokens": 13,
                "output_tokens": 2,
                "cache_read_tokens": 1,
                "cache_write_tokens": 0,
                "provider_cost": Decimal("0.0004"),
                "duration_ms": 321,
            },
        )

    monkeypatch.setattr(admission_module, "execute_recorded_semantic_judge", fake_call)
    evaluation, _job_row, context = await _semantic_eval_fixture(
        db_session, endpoint="https://openrouter.ai/api/v1"
    )
    await execute_recorded_evaluation_job(context, evaluation_id=evaluation.id)

    usage = (
        await db_session.execute(
            select(AIUsage).where(AIUsage.quality_operation_id == evaluation.id)
        )
    ).scalars().one()
    assert usage.provider == "openrouter"
    assert usage.input_tokens == 13
    assert usage.provider_cost == Decimal("0.00040000")
    assert usage.duration_ms == 321
    row = (
        await db_session.execute(
            select(AgentRecordedEvaluationResult).where(
                AgentRecordedEvaluationResult.evaluation_id == evaluation.id
            )
        )
    ).scalars().one()
    assert row.outcome == "error"
    assert row.assertion_outcomes[0]["accounting_attempt_id"]


async def test_semantic_started_marker_is_not_replayed_and_becomes_ambiguous(db_session, monkeypatch):
    calls = 0

    async def fake_call(**_kwargs):
        nonlocal calls
        calls += 1
        return RecordedJudgeCallResult(
            outcome={"type": "llm_judge", "outcome": "passed", "passed": True, "reason": "semantic_judge"},
            usage={"input_tokens": 1, "output_tokens": 1, "cache_read_tokens": 0, "cache_write_tokens": 0, "provider_cost": None},
        )

    monkeypatch.setattr(admission_module, "execute_recorded_semantic_judge", fake_call)
    assertions = [_semantic_assertion(), _semantic_assertion()]
    evaluation, _job_row, context = await _semantic_eval_fixture(db_session, assertions=assertions)
    run_id = UUID(evaluation.frozen_input["runs"][0]["run_id"])
    case_id = UUID(evaluation.frozen_input["cases"][0]["case_id"])
    db_session.add(
        AgentRecordedEvaluationResult(
            evaluation_id=evaluation.id,
            case_id=case_id,
            case_version=1,
            run_id=run_id,
            applicability="applicable",
            applicability_source="caller_declared_default",
            outcome="pending_judge",
            complete=False,
            assertion_outcomes=[
                {
                    "type": "llm_judge",
                    "outcome": "passed",
                    "passed": True,
                    "judge_execution_state": "completed",
                    "assertion_index": 0,
                    "assertion_hash": assertion_hash(assertions[0]),
                },
                {
                    "type": "llm_judge",
                    "outcome": "pending_judge",
                    "passed": False,
                    "judge_execution_state": "started",
                    "accounting_attempt_id": str(uuid4()),
                    "assertion_index": 1,
                    "assertion_hash": assertion_hash(assertions[1]),
                },
            ],
            counts={"passed": 1, "pending_judge": 1},
            evidence_refs=[],
            limitations=[],
        )
    )
    await db_session.commit()

    await execute_recorded_evaluation_job(context, evaluation_id=evaluation.id)
    assert calls == 0
    row = (
        await db_session.execute(
            select(AgentRecordedEvaluationResult).where(
                AgentRecordedEvaluationResult.evaluation_id == evaluation.id
            )
        )
    ).scalars().one()
    assert row.assertion_outcomes[1]["reason"] == "judge_attempt_ambiguous"
    assert row.outcome == "error"


async def test_semantic_paid_marker_with_mismatched_identity_fails_closed(db_session, monkeypatch):
    calls = 0

    async def fake_call(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("mismatched paid marker must not be replayed")

    monkeypatch.setattr(admission_module, "execute_recorded_semantic_judge", fake_call)
    assertions = [_semantic_assertion()]
    evaluation, _job_row, context = await _semantic_eval_fixture(db_session, assertions=assertions)
    run_id = UUID(evaluation.frozen_input["runs"][0]["run_id"])
    case_id = UUID(evaluation.frozen_input["cases"][0]["case_id"])
    db_session.add(
        AgentRecordedEvaluationResult(
            evaluation_id=evaluation.id,
            case_id=case_id,
            case_version=1,
            run_id=run_id,
            applicability="applicable",
            applicability_source="caller_declared_default",
            outcome="pending_judge",
            complete=False,
            assertion_outcomes=[
                {
                    "type": "llm_judge",
                    "outcome": "pending_judge",
                    "passed": False,
                    "judge_execution_state": "started",
                    "accounting_attempt_id": str(uuid4()),
                    "assertion_index": 0,
                    "assertion_hash": assertion_hash({"type": "llm_judge", "params": {"rubric": "old"}}),
                }
            ],
            counts={"pending_judge": 1},
            evidence_refs=[],
            limitations=[],
        )
    )
    await db_session.commit()

    await execute_recorded_evaluation_job(context, evaluation_id=evaluation.id)
    assert calls == 0
    row = (
        await db_session.execute(
            select(AgentRecordedEvaluationResult).where(
                AgentRecordedEvaluationResult.evaluation_id == evaluation.id
            )
        )
    ).scalars().one()
    assert row.assertion_outcomes[0]["outcome"] == "error"
    assert row.assertion_outcomes[0]["reason"] == "judge_marker_identity_mismatch"
    assert row.assertion_outcomes[0]["accounting_attempt_id"]


async def test_semantic_usage_persists_when_lease_expires_before_verdict_write(db_session, monkeypatch):
    from decimal import Decimal

    from src.models.orm.ai_usage import AIUsage

    async def fake_call(**_kwargs):
        async with admission_module.get_db_context() as db:
            job = await db.get(PlatformJob, context.job_id)
            job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()
        return RecordedJudgeCallResult(
            outcome={"type": "llm_judge", "outcome": "passed", "passed": True, "reason": "semantic_judge", "judge_execution_state": "completed"},
            usage={"input_tokens": 4, "output_tokens": 2, "cache_read_tokens": 0, "cache_write_tokens": 0, "provider_cost": Decimal("0.0001")},
        )

    monkeypatch.setattr(admission_module, "execute_recorded_semantic_judge", fake_call)
    evaluation, _job_row, context = await _semantic_eval_fixture(db_session)
    with pytest.raises(PlatformJobCancelled):
        await execute_recorded_evaluation_job(context, evaluation_id=evaluation.id)

    usage = (
        await db_session.execute(
            select(AIUsage).where(AIUsage.quality_operation_id == evaluation.id)
        )
    ).scalars().one()
    assert usage.input_tokens == 4
    row = (
        await db_session.execute(
            select(AgentRecordedEvaluationResult).where(
                AgentRecordedEvaluationResult.evaluation_id == evaluation.id
            )
        )
    ).scalars().one()
    assert row.assertion_outcomes[0]["judge_execution_state"] == "started"


async def test_terminal_failed_job_read_projects_started_semantic_judge_as_ambiguous(db_session):
    evaluation, job, _context = await _semantic_eval_fixture(db_session)
    job.status = "failed"
    assertion = evaluation.frozen_input["cases"][0]["assertions"][0]
    run_id = UUID(evaluation.frozen_input["runs"][0]["run_id"])
    case_id = UUID(evaluation.frozen_input["cases"][0]["case_id"])
    db_session.add(
        AgentRecordedEvaluationResult(
            evaluation_id=evaluation.id,
            case_id=case_id,
            case_version=1,
            run_id=run_id,
            applicability="applicable",
            applicability_source="caller_declared_default",
            outcome="pending_judge",
            complete=False,
            assertion_outcomes=[
                {
                    "type": "llm_judge",
                    "outcome": "pending_judge",
                    "passed": False,
                    "judge_execution_state": "started",
                    "accounting_attempt_id": str(uuid4()),
                    "assertion_index": 0,
                    "assertion_hash": assertion_hash(assertion),
                }
            ],
            counts={"pending_judge": 1},
            evidence_refs=[],
            limitations=[],
        )
    )
    await db_session.commit()

    page = await get_recorded_results_page(
        db_session,
        evaluation_id=evaluation.id,
        user=UserPrincipal(
            user_id=UUID(evaluation.requested_by_user_id),
            email=evaluation.requested_by_email,
            organization_id=evaluation.org_id,
            is_superuser=False,
        ),
        limit=50,
        offset=0,
    )

    result = page.results[0]
    assert result.outcome == "error"
    assert result.error == "judge_attempt_ambiguous"
    assert result.assertion_outcomes[0]["reason"] == "judge_attempt_ambiguous"


async def test_global_recorded_usage_route_uses_platform_scope_for_authorized_admin(db_session):
    from src.models.orm.ai_usage import AIUsage

    requester = await _db_user(db_session, None, is_superuser=True)
    agent = await _agent(db_session, None, "global-agent")
    run_id = await _run(db_session, None, agent.id, caller_user_id=str(requester.id))
    job = await _job(db_session, org_id=None)
    evaluation = AgentRecordedEvaluation(
        id=uuid4(),
        org_id=None,
        agent_id=agent.id,
        requested_by_user_id=str(requester.id),
        requested_by_email=requester.email,
        platform_job_id=job.id,
        frozen_input={
            "judge_mode": "semantic",
            "cases": [],
            "runs": [
                {
                    "run_id": str(run_id),
                    "source_runs": [
                        {"run_id": str(run_id), "agent_id": str(agent.id), "org_id": None}
                    ],
                }
            ],
            "applicability": {},
        },
    )
    db_session.add(evaluation)
    _CREATED_EVALUATION_IDS.add(evaluation.id)
    db_session.add(
        AIUsage(
            provider="openrouter",
            model="judge-v1",
            input_tokens=7,
            output_tokens=3,
            cache_read_tokens=0,
            cache_write_tokens=0,
            provider_cost=Decimal("0.0002"),
            cost=Decimal("0.0002"),
            quality_operation_type="recorded_evaluation",
            quality_operation_id=evaluation.id,
            usage_purpose="recorded_semantic_judge",
            organization_id=None,
        )
    )
    await db_session.commit()

    page = await get_recorded_evaluation_usage(
        evaluation.id,
        db_session,
        UserPrincipal(
            user_id=requester.id,
            email=requester.email,
            organization_id=None,
            is_superuser=True,
        ),
    )

    assert page.overall.input_tokens == 7
    assert page.by_provider_model.items[0].provider == "openrouter"
