"""External sandbox adapter for native Solution Builder turns."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field

from src.core.database import get_db_context
from src.jobs.platform.base import (
    PlatformJobCancelled,
    PlatformJobContext,
    PlatformJobDefinition,
    PlatformJobDeferred,
    PlatformJobFailure,
    PlatformJobPolicy,
)
from src.models.orm.solution_builder import SolutionBuilderTurn, SolutionSourceRevision
from src.services.sandbox_runners import (
    SandboxDispatchFailed,
    SandboxRunnerUnavailable,
    dispatch_sandbox_platform_job,
)


class SolutionBuilderTurnPayload(BaseModel):
    solution_id: UUID
    session_id: UUID
    turn_id: UUID
    base_revision_id: UUID
    message: str = Field(min_length=1, max_length=32_000)


async def run_solution_builder_turn(
    context: PlatformJobContext,
    payload: SolutionBuilderTurnPayload,
) -> dict:
    async with get_db_context() as db:
        turn = await db.get(SolutionBuilderTurn, payload.turn_id)
        base = await db.get(SolutionSourceRevision, payload.base_revision_id)
        if turn is None or base is None or base.solution_id != payload.solution_id:
            raise PlatformJobFailure(
                "builder_turn_missing",
                "The Builder turn or its base revision no longer exists.",
            )
        input_sha256 = base.source_sha256
        if turn.status == "queued":
            turn.status = "running"
            turn.started_at = datetime.now(timezone.utc)
            await db.commit()

    await context.report("Starting isolated Builder workspace", percent=1)
    try:
        dispatch = await dispatch_sandbox_platform_job(
            context.job_id,
            context.lease_token,
            input_sha256=input_sha256,
        )
    except (SandboxRunnerUnavailable, SandboxDispatchFailed) as exc:
        await _fail_turn(payload.turn_id, str(exc))
        raise PlatformJobFailure(
            "sandbox_dispatch_failed",
            str(exc),
            retryable=isinstance(exc, SandboxDispatchFailed),
        ) from exc
    if dispatch.cancelled:
        await _cancel_turn_projection(payload.turn_id)
        raise PlatformJobCancelled

    raise PlatformJobDeferred(
        "Builder is working in an isolated workspace",
        {"turn_id": str(payload.turn_id)},
        external_provider=dispatch.provider,
        external_run_id=dispatch.external_run_id,
        external_started_at=dispatch.started_at,
    )


async def _fail_turn(turn_id: UUID, message: str) -> None:
    async with get_db_context() as db:
        turn = await db.get(SolutionBuilderTurn, turn_id, with_for_update=True)
        if turn is None or turn.status in {"succeeded", "failed"}:
            return
        turn.status = "failed"
        turn.error = message[:4000]
        turn.completed_at = datetime.now(timezone.utc)
        await db.commit()


async def _cancel_turn_projection(turn_id: UUID) -> None:
    async with get_db_context() as db:
        turn = await db.get(SolutionBuilderTurn, turn_id, with_for_update=True)
        if turn is None or turn.status not in {"queued", "running"}:
            return
        turn.status = "cancelled"
        turn.completed_at = datetime.now(timezone.utc)
        await db.commit()


async def cancel_solution_builder_turn(job_id: UUID) -> None:
    """Synchronize the turn projection and terminate external work."""
    from src.models.orm.platform_jobs import PlatformJob
    from src.services.sandbox_runners import cancel_external_sandbox_run

    await _cancel_turn_projection(job_id)
    async with get_db_context() as db:
        platform_job = await db.get(PlatformJob, job_id)
    if platform_job is not None:
        await cancel_external_sandbox_run(platform_job)


SOLUTION_BUILDER_TURN_DEFINITION = PlatformJobDefinition(
    job_type="solution.builder.turn",
    payload_version=1,
    payload_model=SolutionBuilderTurnPayload,
    handler=run_solution_builder_turn,
    policy=PlatformJobPolicy(
        timeout_seconds=2 * 60 * 60,
        max_attempts=2,
        min_memory_headroom_mb=64,
        allow_running_cancellation=True,
    ),
    encrypt_payload=True,
    cancellation_handler=cancel_solution_builder_turn,
)


__all__ = [
    "SOLUTION_BUILDER_TURN_DEFINITION",
    "SolutionBuilderTurnPayload",
    "run_solution_builder_turn",
]
