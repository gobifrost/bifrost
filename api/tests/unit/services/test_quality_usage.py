from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from shared.quality_usage import (
    QualityUsageConflictError,
    QualityUsageError,
    begin_quality_usage_attempt,
    mark_quality_usage_unobserved,
    record_quality_usage_observation,
)
from src.models.orm.agent_runs import AgentRun
from src.models.orm.ai_usage import AIModelPricing, AIUsage, AIUsageAttempt
from src.models.orm.organizations import Organization
from src.models.orm.platform_jobs import PlatformJob

pytestmark = pytest.mark.asyncio


BASE_ATTEMPT = {
    "quality_operation_type": "recorded_evaluation",
    "usage_purpose": "recorded_semantic_judge",
    "provider": "openai",
    "model": "gpt-4.1-mini",
    "request_fingerprint": "req-fingerprint",
    "profile_name": "Recorded judge",
    "profile_fingerprint": "profile-fingerprint",
}


async def _seed_agent_run(db_session, seed_agent):
    run_id = uuid4()
    run = AgentRun(
        id=run_id,
        agent_id=seed_agent.id,
        trigger_type="api",
        status="completed",
        root_run_id=run_id,
    )
    db_session.add(run)
    await db_session.flush()
    return run


async def _cleanup_committed_attempts(async_session_factory, *keys: str) -> None:
    async with async_session_factory() as session:
        attempt_ids = (
            await session.execute(
                select(AIUsageAttempt.id).where(AIUsageAttempt.idempotency_key.in_(keys))
            )
        ).scalars().all()
        if attempt_ids:
            await session.execute(
                delete(AIUsage).where(AIUsage.usage_attempt_id.in_(attempt_ids))
            )
            await session.execute(
                delete(AIUsageAttempt).where(AIUsageAttempt.id.in_(attempt_ids))
            )
            await session.commit()


async def _begin(session, *, key: str = "attempt-key", operation_id=None, **overrides):
    values = {
        **BASE_ATTEMPT,
        "idempotency_key": key,
        "quality_operation_id": operation_id or uuid4(),
    }
    values.update(overrides)
    return await begin_quality_usage_attempt(session, **values)


async def test_begin_attempt_is_idempotent_for_matching_input(db_session):
    operation_id = uuid4()
    first = await _begin(db_session, operation_id=operation_id)
    second = await _begin(db_session, operation_id=operation_id)

    assert first.created is True
    assert second.created is False
    assert second.attempt.id == first.attempt.id


async def test_begin_attempt_rejects_same_key_with_changed_identity(db_session):
    await _begin(db_session, operation_id=uuid4())

    with pytest.raises(QualityUsageConflictError):
        await _begin(db_session, operation_id=uuid4())


async def test_concurrent_begin_same_key_creates_one_attempt(async_session_factory):
    operation_id = uuid4()

    async def worker():
        async with async_session_factory() as session:
            result = await _begin(
                session,
                key="concurrent-begin",
                operation_id=operation_id,
            )
            await session.commit()
            return result.created, result.attempt.id

    try:
        results = await asyncio.gather(worker(), worker())

        assert sorted(created for created, _ in results) == [False, True]
        assert len({attempt_id for _, attempt_id in results}) == 1
    finally:
        await _cleanup_committed_attempts(async_session_factory, "concurrent-begin")


async def test_observation_records_provider_cost_and_duration(db_session):
    operation_id = uuid4()
    await _begin(db_session, operation_id=operation_id)

    result = await record_quality_usage_observation(
        db_session,
        idempotency_key="attempt-key",
        quality_operation_type="recorded_evaluation",
        quality_operation_id=operation_id,
        usage_purpose="recorded_semantic_judge",
        provider="openai",
        model="gpt-4.1-mini",
        request_fingerprint="req-fingerprint",
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=2,
        cache_write_tokens=1,
        provider_cost=Decimal("0.000000015"),
        duration_ms=123,
    )

    assert result.created is True
    assert result.attempt.state == "observed"
    assert result.usage.provider_cost == Decimal("0.00000002")
    assert result.usage.cost == Decimal("0.00000002")
    assert result.usage.duration_ms == 123
    assert result.usage.agent_run_id is None
    assert result.usage.execution_id is None
    assert result.usage.conversation_id is None


