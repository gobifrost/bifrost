"""CRUD service for recurring PlatformJob triggers.

Organization is derived from the operation target (review/suite) and
immutable; the requester is always the caller. All definition validation
goes through the shared registry. Callers own commit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import NoReturn
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models import (
    RecurringTriggerCreate,
    RecurringTriggerPage,
    RecurringTriggerPublic,
    RecurringTriggerUpdate,
    TriggerFirePage,
    TriggerFirePublic,
)
from shared.recurring_trigger_registry import (
    OPERATION_AGENT_EVALUATION_SUITE,
    OPERATION_AGENT_REVIEW,
    TriggerDefinitionError,
    validate_trigger_definition,
)
from src.core.principal import UserPrincipal
from src.models.orm.agent_evaluations import AgentEvaluationSuite
from src.models.orm.agent_reviews import AgentReviewDefinition
from src.models.orm.recurring_triggers import (
    RecurringPlatformJobTrigger,
    RecurringTriggerFire,
)


class TriggerServiceError(Exception):
    def __init__(
        self,
        code: str,
        public_detail: str = "Schedule not found.",
        http_status: int = 404,
    ) -> None:
        super().__init__(public_detail)
        self.code = code
        self.public_detail = public_detail
        self.http_status = http_status


def _service_error(code: str, detail: str, http_status: int) -> NoReturn:
    raise TriggerServiceError(code, public_detail=detail, http_status=http_status)


def _public(trigger: RecurringPlatformJobTrigger) -> RecurringTriggerPublic:
    return RecurringTriggerPublic.model_validate(trigger)


async def _target_org_id(
    db: AsyncSession, *, user: UserPrincipal, operation_type: str, operation_id: UUID
) -> UUID | None:
    """Resolve the trigger org from its operation target (tenant-isolated)."""
    if operation_type == OPERATION_AGENT_REVIEW:
        review = await db.get(AgentReviewDefinition, operation_id)
        if review is None:
            _service_error("target_not_found", "Review not found.", 404)
        if not user.is_superuser and review.org_id != user.organization_id:
            _service_error("target_not_found", "Review not found.", 404)
        return review.org_id
    if operation_type == OPERATION_AGENT_EVALUATION_SUITE:
        suite = await db.get(AgentEvaluationSuite, operation_id)
        if suite is None:
            _service_error("target_not_found", "Suite not found.", 404)
        if not user.is_superuser and suite.org_id != user.organization_id:
            _service_error("target_not_found", "Suite not found.", 404)
        return suite.org_id
    _service_error("unknown_operation", f"Unknown operation_type: {operation_type}.", 422)


async def _validated_params(
    *,
    operation_type: str,
    operation_id: UUID,
    operation_params: dict,
    cron_expression: str,
    timezone_name: str,
    overlap_policy: str,
) -> dict:
    try:
        params = validate_trigger_definition(
            operation_type=operation_type,
            operation_params=operation_params,
            cron_expression=cron_expression,
            timezone_name=timezone_name,
            overlap_policy=overlap_policy,
        )
    except TriggerDefinitionError as exc:
        _service_error("invalid_definition", exc.detail, 422)
    embedded = params.get("review_id", params.get("suite_id"))
    if embedded is None or UUID(str(embedded)) != operation_id:
        _service_error(
            "identity_mismatch",
            "operation_params must reference operation_id.",
            422,
        )
    return params


async def create_trigger(
    db: AsyncSession, *, user: UserPrincipal, body: RecurringTriggerCreate
) -> RecurringTriggerPublic:
    org_id = await _target_org_id(
        db, user=user, operation_type=body.operation_type, operation_id=body.operation_id
    )
    params = await _validated_params(
        operation_type=body.operation_type,
        operation_id=body.operation_id,
        operation_params=body.operation_params,
        cron_expression=body.cron_expression,
        timezone_name=body.timezone,
        overlap_policy=body.overlap_policy,
    )
    now = datetime.now(timezone.utc)
    trigger = RecurringPlatformJobTrigger(
        id=uuid4(),
        org_id=org_id,
        operation_type=body.operation_type,
        operation_id=body.operation_id,
        operation_params=params,
        cron_expression=body.cron_expression.strip(),
        timezone=body.timezone,
        enabled=True,
        overlap_policy=body.overlap_policy,
        requested_by_user_id=user.user_id,
        requested_by_email=user.email,
        requested_by_name=user.name or user.email or "Unknown",
        created_at=now,
        updated_at=now,
    )
    db.add(trigger)
    await db.flush()
    return _public(trigger)


async def _trigger_or_404(
    db: AsyncSession, *, user: UserPrincipal, trigger_id: UUID, lock: bool = False
) -> RecurringPlatformJobTrigger:
    stmt = select(RecurringPlatformJobTrigger).where(
        RecurringPlatformJobTrigger.id == trigger_id
    )
    if lock:
        stmt = stmt.with_for_update()
    trigger = (await db.execute(stmt)).scalar_one_or_none()
    if trigger is None:
        _service_error("trigger_not_found", "Schedule not found.", 404)
    if not user.is_superuser and trigger.org_id != user.organization_id:
        _service_error("trigger_not_found", "Schedule not found.", 404)
    return trigger


async def get_trigger(
    db: AsyncSession, *, user: UserPrincipal, trigger_id: UUID
) -> RecurringTriggerPublic:
    return _public(await _trigger_or_404(db, user=user, trigger_id=trigger_id))


async def list_triggers(
    db: AsyncSession,
    *,
    user: UserPrincipal,
    operation_type: str | None,
    organization_id: UUID | None,
    enabled: bool | None,
    limit: int,
    offset: int,
) -> RecurringTriggerPage:
    filters = []
    if not user.is_superuser:
        if organization_id is not None and organization_id != user.organization_id:
            _service_error(
                "scope_forbidden",
                "Cannot expand schedule search beyond your organization.",
                403,
            )
        filters.append(RecurringPlatformJobTrigger.org_id == user.organization_id)
    elif organization_id is not None:
        filters.append(RecurringPlatformJobTrigger.org_id == organization_id)
    if operation_type is not None:
        if operation_type not in (OPERATION_AGENT_REVIEW, OPERATION_AGENT_EVALUATION_SUITE):
            _service_error("unknown_operation", "Unknown operation_type.", 422)
        filters.append(RecurringPlatformJobTrigger.operation_type == operation_type)
    if enabled is not None:
        filters.append(RecurringPlatformJobTrigger.enabled.is_(enabled))
    total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(RecurringPlatformJobTrigger)
                .where(*filters)
            )
        ).scalar_one()
    )
    rows = (
        (
            await db.execute(
                select(RecurringPlatformJobTrigger)
                .where(*filters)
                .order_by(
                    RecurringPlatformJobTrigger.created_at.desc(),
                    RecurringPlatformJobTrigger.id,
                )
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return RecurringTriggerPage(
        items=[_public(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def update_trigger(
    db: AsyncSession,
    *,
    user: UserPrincipal,
    trigger_id: UUID,
    body: RecurringTriggerUpdate,
) -> RecurringTriggerPublic:
    trigger = await _trigger_or_404(db, user=user, trigger_id=trigger_id, lock=True)
    fields = body.model_fields_set
    cron = body.cron_expression if "cron_expression" in fields else trigger.cron_expression
    tz = body.timezone if "timezone" in fields else trigger.timezone
    params = (
        body.operation_params
        if "operation_params" in fields
        else dict(trigger.operation_params or {})
    )
    validated = await _validated_params(
        operation_type=trigger.operation_type,
        operation_id=trigger.operation_id,
        operation_params=params,
        cron_expression=cron if cron is not None else trigger.cron_expression,
        timezone_name=tz if tz is not None else trigger.timezone,
        overlap_policy=trigger.overlap_policy,
    )
    if cron is not None:
        trigger.cron_expression = cron.strip()
    if tz is not None:
        trigger.timezone = tz
    if "operation_params" in fields:
        trigger.operation_params = validated
    if "enabled" in fields and body.enabled is not None:
        trigger.enabled = body.enabled
    # Any update rebinds the stored requester to its caller: future fires
    # must run under whoever last authorized the request, never a stale owner.
    trigger.requested_by_user_id = user.user_id
    trigger.requested_by_email = user.email
    trigger.requested_by_name = user.name or user.email or "Unknown"
    trigger.updated_at = datetime.now(timezone.utc)
    await db.flush()
    return _public(trigger)


async def list_fires(
    db: AsyncSession,
    *,
    user: UserPrincipal,
    trigger_id: UUID,
    limit: int,
    offset: int,
) -> TriggerFirePage:
    trigger = await _trigger_or_404(db, user=user, trigger_id=trigger_id)
    total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(RecurringTriggerFire)
                .where(RecurringTriggerFire.trigger_id == trigger.id)
            )
        ).scalar_one()
    )
    rows = (
        (
            await db.execute(
                select(RecurringTriggerFire)
                .where(RecurringTriggerFire.trigger_id == trigger.id)
                .order_by(
                    RecurringTriggerFire.scheduled_for.desc(),
                    RecurringTriggerFire.id,
                )
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return TriggerFirePage(
        items=[TriggerFirePublic.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
