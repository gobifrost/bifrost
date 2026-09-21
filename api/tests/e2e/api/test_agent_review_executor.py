from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from shared.agent_reviews import (
    build_review_evidence_input,
    execute_agent_review_job,
    freeze_review_profile_snapshot,
    profile_snapshot_to_dict,
    review_request_fingerprint,
)
from src.jobs.platform.base import PlatformJobCancelled, PlatformJobContext, PlatformJobFailure
from src.models.enums import AgentAccessLevel
from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agent_reviews import AgentReviewDefinition, AgentReviewRun, AgentReviewVersion
from src.models.orm.agent_runs import AgentRun, AgentRunJournalEntry
from src.models.orm.agents import Agent
from src.models.orm.ai_models import AIModelAssignment, AIModelProfile, AIProviderConnection
from src.models.orm.ai_usage import AIUsage, AIUsageAttempt
from src.models.orm.platform_jobs import PlatformJob
from src.services.ai_model_service import AIModelService
from src.services.llm.base import LLMResponse
from src.core.principal import UserPrincipal
from src.services.agent_runtime import types as runtime_types

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def review_cleanup(db_session):
    tracked = {key: [] for key in [
        "finding_ids", "usage_ids", "attempt_ids", "review_run_ids", "version_ids",
        "review_ids", "job_ids", "run_ids", "agent_ids", "assignment_keys",
        "profile_ids", "connection_ids",
    ]}
    try:
        yield tracked
    finally:
        await db_session.rollback()
        for model, key in [
            (AgentFinding, "finding_ids"),
            (AIUsage, "usage_ids"),
            (AIUsageAttempt, "attempt_ids"),
            (AgentReviewRun, "review_run_ids"),
            (AgentReviewVersion, "version_ids"),
            (AgentReviewDefinition, "review_ids"),
            (PlatformJob, "job_ids"),
            (AgentRun, "run_ids"),
            (Agent, "agent_ids"),
            (AIModelAssignment, "assignment_keys"),
            (AIModelProfile, "profile_ids"),
            (AIProviderConnection, "connection_ids"),
        ]:
            ids = tracked[key]
            if ids:
                pk = getattr(model, "assignment_key", None) if model is AIModelAssignment else getattr(model, "id")
                await db_session.execute(delete(model).where(pk.in_(ids)))
        await db_session.commit()


async def _profile(db_session, review_cleanup):
    connection = AIProviderConnection(
        id=uuid4(),
        name=f"review-provider-{uuid4().hex[:8]}",
        provider="openai",
        encrypted_api_key=AIModelService(db_session).encrypt_api_key("test-key"),
    )
    profile = AIModelProfile(
        id=uuid4(),
        name=f"review-profile-{uuid4().hex[:8]}",
        connection_id=connection.id,
        model="gpt-review-test",
        default_max_tokens=123,
    )
    assignment = AIModelAssignment(assignment_key="testing", profile_id=profile.id)
    db_session.add_all([connection, profile, assignment])
    await db_session.flush()
    review_cleanup["connection_ids"].append(connection.id)
    review_cleanup["profile_ids"].append(profile.id)
    review_cleanup["assignment_keys"].append("testing")
    snapshot = await freeze_review_profile_snapshot(db_session, profile_id=None)
    return snapshot


