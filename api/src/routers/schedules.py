"""
Schedules Router

Provides CRON expression validation for the event source UI.
"""

import logging
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter

from src.models import CronValidationRequest, CronValidationResponse
from src.core.auth import Context, CurrentSuperuser
from src.core.log_safety import log_safe
from src.services.cron_parser import (
    cron_to_human_readable,
    is_cron_expression_valid,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/schedules", tags=["Schedules"])

# Minimum interval for schedules (5 minutes)
MIN_INTERVAL_SECONDS = 300


def _get_schedule_zone(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone_name}") from exc


def _validate_cron(
    expression: str,
    timezone_name: str,
) -> tuple[Literal["valid", "warning", "error"], str | None]:
    """Validate a CRON expression and return status with optional message."""
    if not is_cron_expression_valid(expression):
        return "error", "Invalid CRON expression"

    try:
        zone = _get_schedule_zone(timezone_name)
    except ValueError as e:
        return "error", str(e)

    # Check for too-frequent schedules (warning if < 5 minutes)
    try:
        from croniter import croniter
        now = datetime.now(timezone.utc).astimezone(zone)
        cron = croniter(expression, now)
        next1 = cron.get_next(datetime)
        next2 = cron.get_next(datetime)
        interval = (
            next2.astimezone(timezone.utc)
            - next1.astimezone(timezone.utc)
        ).total_seconds()

        if interval < MIN_INTERVAL_SECONDS:
            return "warning", f"Schedule runs more frequently than {MIN_INTERVAL_SECONDS // 60} minutes"
    except (ImportError, ValueError) as e:
        # croniter not installed or invalid expression — already validated above, treat as valid
        logger.debug(f"could not compute cron interval for {log_safe(expression)!r}: {log_safe(e)}")

    return "valid", None


@router.post(
    "/validate",
    response_model=CronValidationResponse,
    summary="Validate a CRON expression",
    description="Validate a CRON expression and return next run times",
)
async def validate_cron_expression(
    body: CronValidationRequest,
    ctx: Context,
    user: CurrentSuperuser,
) -> CronValidationResponse:
    """Validate a CRON expression and return schedule preview."""
    expression = body.expression.strip()

    if not expression:
        return CronValidationResponse(
            valid=False,
            human_readable="Empty expression",
            error="CRON expression is required",
        )

    try:
        zone = _get_schedule_zone(body.timezone)
    except ValueError as e:
        return CronValidationResponse(
            valid=False,
            human_readable="Invalid timezone",
            error=str(e),
        )

    validation_status, validation_message = _validate_cron(expression, body.timezone)

    if validation_status == "error":
        return CronValidationResponse(
            valid=False,
            human_readable="Invalid CRON expression",
            error=validation_message or "Invalid CRON expression",
        )

    human_readable = cron_to_human_readable(expression)

    # Calculate next 5 runs
    next_runs: list[str] = []
    interval_seconds: int | None = None
    try:
        from croniter import croniter
        now = datetime.now(timezone.utc).astimezone(zone)
        cron = croniter(expression, now)
        runs = []
        for _ in range(5):
            runs.append(cron.get_next(datetime))
        next_runs = [r.astimezone(timezone.utc).isoformat() for r in runs]

        if len(runs) >= 2:
            interval_seconds = int(
                (
                    runs[1].astimezone(timezone.utc)
                    - runs[0].astimezone(timezone.utc)
                ).total_seconds()
            )
    except (ImportError, ValueError) as e:
        # croniter not installed or expression rejected — return validation without preview
        logger.debug(f"could not compute next runs for {log_safe(expression)!r}: {log_safe(e)}")

    return CronValidationResponse(
        valid=True,
        human_readable=human_readable,
        next_runs=next_runs if next_runs else None,
        interval_seconds=interval_seconds,
        warning=validation_message if validation_status == "warning" else None,
    )
