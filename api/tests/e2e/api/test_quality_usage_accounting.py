from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from shared.quality_usage import begin_quality_usage_attempt, record_quality_usage_observation
from src.models.orm.ai_usage import AIUsage, AIUsageAttempt
from src.models.orm.platform_jobs import PlatformJob

pytestmark = pytest.mark.asyncio


async def test_platform_job_delete_preserves_quality_usage_provenance(db_session):
    job = PlatformJob(
        job_type="agent.evaluation_recorded_semantic",
        payload_version=1,
        payload={},
        requested_by_user_id=str(uuid4()),
        requested_by_email="reviewer@example.com",
        requested_by_name="Reviewer",
        title="Recorded semantic judge",
    )
    db_session.add(job)
    await db_session.flush()

    operation_id = uuid4()
    await begin_quality_usage_attempt(
        db_session,
        idempotency_key="e2e-quality-usage",
        quality_operation_type="recorded_evaluation",
        quality_operation_id=operation_id,
        usage_purpose="recorded_semantic_judge",
        provider="openai",
        model="gpt-4.1-mini",
        request_fingerprint="fingerprint",
        platform_job_id=job.id,
    )
    observed = await record_quality_usage_observation(
        db_session,
        idempotency_key="e2e-quality-usage",
        quality_operation_type="recorded_evaluation",
        quality_operation_id=operation_id,
        usage_purpose="recorded_semantic_judge",
        provider="openai",
        model="gpt-4.1-mini",
        request_fingerprint="fingerprint",
        input_tokens=3,
        output_tokens=2,
    )
    usage_id = observed.usage.id
    attempt_id = observed.attempt.id
    await db_session.flush()

    await db_session.delete(job)
    await db_session.flush()
    db_session.expire_all()

    attempt = (
        await db_session.execute(
            select(AIUsageAttempt)
            .where(AIUsageAttempt.id == attempt_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    usage = (
        await db_session.execute(
            select(AIUsage)
            .where(AIUsage.id == usage_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    assert attempt.platform_job_id is None
    assert usage.platform_job_id is None
    assert usage.usage_attempt_id == attempt.id
    assert usage.quality_operation_type == "recorded_evaluation"
