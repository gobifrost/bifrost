"""Recurring PlatformJob trigger CRUD and fire receipts (read-only)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from shared.models import (
    RecurringTriggerCreate,
    RecurringTriggerPage,
    RecurringTriggerPublic,
    RecurringTriggerUpdate,
    TriggerFirePage,
)
from shared.recurring_trigger_service import (
    TriggerServiceError,
    create_trigger,
    get_trigger,
    list_fires,
    list_triggers,
    update_trigger,
)
from src.core.auth import CurrentActiveUser
from src.core.db_deps import DbSession
from src.core.principal import UserPrincipal

router = APIRouter(prefix="/api/recurring-triggers", tags=["recurring-triggers"])


def _principal(user: CurrentActiveUser) -> UserPrincipal:
    return UserPrincipal(
        user_id=user.user_id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or "",
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        is_external=user.is_external,
    )


async def _domain(call):
    try:
        return await call
    except TriggerServiceError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail=exc.public_detail
        ) from exc


@router.post("", response_model=RecurringTriggerPublic, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    body: RecurringTriggerCreate, db: DbSession, user: CurrentActiveUser
) -> RecurringTriggerPublic:
    result = await _domain(create_trigger(db, user=_principal(user), body=body))
    await db.commit()
    return result


@router.get("", response_model=RecurringTriggerPage)
async def list_schedules(
    db: DbSession,
    user: CurrentActiveUser,
    operation_type: str | None = None,
    organization_id: UUID | None = None,
    enabled: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RecurringTriggerPage:
    return await _domain(
        list_triggers(
            db,
            user=_principal(user),
            operation_type=operation_type,
            organization_id=organization_id,
            enabled=enabled,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/{trigger_id}", response_model=RecurringTriggerPublic)
async def get_schedule(
    trigger_id: UUID, db: DbSession, user: CurrentActiveUser
) -> RecurringTriggerPublic:
    return await _domain(get_trigger(db, user=_principal(user), trigger_id=trigger_id))


@router.patch("/{trigger_id}", response_model=RecurringTriggerPublic)
async def update_schedule(
    trigger_id: UUID,
    body: RecurringTriggerUpdate,
    db: DbSession,
    user: CurrentActiveUser,
) -> RecurringTriggerPublic:
    result = await _domain(
        update_trigger(db, user=_principal(user), trigger_id=trigger_id, body=body)
    )
    await db.commit()
    return result


@router.get("/{trigger_id}/fires", response_model=TriggerFirePage)
async def list_schedule_fires(
    trigger_id: UUID,
    db: DbSession,
    user: CurrentActiveUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TriggerFirePage:
    return await _domain(
        list_fires(
            db, user=_principal(user), trigger_id=trigger_id, limit=limit, offset=offset
        )
    )
