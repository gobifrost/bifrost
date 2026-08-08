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

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.enums import AgentAccessLevel, MessageRole
from src.models.orm.agents import Agent, Conversation, Message
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
    SolutionBuilderTurn,
)
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.builder.revision_storage import SolutionRevisionStorage
from src.services.builder.scaffold import (
    BUILDER_AGENT_SYSTEM_TOOLS,
    BUILDER_SKILL_BUNDLE_PATH,
    _builder_skill_source,
    builder_agent_id,
)
from src.services.builder.turns import (
    BuilderProjectMissing,
    BuilderTurnConflict,
    BuilderTurnService,
)
from src.services.builder.turn_artifacts import BuilderTurnArtifactStorage
from src.services.llm_config_service import LLMConfigService
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

        await self._append_message(
            conversation,
            role=MessageRole.USER,
            content=user_message,
        )
        turn_id = uuid4()
        turn = SolutionBuilderTurn(
            id=turn_id,
            session_id=session_id,
            requested_by=requested_by,
            base_revision_id=project.current_revision_id,
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
        model: str | None = None,
        token_count_input: int | None = None,
        token_count_output: int | None = None,
    ) -> CompletedAgentTurn:
        """Atomically accept one fenced sandbox result and restore chat state."""
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
            await self._append_message(
                conversation,
                role=MessageRole.ASSISTANT,
                content=final_text,
                model=model,
                token_count_input=token_count_input,
                token_count_output=token_count_output,
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
    ) -> PlatformJob:
        """Persist one failed/cancelled sandbox result under attempt fencing."""
        if status not in {"failed", "cancelled"}:
            raise ValueError(f"Unsupported Builder turn status: {status}")
        turn = await self.db.get(SolutionBuilderTurn, turn_id, with_for_update=True)
        if turn is None:
            raise BuilderProjectMissing(f"Builder turn {turn_id} does not exist")

        from src.services.platform_jobs import (
            publish_platform_job_update,
            stage_external_platform_job_completion,
        )

        platform_job = await stage_external_platform_job_completion(
            self.db,
            turn_id,
            dispatch_attempt,
            status=status,
            result={"turn_id": str(turn_id)},
            error_message=error if status == "failed" else None,
        )
        if platform_job is None:
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
        user = conversation.user

        prompt = (_builder_skill_source() / "SKILL.md").read_text(encoding="utf-8")
        config = await LLMConfigService(self.db).get_config()
        builder_model = (
            config.builder_model.strip()
            if config is not None
            and isinstance(config.builder_model, str)
            and config.builder_model.strip()
            else None
        )
        agent_id = builder_agent_id(solution_id)
        existing = await self.db.get(Agent, agent_id)
        values: dict[str, Any] = {
            "name": f"{solution.name} Builder",
            "description": "Private Solution authoring agent",
            "system_prompt": prompt,
            "bundle_path": BUILDER_SKILL_BUNDLE_PATH,
            "channels": ["chat"],
            "access_level": AgentAccessLevel.ROLE_BASED,
            "organization_id": solution.organization_id,
            "solution_id": solution_id,
            "owner_user_id": session.user_id,
            "is_active": True,
            "knowledge_sources": [],
            "system_tools": BUILDER_AGENT_SYSTEM_TOOLS,
            "llm_model": builder_model,
            "created_by": user.email,
        }
        if existing is None:
            self.db.add(Agent(id=agent_id, **values))
        else:
            await self.db.execute(
                update(Agent).where(Agent.id == agent_id).values(**values)
            )
        conversation.agent_id = agent_id
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
            id=uuid4(),
            conversation_id=conversation.id,
            role=role,
            content=content,
            sequence=int(max_sequence or 0) + 1,
            model=model,
            token_count_input=token_count_input,
            token_count_output=token_count_output,
        )
        self.db.add(message)
        conversation.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        return message


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
