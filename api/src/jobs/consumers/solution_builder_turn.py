"""Run native Solution Builder agent turns on the existing Bifrost Worker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config import get_settings
from src.core.principal import UserPrincipal
from src.jobs.rabbitmq import BaseConsumer
from src.models.orm import Conversation, PlatformJob
from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionBuilderTurn,
)
from src.services.builder.agent_turns import BuilderAgentTurnService
from src.services.builder.agent_identity import (
    BuilderRuntimeProfile,
    build_builder_runtime_profile,
)
from src.services.builder.fs_tools import WorkspaceLimits, WorkspaceRoot
from src.services.builder.runtime_authorization import (
    BuilderRuntimeForbidden,
    authorize_builder_project,
)
from src.services.builder.workspace_archives import (
    BuilderWorkspaceArchiveMismatch,
    BuilderWorkspaceArchiveMissing,
    BuilderWorkspaceArchiveSource,
    hydrate_workspace_archive,
    persist_workspace_archive,
)
from src.services.builder.turn_artifacts import BuilderTurnArtifactStorage
from src.services.authorization import AuthorizationContext
from src.services.solutions.access import SolutionAction

logger = logging.getLogger(__name__)

QUEUE_NAME = "solution-builder-turns"
_CANCEL_CHECK_SECONDS = 1.0
_TURN_TIMEOUT_SECONDS = 2 * 60 * 60
_EXECUTION_CLAIM_TTL_SECONDS = 60
_EXECUTION_CLAIM_RENEW_SECONDS = 20
_RENEW_EXECUTION_CLAIM_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
else
    return 0
end
"""
_RELEASE_EXECUTION_CLAIM_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


@dataclass(frozen=True)
class _TurnRuntime:
    solution_id: UUID
    session: SolutionBuilderSession
    turn: SolutionBuilderTurn
    conversation: Conversation
    profile: BuilderRuntimeProfile
    principal: UserPrincipal
    authorization: AuthorizationContext
    input_sha256: str


class _BuilderRunFailed(RuntimeError):
    pass


