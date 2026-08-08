"""Job-bound callback API used by external sandbox runner attempts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.core.database import get_db
from src.models.contracts.sandbox_runner import (
    SandboxBuilderMessage,
    SandboxBuilderTurnCompletion,
    SandboxBuilderTurnContext,
    SandboxJobCancelled,
    SandboxJobProgressUpdate,
    SandboxLLMCompletionRequest,
    SandboxLLMCompletionResponse,
)
from src.models.orm.agents import Agent, Conversation, Message
from src.models.contracts.solution_builder import BuildJobStatusUpdate
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_build_jobs import SolutionBuildJob
from src.models.orm.solution_builder import (
    SolutionBuilderSession,
    SolutionBuilderTurn,
)
from src.services.builder.capabilities import (
    SANDBOX_ARTIFACT_WRITE,
    SANDBOX_CANCEL_READ,
    SANDBOX_COMPLETE_WRITE,
    SANDBOX_INPUT_READ,
    SANDBOX_LLM_INVOKE,
    SANDBOX_OUTPUT_WRITE,
    SANDBOX_PROGRESS_WRITE,
    SandboxJobCapability,
    require_sandbox_job_capability,
)
from src.services.builder.agent_turns import (
    BuilderAgentTurnService,
    BuilderTurnCompletionFenced,
)
from src.services.builder.fs_tools import WorkspaceViolation
from src.services.builder.revision_storage import SolutionRevisionStorage
from src.services.builder.staged_artifacts import (
    BuildArtifactIntegrityError,
    BuildOutputTooLarge,
    StagedBuildArtifactStorage,
)
from src.services.builder.turn_artifacts import (
    BuilderTurnArtifactStorage,
    BuilderTurnOutputTooLarge,
)
from src.services.builder.turns import (
    BuilderProjectMissing,
    BuilderTurnConflict,
    WorkspaceInvalid,
)
from src.services.builder.llm_proxy import (
    BuilderLLMBudgetExceeded,
    BuilderLLMCompletionFenced,
    BuilderLLMUnavailable,
    complete_builder_llm,
)

router = APIRouter(
    prefix="/api/internal/sandbox/jobs",
    tags=["internal-sandbox-jobs"],
    include_in_schema=False,
)
Db = Annotated[AsyncSession, Depends(get_db)]


async def _capability(
    job_id: UUID,
    db: Db,
    authorization: Annotated[str | None, Header()] = None,
) -> SandboxJobCapability:
    return await require_sandbox_job_capability(job_id, db, authorization)


Capability = Annotated[SandboxJobCapability, Depends(_capability)]


def _require_build(capability: SandboxJobCapability) -> None:
    if capability.job_type != "solution.build":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This callback is not valid for the sandbox job type",
        )


def _require_turn(capability: SandboxJobCapability) -> None:
    if capability.job_type != "solution.builder.turn":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This callback is not valid for the sandbox job type",
        )


@router.get("/{job_id}/input")
async def get_input(
    job_id: UUID,
    db: Db,
    capability: Capability,
) -> StreamingResponse:
    capability.require(SANDBOX_INPUT_READ)
    if capability.job_type == "solution.build":
        build_job = await db.get(SolutionBuildJob, job_id)
        if build_job is None or build_job.status != "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Build job is not running",
            )
        return StreamingResponse(
            StagedBuildArtifactStorage(job_id).open_input_stream(),
            media_type="application/zip",
        )
    _require_turn(capability)
    turn = await db.get(SolutionBuilderTurn, job_id)
    if turn is None or turn.status not in {"queued", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Builder turn is not running",
        )
    if turn.base_revision_id is None:
        raise HTTPException(status_code=409, detail="Builder turn has no base revision")
    return StreamingResponse(
        SolutionRevisionStorage(
            (await _turn_session(db, turn)).solution_id
        ).iter_chunks(turn.base_revision_id),
        media_type="application/zip",
    )


@router.get(
    "/{job_id}/context",
    response_model=SandboxBuilderTurnContext,
)
async def get_turn_context(
    job_id: UUID,
    db: Db,
    capability: Capability,
) -> SandboxBuilderTurnContext:
    capability.require(SANDBOX_INPUT_READ)
    _require_turn(capability)
    turn = await db.get(SolutionBuilderTurn, job_id)
    if turn is None or turn.status not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Builder turn is not running")
    session = await _turn_session(db, turn)
    conversation = await db.get(Conversation, session.conversation_id)
    if conversation is None or conversation.agent_id is None:
        raise HTTPException(status_code=409, detail="Builder conversation is unavailable")
    agent = await db.get(Agent, conversation.agent_id)
    if agent is None or not agent.llm_model:
        raise HTTPException(status_code=409, detail="Builder model is not configured")
    recent = (
        (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.sequence.desc())
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    messages = [
        SandboxBuilderMessage(
            role=message.role.value,
            content=message.content,
            tool_calls=message.tool_calls,
            tool_call_id=message.tool_call_id,
            tool_name=message.tool_name,
        )
        for message in reversed(recent)
    ]
    return SandboxBuilderTurnContext(
        solution_id=str(session.solution_id),
        session_id=str(session.id),
        turn_id=str(turn.id),
        base_revision_id=str(turn.base_revision_id),
        system_prompt=agent.system_prompt,
        model=agent.llm_model,
        max_iterations=agent.max_iterations or 50,
        max_token_budget=agent.max_token_budget or 100_000,
        system_tools=list(agent.system_tools or []),
        messages=messages,
    )


@router.put("/{job_id}/output")
async def put_turn_output(
    job_id: UUID,
    request: Request,
    db: Db,
    capability: Capability,
) -> dict[str, str | int]:
    capability.require(SANDBOX_OUTPUT_WRITE)
    _require_turn(capability)
    turn = await db.get(SolutionBuilderTurn, job_id)
    if turn is None or turn.status not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Builder turn is not running")
    try:
        digest, size = await BuilderTurnArtifactStorage(
            job_id,
            capability.dispatch_attempt,
        ).write_output(
            request.stream(),
            max_bytes=get_settings().builder_output_limit_bytes,
        )
    except BuilderTurnOutputTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return {"sha256": digest, "size": size}


@router.post(
    "/{job_id}/llm/completions",
    response_model=SandboxLLMCompletionResponse,
)
async def complete_turn_llm(
    job_id: UUID,
    body: SandboxLLMCompletionRequest,
    db: Db,
    capability: Capability,
) -> SandboxLLMCompletionResponse:
    capability.require(SANDBOX_LLM_INVOKE)
    _require_turn(capability)
    try:
        return await complete_builder_llm(
            db,
            job_id=job_id,
            dispatch_attempt=capability.dispatch_attempt,
            request=body,
        )
    except BuilderLLMBudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except BuilderLLMCompletionFenced as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except BuilderLLMUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _turn_session(
    db: AsyncSession,
    turn: SolutionBuilderTurn,
) -> SolutionBuilderSession:
    session = await db.get(SolutionBuilderSession, turn.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Builder session not found")
    return session


@router.put("/{job_id}/artifacts/{rel_path:path}")
async def put_artifact(
    job_id: UUID,
    rel_path: str,
    request: Request,
    db: Db,
    capability: Capability,
) -> dict[str, str | int]:
    capability.require(SANDBOX_ARTIFACT_WRITE)
    _require_build(capability)
    build_job = await db.get(SolutionBuildJob, job_id)
    if build_job is None or build_job.status != "running" or build_job.app_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Build job is not running",
        )
    try:
        digest, size = await StagedBuildArtifactStorage(job_id).write_output(
            build_job.app_id,
            rel_path,
            request.stream(),
            get_settings().builder_output_limit_bytes,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except BuildOutputTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc
    return {"sha256": digest, "size": size}


@router.post("/{job_id}/progress", status_code=status.HTTP_204_NO_CONTENT)
async def progress(
    job_id: UUID,
    body: SandboxJobProgressUpdate,
    db: Db,
    capability: Capability,
) -> Response:
    capability.require(SANDBOX_PROGRESS_WRITE)
    build_job = await db.get(SolutionBuildJob, job_id)
    if capability.job_type == "solution.build":
        if build_job is None or build_job.status != "running":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Build job is not running",
            )
        build_job.last_progress_at = datetime.now(timezone.utc)
        await db.commit()

    from src.services.platform_jobs import update_external_platform_job_progress

    updated = await update_external_platform_job_progress(
        job_id,
        capability.dispatch_attempt,
        phase=body.phase,
        current=body.current,
        total=body.total,
        percent=body.percent,
    )
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sandbox job is no longer running",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{job_id}/cancelled", response_model=SandboxJobCancelled)
async def cancelled(
    job_id: UUID,
    db: Db,
    capability: Capability,
) -> SandboxJobCancelled:
    capability.require(SANDBOX_CANCEL_READ)
    job = await db.get(PlatformJob, job_id)
    return SandboxJobCancelled(
        cancelled=job is None or job.status in {"cancel_requested", "cancelled"}
    )


@router.post("/{job_id}/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete_sandbox_job(
    job_id: UUID,
    body: dict[str, object],
    db: Db,
    capability: Capability,
) -> Response:
    try:
        if capability.job_type == "solution.build":
            return await complete_build(
                job_id,
                BuildJobStatusUpdate.model_validate(body),
                db,
                capability,
            )
        _require_turn(capability)
        return await complete_turn(
            job_id,
            SandboxBuilderTurnCompletion.model_validate(body),
            db,
            capability,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def complete_build(
    job_id: UUID,
    body: BuildJobStatusUpdate,
    db: Db,
    capability: Capability,
) -> Response:
    capability.require(SANDBOX_COMPLETE_WRITE)
    _require_build(capability)
    platform_job = await db.get(PlatformJob, job_id)
    build_job = await db.get(SolutionBuildJob, job_id)
    if platform_job is None or build_job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Build job not found")
    if platform_job.status in {"cancel_requested", "cancelled"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Build job was cancelled",
        )
    if build_job.status in {"succeeded", "failed", "cancelled", "timeout"}:
        if build_job.status == body.status:
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Build job already completed",
        )
    if build_job.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invalid build transition",
        )

    manifest = (
        [entry.model_dump(mode="json") for entry in body.output_manifest]
        if body.output_manifest is not None
        else None
    )
    if body.status == "succeeded":
        if not manifest or build_job.app_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A successful build requires an output manifest",
            )
        try:
            await StagedBuildArtifactStorage(job_id).verify_manifest(
                build_job.app_id,
                manifest,
            )
        except (ValueError, BuildArtifactIntegrityError, FileNotFoundError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    encoded_log = (body.log_excerpt or "").encode("utf-8")[
        : get_settings().builder_log_limit_bytes
    ]
    build_job.status = body.status
    build_job.error = body.error
    build_job.log_excerpt = encoded_log.decode("utf-8", errors="ignore") or None
    build_job.output_manifest = manifest
    now = datetime.now(timezone.utc)
    build_job.last_progress_at = now
    build_job.completed_at = now
    await db.commit()

    from src.services.platform_jobs import finish_external_platform_job

    result = {
        "build_job_id": str(job_id),
        "output_manifest": manifest,
        "log_excerpt": build_job.log_excerpt,
    }
    finished = await finish_external_platform_job(
        job_id,
        capability.dispatch_attempt,
        status=(
            "succeeded"
            if body.status == "succeeded"
            else "cancelled"
            if body.status == "cancelled"
            else "failed"
        ),
        result=result,
        error_message=(
            body.error if body.status not in {"succeeded", "cancelled"} else None
        ),
    )
    if not finished:
        current = await db.get(PlatformJob, job_id)
        if current is None or current.status not in {"succeeded", "failed", "cancelled"}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Sandbox job completion was fenced out",
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def complete_turn(
    job_id: UUID,
    body: SandboxBuilderTurnCompletion,
    db: AsyncSession,
    capability: SandboxJobCapability,
) -> Response:
    capability.require(SANDBOX_COMPLETE_WRITE)
    _require_turn(capability)
    platform_job = await db.get(PlatformJob, job_id)
    turn = await db.get(SolutionBuilderTurn, job_id)
    if platform_job is None or turn is None:
        raise HTTPException(status_code=404, detail="Builder turn not found")
    if platform_job.status in {"succeeded", "failed", "cancelled"}:
        if platform_job.status == body.status:
            return Response(status_code=204)
        raise HTTPException(status_code=409, detail="Builder turn already completed")
    if platform_job.status == "cancel_requested" and body.status != "cancelled":
        raise HTTPException(status_code=409, detail="Builder turn was cancelled")

    service = BuilderAgentTurnService(db)
    try:
        if body.status == "succeeded":
            assert body.output_sha256 is not None
            assert body.final_text is not None
            session = await _turn_session(db, turn)
            await service.finalize_agent_turn(
                session.solution_id,
                turn_id=turn.id,
                dispatch_attempt=capability.dispatch_attempt,
                output_sha256=body.output_sha256,
                final_text=body.final_text,
                tool_call_count=body.tool_call_count,
                model=body.model,
                token_count_input=body.token_count_input,
                token_count_output=body.token_count_output,
            )
        else:
            await service.finish_failed_agent_turn(
                turn_id=turn.id,
                dispatch_attempt=capability.dispatch_attempt,
                status=body.status,
                error=body.error,
            )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="Builder output was not uploaded") from exc
    except (WorkspaceInvalid, WorkspaceViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (BuilderProjectMissing, BuilderTurnConflict, BuilderTurnCompletionFenced) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


__all__ = ["router"]
