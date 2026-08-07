"""Run private-Solution builder turns through the platform AgentExecutor."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_session_factory
from src.core.principal import UserPrincipal
from src.models.enums import AgentAccessLevel, MessageRole
from src.models.orm.agents import Agent, Conversation, Message
from src.models.orm.solution_builder import SolutionBuilderSession, SolutionBuilderTurn
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.agent_executor import AgentExecutor
from src.services.builder.fs_tools import WorkspaceRoot
from src.services.builder.revision_storage import SolutionRevisionStorage
from src.services.builder.scaffold import (
    BUILDER_AGENT_SYSTEM_TOOLS,
    BUILDER_SKILL_BUNDLE_PATH,
    _builder_skill_source,
    builder_agent_id,
)
from src.services.builder.turns import BuilderProjectMissing, BuilderTurnService
from src.services.llm_config_service import LLMConfigService
from src.services.solutions.deploy_jobs import create_staged_deploy_job

_SUMMARY_MAX_CHARS = 120


class BuilderModelUnavailable(RuntimeError):
    """The configured builder Agent could not obtain a model response."""


@dataclass
class AgentTurnOutcome:
    turn: SolutionBuilderTurn
    final_text: str
    tool_call_count: int
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

    async def run_agent_turn(
        self,
        solution_id: UUID,
        *,
        session_id: UUID,
        requested_by: UUID | None,
        user_message: str,
    ) -> AgentTurnOutcome:
        session = await self._load_session(solution_id, session_id)
        agent, conversation, principal = await self._ensure_builder_agent(
            solution_id,
            session,
        )
        before_tool_calls = await self._tool_call_count(conversation.id)

        final_text: str | None = None

        async def mutate(workspace: WorkspaceRoot) -> None:
            nonlocal final_text
            executor = AgentExecutor(
                get_session_factory(),
                builder_workspace=workspace,
            )
            error: str | None = None
            async for chunk in executor.chat(
                agent,
                conversation,
                user_message,
                stream=False,
                enable_routing=False,
                user=principal,
            ):
                if chunk.type == "done":
                    final_text = chunk.content or ""
                elif chunk.type == "error":
                    error = chunk.error or "builder Agent failed"
            if error is not None:
                raise BuilderModelUnavailable(error)
            if final_text is None:
                raise BuilderModelUnavailable(
                    "builder Agent ended without a terminal response"
                )

        turn = await self.turns.run_turn(
            solution_id,
            session_id=session_id,
            requested_by=requested_by,
            mutate=mutate,
            summary=_revision_summary(user_message),
        )
        if final_text is None:
            raise BuilderProjectMissing(
                f"builder turn {turn.id} completed without running the Agent"
            )

        after_tool_calls = await self._tool_call_count(conversation.id)
        revision_created = turn.output_revision_id != turn.base_revision_id
        if revision_created and turn.output_revision_id is not None:
            await enqueue_builder_turn_deploy(
                self.db,
                solution_id,
                turn=turn,
                revision_id=turn.output_revision_id,
            )

        return AgentTurnOutcome(
            turn=turn,
            final_text=final_text,
            tool_call_count=max(0, after_tool_calls - before_tool_calls),
            revision_created=revision_created,
        )

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

    async def _ensure_builder_agent(
        self,
        solution_id: UUID,
        session: SolutionBuilderSession,
    ) -> tuple[Agent, Conversation, UserPrincipal]:
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

        agent = (
            await self.db.execute(
                select(Agent)
                .where(Agent.id == agent_id)
                .options(
                    selectinload(Agent.tools),
                    selectinload(Agent.delegated_agents),
                )
            )
        ).scalar_one()
        conversation = (
            await self.db.execute(
                select(Conversation)
                .where(Conversation.id == conversation.id)
                .options(selectinload(Conversation.user))
            )
        ).scalar_one()
        principal = UserPrincipal(
            user_id=user.id,
            email=user.email,
            name=user.name or "",
            organization_id=user.organization_id,
            is_superuser=user.is_superuser,
            is_external=user.is_external,
        )
        return agent, conversation, principal

    async def _tool_call_count(self, conversation_id: UUID) -> int:
        value = await self.db.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == MessageRole.TOOL_CALL,
            )
        )
        return int(value or 0)