class SolutionBuilderTurnConsumer(BaseConsumer):
    """Consume one bounded Builder turn at a time per Worker replica."""

    def __init__(self) -> None:
        settings = get_settings()
        super().__init__(
            queue_name=QUEUE_NAME,
            prefetch_count=settings.builder_max_concurrent_turns,
        )
        from src.core.database import get_session_factory

        self._session_factory = get_session_factory()
        self._settings = settings

    async def process_message(self, body: dict[str, Any]) -> None:
        if body.get("kind") == "probe":
            await self._complete_probe(str(body["probe_id"]))
            return
        job_id = UUID(str(body["job_id"]))
        dispatch_attempt = int(body["dispatch_attempt"])
        input_sha256 = str(body.get("input_sha256") or "")
        if dispatch_attempt < 1:
            raise ValueError("dispatch_attempt must be positive")
        if len(input_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in input_sha256
        ):
            raise ValueError("input_sha256 must be a lowercase sha256 hex digest")

        async with self._execution_claim(job_id, dispatch_attempt) as claimed:
            if not claimed:
                logger.info(
                    "Ignoring stale Builder dispatch while waiting for execution claim",
                    extra={
                        "platform_job_id": str(job_id),
                        "attempt": dispatch_attempt,
                    },
                )
                return
            await self._process_claimed_message(
                job_id,
                dispatch_attempt,
                input_sha256=input_sha256,
            )

    async def _process_claimed_message(
        self,
        job_id: UUID,
        dispatch_attempt: int,
        *,
        input_sha256: str,
    ) -> None:
        try:
            runtime = await self._load_runtime(
                job_id,
                dispatch_attempt,
                input_sha256=input_sha256,
            )
        except _BuilderRunFailed as exc:
            await self._finish_unloaded(job_id, dispatch_attempt, str(exc))
            return
        if runtime is None:
            logger.info(
                "Ignoring stale Builder dispatch",
                extra={"platform_job_id": str(job_id), "attempt": dispatch_attempt},
            )
            return

        with tempfile.TemporaryDirectory(prefix="bifrost-builder-worker-") as name:
            scratch = Path(name)
            os.chmod(scratch, 0o700)
            workspace_path = scratch / "workspace"
            workspace_path.mkdir(mode=0o700)
            try:
                await self._materialize_workspace(runtime, workspace_path)
            except Exception as exc:
                await self._finish_failed(
                    runtime,
                    workspace_path,
                    dispatch_attempt,
                    str(exc),
                )
                return
            workspace = WorkspaceRoot(workspace_path, WorkspaceLimits())

            run_task = asyncio.create_task(
                self._run_agent(runtime, workspace, job_id, dispatch_attempt)
            )
            cancel_task = asyncio.create_task(self._cancel_watcher(job_id, run_task))
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(run_task),
                    timeout=self._job_timeout_seconds(job_id, dispatch_attempt),
                )
            except asyncio.CancelledError:
                await self._preserve_cancelled_workspace(
                    runtime,
                    workspace_path,
                    dispatch_attempt,
                )
                return
            except TimeoutError:
                run_task.cancel()
                await _swallow_cancelled(run_task)
                await self._finish_failed(
                    runtime,
                    workspace_path,
                    dispatch_attempt,
                    f"Builder turn timed out after {_TURN_TIMEOUT_SECONDS} seconds",
                )
                return
            except _BuilderRunFailed as exc:
                await self._finish_failed(
                    runtime,
                    workspace_path,
                    dispatch_attempt,
                    str(exc),
                )
                return
            except Exception:
                logger.exception(
                    "Builder turn failed in the local Worker",
                    extra={"platform_job_id": str(job_id)},
                )
                await self._finish_failed(
                    runtime,
                    workspace_path,
                    dispatch_attempt,
                    "Builder execution failed unexpectedly; see server logs.",
                )
                return
            finally:
                cancel_task.cancel()
                await _swallow_cancelled(cancel_task)

            try:
                await self._finalize_success(
                    runtime,
                    workspace_path,
                    dispatch_attempt,
                    result,
                )
            except Exception:
                logger.exception(
                    "Builder turn finalization failed",
                    extra={"platform_job_id": str(job_id)},
                )
                await self._finish_failed(
                    runtime,
                    workspace_path,
                    dispatch_attempt,
                    "Builder output could not be finalized; see server logs.",
                )

    @contextlib.asynccontextmanager
    async def _execution_claim(
        self,
        job_id: UUID,
        dispatch_attempt: int,
    ) -> AsyncIterator[bool]:
        """Serialize at-least-once queue deliveries for one external attempt.

        A broker/channel failure can redeliver a message while the original
        coroutine is still alive. The short Redis lease is renewed by the live
        holder, lets duplicates wait without mutating, and expires quickly when
        a Worker really dies so a redelivery can recover the turn.
        """
        from src.core.cache.redis_client import get_redis

        key = f"bifrost:builder:execution:{job_id}:{dispatch_attempt}"
        token = uuid4().hex
        async with get_redis() as redis:
            renew = redis.register_script(_RENEW_EXECUTION_CLAIM_LUA)
            release = redis.register_script(_RELEASE_EXECUTION_CLAIM_LUA)

            while not await redis.set(
                key,
                token,
                nx=True,
                ex=_EXECUTION_CLAIM_TTL_SECONDS,
            ):
                async with self._session_factory() as db:
                    job = await db.get(PlatformJob, job_id)
                    turn = await db.get(SolutionBuilderTurn, job_id)
                if (
                    job is None
                    or turn is None
                    or job.attempt != dispatch_attempt
                    or job.status not in {"running", "waiting", "cancel_requested"}
                    or turn.status not in {"queued", "running"}
                ):
                    yield False
                    return
                await asyncio.sleep(_CANCEL_CHECK_SECONDS)

            async def _renew() -> None:
                while True:
                    await asyncio.sleep(_EXECUTION_CLAIM_RENEW_SECONDS)
                    try:
                        await renew(
                            keys=[key],
                            args=[token, _EXECUTION_CLAIM_TTL_SECONDS],
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001 - retry before the lease expires
                        logger.warning(
                            "Builder execution-claim renewal failed",
                            extra={"platform_job_id": str(job_id)},
                        )

            watchdog = asyncio.create_task(_renew())
            try:
                yield True
            finally:
                watchdog.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watchdog
                with contextlib.suppress(Exception):
                    await release(keys=[key], args=[token])

    def _job_timeout_seconds(self, job_id: UUID, dispatch_attempt: int) -> int:
        del job_id, dispatch_attempt
        return _TURN_TIMEOUT_SECONDS

    async def _complete_probe(self, probe_id: str) -> None:
        from src.core.cache.redis_client import get_redis

        async with get_redis() as redis:
            await redis.setex(
                f"bifrost:builder:probe:{probe_id}",
                60,
                "ready",
            )

    async def _load_runtime(
        self,
        job_id: UUID,
        dispatch_attempt: int,
        *,
        input_sha256: str,
    ) -> _TurnRuntime | None:
        async with self._session_factory() as db:
            return await self._authorize_runtime(
                db,
                job_id=job_id,
                dispatch_attempt=dispatch_attempt,
                input_sha256=input_sha256,
            )

    async def _authorize_runtime(
        self,
        db: Any,
        *,
        job_id: UUID,
        dispatch_attempt: int,
        input_sha256: str | None = None,
    ) -> _TurnRuntime | None:
        job = await db.get(PlatformJob, job_id)
        if (
            job is None
            or job.job_type != "solution.builder.turn"
            or job.attempt != dispatch_attempt
            or job.status not in {"running", "waiting"}
        ):
            return None
        turn = await db.get(SolutionBuilderTurn, job_id)
        if turn is None or turn.status not in {"queued", "running"}:
            return None
        session = await db.get(SolutionBuilderSession, turn.session_id)
        if session is None:
            raise _BuilderRunFailed("The Builder session no longer exists")
        conversation = (
            await db.execute(
                select(Conversation)
                .options(selectinload(Conversation.user))
                .where(Conversation.id == session.conversation_id)
            )
        ).scalar_one_or_none()
        if conversation is None:
            raise _BuilderRunFailed("The Builder conversation is not available")
        if turn.requested_by is None:
            raise _BuilderRunFailed(
                "The user who requested this turn no longer exists"
            )
        project = await db.get(SolutionBuilderProject, session.solution_id)
        if project is None:
            raise _BuilderRunFailed("The Builder project no longer exists")
        required_capabilities = ["builder.execute"]
        if project.target_kind == "solution":
            required_capabilities.extend(
                [
                    "solutions.readwrite",
                    "solutions.build.execute",
                    "solutions.deploy.execute",
                ]
            )
        try:
            authorized = await authorize_builder_project(
                db,
                solution_id=session.solution_id,
                requester_user_id=turn.requested_by,
                action=SolutionAction.EDIT,
                required_capabilities=tuple(required_capabilities),
            )
        except BuilderRuntimeForbidden as exc:
            raise _BuilderRunFailed(str(exc)) from exc
        profile = build_builder_runtime_profile(
            authorized.solution,
            target_kind=authorized.project.target_kind,
            authorization=authorized.authorization,
        )
        return _TurnRuntime(
            solution_id=session.solution_id,
            session=session,
            turn=turn,
            conversation=conversation,
            profile=profile,
            principal=authorized.principal,
            authorization=authorized.authorization,
            input_sha256=input_sha256 or "",
        )

    async def _materialize_workspace(
        self,
        runtime: _TurnRuntime,
        workspace_path: Path,
    ) -> None:
        source = self._workspace_source(runtime)
        limits = WorkspaceLimits()
        try:
            await hydrate_workspace_archive(
                source,
                workspace_path,
                limits=limits,
            )
        except BuilderWorkspaceArchiveMissing as exc:
            raise _BuilderRunFailed(
                "The Builder base workspace is no longer available"
            ) from exc
        except BuilderWorkspaceArchiveMismatch as exc:
            raise _BuilderRunFailed(str(exc)) from exc

    def _workspace_source(
        self,
        runtime: _TurnRuntime,
    ) -> BuilderWorkspaceArchiveSource:
        if runtime.turn.resume_from_turn_id is not None:
            return BuilderWorkspaceArchiveSource(
                kind="checkpoint",
                solution_id=runtime.solution_id,
                session_id=runtime.session.id,
                archive_id=runtime.turn.resume_from_turn_id,
                expected_sha256=runtime.input_sha256,
            )
        return BuilderWorkspaceArchiveSource(
            kind="revision",
            solution_id=runtime.solution_id,
            session_id=runtime.session.id,
            archive_id=runtime.turn.base_revision_id,
            expected_sha256=runtime.input_sha256,
        )

    async def _run_agent(
        self,
        runtime: _TurnRuntime,
        workspace: WorkspaceRoot,
        job_id: UUID,
        dispatch_attempt: int,
    ) -> dict[str, Any]:
        from src.services.agent_executor import AgentExecutor
        from src.services.agent_runtime import ModelCallEvent

        async def report_model_state(event: ModelCallEvent) -> None:
            if event.type == "request":
                await self._report_progress(
                    job_id,
                    dispatch_attempt,
                    "AI is working",
                )
            elif event.type == "error":
                await self._report_progress(
                    job_id,
                    dispatch_attempt,
                    "AI request failed",
                )

        executor = AgentExecutor(
            self._session_factory,
            builder_workspace=workspace,
            model_profile="builder",
            runtime_model_event_handler=report_model_state,
        )
        final_text = ""
        token_count_input: int | None = None
        token_count_output: int | None = None
        tool_call_count = 0
        compaction_count = 0

        async for chunk in executor.chat(
            agent=runtime.profile,
            conversation=runtime.conversation,
            user_message=(await self._user_message(runtime.turn)),
            stream=True,
            enable_routing=False,
            user=runtime.principal,
            authorization_context=runtime.authorization,
            existing_user_message_id=runtime.turn.user_message_id,
        ):
            await self._publish_chunk(runtime.conversation.id, chunk)
            if chunk.type == "tool_call" and chunk.tool_call is not None:
                tool_call_count += 1
                await self._report_progress(
                    job_id,
                    dispatch_attempt,
                    f"Using {chunk.tool_call.name}",
                )
            elif chunk.type == "artifact_started":
                await self._report_progress(
                    job_id,
                    dispatch_attempt,
                    f"Creating {chunk.content or 'artifact'}",
                )
            elif chunk.type == "context_warning" and chunk.context_warning is not None:
                if chunk.context_warning.action == "compacted":
                    compaction_count += 1
                    await self._report_progress(
                        job_id,
                        dispatch_attempt,
                        "Compacting Builder context",
                    )
            elif chunk.type == "done":
                final_text = chunk.content or ""
                token_count_input = chunk.token_count_input
                token_count_output = chunk.token_count_output
            elif chunk.type == "error":
                raise _BuilderRunFailed(chunk.error or "Builder agent execution failed")

        model_request_count = (
            executor.active_usage.requests if executor.active_usage is not None else 0
        )

        return {
            "final_text": final_text,
            "token_count_input": token_count_input,
            "token_count_output": token_count_output,
            "model_request_count": model_request_count,
            "tool_call_count": tool_call_count,
            "harness_diagnostics": {
                "model_request_count": model_request_count,
                "tool_call_count": tool_call_count,
                "compaction_count": compaction_count,
            },
        }

    async def _user_message(self, turn: SolutionBuilderTurn) -> str:
        from src.models.orm.agents import Message

        if turn.user_message_id is None:
            raise _BuilderRunFailed("The Builder prompt is missing")
        async with self._session_factory() as db:
            message = await db.get(Message, turn.user_message_id)
            if message is None:
                raise _BuilderRunFailed("The Builder prompt is missing")
            return message.content or ""

    async def _publish_chunk(self, conversation_id: UUID, chunk: Any) -> None:
        from src.core.pubsub import manager

        payload = chunk.model_dump(mode="json", exclude_none=True)
        payload["conversation_id"] = str(conversation_id)
        await manager.broadcast(f"chat:{conversation_id}", payload)

    async def _report_progress(
        self,
        job_id: UUID,
        dispatch_attempt: int,
        phase: str,
    ) -> None:
        from src.services.platform_jobs import update_external_platform_job_progress

        await update_external_platform_job_progress(
            job_id,
            dispatch_attempt,
            phase=phase,
            current=0,
            total=None,
            percent=None,
        )

    async def _finalize_success(
        self,
        runtime: _TurnRuntime,
        workspace_path: Path,
        dispatch_attempt: int,
        result: dict[str, Any],
    ) -> None:
        output_sha256 = await persist_workspace_archive(
            workspace=workspace_path,
            turn_id=runtime.turn.id,
            dispatch_attempt=dispatch_attempt,
            max_bytes=self._settings.builder_output_limit_bytes,
        )
        async with self._session_factory() as db:
            revalidated = await self._authorize_runtime(
                db,
                job_id=runtime.turn.id,
                dispatch_attempt=dispatch_attempt,
                input_sha256=runtime.input_sha256,
            )
            if revalidated is None:
                raise _BuilderRunFailed(
                    "The Builder turn is no longer authorized to finalize"
                )
            await BuilderAgentTurnService(db).finalize_agent_turn(
                runtime.solution_id,
                turn_id=runtime.turn.id,
                dispatch_attempt=dispatch_attempt,
                output_sha256=output_sha256,
                final_text=result["final_text"],
                tool_call_count=result["tool_call_count"],
                model_request_count=result["model_request_count"],
                token_count_input=result["token_count_input"],
                token_count_output=result["token_count_output"],
                harness_diagnostics=result["harness_diagnostics"],
                persist_assistant_message=False,
            )

    async def _finish_failed(
        self,
        runtime: _TurnRuntime,
        workspace_path: Path,
        dispatch_attempt: int,
        error: str,
    ) -> None:
        output_sha256 = await self._stage_checkpoint(
            runtime.turn.id,
            workspace_path,
            dispatch_attempt,
        )
        async with self._session_factory() as db:
            await BuilderAgentTurnService(db).finish_failed_agent_turn(
                turn_id=runtime.turn.id,
                dispatch_attempt=dispatch_attempt,
                status="failed",
                error=error,
                checkpoint_output_sha256=output_sha256,
            )

    async def _finish_unloaded(
        self,
        job_id: UUID,
        dispatch_attempt: int,
        error: str,
    ) -> None:
        from src.services.platform_jobs import finish_external_platform_job

        async with self._session_factory() as db:
            turn = await db.get(SolutionBuilderTurn, job_id, with_for_update=True)
            if turn is not None and turn.status in {"queued", "running"}:
                from datetime import datetime, timezone

                turn.status = "failed"
                turn.error = error[:4000]
                turn.completed_at = datetime.now(timezone.utc)
                await db.commit()
        await finish_external_platform_job(
            job_id,
            dispatch_attempt,
            status="failed",
            error_message=error,
        )

    async def _preserve_cancelled_workspace(
        self,
        runtime: _TurnRuntime,
        workspace_path: Path,
        dispatch_attempt: int,
    ) -> None:
        output_sha256 = await self._stage_checkpoint(
            runtime.turn.id,
            workspace_path,
            dispatch_attempt,
        )
        artifact_storage = BuilderTurnArtifactStorage(runtime.turn.id, dispatch_attempt)
        async with self._session_factory() as db:
            job = await db.get(PlatformJob, runtime.turn.id)
            service = BuilderAgentTurnService(db)
            if job is not None and job.status in {"running", "waiting", "cancel_requested"}:
                await service.finish_failed_agent_turn(
                    turn_id=runtime.turn.id,
                    dispatch_attempt=dispatch_attempt,
                    status="cancelled",
                    error=None,
                    checkpoint_output_sha256=output_sha256,
                )
            else:
                await service.preserve_agent_turn_checkpoint(
                    turn_id=runtime.turn.id,
                    dispatch_attempt=dispatch_attempt,
                    output_sha256=output_sha256,
                )
                await db.commit()
                await artifact_storage.delete()

    async def _stage_checkpoint(
        self,
        turn_id: UUID,
        workspace_path: Path,
        dispatch_attempt: int,
    ) -> str:
        return await persist_workspace_archive(
            workspace=workspace_path,
            turn_id=turn_id,
            dispatch_attempt=dispatch_attempt,
            max_bytes=self._settings.builder_output_limit_bytes,
            archive_name="checkpoint.zip",
        )

    async def _cancel_watcher(self, job_id: UUID, task: asyncio.Task[Any]) -> None:
        try:
            while not task.done():
                async with self._session_factory() as db:
                    status = await db.scalar(
                        select(PlatformJob.status).where(PlatformJob.id == job_id)
                    )
                if status in {"cancel_requested", "cancelled"}:
                    task.cancel()
                    return
                if status in {"succeeded", "failed"} or status is None:
                    task.cancel()
                    return
                await asyncio.sleep(_CANCEL_CHECK_SECONDS)
        except asyncio.CancelledError:
            return


async def _swallow_cancelled(task: asyncio.Task[Any]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        pass


__all__ = ["QUEUE_NAME", "SolutionBuilderTurnConsumer"]