async def test_repeated_observation_matches_rounded_persisted_provider_cost(
    async_session_factory,
):
    operation_id = uuid4()
    try:
        async with async_session_factory() as session:
            await _begin(session, key="rounded-cost", operation_id=operation_id)
            await record_quality_usage_observation(
                session,
                idempotency_key="rounded-cost",
                quality_operation_type="recorded_evaluation",
                quality_operation_id=operation_id,
                usage_purpose="recorded_semantic_judge",
                provider="openai",
                model="gpt-4.1-mini",
                request_fingerprint="req-fingerprint",
                input_tokens=10,
                output_tokens=5,
                provider_cost=Decimal("0.000000015"),
            )
            await session.commit()

        async with async_session_factory() as session:
            repeated = await record_quality_usage_observation(
                session,
                idempotency_key="rounded-cost",
                quality_operation_type="recorded_evaluation",
                quality_operation_id=operation_id,
                usage_purpose="recorded_semantic_judge",
                provider="openai",
                model="gpt-4.1-mini",
                request_fingerprint="req-fingerprint",
                input_tokens=10,
                output_tokens=5,
                provider_cost=Decimal("0.000000015"),
            )

        assert repeated.created is False
        assert repeated.usage.provider_cost == Decimal("0.00000002")
    finally:
        await _cleanup_committed_attempts(async_session_factory, "rounded-cost")


async def test_observation_copies_attempt_provenance_fields(db_session, seed_user):
    org = Organization(
        id=uuid4(),
        name="Quality Usage Org",
        created_by="tester@example.com",
    )
    job = PlatformJob(
        job_type="agent.evaluation_recorded_semantic",
        payload_version=1,
        payload={},
        organization_id=org.id,
        requested_by_user_id=str(seed_user.id),
        requested_by_email=seed_user.email,
        requested_by_name=seed_user.name or seed_user.email,
        title="Recorded semantic judge",
    )
    db_session.add_all([org, job])
    await db_session.flush()

    operation_id = uuid4()
    item_id = "case:run:assertion"
    profile_id = uuid4()
    await _begin(
        db_session,
        key="copy-provenance",
        operation_id=operation_id,
        quality_operation_item_id=item_id,
        organization_id=org.id,
        user_id=seed_user.id,
        platform_job_id=job.id,
        profile_id=profile_id,
        profile_name="Recorded Judge Profile",
        profile_fingerprint="profile-fp",
    )

    result = await record_quality_usage_observation(
        db_session,
        idempotency_key="copy-provenance",
        quality_operation_type="recorded_evaluation",
        quality_operation_id=operation_id,
        quality_operation_item_id=item_id,
        usage_purpose="recorded_semantic_judge",
        provider="openai",
        model="gpt-4.1-mini",
        request_fingerprint="req-fingerprint",
        input_tokens=4,
        output_tokens=2,
        duration_ms=77,
    )

    usage = result.usage
    assert usage.organization_id == org.id
    assert usage.user_id == seed_user.id
    assert usage.platform_job_id == job.id
    assert usage.profile_id == profile_id
    assert usage.profile_name == "Recorded Judge Profile"
    assert usage.profile_fingerprint == "profile-fp"
    assert usage.quality_operation_type == "recorded_evaluation"
    assert usage.quality_operation_id == operation_id
    assert usage.quality_operation_item_id == item_id
    assert usage.usage_purpose == "recorded_semantic_judge"
    assert usage.duration_ms == 77
    assert usage.timestamp == result.attempt.started_at
    assert result.attempt.observed_at is not None


async def test_duplicate_observation_conflict_rejects_without_adding_cost(db_session):
    operation_id = uuid4()
    await _begin(db_session, operation_id=operation_id)
    await record_quality_usage_observation(
        db_session,
        idempotency_key="attempt-key",
        quality_operation_type="recorded_evaluation",
        quality_operation_id=operation_id,
        usage_purpose="recorded_semantic_judge",
        provider="openai",
        model="gpt-4.1-mini",
        request_fingerprint="req-fingerprint",
        input_tokens=10,
        output_tokens=5,
    )

    with pytest.raises(QualityUsageConflictError):
        await record_quality_usage_observation(
            db_session,
            idempotency_key="attempt-key",
            quality_operation_type="recorded_evaluation",
            quality_operation_id=operation_id,
            usage_purpose="recorded_semantic_judge",
            provider="openai",
            model="gpt-4.1-mini",
            request_fingerprint="req-fingerprint",
            input_tokens=11,
            output_tokens=5,
        )

    count = (
        await db_session.execute(
            select(func.count(AIUsage.id)).where(
                AIUsage.quality_operation_id == operation_id
            )
        )
    ).scalar_one()
    assert count == 1


