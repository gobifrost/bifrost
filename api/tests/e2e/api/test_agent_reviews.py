"""Agent review API/admission contracts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import AgentAccessLevel
from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agent_reviews import AgentReviewDefinition, AgentReviewRun, AgentReviewVersion
from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.models.orm.agents import Agent
from src.models.orm.ai_models import AIModelAssignment, AIModelProfile, AIProviderConnection
from src.models.orm.ai_usage import AIUsage, AIUsageAttempt
from src.models.orm.platform_jobs import PlatformJob
from src.core.principal import UserPrincipal
from shared.agent_review_admission import admit_agent_review_run, get_review_results, get_review_run
from shared.quality_usage import begin_quality_usage_attempt, record_quality_usage_observation
from shared.agent_reviews import AgentReviewServiceError
from shared.models import AgentReviewRunCreate
from src.routers.agent_reviews import create_run as create_review_run_route, get_results as get_review_results_route, get_usage as get_review_usage_route, get_run as get_review_run_route
from src.services.agent_runtime import types as runtime_types
from src.services.ai_model_service import AIModelService

pytestmark = pytest.mark.asyncio


def _route_user(user):
    return SimpleNamespace(
        user_id=user.user_id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or "",
        is_active=getattr(user, "is_active", True),
        is_superuser=getattr(user, "is_superuser", False),
        is_verified=getattr(user, "is_verified", True),
        is_external=getattr(user, "is_external", False),
    )


@pytest_asyncio.fixture
async def review_profile(db_session: AsyncSession):
    prior = await db_session.get(AIModelAssignment, "testing")
    prior_profile_id = prior.profile_id if prior is not None else None
    connection = AIProviderConnection(
        id=uuid4(),
        name=f"api-review-provider-{uuid4().hex[:8]}",
        provider="openai",
        encrypted_api_key=AIModelService(db_session).encrypt_api_key("test-key"),
    )
    db_session.add(connection)
    await db_session.flush()
    profile = AIModelProfile(
        id=uuid4(),
        name=f"api-review-profile-{uuid4().hex[:8]}",
        connection_id=connection.id,
        model="gpt-review-api-test",
        default_max_tokens=123,
    )
    db_session.add(profile)
    await db_session.flush()
    if prior is None:
        db_session.add(AIModelAssignment(assignment_key="testing", profile_id=profile.id))
    else:
        prior.profile_id = profile.id
    await db_session.commit()
    profile_id = profile.id
    connection_id = connection.id
    yield profile
    await db_session.rollback()
    assignment = await db_session.get(AIModelAssignment, "testing")
    if prior_profile_id is None:
        if assignment is not None:
            await db_session.delete(assignment)
    elif assignment is not None:
        assignment.profile_id = prior_profile_id
    else:
        db_session.add(AIModelAssignment(assignment_key="testing", profile_id=prior_profile_id))
    await db_session.flush()
    await db_session.execute(delete(AIModelProfile).where(AIModelProfile.id == profile_id))
    await db_session.execute(delete(AIProviderConnection).where(AIProviderConnection.id == connection_id))
    await db_session.commit()


@pytest_asyncio.fixture
async def review_agent(e2e_client, platform_admin, org1):
    resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Review API Agent {uuid4().hex[:8]}",
            "system_prompt": "Answer helpfully.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": org1["id"],
        },
        headers=platform_admin.headers,
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    yield agent
    e2e_client.delete(f"/api/agents/{agent['id']}", headers=platform_admin.headers)


@pytest_asyncio.fixture
async def source_run(db_session: AsyncSession, review_agent, org1_user):
    run = AgentRun(
        id=uuid4(),
        agent_id=UUID(review_agent["id"]),
        org_id=org1_user.organization_id,
        trigger_type="api",
        status="completed",
        caller_user_id=str(org1_user.user_id),
        output={"ok": True},
    )
    run.root_run_id = run.id
    db_session.add(run)
    await db_session.flush()
    db_session.add(AgentRunJournalEntry(run_id=run.id, sequence=1, kind=runtime_types.JOURNAL_COMPLETION, data={"status": "completed"}))
    await db_session.commit()
    run_id = run.id
    yield run
    await db_session.rollback()
    await db_session.execute(delete(AgentRunJournalEntry).where(AgentRunJournalEntry.run_id == run_id))
    await db_session.execute(delete(AgentRun).where(AgentRun.id == run_id))
    await db_session.commit()


async def _cleanup_reviews(db_session: AsyncSession, *review_ids: str) -> None:
    ids = [UUID(item) for item in review_ids if item]
    if not ids:
        return
    await db_session.rollback()
    runs = (await db_session.execute(select(AgentReviewRun).where(AgentReviewRun.review_id.in_(ids)))).scalars().all()
    job_ids = [run.platform_job_id for run in runs if run.platform_job_id]
    run_ids = [run.id for run in runs]
    if run_ids:
        await db_session.execute(delete(AIUsage).where(AIUsage.quality_operation_type == "agent_review", AIUsage.quality_operation_id.in_(run_ids)))
        await db_session.execute(delete(AIUsageAttempt).where(AIUsageAttempt.quality_operation_type == "agent_review", AIUsageAttempt.quality_operation_id.in_(run_ids)))
        await db_session.execute(delete(AgentFinding).where(AgentFinding.source_review_run_id.in_(run_ids)))
        await db_session.execute(delete(AgentReviewRun).where(AgentReviewRun.id.in_(run_ids)))
    await db_session.execute(delete(AgentReviewVersion).where(AgentReviewVersion.review_id.in_(ids)))
    await db_session.execute(delete(AgentReviewDefinition).where(AgentReviewDefinition.id.in_(ids)))
    if job_ids:
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id.in_(job_ids)))
    await db_session.commit()


async def test_review_definition_scope_authorization_matrix(e2e_client, platform_admin, org1, org2, org1_user, org2_user, review_profile, db_session: AsyncSession):
    global_agent_resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Global Review Agent {uuid4().hex[:8]}",
            "system_prompt": "Answer globally.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": None,
        },
        headers=platform_admin.headers,
    )
    assert global_agent_resp.status_code == 201, global_agent_resp.text
    global_agent = global_agent_resp.json()
    private_agent_resp = e2e_client.post(
        "/api/agents",
        json={
            "name": f"Private Review Agent {uuid4().hex[:8]}",
            "system_prompt": "Private.",
            "channels": ["chat"],
            "access_level": "authenticated",
            "organization_id": org1["id"],
        },
        headers=platform_admin.headers,
    )
    assert private_agent_resp.status_code == 201, private_agent_resp.text
    private_agent = private_agent_resp.json()
    scoped_review_id: str | None = None
    global_review_id: str | None = None
    try:
        private_row = await db_session.get(Agent, UUID(private_agent["id"]))
        assert private_row is not None
        private_row.access_level = AgentAccessLevel.PRIVATE
        private_row.owner_user_id = org1_user.user_id
        await db_session.commit()

        scoped = e2e_client.post(
            "/api/agent-reviews",
            json={
                "agent_id": global_agent["id"],
                "organization_id": org1["id"],
                "name": "Tenant scoped global-agent review",
                "review_statement": "Review tenant evidence.",
            },
            headers=platform_admin.headers,
        )
        assert scoped.status_code == 201, scoped.text
        scoped_review_id = scoped.json()["id"]
        assert scoped.json()["org_id"] == org1["id"]

        global_review = e2e_client.post(
            "/api/agent-reviews",
            json={
                "agent_id": global_agent["id"],
                "name": "Global review",
                "review_statement": "Review global evidence.",
            },
            headers=platform_admin.headers,
        )
        assert global_review.status_code == 201, global_review.text
        global_review_id = global_review.json()["id"]
        assert global_review.json()["org_id"] is None

        wrong_org = e2e_client.post(
            "/api/agent-reviews",
            json={
                "agent_id": private_agent["id"],
                "organization_id": org2["id"],
                "name": "Wrong org",
                "review_statement": "Wrong org.",
            },
            headers=platform_admin.headers,
        )
        assert wrong_org.status_code == 422, wrong_org.text

        private_hidden = e2e_client.post(
            "/api/agent-reviews",
            json={
                "agent_id": private_agent["id"],
                "name": "Hidden",
                "review_statement": "Should be hidden.",
            },
            headers=org2_user.headers,
        )
        assert private_hidden.status_code == 404, private_hidden.text

        null_org_denied = e2e_client.post(
            "/api/agent-reviews",
            json={
                "agent_id": global_agent["id"],
                "organization_id": None,
                "name": "Null org denied",
                "review_statement": "Tenant users cannot create global reviews.",
            },
            headers=org1_user.headers,
        )
        assert null_org_denied.status_code == 403, null_org_denied.text
    finally:
        await _cleanup_reviews(db_session, scoped_review_id, global_review_id)
        e2e_client.delete(f"/api/agents/{private_agent['id']}", headers=platform_admin.headers)
        e2e_client.delete(f"/api/agents/{global_agent['id']}", headers=platform_admin.headers)


async def test_review_definition_crud_and_version_scope(e2e_client, org1_user, org2_user, review_agent, review_profile, db_session: AsyncSession):
    denied = e2e_client.post(
        "/api/agent-reviews",
        json={
            "agent_id": review_agent["id"],
            "organization_id": None,
            "name": "Denied",
            "review_statement": "Check evidence.",
        },
        headers=org1_user.headers,
    )
    assert denied.status_code == 403, denied.text

    profile_denied = e2e_client.post(
        "/api/agent-reviews",
        json={
            "agent_id": review_agent["id"],
            "name": "Denied profile",
            "review_statement": "Check evidence.",
            "model_profile_id": str(review_profile.id),
        },
        headers=org1_user.headers,
    )
    assert profile_denied.status_code == 403, profile_denied.text

    created = e2e_client.post(
        "/api/agent-reviews",
        json={
            "agent_id": review_agent["id"],
            "name": "Review contract",
            "review_statement": "Check evidence.",
        },
        headers=org1_user.headers,
    )
    assert created.status_code == 201, created.text
    review = created.json()
    try:
        assert review["org_id"] == str(org1_user.organization_id)
        listed = e2e_client.get(f"/api/agent-reviews?agent_id={review_agent['id']}", headers=org1_user.headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] == 1

        hidden = e2e_client.get(f"/api/agent-reviews/{review['id']}", headers=org2_user.headers)
        assert hidden.status_code == 404

        patched = e2e_client.patch(
            f"/api/agent-reviews/{review['id']}",
            json={"name": "Renamed", "status": "disabled"},
            headers=org1_user.headers,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["status"] == "disabled"

        version_denied = e2e_client.post(
            f"/api/agent-reviews/{review['id']}/versions",
            json={"review_statement": "New statement."},
            headers=org1_user.headers,
        )
        assert version_denied.status_code == 409
    finally:
        await _cleanup_reviews(db_session, review.get("id"))


async def test_review_public_run_results_and_usage_routes(monkeypatch, e2e_client, org1_user, org2_user, review_agent, review_profile, source_run, db_session: AsyncSession):
    import shared.agent_review_admission as admission_module
    from src.services.platform_jobs import enqueue_platform_job as real_enqueue

    async def safe_enqueue(db, *args, **kwargs):
        job, reused = await real_enqueue(db, *args, **kwargs)
        if not reused:
            job.available_at = datetime.now(timezone.utc) + timedelta(days=1)
        return job, reused

    monkeypatch.setattr(admission_module, "enqueue_platform_job", safe_enqueue)

    created = e2e_client.post(
        "/api/agent-reviews",
        json={"agent_id": review_agent["id"], "name": "Public run review", "review_statement": "Check public route behavior."},
        headers=org1_user.headers,
    )
    assert created.status_code == 201, created.text
    review_id = created.json()["id"]
    try:
        route_org1_user = _route_user(org1_user)
        route_org2_user = _route_user(org2_user)
        response = Response()
        accepted = await create_review_run_route(
            UUID(review_id),
            AgentReviewRunCreate(run_ids=[source_run.id]),
            response,
            db_session,
            route_org1_user,
        )
        await db_session.commit()
        assert accepted.reused is False
        assert response.headers["Location"] == f"/api/platform-jobs/{accepted.job_id}"
        assert response.headers["X-Agent-Review-Run-Id"] == str(accepted.review_run_id)

        run = await db_session.get(AgentReviewRun, accepted.review_run_id)
        assert run is not None
        assert run.platform_job_id == accepted.job_id
        finding = AgentFinding(
            agent_id=run.agent_id,
            org_id=run.org_id,
            status="open",
            description="The agent omitted required evidence.",
            expected_behavior="The agent should cite evidence.",
            source_kind="run",
            source_run_id=source_run.id,
            finding_kind="problem",
            evidence_markdown="Evidence excerpt.",
            source_review_id=run.review_id,
            source_review_version_id=run.review_version_id,
            source_review_run_id=run.id,
            source_review_version=run.review_version,
            source_run_refs=list(run.source_refs or []),
            source_ordinal=0,
            created_by=org1_user.user_id,
        )
        db_session.add(finding)
        attempt = await begin_quality_usage_attempt(
            db_session,
            idempotency_key=f"agent-review-route-{run.id}",
            quality_operation_type="agent_review",
            quality_operation_id=run.id,
            quality_operation_item_id="review",
            usage_purpose="agent_review",
            provider="openai",
            model="gpt-review-api-test",
            request_fingerprint=run.request_fingerprint,
            organization_id=run.org_id,
            user_id=org1_user.user_id,
            platform_job_id=run.platform_job_id,
            profile_fingerprint=run.profile_fingerprint,
        )
        await record_quality_usage_observation(
            db_session,
            idempotency_key=attempt.attempt.idempotency_key,
            quality_operation_type="agent_review",
            quality_operation_id=run.id,
            quality_operation_item_id="review",
            usage_purpose="agent_review",
            provider="openai",
            model="gpt-review-api-test",
            request_fingerprint=run.request_fingerprint,
            input_tokens=12,
            output_tokens=3,
            cache_read_tokens=1,
            cache_write_tokens=0,
            provider_cost=Decimal("0.00012345"),
            duration_ms=25,
        )
        await db_session.commit()

        run_public = await get_review_run_route(run.id, db_session, route_org1_user)
        assert run_public.id == run.id
        assert run_public.source_refs[0].run_id == source_run.id

        results = await get_review_results_route(run.id, db_session, route_org1_user)
        assert results.review_run.id == run.id
        assert results.usage_operation_id == run.id
        assert len(results.findings) == 1
        assert results.findings[0].description == "The agent omitted required evidence."

        usage = await get_review_usage_route(run.id, db_session, route_org1_user)
        assert usage.overall.call_count == 1
        assert usage.overall.input_tokens == 12
        assert usage.overall.output_tokens == 3
        assert usage.overall.observed_provider_cost == Decimal("0.00012345")
        usage_json = usage.model_dump(mode="json")
        assert usage_json["overall"]["observed_provider_cost"] == "0.00012345"
        assert usage_json["overall"]["known_cost"] == "0.00012345"
        assert usage.by_purpose.items[0].purpose == "agent_review"
        assert usage.by_purpose.items[0].totals.call_count == 1
        assert usage.by_operation.items[0].operation_type == "agent_review"
        assert usage.by_operation.items[0].operation_id == run.id

        with pytest.raises(HTTPException) as other_run:
            await get_review_run_route(run.id, db_session, route_org2_user)
        assert other_run.value.status_code == 404

        source_run.org_id = org2_user.organization_id
        await db_session.commit()
        for route in (get_review_run_route, get_review_results_route, get_review_usage_route):
            with pytest.raises(HTTPException) as exc:
                await route(run.id, db_session, route_org1_user)
            assert exc.value.status_code == 404
    finally:
        await _cleanup_reviews(db_session, review_id)


async def test_review_admission_dedupe_terminal_rerun_and_read_revocation(e2e_client, monkeypatch, org1_user, org2_user, review_agent, review_profile, source_run, db_session: AsyncSession, async_session_factory):
    import shared.agent_review_admission as admission_module
    from src.services.platform_jobs import enqueue_platform_job as real_enqueue

    async def safe_enqueue(db, *args, **kwargs):
        job, reused = await real_enqueue(db, *args, **kwargs)
        if not reused:
            job.available_at = datetime.now(timezone.utc) + timedelta(days=1)
        return job, reused

    monkeypatch.setattr(admission_module, "enqueue_platform_job", safe_enqueue)

    created = e2e_client.post(
        "/api/agent-reviews",
        json={"agent_id": review_agent["id"], "name": "Run review", "review_statement": "Check evidence."},
        headers=org1_user.headers,
    )
    assert created.status_code == 201, created.text
    review_id = created.json()["id"]
    principal = UserPrincipal(
        user_id=org1_user.user_id,
        email=org1_user.email,
        organization_id=org1_user.organization_id,
        name=org1_user.name or "",
        is_active=getattr(org1_user, "is_active", True),
        is_superuser=getattr(org1_user, "is_superuser", False),
        is_verified=getattr(org1_user, "is_verified", True),
        is_external=getattr(org1_user, "is_external", False),
    )
    other_principal = UserPrincipal(
        user_id=org2_user.user_id,
        email=org2_user.email,
        organization_id=org2_user.organization_id,
        name=org2_user.name or "",
        is_active=getattr(org2_user, "is_active", True),
        is_superuser=getattr(org2_user, "is_superuser", False),
        is_verified=getattr(org2_user, "is_verified", True),
        is_external=getattr(org2_user, "is_external", False),
    )
    try:
        async def _admit_once():
            async with async_session_factory() as session:
                run, job, reused = await admit_agent_review_run(
                    session,
                    user=principal,
                    review_id=UUID(review_id),
                    body=AgentReviewRunCreate(run_ids=[source_run.id]),
                )
                await session.commit()
                return run.id, job.id, reused

        first_a, first_b = await asyncio.gather(_admit_once(), _admit_once())
        assert {first_a[0], first_b[0]} == {first_a[0]}
        assert {first_a[1], first_b[1]} == {first_a[1]}
        assert {first_a[2], first_b[2]} == {False, True}
        first_run_id, first_job_id = first_a[0], first_a[1]
        run_count = int((await db_session.execute(select(func.count()).select_from(AgentReviewRun).where(AgentReviewRun.review_id == UUID(review_id)))).scalar() or 0)
        assert run_count == 1
        first_job = await db_session.get(PlatformJob, first_job_id)
        assert first_job.payload == {"review_run_id": str(first_run_id)}
        assert first_job.result is None
        assert first_job.available_at > datetime.now(timezone.utc)

        async with async_session_factory() as session:
            with pytest.raises(AgentReviewServiceError) as exc:
                await admit_agent_review_run(
                    session,
                    user=other_principal,
                    review_id=UUID(review_id),
                    body=AgentReviewRunCreate(run_ids=[source_run.id]),
                )
        assert exc.value.code == "review_not_found"

        first_job.status = "succeeded"
        first_job.completed_at = datetime.now(timezone.utc)
        await db_session.commit()
        rerun_id, rerun_job_id, rerun_reused = await _admit_once()
        assert rerun_reused is False
        assert rerun_id != first_run_id
        assert rerun_job_id != first_job_id

        appended = e2e_client.post(
            f"/api/agent-reviews/{review_id}/versions",
            json={"review_statement": "Second version."},
            headers=org1_user.headers,
        )
        assert appended.status_code == 201, appended.text
        first_run = await db_session.get(AgentReviewRun, first_run_id)
        assert first_run.review_version == 1

        detail = await get_review_run(db_session, user=principal, review_run_id=first_run_id)
        assert detail.source_refs[0].run_id == source_run.id

        source_run.org_id = org2_user.organization_id
        await db_session.commit()
        with pytest.raises(AgentReviewServiceError) as exc:
            await get_review_results(db_session, user=principal, review_run_id=first_run_id)
        assert exc.value.code == "review_run_not_found"
    finally:
        await _cleanup_reviews(db_session, review_id)
