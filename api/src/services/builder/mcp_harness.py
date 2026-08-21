"""MCP gateway bridge for external harnesses driving Builder workspaces."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import pydantic_core
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.orm.solution_builder import (
    SolutionBuilderProject,
    SolutionBuilderSession,
)
from src.services.builder.agent_identity import (
    BuilderRuntimeProfile,
    build_builder_runtime_profile,
)
from src.services.builder.fs_tools import (
    WorkspaceLimits,
    WorkspaceRoot,
    WorkspaceViolation,
    safe_extract_zip,
)
from src.services.builder.revision_storage import SolutionRevisionStorage
from src.services.builder.runtime_authorization import (
    BuilderRuntimeForbidden,
    authorize_builder_project,
)
from src.services.builder.scaffold import strip_legacy_builder_assets
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


@dataclass(frozen=True, slots=True)
class AuthorizedBuilderSession:
    session: SolutionBuilderSession
    profile: BuilderRuntimeProfile
    principal: UserPrincipal


class BuilderMCPHarness:
    """Execute existing Builder workspace tools from the progressive MCP gateway."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        limits: WorkspaceLimits | None = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.limits = limits or WorkspaceLimits()

    async def execute(
        self,
        *,
        tool_name: str,
        builder_session_id: UUID,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            workspace_arguments = dict(arguments)
            finalize = workspace_arguments.pop("finalize", False) is True
            authorized = await self._authorize(
                builder_session_id,
                tool_name,
                finalize=finalize,
            )
            if tool_name in NON_MUTATING_BUILDER_TOOLS:
                if finalize:
                    raise BuilderMCPHarnessError(
                        "finalize is supported only by mutating Builder tools"
                    )
                return await self._read_only(
                    authorized=authorized,
                    tool_name=tool_name,
                    arguments=workspace_arguments,
                )
            if tool_name in MUTATING_BUILDER_TOOLS:
                return await self._mutate(
                    authorized=authorized,
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

    async def load_authorized_profile(
        self,
        *,
        builder_session_id: UUID,
        action: SolutionAction = SolutionAction.VIEW,
        required_capabilities: tuple[str, ...] | None = None,
    ) -> AuthorizedBuilderSession:
        """Load one session and its maintained coding profile through central auth."""
        session = await self.db.get(SolutionBuilderSession, builder_session_id)
        if session is None:
            raise BuilderMCPHarnessForbidden("Builder session not found")
        project = await self.db.get(SolutionBuilderProject, session.solution_id)
        if project is None:
            raise BuilderMCPHarnessForbidden("Builder session not found")
        if required_capabilities is None:
            required_capabilities = ("builder.read",)
            if project.target_kind == "solution":
                required_capabilities += ("solutions.read",)
        try:
            authorized = await authorize_builder_project(
                self.db,
                solution_id=session.solution_id,
                requester_user_id=self.user_id,
                action=action,
                required_capabilities=required_capabilities,
            )
        except BuilderRuntimeForbidden as exc:
            raise BuilderMCPHarnessForbidden("Builder session not found") from exc
        return AuthorizedBuilderSession(
            session=session,
            profile=build_builder_runtime_profile(
                authorized.solution,
                target_kind=authorized.project.target_kind,
                authorization=authorized.authorization,
            ),
            principal=authorized.principal,
        )

    async def _authorize(
        self,
        builder_session_id: UUID,
        tool_name: str,
        *,
        finalize: bool,
    ) -> AuthorizedBuilderSession:
        read_only = tool_name in READ_ONLY_BUILDER_TOOLS or tool_name == (
            "bifrost_read_agent_skill_file"
        )
        session = await self.db.get(SolutionBuilderSession, builder_session_id)
        project = (
            await self.db.get(SolutionBuilderProject, session.solution_id)
            if session is not None
            else None
        )
        if project is None:
            raise BuilderMCPHarnessForbidden("Builder session not found")
        if project.target_kind == "organization" and tool_name not in {
            "bifrost_read_agent_skill_file"
        }:
            raise BuilderMCPHarnessForbidden(
                "Organization targets do not expose source-workspace tools"
            )
        capabilities = ["builder.read" if read_only else "builder.execute"]
        if project.target_kind == "solution":
            capabilities.append(
                "solutions.read" if read_only else "solutions.readwrite"
            )
        if tool_name == TEST_SOLUTION_BUILD_TOOL_ID:
            capabilities.append("solutions.build.execute")
        if finalize:
            capabilities.extend(
                ["solutions.build.execute", "solutions.deploy.execute"]
            )
        return await self.load_authorized_profile(
            builder_session_id=builder_session_id,
            action=SolutionAction.VIEW if read_only else SolutionAction.BUILD,
            required_capabilities=tuple(dict.fromkeys(capabilities)),
        )

    async def skill_package(
        self,
        *,
        builder_session_id: UUID,
    ) -> tuple[str, list[str]]:
        """Read the maintained Skill instructions and companion file names."""
        authorized = await self._authorize(
            builder_session_id,
            "bifrost_read_agent_skill_file",
            finalize=False,
        )
        root = authorized.profile.skill_asset_root
        if root is None:
            raise BuilderMCPHarnessError("Builder profile has no Skill bundle")
        files = sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and path.relative_to(root).as_posix() != "SKILL.md"
        )
        return authorized.profile.system_prompt, files

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
            strip_legacy_builder_assets(
                workspace_path,
                solution_id=session.solution_id,
            )
            yield WorkspaceRoot(workspace_path, self.limits)

    async def _read_only(
        self,
        *,
        authorized: AuthorizedBuilderSession,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        session = authorized.session
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
                authorized=authorized,
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
        authorized: AuthorizedBuilderSession,
        tool_name: str,
        arguments: dict[str, Any],
        finalize: bool,
    ) -> dict[str, Any]:
        session = authorized.session
        captured: dict[str, Any] = {}

        async def mutate(workspace: WorkspaceRoot) -> None:
            captured.update(
                await self._call_workspace_tool(
                    authorized=authorized,
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

            await self._authorize(
                session.id,
                tool_name,
                finalize=True,
            )
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
        authorized: AuthorizedBuilderSession,
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
        profile = authorized.profile
        principal = authorized.principal
        context = MCPContext(
            user_id=principal.user_id,
            org_id=principal.organization_id,
            is_platform_admin=principal.is_platform_admin,
            is_external=principal.is_external,
            user_email=principal.email,
            user_name=principal.name,
            agent_bundle_path=profile.bundle_path,
            agent_solution_id=profile.solution_id,
            agent_skill_root=profile.skill_asset_root,
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
