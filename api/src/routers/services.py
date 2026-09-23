"""
Services Router

Control plane for supervised @service executables: definitions, desired
state, policy, and attempt history. All mutations change durable rows; the
claim loop (Slice 3) performs the actual supervision. Platform admin only,
mirroring event-source management.
"""

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from src.core.auth import Context, CurrentSuperuser
from src.core.db_deps import DbSession
from src.core.log_safety import log_safe
from src.models.contracts.services import (
    ServiceAttemptListResponse,
    ServiceAttemptResponse,
    ServiceListResponse,
    ServiceLogListResponse,
    ServiceLogResponse,
    ServicePolicyUpdate,
    ServiceResponse,
)
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.services import service_lifecycle
from src.services.service_lifecycle import (
    ServiceConflictError,
    ServiceValidationError,
)
from src.services import service_memory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/services", tags=["Services"])


def _assemble_service_response(
    loaded: ServiceDefinition,
    live: ServiceAttempt | None,
    last_terminal: ServiceAttempt | None,
    total_attempts: int,
    memory_by_attempt: dict[str, float],
) -> ServiceResponse:
    """Pure assembly: no I/O, shared by the single and batch paths."""
    workflow = loaded.workflow
    return ServiceResponse(
        id=loaded.id,
        workflow_id=loaded.workflow_id,
        workflow_name=workflow.name if workflow else "",
        workflow_path=workflow.path if workflow else "",
        organization_id=loaded.organization_id,
        solution_id=loaded.solution_id,
        enabled=loaded.enabled,
        startup_policy=loaded.startup_policy,  # type: ignore[arg-type]
        restart_policy=loaded.restart_policy,  # type: ignore[arg-type]
        desired_state=loaded.desired_state,  # type: ignore[arg-type]
        blocked_reason=loaded.blocked_reason,  # type: ignore[arg-type]
        restart_eligible_at=loaded.restart_eligible_at,
        current_revision=loaded.current_revision,
        graceful_shutdown_seconds=loaded.graceful_shutdown_seconds,
        startup_grace_seconds=loaded.startup_grace_seconds,
        restart_backoff_initial_seconds=loaded.restart_backoff_initial_seconds,
        restart_backoff_max_seconds=loaded.restart_backoff_max_seconds,
        crash_loop_max_restarts=loaded.crash_loop_max_restarts,
        crash_loop_window_seconds=loaded.crash_loop_window_seconds,
        observed_state=service_lifecycle.describe_observed_state(loaded, live),
        active_attempt_id=live.id if live else None,
        active_attempt=(
            ServiceAttemptResponse.model_validate(live) if live else None
        ),
        last_exit_reason=(
            last_terminal.exit_reason if last_terminal else None
        ),
        memory_mb=(
            memory_by_attempt.get(str(live.id)) if live else None
        ),
        restart_count=total_attempts,
        created_by=loaded.created_by,
        created_at=loaded.created_at,
        updated_at=loaded.updated_at,
    )


async def _build_service_response(
    db: DbSession,
    definition: ServiceDefinition,
    memory_by_attempt: dict[str, float] | None = None,
) -> ServiceResponse:
    """Assemble the public view with source identity and observed state.

    Single-row path (get/update endpoints): reloads the definition with
    its workflow plus the per-row observed-state lookups. The list
    endpoint uses the batch path below instead.
    """
    result = await db.execute(
        select(ServiceDefinition)
        .options(joinedload(ServiceDefinition.workflow))
        .where(ServiceDefinition.id == definition.id)
    )
    loaded = result.unique().scalar_one()
    live = await service_lifecycle.get_live_attempt(db, loaded.id)
    last_terminal = await service_lifecycle.get_last_terminal_attempt(
        db, loaded.id
    )
    if memory_by_attempt is None:
        memory_by_attempt = await service_memory.read_service_memory()
    from sqlalchemy import func as sa_func

    total_attempts = (
        await db.scalar(
            select(sa_func.count(ServiceAttempt.id)).where(
                ServiceAttempt.service_id == loaded.id
            )
        )
        or 0
    )
    return _assemble_service_response(
        loaded, live, last_terminal, total_attempts, memory_by_attempt
    )