async def _review_fixture(db_session, org1_user, review_cleanup):
    snapshot = await _profile(db_session, review_cleanup)
    agent = Agent(
        id=uuid4(),
        name=f"Review Agent {uuid4().hex[:8]}",
        system_prompt="Help users.",
        access_level=AgentAccessLevel.AUTHENTICATED,
        organization_id=org1_user.organization_id,
        created_by="test",
        is_active=True,
    )
    source_run = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        org_id=org1_user.organization_id,
        trigger_type="api",
        status="completed",
        root_run_id=None,
        parent_run_id=None,
        caller_user_id=str(org1_user.user_id),
    )
    source_run.root_run_id = source_run.id
    review = AgentReviewDefinition(
        id=uuid4(),
        agent_id=agent.id,
        org_id=org1_user.organization_id,
        name="Review definition",
        status="active",
        latest_version=1,
        created_by=org1_user.user_id,
    )
    version = AgentReviewVersion(
        id=uuid4(),
        review_id=review.id,
        version=1,
        review_statement="Find problems in the recorded evidence.",
        evidence_format_instructions="Cite run IDs.",
        created_by=org1_user.user_id,
    )
    review_run_id = uuid4()
    lease_token = uuid4()
    job = PlatformJob(
        id=uuid4(),
        job_type="agent.review",
        payload_version=1,
        payload={"review_run_id": str(review_run_id)},
        organization_id=org1_user.organization_id,
        requested_by_user_id=str(org1_user.user_id),
        requested_by_email=org1_user.email,
        requested_by_name="Reviewer",
        resource_type="agent_review_run",
        resource_id=str(review_run_id),
        title="Agent review",
        status="running",
        phase="Running",
        lease_token=lease_token,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    source_refs = [
        {
            "run_id": str(source_run.id),
            "agent_id": str(agent.id),
            "org_id": str(org1_user.organization_id),
            "root_run_id": str(source_run.id),
            "parent_run_id": None,
            "trigger_type": "api",
        }
    ]
    review_input = {
        "review_statement": version.review_statement,
        "evidence_format_instructions": version.evidence_format_instructions,
        "selected_runs": [{"run_id": str(source_run.id), "evidence": {"run_id": str(source_run.id)}, "completeness": {"usage.tokens": False}, "limitations": ["usage incomplete"]}],
        "instructions": "Return JSON only.",
    }
    review_run = AgentReviewRun(
        id=review_run_id,
        review_id=review.id,
        review_version_id=version.id,
        review_version=1,
        agent_id=agent.id,
        org_id=org1_user.organization_id,
        platform_job_id=job.id,
        requested_by_user_id=org1_user.user_id,
        requested_run_ids=[source_run.id],
        selected_run_ids=[source_run.id],
        source_evidence=review_input,
        source_refs=source_refs,
        profile_snapshot=profile_snapshot_to_dict(snapshot),
        profile_fingerprint=snapshot.fingerprint,
        request_fingerprint=review_request_fingerprint(
            review_id=review.id,
            review_version_id=version.id,
            review_version=version.version,
            selected_run_ids=[source_run.id],
            review_input=review_input,
            source_refs=source_refs,
            profile_fingerprint=snapshot.fingerprint,
        ),
        input_bytes=len(json.dumps(review_input, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")),
    )
    db_session.add(agent)
    await db_session.flush()
    db_session.add(source_run)
    await db_session.flush()
    db_session.add(review)
    await db_session.flush()
    db_session.add(version)
    await db_session.flush()
    db_session.add(job)
    await db_session.flush()
    db_session.add(review_run)
    await db_session.flush()
    for key, value in [
        ("agent_ids", agent.id), ("run_ids", source_run.id), ("review_ids", review.id),
        ("version_ids", version.id), ("job_ids", job.id), ("review_run_ids", review_run.id),
    ]:
        review_cleanup[key].append(value)
    context = PlatformJobContext(
        job_id=job.id,
        lease_token=lease_token,
        organization_id=org1_user.organization_id,
        requested_by_user_id=str(org1_user.user_id),
        requested_by_email=org1_user.email,
        requested_by_name="Reviewer",
    )
    return review_run, source_run, job, context


async def _collect_created(db_session, review_cleanup, review_run_id):
    attempts = (await db_session.execute(select(AIUsageAttempt).where(AIUsageAttempt.quality_operation_id == review_run_id))).scalars().all()
    usages = (await db_session.execute(select(AIUsage).where(AIUsage.quality_operation_id == review_run_id))).scalars().all()
    findings = (await db_session.execute(select(AgentFinding).where(AgentFinding.source_review_run_id == review_run_id))).scalars().all()
    for attempt in attempts:
        if attempt.id not in review_cleanup["attempt_ids"]:
            review_cleanup["attempt_ids"].append(attempt.id)
    for usage in usages:
        if usage.id not in review_cleanup["usage_ids"]:
            review_cleanup["usage_ids"].append(usage.id)
    for finding in findings:
        if finding.id not in review_cleanup["finding_ids"]:
            review_cleanup["finding_ids"].append(finding.id)
    return attempts, usages, findings


async def test_build_review_evidence_input_uses_real_recorded_reader_shape(db_session, org1_user, review_cleanup):
    agent = Agent(
        id=uuid4(),
        name=f"Evidence Agent {uuid4().hex[:8]}",
        system_prompt="Help users.",
        access_level=AgentAccessLevel.AUTHENTICATED,
        organization_id=org1_user.organization_id,
        created_by="test",
        is_active=True,
    )
    run = AgentRun(
        id=uuid4(),
        agent_id=agent.id,
        org_id=org1_user.organization_id,
        trigger_type="api",
        status="completed",
        root_run_id=None,
        parent_run_id=None,
        caller_user_id=str(org1_user.user_id),
        output={"ok": True},
    )
    run.root_run_id = run.id
    review = AgentReviewDefinition(
        id=uuid4(),
        agent_id=agent.id,
        org_id=org1_user.organization_id,
        name="Review definition",
        status="active",
        latest_version=1,
        created_by=org1_user.user_id,
    )
    version = AgentReviewVersion(
        id=uuid4(),
        review_id=review.id,
        version=1,
        review_statement="Find problems.",
        created_by=org1_user.user_id,
    )
    db_session.add(agent)
    await db_session.flush()
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        AgentRunJournalEntry(
            run_id=run.id,
            sequence=1,
            kind=runtime_types.JOURNAL_COMPLETION,
            data={"status": "completed"},
        )
    )
    db_session.add(review)
    await db_session.flush()
    db_session.add(version)
    await db_session.flush()
    review_cleanup["agent_ids"].append(agent.id)
    review_cleanup["run_ids"].append(run.id)
    review_cleanup["review_ids"].append(review.id)
    review_cleanup["version_ids"].append(version.id)

    principal = UserPrincipal(
        user_id=org1_user.user_id,
        email=org1_user.email,
        organization_id=org1_user.organization_id,
    )

    built = await build_review_evidence_input(
        db_session,
        principal,
        review=review,
        version=version,
        requested_run_ids=[run.id],
    )

    assert built.selected_run_ids == [run.id]
    assert built.source_refs == [
        {
            "run_id": str(run.id),
            "agent_id": str(agent.id),
            "org_id": str(org1_user.organization_id),
            "root_run_id": str(run.id),
            "parent_run_id": None,
            "trigger_type": "api",
        }
    ]
    selected = built.input["selected_runs"][0]
    assert selected["run_id"] == str(run.id)
    assert "evidence_refs" in selected
    assert "limitations" in selected
    assert built.input["response_schema"]["findings"][0]["kind"] == "problem or opportunity"
    assert "expected_behavior" in built.input["instructions"]


async def test_agent_review_executor_records_usage_and_inserts_bounded_findings(monkeypatch, db_session, org1_user, review_cleanup):
    review_run, source_run, _job, context = await _review_fixture(db_session, org1_user, review_cleanup)
    await db_session.commit()
    calls = 0

    async def fake_call(config, review_input):
        nonlocal calls
        calls += 1
        return LLMResponse(
            content=(
                '{"summary":"summary only","findings":[{"kind":"problem",'
                '"description":"Missed escalation","expected_behavior":"Escalate",'
                '"evidence_markdown":"Evidence",'
                f'"source_run_ids":["{source_run.id}"]' + '}]} '
            ),
            input_tokens=10,
            output_tokens=5,
            provider_cost=Decimal("0.00010000"),
        ), 7

    monkeypatch.setattr("shared.agent_reviews._call_review_model", fake_call)

    result = await execute_agent_review_job(context, review_run.id)

    attempts, usages, findings = await _collect_created(db_session, review_cleanup, review_run.id)
    assert result == {"review_run_id": str(review_run.id), "findings_created": 1}
    assert calls == 1
    assert len(attempts) == 1
    assert len(usages) == 1
    assert usages[0].usage_purpose == "agent_review"
    assert usages[0].input_tokens == 10
    assert len(findings) == 1
    assert findings[0].source_review_run_id == review_run.id
    assert findings[0].source_run_refs[0]["run_id"] == str(source_run.id)
    refreshed = (
        await db_session.execute(
            select(AgentReviewRun)
            .where(AgentReviewRun.id == review_run.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert refreshed.result_summary == "summary only"


async def test_agent_review_executor_records_usage_before_invalid_response(monkeypatch, db_session, org1_user, review_cleanup):
    review_run, _source_run, _job, context = await _review_fixture(db_session, org1_user, review_cleanup)
    await db_session.commit()

    async def fake_call(config, review_input):
        return LLMResponse(content='{"summary":"ok","findings":"bad"}', input_tokens=4, output_tokens=2), 5

    monkeypatch.setattr("shared.agent_reviews._call_review_model", fake_call)

    with pytest.raises(PlatformJobFailure) as exc:
        await execute_agent_review_job(context, review_run.id)
    assert exc.value.code == "invalid_review_response"

    attempts, usages, findings = await _collect_created(db_session, review_cleanup, review_run.id)
    assert len(attempts) == 1
    assert len(usages) == 1
    assert findings == []


async def test_agent_review_executor_missing_usage_marks_unobserved_without_zero_row(monkeypatch, db_session, org1_user, review_cleanup):
    review_run, _source_run, _job, context = await _review_fixture(db_session, org1_user, review_cleanup)
    await db_session.commit()

    async def fake_call(config, review_input):
        return LLMResponse(content='{"summary":null,"findings":[]}', input_tokens=None, output_tokens=2), 5

    monkeypatch.setattr("shared.agent_reviews._call_review_model", fake_call)

    await execute_agent_review_job(context, review_run.id)

    attempts, usages, findings = await _collect_created(db_session, review_cleanup, review_run.id)
    assert len(attempts) == 1
    assert attempts[0].state == "unobserved"
    assert attempts[0].unobserved_reason == "missing_usage"
    assert usages == []
    assert findings == []


async def test_agent_review_executor_stale_after_response_preserves_usage_without_findings(monkeypatch, db_session, org1_user, review_cleanup, async_session_factory):
    review_run, _source_run, job, context = await _review_fixture(db_session, org1_user, review_cleanup)
    await db_session.commit()

    async def fake_call(config, review_input):
        async with async_session_factory() as other:
            row = await other.get(PlatformJob, job.id)
            row.cancel_requested_at = datetime.now(timezone.utc)
            await other.commit()
        return LLMResponse(content='{"summary":null,"findings":[]}', input_tokens=3, output_tokens=1), 5

    monkeypatch.setattr("shared.agent_reviews._call_review_model", fake_call)

    with pytest.raises(PlatformJobCancelled):
        await execute_agent_review_job(context, review_run.id)

    attempts, usages, findings = await _collect_created(db_session, review_cleanup, review_run.id)
    assert len(attempts) == 1
    assert len(usages) == 1
    assert findings == []
    refreshed = (
        await db_session.execute(
            select(AgentReviewRun)
            .where(AgentReviewRun.id == review_run.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert refreshed.result_summary is None


async def test_agent_review_executor_duplicate_attempt_does_not_call_provider(monkeypatch, db_session, org1_user, review_cleanup):
    review_run, _source_run, _job, context = await _review_fixture(db_session, org1_user, review_cleanup)
    await db_session.commit()

    async def fake_call(config, review_input):
        return LLMResponse(content='{"summary":null,"findings":[]}', input_tokens=1, output_tokens=1), 1

    monkeypatch.setattr("shared.agent_reviews._call_review_model", fake_call)
    await execute_agent_review_job(context, review_run.id)

    async def should_not_call(config, review_input):
        raise AssertionError("provider should not be called")

    monkeypatch.setattr("shared.agent_reviews._call_review_model", should_not_call)
    with pytest.raises(PlatformJobFailure) as exc:
        await execute_agent_review_job(context, review_run.id)
    assert exc.value.code == "review_attempt_already_started"

    attempts, usages, findings = await _collect_created(db_session, review_cleanup, review_run.id)
    assert len(attempts) == 1
    assert len(usages) == 1
    assert findings == []


async def test_agent_review_executor_wrong_job_binding_writes_nothing(db_session, org1_user, review_cleanup):
    review_run, _source_run, _job, context = await _review_fixture(db_session, org1_user, review_cleanup)
    bad_context = PlatformJobContext(
        job_id=context.job_id,
        lease_token=context.lease_token,
        organization_id=uuid4(),
        requested_by_user_id=context.requested_by_user_id,
        requested_by_email=context.requested_by_email,
        requested_by_name=context.requested_by_name,
    )
    await db_session.commit()

    with pytest.raises(PlatformJobFailure) as exc:
        await execute_agent_review_job(bad_context, review_run.id)
    assert exc.value.code == "job_mismatch"

    attempts, usages, findings = await _collect_created(db_session, review_cleanup, review_run.id)
    assert attempts == []
    assert usages == []
    assert findings == []


async def test_agent_review_executor_tampered_request_fingerprint_writes_nothing_and_skips_provider(monkeypatch, db_session, org1_user, review_cleanup):
    review_run, _source_run, _job, context = await _review_fixture(db_session, org1_user, review_cleanup)
    review_run.request_fingerprint = "tampered"
    await db_session.commit()

    async def should_not_call(config, review_input):
        raise AssertionError("provider should not be called")

    monkeypatch.setattr("shared.agent_reviews._call_review_model", should_not_call)
    with pytest.raises(PlatformJobFailure) as exc:
        await execute_agent_review_job(context, review_run.id)
    assert exc.value.code == "job_mismatch"

    attempts, usages, findings = await _collect_created(db_session, review_cleanup, review_run.id)
    assert attempts == []
    assert usages == []
    assert findings == []


async def test_agent_review_executor_job_requester_binding_writes_nothing_and_skips_provider(monkeypatch, db_session, org1_user, review_cleanup):
    review_run, _source_run, job, context = await _review_fixture(db_session, org1_user, review_cleanup)
    job.requested_by_user_id = str(uuid4())
    await db_session.commit()

    async def should_not_call(config, review_input):
        raise AssertionError("provider should not be called")

    monkeypatch.setattr("shared.agent_reviews._call_review_model", should_not_call)
    with pytest.raises(PlatformJobFailure) as exc:
        await execute_agent_review_job(context, review_run.id)
    assert exc.value.code == "job_mismatch"

    attempts, usages, findings = await _collect_created(db_session, review_cleanup, review_run.id)
    assert attempts == []
    assert usages == []
    assert findings == []


async def test_agent_review_executor_input_byte_tamper_skips_provider(monkeypatch, db_session, org1_user, review_cleanup):
    review_run, _source_run, _job, context = await _review_fixture(db_session, org1_user, review_cleanup)
    review_run.input_bytes = review_run.input_bytes + 1
    await db_session.commit()

    async def should_not_call(config, review_input):
        raise AssertionError("provider should not be called")

    monkeypatch.setattr("shared.agent_reviews._call_review_model", should_not_call)
    with pytest.raises(PlatformJobFailure) as exc:
        await execute_agent_review_job(context, review_run.id)
    assert exc.value.code == "source_run_not_found"

    attempts, usages, findings = await _collect_created(db_session, review_cleanup, review_run.id)
    assert attempts == []
    assert usages == []
    assert findings == []


async def test_agent_review_executor_missing_nullable_source_ref_key_skips_provider(monkeypatch, db_session, org1_user, review_cleanup):
    review_run, _source_run, _job, context = await _review_fixture(db_session, org1_user, review_cleanup)
    ref = dict(review_run.source_refs[0])
    ref.pop("parent_run_id")
    review_run.source_refs = [ref]
    review_run.request_fingerprint = review_request_fingerprint(
        review_id=review_run.review_id,
        review_version_id=review_run.review_version_id,
        review_version=review_run.review_version,
        selected_run_ids=review_run.selected_run_ids,
        review_input=review_run.source_evidence,
        source_refs=review_run.source_refs,
        profile_fingerprint=review_run.profile_fingerprint,
    )
    await db_session.commit()

    async def should_not_call(config, review_input):
        raise AssertionError("provider should not be called")

    monkeypatch.setattr("shared.agent_reviews._call_review_model", should_not_call)
    with pytest.raises(PlatformJobFailure) as exc:
        await execute_agent_review_job(context, review_run.id)
    assert exc.value.code == "source_run_not_found"

    attempts, usages, findings = await _collect_created(db_session, review_cleanup, review_run.id)
    assert attempts == []
    assert usages == []
    assert findings == []


async def test_agent_review_executor_private_agent_revocation_skips_provider(monkeypatch, db_session, org1_user, org2_user, review_cleanup):
    review_run, _source_run, _job, context = await _review_fixture(db_session, org1_user, review_cleanup)
    agent = await db_session.get(Agent, review_run.agent_id)
    agent.access_level = AgentAccessLevel.PRIVATE
    agent.owner_user_id = org2_user.user_id
    await db_session.commit()

    async def should_not_call(config, review_input):
        raise AssertionError("provider should not be called")

    monkeypatch.setattr("shared.agent_reviews._call_review_model", should_not_call)
    with pytest.raises(PlatformJobFailure) as exc:
        await execute_agent_review_job(context, review_run.id)
    assert exc.value.code == "source_run_not_found"

    attempts, usages, findings = await _collect_created(db_session, review_cleanup, review_run.id)
    assert attempts == []
    assert usages == []
    assert findings == []


async def test_agent_review_executor_final_binding_rejection_preserves_usage(monkeypatch, db_session, org1_user, review_cleanup, async_session_factory):
    review_run, _source_run, _job, context = await _review_fixture(db_session, org1_user, review_cleanup)
    await db_session.commit()

    async def fake_call(config, review_input):
        async with async_session_factory() as other:
            row = await other.get(AgentReviewRun, review_run.id)
            row.profile_fingerprint = "tampered-after-call"
            await other.commit()
        return LLMResponse(content='{"summary":null,"findings":[]}', input_tokens=9, output_tokens=1), 5

    monkeypatch.setattr("shared.agent_reviews._call_review_model", fake_call)
    with pytest.raises(PlatformJobFailure) as exc:
        await execute_agent_review_job(context, review_run.id)
    assert exc.value.code == "job_mismatch"

    attempts, usages, findings = await _collect_created(db_session, review_cleanup, review_run.id)
    assert len(attempts) == 1
    assert len(usages) == 1
    assert findings == []


async def test_agent_review_executor_existing_extra_finding_conflicts_after_usage(monkeypatch, db_session, org1_user, review_cleanup):
    review_run, source_run, _job, context = await _review_fixture(db_session, org1_user, review_cleanup)
    existing = AgentFinding(
        agent_id=review_run.agent_id,
        org_id=review_run.org_id,
        status="dismissed",
        description="stale extra",
        expected_behavior=None,
        source_kind="run",
        source_run_id=source_run.id,
        finding_kind="problem",
        evidence_markdown=None,
        source_review_id=review_run.review_id,
        source_review_version_id=review_run.review_version_id,
        source_review_run_id=review_run.id,
        source_review_version=review_run.review_version,
        source_run_refs=[review_run.source_refs[0]],
        source_ordinal=99,
        created_by=review_run.requested_by_user_id,
    )
    db_session.add(existing)
    await db_session.flush()
    review_cleanup["finding_ids"].append(existing.id)
    await db_session.commit()

    async def fake_call(config, review_input):
        return LLMResponse(content='{"summary":null,"findings":[]}', input_tokens=4, output_tokens=2), 5

    monkeypatch.setattr("shared.agent_reviews._call_review_model", fake_call)
    with pytest.raises(PlatformJobFailure) as exc:
        await execute_agent_review_job(context, review_run.id)
    assert exc.value.code == "review_finding_conflict"

    attempts, usages, findings = await _collect_created(db_session, review_cleanup, review_run.id)
    assert len(attempts) == 1
    assert len(usages) == 1
    assert len(findings) == 1
    assert findings[0].status == "dismissed"