async def test_unobserved_gap_never_inserts_zero_usage_and_can_later_settle(db_session):
    operation_id = uuid4()
    await _begin(db_session, operation_id=operation_id)

    gap = await mark_quality_usage_unobserved(
        db_session,
        idempotency_key="attempt-key",
        reason="missing_usage",
    )
    assert gap.updated is True
    assert gap.attempt.state == "unobserved"
    assert (
        await db_session.execute(
            select(func.count(AIUsage.id)).where(AIUsage.quality_operation_id == operation_id)
        )
    ).scalar_one() == 0

    observed = await record_quality_usage_observation(
        db_session,
        idempotency_key="attempt-key",
        quality_operation_type="recorded_evaluation",
        quality_operation_id=operation_id,
        usage_purpose="recorded_semantic_judge",
        provider="openai",
        model="gpt-4.1-mini",
        request_fingerprint="req-fingerprint",
        input_tokens=1,
        output_tokens=1,
    )
    assert observed.created is True
    assert observed.attempt.state == "observed"
    assert observed.attempt.unobserved_reason is None


async def test_observed_attempt_cannot_be_downgraded_from_stale_identity_map(
    async_session_factory: async_sessionmaker,
):
    operation_id = uuid4()
    try:
        async with async_session_factory() as setup_session:
            created = await _begin(
                setup_session,
                key="stale-downgrade",
                operation_id=operation_id,
            )
            attempt_id = created.attempt.id
            await setup_session.commit()

        async with async_session_factory() as stale_session:
            stale_attempt = await stale_session.get(AIUsageAttempt, attempt_id)
            assert stale_attempt is not None
            assert stale_attempt.state == "started"

            async with async_session_factory() as observer_session:
                await record_quality_usage_observation(
                    observer_session,
                    idempotency_key="stale-downgrade",
                    quality_operation_type="recorded_evaluation",
                    quality_operation_id=operation_id,
                    usage_purpose="recorded_semantic_judge",
                    provider="openai",
                    model="gpt-4.1-mini",
                    request_fingerprint="req-fingerprint",
                    input_tokens=1,
                    output_tokens=1,
                )
                await observer_session.commit()

            result = await mark_quality_usage_unobserved(
                stale_session,
                idempotency_key="stale-downgrade",
                reason="runner_lost",
            )
            assert result.updated is False
            assert result.attempt.state == "observed"
            await stale_session.rollback()
    finally:
        await _cleanup_committed_attempts(async_session_factory, "stale-downgrade")


async def test_concurrent_observation_writes_one_usage_row(async_session_factory):
    operation_id = uuid4()
    try:
        async with async_session_factory() as setup_session:
            await _begin(setup_session, key="concurrent-observe", operation_id=operation_id)
            await setup_session.commit()

        async def worker():
            async with async_session_factory() as session:
                result = await record_quality_usage_observation(
                    session,
                    idempotency_key="concurrent-observe",
                    quality_operation_type="recorded_evaluation",
                    quality_operation_id=operation_id,
                    usage_purpose="recorded_semantic_judge",
                    provider="openai",
                    model="gpt-4.1-mini",
                    request_fingerprint="req-fingerprint",
                    input_tokens=10,
                    output_tokens=5,
                )
                await session.commit()
                return result.created, result.usage.id

        results = await asyncio.gather(worker(), worker())

        assert sorted(created for created, _ in results) == [False, True]
        assert len({usage_id for _, usage_id in results}) == 1
    finally:
        await _cleanup_committed_attempts(async_session_factory, "concurrent-observe")


async def test_invalid_tokens_duration_and_cost_reject(db_session):
    operation_id = uuid4()
    await _begin(db_session, operation_id=operation_id)

    with pytest.raises(QualityUsageError):
        await record_quality_usage_observation(
            db_session,
            idempotency_key="attempt-key",
            quality_operation_type="recorded_evaluation",
            quality_operation_id=operation_id,
            usage_purpose="recorded_semantic_judge",
            provider="openai",
            model="gpt-4.1-mini",
            request_fingerprint="req-fingerprint",
            input_tokens=True,
            output_tokens=1,
        )
    with pytest.raises(QualityUsageError):
        await record_quality_usage_observation(
            db_session,
            idempotency_key="attempt-key",
            quality_operation_type="recorded_evaluation",
            quality_operation_id=operation_id,
            usage_purpose="recorded_semantic_judge",
            provider="openai",
            model="gpt-4.1-mini",
            request_fingerprint="req-fingerprint",
            input_tokens=1,
            output_tokens=1,
            duration_ms=-1,
        )
    with pytest.raises(QualityUsageError):
        await record_quality_usage_observation(
            db_session,
            idempotency_key="attempt-key",
            quality_operation_type="recorded_evaluation",
            quality_operation_id=operation_id,
            usage_purpose="recorded_semantic_judge",
            provider="openai",
            model="gpt-4.1-mini",
            request_fingerprint="req-fingerprint",
            input_tokens=1,
            output_tokens=1,
            provider_cost=Decimal("NaN"),
        )


