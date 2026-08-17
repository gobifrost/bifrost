"""Queue private-Solution Builder turns for isolated sandbox execution."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.enums import MessageRole
from src.models.orm.agents import Agent, Conversation, Message
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionBuilderTurn,
)
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.builder.agent_identity import ensure_builder_agent
from src.services.builder.revision_storage import SolutionRevisionStorage
from src.services.builder.turns import (
    BuilderProjectMissing,
    BuilderTurnConflict,
    BuilderTurnService,
)
from src.services.builder.turn_artifacts import BuilderTurnArtifactStorage
from src.services.solutions.deploy_jobs import create_staged_deploy_job

_SUMMARY_MAX_CHARS = 120
logger = logging.getLogger(__name__)


class BuilderTurnCompletionFenced(RuntimeError):
    """An external callback no longer owns the active PlatformJob attempt."""


@dataclass
class QueuedAgentTurn:
    turn: SolutionBuilderTurn
    platform_job: PlatformJob


@dataclass
class CompletedAgentTurn:
    turn: SolutionBuilderTurn
    platform_job: PlatformJob
    revision_created: bool


def _builder_usage_result(
    *,
    model_request_count: int,
    token_count_input: int | None,
    token_count_output: int | None,
    max_requests: int | None,
    max_tokens: int | None,
) -> dict[str, dict[str, int | None]]:
    """Project shared runtime usage into the durable Builder job contract."""

    return {
        "llm_usage": {
            "calls": max(0, model_request_count),
            "input_tokens": max(0, token_count_input or 0),
            "output_tokens": max(0, token_count_output or 0),
        },
        "llm_limits": {
            "max_calls": max_requests,
            "max_tokens": max_tokens,
        },
    }


async def enqueue_builder_turn_deploy(
    db: AsyncSession,
    solution_id: UUID,
    *,
    turn: SolutionBuilderTurn,
    revision_id: UUID,
) -> None:
    """Stage an immutable revision and queue the durable preview deploy."""
    solution = await db.get(Solution, solution_id)
    if solution is None:
        raise BuilderProjectMissing(f"Solution {solution_id} does not exist")
    requester = await db.get(User, turn.requested_by) if turn.requested_by else None
    with tempfile.TemporaryDirectory(prefix="bifrost-builder-deploy-") as tmp:
        source_zip = Path(tmp) / "source.zip"
        copied = await SolutionRevisionStorage(solution_id).copy_to_path(
            revision_id,
            source_zip,
        )
        if not copied:
            raise BuilderProjectMissing(f"revision {revision_id} content is missing")
        job = await create_staged_deploy_job(
            db,
            kind="deploy",
            install_id=solution_id,
            organization_id=solution.organization_id,
            requested_by_user_id=turn.requested_by or "system",
            requested_by_email=(
                requester.email if requester else "system@gobifrost.local"
            ),
            requested_by_name=(
                requester.name or requester.email
                if requester
                else "Bifrost Builder"
            ),
            options={
                "force": True,
                "isolated_app_builds": True,
                "source_revision_id": str(revision_id),
                "builder_turn_id": str(turn.id),
            },
            input_path=source_zip,
        )
    turn.deploy_job_id = job.id
    turn.status = "queued"
    turn.error = None
    turn.completed_at = None
    await db.commit()


def _revision_summary(user_message: str) -> str:
    first_line = (
        user_message.strip().splitlines()[0].strip()
        if user_message.strip()
        else ""
    )
    if not first_line:
        return "builder turn"
    if len(first_line) <= _SUMMARY_MAX_CHARS:
        return first_line
    return first_line[: _SUMMARY_MAX_CHARS - 1].rstrip() + "…"


class BuilderAgentTurnService:
    """Compose the existing Agent runtime with the immutable revision lifecycle."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.turns = BuilderTurnService(db)

    async def enqueue_agent_turn(
        self,
        solution_id: UUID,
        *,
        session_id: UUID,
        requested_by: UUID,
        user_message: str,
        attachment_ids: list[UUID] | None = None,
        resume_from_turn_id: UUID | None = None,
    ) -> QueuedAgentTurn:
        """Persist the prompt and enqueue one encrypted sandbox PlatformJob."""
        session = await self._load_session(solution_id, session_id)
        conversation = await self._ensure_builder_agent(
            solution_id,
            session,
        )
        project = await self.db.get(SolutionBuilderProject, solution_id)
        if project is None or project.current_revision_id is None:
            raise BuilderProjectMissing(f"Solution {solution_id} has no current revision")
        resume_from = None
        if resume_from_turn_id is not None:
            resume_from = await self.db.get(SolutionBuilderTurn, resume_from_turn_id)
            if (
                resume_from is None
                or resume_from.session_id != session_id
                or resume_from.status not in {"failed", "cancelled"}
                or resume_from.checkpoint_sha256 is None
            ):
                raise BuilderProjectMissing(
                    "The requested Builder checkpoint is not available in this session"
                )
            if resume_from.base_revision_id != project.current_revision_id:
                raise BuilderTurnConflict(
                    "The Solution changed after this checkpoint was captured"
                )
        await self.db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('bifrost:builder-turn:' || :solution_id))"
            ),
            {"solution_id": str(solution_id)},
        )
        active = (
            await self.db.execute(
                select(SolutionBuilderTurn.id)
                .join(
                    SolutionBuilderSession,
                    SolutionBuilderSession.id == SolutionBuilderTurn.session_id,
                )
                .where(
                    SolutionBuilderSession.solution_id == solution_id,
                    SolutionBuilderTurn.status.in_(("queued", "running")),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if active is not None:
            raise BuilderTurnConflict(
                f"another Builder turn is already active for Solution {solution_id}"
            )

        user_message_row = await self._append_message(
            conversation,
            role=MessageRole.USER,
            content=user_message,
            attachment_ids=attachment_ids,
        )
        turn_id = uuid4()
        turn = SolutionBuilderTurn(
            id=turn_id,
            session_id=session_id,
            requested_by=requested_by,
            user_message_id=user_message_row.id,
            base_revision_id=project.current_revision_id,
            resume_from_turn_id=(resume_from.id if resume_from is not None else None),
            status="queued",
        )
        self.db.add(turn)

        from src.jobs.platform.solution_builder_turn import (
            SOLUTION_BUILDER_TURN_DEFINITION,
            SolutionBuilderTurnPayload,
        )
        from src.services.platform_jobs import enqueue_platform_job

        solution = await self.db.get(Solution, solution_id)
        requester = await self.db.get(User, requested_by)
        if solution is None or requester is None:
            raise BuilderProjectMissing("The Solution owner no longer exists")
        platform_job, _ = await enqueue_platform_job(
            self.db,
            SOLUTION_BUILDER_TURN_DEFINITION,
            SolutionBuilderTurnPayload(
                solution_id=solution_id,
                session_id=session_id,
                turn_id=turn_id,
                base_revision_id=project.current_revision_id,
                message=user_message,
            ),
            dedupe_key=str(turn_id),
            resource_lock_key=f"solution:{solution_id}",
            priority=400,
            organization_id=solution.organization_id,
            requested_by_user_id=requested_by,
            requested_by_email=requester.email,
            requested_by_name=requester.name or requester.email,
            resource_type="solution_builder_turn",
            resource_id=str(turn_id),
            title=f"Building {solution.name}",
            action_url=f"/solutions/{solution_id}/builder",
            job_id=turn_id,
        )
        await self.db.commit()

        from src.services.platform_jobs import publish_platform_job_update

        await publish_platform_job_update(platform_job)
        return QueuedAgentTurn(turn=turn, platform_job=platform_job)

    async def finalize_agent_turn(
        self,
        solution_id: UUID,
        *,
        turn_id: UUID,
        dispatch_attempt: int,
        output_sha256: str,
        final_text: str,
        tool_call_count: int,
        model_request_count: int,
        model: str | None = None,
        token_count_input: int | None = None,
        token_count_output: int | None = None,
        assistant_message_id: UUID | None = None,
        duration_ms: int | None = None,
        harness_diagnostics: dict[str, Any] | None = None,
        persist_assistant_message: bool = True,
    ) -> CompletedAgentTurn:
        """Atomically accept one fenced sandbox result and persist chat state."""
        artifact_storage = BuilderTurnArtifactStorage(turn_id, dispatch_attempt)
        created_revision_id: UUID | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="bifrost-builder-result-") as tmp:
                output_path = Path(tmp) / "output.zip"
                await artifact_storage.copy_to_path(output_path)
                actual_sha256 = await asyncio.to_thread(_file_sha256, output_path)
                if actual_sha256 != output_sha256:
                    raise ValueError("Builder output archive digest does not match upload")

                turn = await self.db.get(SolutionBuilderTurn, turn_id)
                if turn is None:
                    raise BuilderProjectMissing(f"Builder turn {turn_id} does not exist")
                session = await self._load_session(solution_id, turn.session_id)
                user_message = await self._latest_user_message(session.conversation_id)
                materialized = await self.turns.materialize_external_output(
                    solution_id,
                    turn_id=turn_id,
                    output_zip=output_path,
                    summary=_revision_summary(user_message),
                )
                if materialized.revision_created:
                    created_revision_id = materialized.turn.output_revision_id

            conversation = await self.db.get(Conversation, session.conversation_id)
            if conversation is None:
                raise BuilderProjectMissing(
                    f"Builder conversation {session.conversation_id} is missing"
                )
            agent = (
                await self.db.get(Agent, conversation.agent_id)
                if conversation.agent_id is not None
                else None
            )
            if agent is None:
                raise BuilderProjectMissing(
                    f"Builder agent for conversation {session.conversation_id} is missing"
                )

            if persist_assistant_message:
                await self._append_message(
                    conversation,
                    role=MessageRole.ASSISTANT,
                    content=final_text,
                    model=model,
                    token_count_input=token_count_input,
                    token_count_output=token_count_output,
                    message_id=assistant_message_id,
                    duration_ms=duration_ms,
                )

            from src.services.platform_jobs import (
                publish_platform_job_update,
                stage_external_platform_job_completion,
            )

            platform_job = await stage_external_platform_job_completion(
                self.db,
                turn_id,
                dispatch_attempt,
                status="succeeded",
                result={
                    "turn_id": str(turn_id),
                    "revision_id": str(materialized.turn.output_revision_id),
                    "revision_created": materialized.revision_created,
                    "tool_call_count": tool_call_count,
                    "harness_diagnostics": harness_diagnostics,
                    **_builder_usage_result(
                        model_request_count=model_request_count,
                        token_count_input=token_count_input,
                        token_count_output=token_count_output,
                        max_requests=agent.max_iterations,
                        max_tokens=agent.max_token_budget,
                    ),
                },
            )
            if platform_job is None:
                raise BuilderTurnCompletionFenced(
                    "Builder turn completion was fenced out by a newer attempt"
                )

            if (
                materialized.revision_created
                and materialized.turn.output_revision_id is not None
            ):
                project = await self.db.get(SolutionBuilderProject, solution_id)
                if project is not None and project.target_kind == "global_repo":
                    await self.db.commit()
                else:
                    await enqueue_builder_turn_deploy(
                        self.db,
                        solution_id,
                        turn=materialized.turn,
                        revision_id=materialized.turn.output_revision_id,
                    )
            else:
                await self.db.commit()
            await publish_platform_job_update(platform_job)
            return CompletedAgentTurn(
                turn=materialized.turn,
                platform_job=platform_job,
                revision_created=materialized.revision_created,
            )
        except Exception:
            await self.db.rollback()
            if created_revision_id is not None:
                try:
                    await SolutionRevisionStorage(solution_id).delete(created_revision_id)
                except Exception:  # noqa: BLE001 - preserve the completion failure
                    logger.warning(
                        "Failed to delete an uncommitted Builder revision",
                        extra={"revision_id": str(created_revision_id)},
                        exc_info=True,
                    )
            raise
        finally:
            try:
                await artifact_storage.delete()
            except Exception:  # noqa: BLE001 - staged output cleanup is best effort
                logger.warning(
                    "Failed to delete staged Builder output",
                    extra={"turn_id": str(turn_id)},
                    exc_info=True,
                )

    async def finish_failed_agent_turn(
        self,
        *,
        turn_id: UUID,
        dispatch_attempt: int,
        status: str,
        error: str | None,
        harness_diagnostics: dict[str, Any] | None = None,
        checkpoint_output_sha256: str | None = None,
    ) -> PlatformJob:
        """Persist one failed/cancelled sandbox result under attempt fencing."""
        if status not in {"failed", "cancelled"}:
            raise ValueError(f"Unsupported Builder turn status: {status}")
        turn = await self.db.get(SolutionBuilderTurn, turn_id, with_for_update=True)
        if turn is None:
            raise BuilderProjectMissing(f"Builder turn {turn_id} does not exist")
        checkpoint_location: tuple[UUID, UUID] | None = None
        if checkpoint_output_sha256 is not None:
            await self.preserve_agent_turn_checkpoint(
                turn_id=turn_id,
                dispatch_attempt=dispatch_attempt,
                output_sha256=checkpoint_output_sha256,
            )
            checkpoint_session = await self.db.get(
                SolutionBuilderSession,
                turn.session_id,
            )
            if checkpoint_session is None:
                raise BuilderProjectMissing(
                    f"Builder session {turn.session_id} is missing"
                )
            checkpoint_location = (
                checkpoint_session.solution_id,
                checkpoint_session.id,
            )

        from src.services.platform_jobs import (
            publish_platform_job_update,
            stage_external_platform_job_completion,
        )

        platform_job = await stage_external_platform_job_completion(
            self.db,
            turn_id,
            dispatch_attempt,
            status=status,
            result={
                "turn_id": str(turn_id),
                "harness_diagnostics": harness_diagnostics,
            },
            error_message=error if status == "failed" else None,
        )
        if platform_job is None:
            await self.db.rollback()
            if checkpoint_location is not None:
                checkpoint_solution_id, checkpoint_session_id = checkpoint_location
                checkpoint_artifacts = BuilderTurnArtifactStorage(
                    turn_id,
                    dispatch_attempt,
                )
                try:
                    await checkpoint_artifacts.delete_checkpoint(
                        checkpoint_solution_id,
                        checkpoint_session_id,
                        turn_id,
                    )
                except Exception:  # noqa: BLE001 - preserve the fencing failure
                    logger.warning(
                        "Failed to delete a fenced Builder workspace checkpoint",
                        extra={"turn_id": str(turn_id)},
                        exc_info=True,
                    )
                try:
                    await checkpoint_artifacts.delete()
                except Exception:  # noqa: BLE001 - preserve the fencing failure
                    logger.warning(
                        "Failed to delete fenced staged Builder output",
                        extra={"turn_id": str(turn_id)},
                        exc_info=True,
                    )
            raise BuilderTurnCompletionFenced(
                "Builder turn completion was fenced out by a newer attempt"
            )
        turn.status = status
        turn.error = error[:4000] if error else None
        turn.completed_at = datetime.now(timezone.utc)
        await self.db.commit()
        await publish_platform_job_update(platform_job)
        try:
            await BuilderTurnArtifactStorage(turn_id, dispatch_attempt).delete()
        except Exception:  # noqa: BLE001 - terminal state is already durable
            logger.warning(
                "Failed to delete staged Builder output",
                extra={"turn_id": str(turn_id)},
                exc_info=True,
            )
        return platform_job

    async def preserve_agent_turn_checkpoint(
        self,
        *,
        turn_id: UUID,
        dispatch_attempt: int,
        output_sha256: str,
    ) -> SolutionBuilderTurn:
        """Accept an inert workspace checkpoint without publishing it."""
        turn = await self.db.get(SolutionBuilderTurn, turn_id, with_for_update=True)
        if turn is None:
            raise BuilderProjectMissing(f"Builder turn {turn_id} does not exist")
        session = await self.db.get(SolutionBuilderSession, turn.session_id)
        if session is None:
            raise BuilderProjectMissing(f"Builder session {turn.session_id} is missing")
        artifact_storage = BuilderTurnArtifactStorage(turn_id, dispatch_attempt)
        checkpoint_promoted = False
        with tempfile.TemporaryDirectory(prefix="bifrost-builder-checkpoint-") as tmp:
            output_path = Path(tmp) / "output.zip"
            await artifact_storage.copy_to_path(output_path)
            actual_sha256 = await asyncio.to_thread(_file_sha256, output_path)
            if actual_sha256 != output_sha256:
                raise ValueError("Builder checkpoint digest does not match upload")
        try:
            await artifact_storage.promote_checkpoint(
                solution_id=session.solution_id,
                session_id=session.id,
            )
            checkpoint_promoted = True
            turn.checkpoint_sha256 = output_sha256
            await self.db.flush()
            return turn
        except Exception:
            if checkpoint_promoted:
                try:
                    await artifact_storage.delete_checkpoint(
                        session.solution_id,
                        session.id,
                        turn.id,
                    )
                except Exception:  # noqa: BLE001 - preserve the checkpoint failure
                    logger.warning(
                        "Failed to delete an incomplete Builder workspace checkpoint",
                        extra={"turn_id": str(turn_id)},
                        exc_info=True,
                    )
            raise

    async def retry_external_agent_turn(
        self,
        *,
        turn_id: UUID,
        dispatch_attempt: int,
        error: str,
    ) -> PlatformJob | None:
        """Atomically requeue a transient sandbox fault without failing the turn."""
        from src.services.platform_jobs import (
            publish_platform_job_update,
            stage_external_platform_job_retry,
        )

        platform_job = await stage_external_platform_job_retry(
            self.db,
            turn_id,
            dispatch_attempt,
            error_message=error,
        )
        if platform_job is None:
            return None
        turn = await self.db.get(SolutionBuilderTurn, turn_id, with_for_update=True)
        if turn is None:
            return None
        turn.status = "queued"
        turn.error = None
        turn.completed_at = None
        await self.db.commit()
        await publish_platform_job_update(platform_job)
        try:
            await BuilderTurnArtifactStorage(turn_id, dispatch_attempt).delete()
        except Exception:  # noqa: BLE001 - retry state is already durable
            logger.warning(
                "Failed to delete staged Builder output before retry",
                extra={"turn_id": str(turn_id)},
                exc_info=True,
            )
        return platform_job

    async def _load_session(
        self,
        solution_id: UUID,
        session_id: UUID,
    ) -> SolutionBuilderSession:
        session = await self.db.get(SolutionBuilderSession, session_id)
        if session is None or session.solution_id != solution_id:
            raise BuilderProjectMissing(
                f"builder session {session_id} does not belong to Solution {solution_id}"
            )
        return session

    async def _latest_user_message(self, conversation_id: UUID) -> str:
        content = await self.db.scalar(
            select(Message.content)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.USER,
            )
            .order_by(Message.sequence.desc())
            .limit(1)
        )
        return content or "builder turn"

    async def _ensure_builder_agent(
        self,
        solution_id: UUID,
        session: SolutionBuilderSession,
    ) -> Conversation:
        solution = await self.db.get(Solution, solution_id)
        if solution is None:
            raise BuilderProjectMissing(f"Solution {solution_id} does not exist")
        conversation = (
            await self.db.execute(
                select(Conversation)
                .where(Conversation.id == session.conversation_id)
                .options(selectinload(Conversation.user))
            )
        ).scalar_one()
        agent = await ensure_builder_agent(self.db, solution=solution)
        conversation.agent_id = agent.id
        await self.db.commit()

        conversation = (
            await self.db.execute(
                select(Conversation)
                .where(Conversation.id == conversation.id)
                .options(selectinload(Conversation.user))
            )
        ).scalar_one()
        return conversation

    async def _append_message(
        self,
        conversation: Conversation,
        *,
        role: MessageRole,
        content: str,
        model: str | None = None,
        token_count_input: int | None = None,
        token_count_output: int | None = None,
        attachment_ids: list[UUID] | None = None,
        message_id: UUID | None = None,
        duration_ms: int | None = None,
    ) -> Message:
        await self.db.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtext('bifrost:conversation:' || :conversation_id))"
            ),
            {"conversation_id": str(conversation.id)},
        )
        max_sequence = await self.db.scalar(
            select(func.coalesce(func.max(Message.sequence), 0)).where(
                Message.conversation_id == conversation.id
            )
        )
        message = Message(
            id=message_id or uuid4(),
            conversation_id=conversation.id,
            role=role,
            content=content,
            sequence=int(max_sequence or 0) + 1,
            model=model,
            token_count_input=token_count_input,
            token_count_output=token_count_output,
            duration_ms=duration_ms,
        )
        self.db.add(message)
        if attachment_ids:
            from src.services.chat_attachments import ChatAttachmentService

            await ChatAttachmentService(self.db).bind(
                attachment_ids=attachment_ids,
                message_id=message.id,
                conversation_id=conversation.id,
            )
        conversation.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return message


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