async def _build_service_response_batch(
    db: DbSession,
    definitions: list[ServiceDefinition],
    memory_by_attempt: dict[str, float],
) -> list[ServiceResponse]:
    """Assemble one page: 3 observed-state queries total, not 3N.

    Definitions already carry their workflow (list_definitions
    joinedloads it); live/terminal/count maps come from one batched
    lookup each. The response contract is identical to the single path.
    """
    live_map, terminal_map, count_map = (
        await service_lifecycle.batch_list_embedding(
            db, [d.id for d in definitions]
        )
    )
    return [
        _assemble_service_response(
            d,
            live_map.get(d.id),
            terminal_map.get(d.id),
            count_map.get(d.id, 0),
            memory_by_attempt,
        )
        for d in definitions
    ]


async def _get_definition_or_404(db: DbSession, service_id: UUID) -> ServiceDefinition:
    definition = await service_lifecycle.get_definition(db, service_id)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service '{service_id}' not found",
        )
    return definition


@router.get("", response_model=ServiceListResponse, summary="List services")
async def list_services(
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    offset: int = Query(0, ge=0, description="Skip results"),
) -> ServiceListResponse:
    """List service definitions (platform admin only)."""
    definitions, total = await service_lifecycle.list_definitions(
        db, limit=limit, offset=offset
    )
    # One pool-hash scan serves every row in the page (no per-row rescan).
    memory_by_attempt = await service_memory.read_service_memory()
    items = await _build_service_response_batch(db, definitions, memory_by_attempt)
    return ServiceListResponse(items=items, total=total)


