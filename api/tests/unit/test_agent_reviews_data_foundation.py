"""Agent review data-foundation database contracts."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from src.models.orm.agent_findings import AgentFinding
from src.models.orm.agent_reviews import (
    AgentReviewDefinition,
    AgentReviewRun,
    AgentReviewVersion,
)
from src.models.orm.ai_usage import AIUsage, AIUsageAttempt


def _review_definition(agent_id: UUID, **overrides) -> AgentReviewDefinition:
    data = dict(
        id=uuid4(),
        agent_id=agent_id,
        org_id=None,
        name="Safety review",
        status="active",
        latest_version=1,
    )
    data.update(overrides)
    return AgentReviewDefinition(**data)


def _review_version(review_id: UUID, **overrides) -> AgentReviewVersion:
    data = dict(
        id=uuid4(),
        review_id=review_id,
        version=1,
        review_statement="Find customer-impacting problems.",
        evidence_format_instructions="Use Markdown bullets.",
    )
    data.update(overrides)
    return AgentReviewVersion(**data)


def _review_run(
    *,
    review_id: UUID,
    review_version_id: UUID,
    agent_id: UUID,
    **overrides,
) -> AgentReviewRun:
    run_id = uuid4()
    data = dict(
        id=run_id,
        review_id=review_id,
        review_version_id=review_version_id,
        review_version=1,
        agent_id=agent_id,
        org_id=None,
        platform_job_id=None,
        requested_by_user_id=uuid4(),
        requested_run_ids=[uuid4()],
        selected_run_ids=[uuid4()],
        source_evidence={"runs": []},
        source_refs=[{"run_id": str(uuid4())}],
        profile_snapshot={"provider": "openai", "model": "gpt-test"},
        profile_fingerprint="profile-fingerprint",
        request_fingerprint="request-fingerprint",
        input_bytes=128,
        result_summary=None,
    )
    data.update(overrides)
    return AgentReviewRun(**data)


def _review_finding(
    *,
    agent_id: UUID,
    review_id: UUID,
    version_id: UUID,
    run_id: UUID,
    ordinal: int = 0,
    **overrides,
) -> AgentFinding:
    data = dict(
        id=uuid4(),
        agent_id=agent_id,
        org_id=None,
        status="open",
        description="The agent skipped a required confirmation.",
        expected_behavior="Ask for confirmation before acting.",
        source_kind="run",
        finding_kind="problem",
        evidence_markdown="- Evidence from selected runs",
        source_review_id=review_id,
        source_review_version_id=version_id,
        source_review_run_id=run_id,
        source_review_version=1,
        source_run_refs=[{"run_id": str(uuid4())}],
        source_ordinal=ordinal,
    )
    data.update(overrides)
    return AgentFinding(**data)


@pytest.mark.asyncio
async def test_review_version_number_is_unique_per_definition(db_session, seed_agent):
    definition = _review_definition(seed_agent.id)
    db_session.add(definition)
    await db_session.flush()

    db_session.add_all(
        [
            _review_version(definition.id),
            _review_version(definition.id, id=uuid4()),
        ]
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_review_run_allows_terminal_rerun_identity(db_session, seed_agent):
    definition = _review_definition(seed_agent.id)
    db_session.add(definition)
    await db_session.flush()
    version = _review_version(definition.id)
    db_session.add(version)
    await db_session.flush()

    requester_id = uuid4()
    requested_run_ids = [uuid4()]
    selected_run_ids = [uuid4()]
    first = _review_run(
        review_id=definition.id,
        review_version_id=version.id,
        agent_id=seed_agent.id,
        requested_by_user_id=requester_id,
        requested_run_ids=requested_run_ids,
        selected_run_ids=selected_run_ids,
        request_fingerprint="same-request",
    )
    second = _review_run(
        review_id=definition.id,
        review_version_id=version.id,
        agent_id=seed_agent.id,
        requested_by_user_id=requester_id,
        requested_run_ids=requested_run_ids,
        selected_run_ids=selected_run_ids,
        request_fingerprint="same-request",
    )

    db_session.add_all([first, second])
    await db_session.flush()

    rows = (
        (
            await db_session.execute(
                select(AgentReviewRun).where(
                    AgentReviewRun.request_fingerprint == "same-request"
                )
            )
        )
        .scalars()
        .all()
    )
    assert {row.id for row in rows} == {first.id, second.id}


@pytest.mark.asyncio
async def test_review_run_rejects_empty_selected_runs(db_session, seed_agent):
    definition = _review_definition(seed_agent.id)
    db_session.add(definition)
    await db_session.flush()
    version = _review_version(definition.id)
    db_session.add(version)
    await db_session.flush()

    db_session.add(
        _review_run(
            review_id=definition.id,
            review_version_id=version.id,
            agent_id=seed_agent.id,
            selected_run_ids=[],
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_review_generated_finding_ordinal_is_idempotent(db_session, seed_agent):
    definition = _review_definition(seed_agent.id)
    version = _review_version(definition.id)
    review_run = _review_run(
        review_id=definition.id,
        review_version_id=version.id,
        agent_id=seed_agent.id,
    )
    db_session.add(definition)
    await db_session.flush()
    db_session.add(version)
    await db_session.flush()
    db_session.add(review_run)
    await db_session.flush()

    db_session.add_all(
        [
            _review_finding(
                agent_id=seed_agent.id,
                review_id=definition.id,
                version_id=version.id,
                run_id=review_run.id,
                ordinal=0,
            ),
            _review_finding(
                agent_id=seed_agent.id,
                review_id=definition.id,
                version_id=version.id,
                run_id=review_run.id,
                ordinal=0,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_review_generated_finding_requires_complete_provenance(
    db_session, seed_agent
):
    db_session.add(
        AgentFinding(
            id=uuid4(),
            agent_id=seed_agent.id,
            status="open",
            description="Partial review provenance must not persist.",
            source_kind="run",
            finding_kind="problem",
            source_review_run_id=uuid4(),
            source_ordinal=0,
            source_run_refs=[{"run_id": str(uuid4())}],
        )
    )

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_review_delete_preserves_finding_provenance_snapshots(
    db_session, seed_agent
):
    definition = _review_definition(seed_agent.id)
    version = _review_version(definition.id)
    review_run = _review_run(
        review_id=definition.id,
        review_version_id=version.id,
        agent_id=seed_agent.id,
    )
    finding = _review_finding(
        agent_id=seed_agent.id,
        review_id=definition.id,
        version_id=version.id,
        run_id=review_run.id,
    )
    db_session.add(definition)
    await db_session.flush()
    db_session.add(version)
    await db_session.flush()
    db_session.add(review_run)
    await db_session.flush()
    db_session.add(finding)
    await db_session.flush()

    await db_session.execute(
        delete(AgentReviewDefinition).where(AgentReviewDefinition.id == definition.id)
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(AgentFinding).where(AgentFinding.id == finding.id)
        )
    ).scalar_one()
    assert row.source_review_id == definition.id
    assert row.source_review_version_id == version.id
    assert row.source_review_run_id == review_run.id


@pytest.mark.asyncio
async def test_agent_delete_cascades_review_domain_but_keeps_accounting_identity(
    db_session, seed_agent
):
    definition = _review_definition(seed_agent.id)
    version = _review_version(definition.id)
    review_run = _review_run(
        review_id=definition.id,
        review_version_id=version.id,
        agent_id=seed_agent.id,
    )
    finding = _review_finding(
        agent_id=seed_agent.id,
        review_id=definition.id,
        version_id=version.id,
        run_id=review_run.id,
    )
    attempt = AIUsageAttempt(
        id=uuid4(),
        idempotency_key=f"review-{review_run.id}",
        quality_operation_type="agent_review",
        quality_operation_id=review_run.id,
        usage_purpose="agent_review",
        provider="openai",
        model="gpt-test",
        request_fingerprint="request-fingerprint",
        state="observed",
        observed_at=datetime.now(timezone.utc),
    )
    usage = AIUsage(
        quality_operation_type="agent_review",
        quality_operation_id=review_run.id,
        usage_purpose="agent_review",
        usage_attempt_id=attempt.id,
        provider="openai",
        model="gpt-test",
        input_tokens=10,
        output_tokens=5,
        provider_cost=Decimal("0.00000100"),
        cost=Decimal("0.00000100"),
        timestamp=datetime.now(timezone.utc),
        sequence=1,
    )
    db_session.add(definition)
    await db_session.flush()
    db_session.add(version)
    await db_session.flush()
    db_session.add(review_run)
    await db_session.flush()
    db_session.add(finding)
    await db_session.flush()
    db_session.add(attempt)
    await db_session.flush()
    db_session.add(usage)
    await db_session.flush()

    await db_session.delete(seed_agent)
    await db_session.flush()

    assert (
        await db_session.execute(
            select(AgentReviewRun).where(AgentReviewRun.id == review_run.id)
        )
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(
            select(AgentFinding).where(AgentFinding.id == finding.id)
        )
    ).scalar_one_or_none() is None

    usage_row = (
        await db_session.execute(
            select(AIUsage).where(AIUsage.usage_attempt_id == attempt.id)
        )
    ).scalar_one()
    attempt_row = (
        await db_session.execute(
            select(AIUsageAttempt).where(AIUsageAttempt.id == attempt.id)
        )
    ).scalar_one()
    assert usage_row.quality_operation_id == review_run.id
    assert attempt_row.quality_operation_id == review_run.id