async def test_local_pricing_estimates_only_when_complete(db_session):
    db_session.add(
        AIModelPricing(
            provider="openai",
            model="priced-model",
            input_price_per_million=Decimal("2"),
            output_price_per_million=Decimal("6"),
            cache_read_price_per_million=Decimal("1"),
            cache_write_price_per_million=Decimal("4"),
        )
    )
    db_session.add(
        AIModelPricing(
            provider="openai",
            model="partial-cache-model",
            input_price_per_million=Decimal("2"),
            output_price_per_million=Decimal("6"),
            cache_read_price_per_million=None,
            cache_write_price_per_million=None,
        )
    )
    await db_session.flush()

    priced_operation = uuid4()
    await _begin(
        db_session,
        key="priced",
        operation_id=priced_operation,
        model="priced-model",
    )
    priced = await record_quality_usage_observation(
        db_session,
        idempotency_key="priced",
        quality_operation_type="recorded_evaluation",
        quality_operation_id=priced_operation,
        usage_purpose="recorded_semantic_judge",
        provider="openai",
        model="priced-model",
        request_fingerprint="req-fingerprint",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=100,
        cache_write_tokens=50,
    )
    assert priced.usage.cost == Decimal("0.00500000")

    partial_operation = uuid4()
    await _begin(
        db_session,
        key="partial-cache",
        operation_id=partial_operation,
        model="partial-cache-model",
    )
    partial = await record_quality_usage_observation(
        db_session,
        idempotency_key="partial-cache",
        quality_operation_type="recorded_evaluation",
        quality_operation_id=partial_operation,
        usage_purpose="recorded_semantic_judge",
        provider="openai",
        model="partial-cache-model",
        request_fingerprint="req-fingerprint",
        input_tokens=1000,
        output_tokens=500,
        cache_read_tokens=1,
    )
    assert partial.usage.cost is None


async def test_empty_and_half_context_ai_usage_rows_fail(db_session):
    db_session.add(
        AIUsage(
            provider="openai",
            model="gpt-4.1-mini",
            input_tokens=1,
            output_tokens=1,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()

    db_session.add(
        AIUsage(
            provider="openai",
            model="gpt-4.1-mini",
            input_tokens=1,
            output_tokens=1,
            quality_operation_type="recorded_evaluation",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_quality_operation_requires_usage_purpose(db_session):
    db_session.add(
        AIUsage(
            provider="openai",
            model="gpt-4.1-mini",
            input_tokens=1,
            output_tokens=1,
            quality_operation_type="recorded_evaluation",
            quality_operation_id=uuid4(),
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_usage_purpose_cannot_attach_to_runtime_context(
    db_session, seed_agent
):
    run = await _seed_agent_run(db_session, seed_agent)
    db_session.add(
        AIUsage(
            provider="openai",
            model="gpt-4.1-mini",
            input_tokens=1,
            output_tokens=1,
            agent_run_id=run.id,
        )
    )
    await db_session.flush()

    db_session.add(
        AIUsage(
            provider="openai",
            model="gpt-4.1-mini",
            input_tokens=1,
            output_tokens=1,
            agent_run_id=run.id,
            usage_purpose="recorded_semantic_judge",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_usage_attempt_cannot_attach_to_runtime_context(
    db_session, seed_agent
):
    run = await _seed_agent_run(db_session, seed_agent)
    attempt = AIUsageAttempt(
        idempotency_key="constraint-attempt",
        quality_operation_type="recorded_evaluation",
        quality_operation_id=uuid4(),
        usage_purpose="recorded_semantic_judge",
        provider="openai",
        model="gpt-4.1-mini",
        request_fingerprint="fingerprint",
    )
    db_session.add(attempt)
    db_session.add(
        AIUsage(
            provider="openai",
            model="gpt-4.1-mini",
            input_tokens=1,
            output_tokens=1,
            agent_run_id=run.id,
        )
    )
    await db_session.flush()

    db_session.add(
        AIUsage(
            provider="openai",
            model="gpt-4.1-mini",
            input_tokens=1,
            output_tokens=1,
            agent_run_id=run.id,
            usage_attempt_id=attempt.id,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_unobserved_attempt_requires_reason(db_session):
    db_session.add(
        AIUsageAttempt(
            idempotency_key="bad-unobserved",
            quality_operation_type="recorded_evaluation",
            quality_operation_id=uuid4(),
            usage_purpose="recorded_semantic_judge",
            provider="openai",
            model="gpt-4.1-mini",
            request_fingerprint="fingerprint",
            state="unobserved",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