@router.get("/{service_id}", response_model=ServiceResponse, summary="Get service")
async def get_service(
    service_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> ServiceResponse:
    """Get a service definition with observed state (platform admin only)."""
    definition = await _get_definition_or_404(db, service_id)
    return await _build_service_response(db, definition)


@router.patch("/{service_id}", response_model=ServiceResponse, summary="Update service policy")
async def update_service_policy(
    service_id: UUID,
    request: ServicePolicyUpdate,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> ServiceResponse:
    """Update lifecycle policy fields (platform admin only)."""
    definition = await _get_definition_or_404(db, service_id)
    try:
        updated = await service_lifecycle.update_policy(
            db,
            definition,
            **request.model_dump(exclude_unset=True),
        )
    except ServiceValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    await db.commit()
    logger.info(f"Updated policy for service {log_safe(service_id)}")
    return await _build_service_response(db, updated)


@router.post("/{service_id}/start", response_model=ServiceResponse, summary="Start service")
async def start_service(
    service_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> ServiceResponse:
    """Request running: clears suppression so the claim loop picks it up."""
    definition = await _get_definition_or_404(db, service_id)
    try:
        updated = await service_lifecycle.start_service(db, definition)
    except ServiceConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(e)
        ) from e
    await db.commit()
    logger.info(f"Started service {log_safe(service_id)}")
    return await _build_service_response(db, updated)


@router.post("/{service_id}/stop", response_model=ServiceResponse, summary="Stop service")
async def stop_service(
    service_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> ServiceResponse:
    """Request stopped: durable desire is stored before termination."""
    definition = await _get_definition_or_404(db, service_id)
    updated = await service_lifecycle.stop_service(db, definition)
    await db.commit()
    logger.info(f"Stopped service {log_safe(service_id)}")
    return await _build_service_response(db, updated)


@router.post("/{service_id}/restart", response_model=ServiceResponse, summary="Restart service")
async def restart_service(
    service_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> ServiceResponse:
    """Rolling restart: stays desired-running and stops the live attempt, if any."""
    definition = await _get_definition_or_404(db, service_id)
    try:
        updated = await service_lifecycle.restart_service(db, definition)
    except ServiceConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(e)
        ) from e
    await db.commit()
    logger.info(f"Restarted service {log_safe(service_id)}")
    return await _build_service_response(db, updated)


@router.post("/{service_id}/enable", response_model=ServiceResponse, summary="Enable service")
async def enable_service(
    service_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> ServiceResponse:
    """Enable a service (distinct from start: no desired-state change)."""
    definition = await _get_definition_or_404(db, service_id)
    updated = await service_lifecycle.set_enabled(db, definition, True)
    await db.commit()
    logger.info(f"Enabled service {log_safe(service_id)}")
    return await _build_service_response(db, updated)


@router.post("/{service_id}/disable", response_model=ServiceResponse, summary="Disable service")
async def disable_service(
    service_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
) -> ServiceResponse:
    """Disable a service: stops the live attempt and blocks future claims."""
    definition = await _get_definition_or_404(db, service_id)
    updated = await service_lifecycle.set_enabled(db, definition, False)
    await db.commit()
    logger.info(f"Disabled service {log_safe(service_id)}")
    return await _build_service_response(db, updated)


@router.get(
    "/{service_id}/attempts",
    response_model=ServiceAttemptListResponse,
    summary="List service attempts",
)
async def list_service_attempts(
    service_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    limit: int = Query(100, ge=1, le=1000, description="Max results"),
    offset: int = Query(0, ge=0, description="Skip results"),
) -> ServiceAttemptListResponse:
    """List attempt history newest-first (platform admin only)."""
    await _get_definition_or_404(db, service_id)
    attempts, total = await service_lifecycle.list_attempts(
        db, service_id, limit=limit, offset=offset
    )
    return ServiceAttemptListResponse(
        items=[ServiceAttemptResponse.model_validate(a) for a in attempts],
        total=total,
    )


@router.get(
    "/{service_id}/logs",
    response_model=ServiceLogListResponse,
    summary="List service logs",
)
async def list_service_logs(
    service_id: UUID,
    ctx: Context,
    user: CurrentSuperuser,
    db: DbSession,
    attempt_id: UUID | None = Query(default=None, description="Scope to one attempt"),
    levels: list[str] | None = Query(default=None, description="Level allowlist (e.g. ?levels=INFO&levels=ERROR)"),
    start_date: str | None = Query(default=None, description="ISO timestamp lower bound"),
    end_date: str | None = Query(default=None, description="ISO timestamp upper bound"),
    limit: int = Query(200, ge=1, le=1000, description="Max lines per page"),
    continuation_token: str | None = Query(
        default=None,
        description="Keyset cursor from the previous page (load-older paging)",
    ),
    order: str = Query("chronological", description="chronological | newest_first"),
) -> ServiceLogListResponse:
    """Trailing service logs from Postgres (platform admin only).

    The surface is platform-admin-only, so no DEBUG/TRACEBACK hiding applies
    (unlike execution logs, which serve non-admins). Live tail (<10s old,
    pre-flush) arrives over the service WebSocket channel, not here.
    """
    await _get_definition_or_404(db, service_id)
    start = _parse_log_date(start_date, "start_date")
    end = _parse_log_date(end_date, "end_date")
    if order not in ("chronological", "newest_first"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="order must be chronological or newest_first",
        )
    from src.repositories.execution_logs import decode_execution_log_cursor

    # Strict like the executions logs endpoint: a token that decodes to
    # nothing is corruption (or a hand-typed value), and silently
    # restarting at page one would duplicate lines into the reader.
    # No legacy-offset fallback: this endpoint only ever minted keysets.
    cursor = None
    if continuation_token:
        cursor = decode_execution_log_cursor(continuation_token)
        if cursor is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="continuation_token is invalid",
            )

    rows, total, next_token = await service_lifecycle.list_service_logs(
        db,
        service_id,
        attempt_id=attempt_id,
        levels=levels,
        start=start,
        end=end,
        limit=limit,
        cursor=cursor,
        newest_first=order == "newest_first",
    )
    return ServiceLogListResponse(
        items=[ServiceLogResponse.model_validate(r) for r in rows],
        total=total,
        continuation_token=next_token,
    )


def _parse_log_date(value: str | None, name: str) -> datetime | None:
    """Lenient ISO date parse (invalid values are ignored, not 422s)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed
    except ValueError:
        logger.debug("invalid %s %r, ignoring filter", name, log_safe(value))
        return None


__all__ = ["router"]
