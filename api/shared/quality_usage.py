"""Shared accounting foundation for quality-operation LLM usage.

These helpers record accounting provenance only. They do not authorize work,
call providers, manage jobs, emit progress, or update domain verdicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import (
    QualityUsageOperationType,
    QualityUsagePurpose,
    QualityUsageUnobservedReason,
)
from src.models.orm.ai_usage import AIModelPricing, AIUsage, AIUsageAttempt
from src.services.ai_usage_service import calculate_cost
from src.services.model_pricing import canonical_provider

QUALITY_OPERATION_TYPES: set[str] = {
    "recorded_evaluation",
    "synthetic_evaluation",
    "agent_review",
    "quality_designer",
}
QUALITY_USAGE_PURPOSES: set[str] = {
    "recorded_semantic_judge",
    "synthetic_semantic_judge",
    "agent_review",
    "test_designer",
}
DB_COST_QUANTUM = Decimal("0.00000001")

UNOBSERVED_REASONS: set[str] = {
    "provider_error",
    "missing_usage",
    "ambiguous_after_call",
    "cancelled_before_call",
    "runner_lost",
}


class QualityUsageError(ValueError):
    """Base class for strict quality usage validation failures."""


class QualityUsageConflictError(QualityUsageError):
    """Raised when an idempotency key is reused with different facts."""


@dataclass(frozen=True)
class QualityUsageAttemptResult:
    attempt: AIUsageAttempt
    created: bool


@dataclass(frozen=True)
class QualityUsageObservationResult:
    attempt: AIUsageAttempt
    usage: AIUsage
    created: bool


@dataclass(frozen=True)
class QualityUsageUnobservedResult:
    attempt: AIUsageAttempt
    updated: bool


async def begin_quality_usage_attempt(
    session: AsyncSession,
    *,
    idempotency_key: str,
    quality_operation_type: QualityUsageOperationType,
    quality_operation_id: UUID,
    usage_purpose: QualityUsagePurpose,
    provider: str,
    model: str,
    request_fingerprint: str,
    quality_operation_item_id: str | None = None,
    organization_id: UUID | None = None,
    user_id: UUID | None = None,
    platform_job_id: UUID | None = None,
    profile_id: UUID | None = None,
    profile_name: str | None = None,
    profile_fingerprint: str | None = None,
) -> QualityUsageAttemptResult:
    """Insert or return the immutable pre-call accounting marker.

    The caller must already hold the relevant domain/job fence and must commit
    before dispatching a provider call. Duplicate matching input returns the
    existing marker and ``created=False``.
    """

    provider = _normalize_provider(provider)
    values = {
        "idempotency_key": _bounded("idempotency_key", idempotency_key, 255),
        "quality_operation_type": _quality_operation_type(quality_operation_type),
        "quality_operation_id": quality_operation_id,
        "quality_operation_item_id": _optional_bounded(
            "quality_operation_item_id", quality_operation_item_id, 255
        ),
        "usage_purpose": _usage_purpose(usage_purpose),
        "organization_id": organization_id,
        "user_id": user_id,
        "platform_job_id": platform_job_id,
        "profile_id": profile_id,
        "profile_name": _optional_bounded("profile_name", profile_name, 255),
        "profile_fingerprint": _optional_bounded(
            "profile_fingerprint", profile_fingerprint, 128
        ),
        "provider": provider,
        "model": _bounded("model", model, 100),
        "request_fingerprint": _bounded("request_fingerprint", request_fingerprint, 128),
        "state": "started",
    }
    statement = (
        insert(AIUsageAttempt)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(AIUsageAttempt.id)
    )
    inserted_id = (await session.execute(statement)).scalar_one_or_none()
    if inserted_id is not None:
        inserted = (
            await session.execute(
                select(AIUsageAttempt).where(AIUsageAttempt.id == inserted_id)
            )
        ).scalar_one()
        return QualityUsageAttemptResult(attempt=inserted, created=True)

    existing = await _attempt_by_key(session, values["idempotency_key"], for_update=True)
    if existing is None:
        raise QualityUsageConflictError("quality usage attempt conflict was not readable")
    _assert_attempt_matches(existing, values)
    return QualityUsageAttemptResult(attempt=existing, created=False)


async def record_quality_usage_observation(
    session: AsyncSession,
    *,
    idempotency_key: str,
    quality_operation_type: QualityUsageOperationType,
    quality_operation_id: UUID,
    usage_purpose: QualityUsagePurpose,
    provider: str,
    model: str,
    request_fingerprint: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    provider_cost: Decimal | str | int | None = None,
    duration_ms: int | None = None,
    quality_operation_item_id: str | None = None,
) -> QualityUsageObservationResult:
    """Persist observed usage for a committed matching attempt.

    This may run after domain cancellation or lease loss. It records only the
    already-observed cost and does not authorize a domain result write.
    """

    identity = {
        "quality_operation_type": _quality_operation_type(quality_operation_type),
        "quality_operation_id": quality_operation_id,
        "quality_operation_item_id": _optional_bounded(
            "quality_operation_item_id", quality_operation_item_id, 255
        ),
        "usage_purpose": _usage_purpose(usage_purpose),
        "provider": _normalize_provider(provider),
        "model": _bounded("model", model, 100),
        "request_fingerprint": _bounded("request_fingerprint", request_fingerprint, 128),
    }
    attempt = await _attempt_by_key(session, _bounded("idempotency_key", idempotency_key, 255), for_update=True)
    if attempt is None:
        raise QualityUsageError("quality usage attempt does not exist")
    _assert_attempt_matches(attempt, identity)

    input_count = _nonnegative_int("input_tokens", input_tokens)
    output_count = _nonnegative_int("output_tokens", output_tokens)
    cache_read_count = _nonnegative_int("cache_read_tokens", cache_read_tokens)
    cache_write_count = _nonnegative_int("cache_write_tokens", cache_write_tokens)
    duration = _optional_nonnegative_int("duration_ms", duration_ms)
    observed_provider_cost = _optional_nonnegative_decimal("provider_cost", provider_cost)

    existing = await _usage_for_attempt(session, attempt.id)
    if existing is not None:
        _assert_usage_matches(
            existing,
            attempt,
            input_count,
            output_count,
            cache_read_count,
            cache_write_count,
            observed_provider_cost,
            duration,
        )
        return QualityUsageObservationResult(
            attempt=attempt,
            usage=existing,
            created=False,
        )

    cost = observed_provider_cost
    if cost is None:
        cost = await _estimate_complete_cost(
            session,
            provider=attempt.provider,
            model=attempt.model,
            input_tokens=input_count,
            output_tokens=output_count,
            cache_read_tokens=cache_read_count,
            cache_write_tokens=cache_write_count,
        )

    if cost is not None:
        cost = cost.quantize(DB_COST_QUANTUM, rounding=ROUND_HALF_UP)

    usage = AIUsage(
        provider=attempt.provider,
        model=attempt.model,
        input_tokens=input_count,
        output_tokens=output_count,
        cache_read_tokens=cache_read_count,
        cache_write_tokens=cache_write_count,
        provider_cost=observed_provider_cost,
        cost=cost,
        duration_ms=duration,
        organization_id=attempt.organization_id,
        user_id=attempt.user_id,
        quality_operation_type=attempt.quality_operation_type,
        quality_operation_id=attempt.quality_operation_id,
        quality_operation_item_id=attempt.quality_operation_item_id,
        usage_purpose=attempt.usage_purpose,
        profile_id=attempt.profile_id,
        profile_name=attempt.profile_name,
        profile_fingerprint=attempt.profile_fingerprint,
        platform_job_id=attempt.platform_job_id,
        usage_attempt_id=attempt.id,
        timestamp=attempt.started_at,
    )
    session.add(usage)
    attempt.state = "observed"
    attempt.unobserved_reason = None
    attempt.observed_at = datetime.now(timezone.utc)
    await session.flush()
    return QualityUsageObservationResult(attempt=attempt, usage=usage, created=True)


async def mark_quality_usage_unobserved(
    session: AsyncSession,
    *,
    idempotency_key: str,
    reason: QualityUsageUnobservedReason,
) -> QualityUsageUnobservedResult:
    """Mark an attempt as a coverage gap without inserting zero usage."""

    reason_value = _unobserved_reason(reason)
    attempt = await _attempt_by_key(session, _bounded("idempotency_key", idempotency_key, 255), for_update=True)
    if attempt is None:
        raise QualityUsageError("quality usage attempt does not exist")
    if attempt.state == "observed":
        return QualityUsageUnobservedResult(attempt=attempt, updated=False)
    updated = attempt.state != "unobserved" or attempt.unobserved_reason != reason_value
    if updated:
        attempt.state = "unobserved"
        attempt.unobserved_reason = reason_value
        await session.flush()
    return QualityUsageUnobservedResult(attempt=attempt, updated=updated)


async def _attempt_by_key(
    session: AsyncSession,
    idempotency_key: str,
    *,
    for_update: bool,
) -> AIUsageAttempt | None:
    query = select(AIUsageAttempt).where(AIUsageAttempt.idempotency_key == idempotency_key)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(query)).scalar_one_or_none()


async def _usage_for_attempt(session: AsyncSession, attempt_id: UUID) -> AIUsage | None:
    return (
        await session.execute(
            select(AIUsage).where(AIUsage.usage_attempt_id == attempt_id)
        )
    ).scalar_one_or_none()


async def _estimate_complete_cost(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> Decimal | None:
    pricing = (
        await session.execute(
            select(AIModelPricing).where(
                AIModelPricing.provider == provider,
                AIModelPricing.model == model,
            )
        )
    ).scalar_one_or_none()
    if pricing is None:
        return None
    if pricing.input_price_per_million is None or pricing.output_price_per_million is None:
        return None
    if cache_read_tokens and pricing.cache_read_price_per_million is None:
        return None
    if cache_write_tokens and pricing.cache_write_price_per_million is None:
        return None
    return calculate_cost(
        input_tokens,
        output_tokens,
        pricing.input_price_per_million,
        pricing.output_price_per_million,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_read_price_per_million=pricing.cache_read_price_per_million,
        cache_write_price_per_million=pricing.cache_write_price_per_million,
    )


def _assert_attempt_matches(attempt: AIUsageAttempt, expected: dict[str, object]) -> None:
    for field, expected_value in expected.items():
        if field == "state":
            continue
        actual = getattr(attempt, field)
        if actual != expected_value:
            raise QualityUsageConflictError(
                f"quality usage attempt idempotency conflict on {field}"
            )


def _assert_usage_matches(
    usage: AIUsage,
    attempt: AIUsageAttempt,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    provider_cost: Decimal | None,
    duration_ms: int | None,
) -> None:
    expected = {
        "provider": attempt.provider,
        "model": attempt.model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "provider_cost": provider_cost,
        "duration_ms": duration_ms,
        "quality_operation_type": attempt.quality_operation_type,
        "quality_operation_id": attempt.quality_operation_id,
        "quality_operation_item_id": attempt.quality_operation_item_id,
        "usage_purpose": attempt.usage_purpose,
    }
    for field, expected_value in expected.items():
        if getattr(usage, field) != expected_value:
            raise QualityUsageConflictError(
                f"quality usage observation conflict on {field}"
            )


def _normalize_provider(provider: str) -> str:
    return _bounded("provider", canonical_provider(provider), 50)


def _quality_operation_type(value: QualityUsageOperationType | str) -> str:
    return _literal("quality_operation_type", value, QUALITY_OPERATION_TYPES, 64)


def _usage_purpose(value: QualityUsagePurpose | str) -> str:
    return _literal("usage_purpose", value, QUALITY_USAGE_PURPOSES, 64)


def _unobserved_reason(value: QualityUsageUnobservedReason | str) -> str:
    return _literal("unobserved_reason", value, UNOBSERVED_REASONS, 64)


def _literal(
    field: str,
    value: str,
    allowed: set[str],
    max_length: int,
) -> str:
    value = _bounded(field, value, max_length)
    if value not in allowed:
        raise QualityUsageError(f"invalid {field}")
    return value


def _bounded(field: str, value: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise QualityUsageError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise QualityUsageError(f"{field} is required")
    if len(normalized) > max_length:
        raise QualityUsageError(f"{field} is too long")
    return normalized


def _optional_bounded(field: str, value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    return _bounded(field, value, max_length)


def _nonnegative_int(field: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise QualityUsageError(f"{field} must be an integer")
    if value < 0:
        raise QualityUsageError(f"{field} must be nonnegative")
    return value


def _optional_nonnegative_int(field: str, value: int | None) -> int | None:
    if value is None:
        return None
    return _nonnegative_int(field, value)


def _optional_nonnegative_decimal(
    field: str,
    value: Decimal | str | int | None,
) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise QualityUsageError(f"{field} must be a decimal")
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise QualityUsageError(f"{field} must be a decimal") from exc
    if not decimal_value.is_finite() or decimal_value < 0:
        raise QualityUsageError(f"{field} must be finite and nonnegative")
    return decimal_value.quantize(DB_COST_QUANTUM, rounding=ROUND_HALF_UP)
