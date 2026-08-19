"""MCP gateway bridge for external harnesses driving Builder workspaces."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

import pydantic_core
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.orm.agents import Agent
from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
)
from src.services.builder.fs_tools import (
    WorkspaceLimits,
    WorkspaceRoot,
    WorkspaceViolation,
    safe_extract_zip,
)
from src.services.builder.revision_storage import SolutionRevisionStorage
from src.services.builder.scaffold import builder_agent_id
from src.services.builder.turns import (
    BuilderProjectMissing,
    BuilderTurnError,
    BuilderTurnService,
)
from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools.builder_workspace import (
    BUILDER_BIFROST_TOOL_IDS,
    BUILDER_WORKSPACE_TOOL_IDS,
    TEST_SOLUTION_BUILD_TOOL_ID,
)
from src.services.solutions.access import SolutionAction

READ_ONLY_BUILDER_TOOLS = frozenset(
    {
        "list_files",
        "read_file",
        "search_text",
        "validate_solution",
        "bifrost_read_agent_skill_file",
    }
)
NON_MUTATING_BUILDER_TOOLS = READ_ONLY_BUILDER_TOOLS | BUILDER_BIFROST_TOOL_IDS
MUTATING_BUILDER_TOOLS = frozenset(BUILDER_WORKSPACE_TOOL_IDS) - READ_ONLY_BUILDER_TOOLS
BUILDER_TOOL_IDS = (
    frozenset(BUILDER_WORKSPACE_TOOL_IDS)
    | BUILDER_BIFROST_TOOL_IDS
    | {"bifrost_read_agent_skill_file"}
)


class BuilderMCPHarnessError(Exception):
    """An external MCP Builder workspace call is invalid or unauthorized."""


class BuilderMCPHarnessForbidden(BuilderMCPHarnessError):
    """Caller cannot access the requested Builder session."""


class BuilderMCPHarness:
    """Execute existing Builder workspace tools from the progressive MCP gateway."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        org_id: UUID | None,
        is_platform_admin: bool,
        is_external: bool,
        user_email: str,
        user_name: str,
        can_build: bool = False,
        can_support_builds: bool = False,
        limits: WorkspaceLimits | None = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.org_id = org_id
        self.is_platform_admin = is_platform_admin
        self.is_external = is_external
        self.user_email = user_email
        self.user_name = user_name
        self.can_build = can_build
        self.can_support_builds = can_support_builds
        self.limits = limits or WorkspaceLimits()

    async def execute(
        self,
        *,
        agent: Agent,
        tool_name: str,
        builder_session_id: UUID,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            workspace_arguments = dict(arguments)
            finalize = workspace_arguments.pop("finalize", False) is True
            session = await self._authorize(agent, builder_session_id, tool_name)
            if tool_name in NON_MUTATING_BUILDER_TOOLS:
                if finalize:
                    raise BuilderMCPHarnessError(
                        "finalize is supported only by mutating Builder tools"
                    )
                return await self._read_only(
                    session=session,
                    agent=agent,
                    tool_name=tool_name,
                    arguments=workspace_arguments,
                )
            if tool_name in MUTATING_BUILDER_TOOLS:
                return await self._mutate(
                    session=session,
                    agent=agent,
                    tool_name=tool_name,
                    arguments=workspace_arguments,
                    finalize=finalize,
                )
            raise BuilderMCPHarnessError(
                f"{tool_name} is not a Builder workspace tool"
            )
        except BuilderMCPHarnessError:
            raise
        except (BuilderTurnError, WorkspaceViolation) as exc:
            raise BuilderMCPHarnessError(str(exc)) from exc

    async def load_authorized_agent(
        self,
        *,
        builder_session_id: UUID,
        agent_id: UUID | None = None,
        action: SolutionAction = SolutionAction.VIEW,
    ) -> tuple[Agent, SolutionBuilderSession]:
        """Load the deterministic Builder Agent through the private access gate."""
        from src.services.builder.private_solutions import (
            load_accessible_private_solution,
        )

        if not self.can_build:
            raise BuilderMCPHarnessForbidden(
                "The solutions.build scope is required for Builder MCP access"
            )
        session = await self.db.get(SolutionBuilderSession, builder_session_id)
        if session is None:
            raise BuilderMCPHarnessForbidden("Builder session not found")
        expected_agent_id = builder_agent_id(session.solution_id)
        if agent_id is not None and agent_id != expected_agent_id:
            raise BuilderMCPHarnessForbidden(
                "Builder session does not belong to the selected Builder agent"
            )
        loaded = await load_accessible_private_solution(
            self.db,
            solution_id=session.solution_id,
            action=action,
            actor_user_id=self.user_id,
            is_platform_admin=self.is_platform_admin,
            is_external=self.is_external,
            can_support=self.can_support_builds,
        )
        if loaded is None:
            raise BuilderMCPHarnessForbidden("Builder session not found")
        agent = await self.db.scalar(
            select(Agent)
            .where(
                Agent.id == expected_agent_id,
                Agent.solution_id == session.solution_id,
                Agent.is_active.is_(True),
            )
            .options(
                selectinload(Agent.tools),
                selectinload(Agent.delegated_agents),
                selectinload(Agent.roles),
                selectinload(Agent.owner),
                selectinload(Agent.mcp_connections),
            )
        )
        if agent is None:
            raise BuilderMCPHarnessForbidden("Builder session not found")
        return agent, session

    async def _authorize(
        self,
        agent: Agent,
        builder_session_id: UUID,
        tool_name: str,
    ) -> SolutionBuilderSession:
        action = (
            SolutionAction.VIEW
            if tool_name in READ_ONLY_BUILDER_TOOLS
            else SolutionAction.EDIT
        )
        _loaded_agent, session = await self.load_authorized_agent(
            builder_session_id=builder_session_id,
            agent_id=agent.id,
            action=action,
        )
        if agent.solution_id != session.solution_id:
            raise BuilderMCPHarnessForbidden(
                "Builder session does not belong to the selected Builder agent"
            )
        return session

    async def skill_package(
        self,
        *,
        agent: Agent,
        builder_session_id: UUID,
    ) -> tuple[str, list[str]]:
        """Read current-revision Skill instructions and companion file names."""
        session = await self._authorize(
            agent,
            builder_session_id,
            "bifrost_read_agent_skill_file",
        )
        if not agent.bundle_path:
            raise BuilderMCPHarnessError("Builder agent has no Skill bundle")
        async with self._materialized_workspace(session) as workspace:
            skill_path = f"{agent.bundle_path.rstrip('/')}/SKILL.md"
            markdown, truncated = workspace.read_file(skill_path)
            if truncated:
                raise BuilderMCPHarnessError("Builder SKILL.md exceeds the read limit")
            prefix = agent.bundle_path.rstrip("/") + "/"
            files = [
                path.removeprefix(prefix)
                for path in workspace.list_files()
                if path.startswith(prefix) and path != skill_path
            ]
        try:
            return markdown.decode("utf-8"), files
        except UnicodeDecodeError as exc:
            raise BuilderMCPHarnessError("Builder SKILL.md must be UTF-8") from exc

    @asynccontextmanager
    async def _materialized_workspace(
        self,
        session: SolutionBuilderSession,
    ) -> AsyncIterator[WorkspaceRoot]:
        project = await self.db.get(SolutionBuilderProject, session.solution_id)
        if project is None or project.current_revision_id is None:
            raise BuilderProjectMissing(
                f"Solution {session.solution_id} has no current revision"
            )
        storage = SolutionRevisionStorage(session.solution_id)
        with tempfile.TemporaryDirectory(prefix="bifrost-builder-mcp-") as name:
            scratch = Path(name)
            os.chmod(scratch, 0o700)
            source_zip = scratch / "source.zip"
            if not await storage.copy_to_path(project.current_revision_id, source_zip):
                raise BuilderProjectMissing(
                    f"revision {project.current_revision_id} content is missing"
                )
            workspace_path = scratch / "workspace"
            workspace_path.mkdir(mode=0o700)
            safe_extract_zip(source_zip, workspace_path, self.limits)
            yield WorkspaceRoot(workspace_path, self.limits)

    async def _read_only(
        self,
        *,
        session: SolutionBuilderSession,
        agent: Agent,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        project = await self.db.get(SolutionBuilderProject, session.solution_id)
        if project is None or project.current_revision_id is None:
            raise BuilderProjectMissing(
                f"Solution {session.solution_id} has no current revision"
            )
        async with self._materialized_workspace(session) as workspace:
            if tool_name == TEST_SOLUTION_BUILD_TOOL_ID:
                # Building may wait on a durable worker/Cloudflare job. Release
                # the authorization read transaction before that long wait.
                await self.db.commit()
            return await self._call_workspace_tool(
                agent=agent,
                workspace=workspace,
                tool_name=tool_name,
                arguments=arguments,
                revision_id=project.current_revision_id,
                session_id=session.id,
                changed=False,
            )

    async def _mutate(
        self,
        *,
        session: SolutionBuilderSession,
        agent: Agent,
        tool_name: str,
        arguments: dict[str, Any],
        finalize: bool,
    ) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        async def mutate(workspace: WorkspaceRoot) -> None:
            captured.update(
                await self._call_workspace_tool(
                    agent=agent,
                    workspace=workspace,
                    tool_name=tool_name,
                    arguments=arguments,
                    revision_id=None,
                    session_id=session.id,
                    changed=True,
                )
            )

        turn = await BuilderTurnService(self.db, limits=self.limits).run_turn(
            session.solution_id,
            session_id=session.id,
            requested_by=self.user_id,
            mutate=mutate,
            summary=f"MCP {tool_name}",
        )
        deploy_job_id: UUID | None = None
        revision_created = turn.output_revision_id != turn.base_revision_id
        if finalize and revision_created and turn.output_revision_id is not None:
            from src.services.builder.agent_turns import enqueue_builder_turn_deploy

            await enqueue_builder_turn_deploy(
                self.db,
                session.solution_id,
                turn=turn,
                revision_id=turn.output_revision_id,
            )
            deploy_job_id = turn.deploy_job_id
        else:
            await self.db.commit()

        return {
            **captured,
            "builder_session_id": str(session.id),
            "turn_id": str(turn.id),
            "base_revision_id": str(turn.base_revision_id),
            "revision_id": (
                str(turn.output_revision_id) if turn.output_revision_id else None
            ),
            "revision_created": revision_created,
            "finalized": finalize and revision_created,
            "deploy_job_id": str(deploy_job_id) if deploy_job_id else None,
        }

    async def _call_workspace_tool(
        self,
        *,
        agent: Agent,
        workspace: WorkspaceRoot,
        tool_name: str,
        arguments: dict[str, Any],
        revision_id: UUID | None,
        session_id: UUID,
        changed: bool,
    ) -> dict[str, Any]:
        from src.services.mcp_server.server import get_system_tool_function

        func = get_system_tool_function(tool_name)
        if func is None:
            raise BuilderMCPHarnessError(f"Builder tool '{tool_name}' is unavailable")
        context = MCPContext(
            user_id=self.user_id,
            org_id=self.org_id,
            is_platform_admin=self.is_platform_admin,
            is_external=self.is_external,
            user_email=self.user_email,
            user_name=self.user_name,
            agent_bundle_path=agent.bundle_path,
            agent_solution_id=agent.solution_id,
            agent_skill_id=agent.id if agent.bundle_path else None,
            builder_workspace=workspace,
        )
        result = await func(context, **arguments)
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict) and structured.get("error"):
            raise BuilderMCPHarnessError(str(structured["error"]))
        payload = {
            "content": pydantic_core.to_jsonable_python(
                getattr(result, "content", result),
                fallback=str,
            ),
            "structured_content": pydantic_core.to_jsonable_python(
                structured,
                fallback=str,
            ),
            "builder_session_id": str(session_id),
            "revision_id": str(revision_id) if revision_id else None,
            "revision_created": changed,
        }
        return payload
