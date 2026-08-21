"""Conversation-scoped POSIX workspace backed by canonical artifacts."""

from __future__ import annotations

import mimetypes
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm import Artifact, Conversation
from src.services.artifacts import ArtifactService, artifact_ref
from src.services.authorization import AuthorizationContext
from src.services.builder.fs_tools import WorkspaceLimits, WorkspaceRoot
from src.services.builder.workspace_tool_runtime import (
    BuilderWorkspaceToolResult,
    execute_builder_workspace_tool,
)
from src.services.llm import ToolDefinition

CONVERSATION_WORKSPACE_TOOL_NAMES = frozenset(
    {
        "workspace_list_files",
        "workspace_read_file",
        "workspace_search_text",
        "workspace_write_file",
        "workspace_apply_patch",
        "workspace_delete_file",
    }
)

WORKSPACE_INSTRUCTIONS = """

You have a conversation workspace backed by uploaded and generated artifacts.
Use workspace_list_files/read/search/write/apply_patch/delete for file work.
Paths are relative to this conversation only. Files you create or change are
saved back as durable artifacts and can be reused in later turns.
"""

_CONVERSATION_TO_NEUTRAL_TOOL_NAME = {
    "workspace_list_files": "list_files",
    "workspace_read_file": "read_file",
    "workspace_search_text": "search_text",
    "workspace_write_file": "write_file",
    "workspace_apply_patch": "apply_patch",
    "workspace_delete_file": "delete_file",
}


def conversation_workspace_tool_definitions() -> list[ToolDefinition]:
    """Return model-visible tool definitions for conversation file work."""

    return [
        ToolDefinition(
            name="workspace_list_files",
            description="List files in the conversation artifact workspace.",
            parameters={"type": "object", "properties": {}, "required": []},
        ),
        ToolDefinition(
            name="workspace_read_file",
            description="Read a UTF-8 file from the conversation artifact workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        ToolDefinition(
            name="workspace_search_text",
            description="Regex-search text files in the conversation artifact workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "glob": {"type": "string", "default": "**/*"},
                },
                "required": ["pattern"],
            },
        ),
        ToolDefinition(
            name="workspace_write_file",
            description="Create or replace a conversation workspace file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        ),
        ToolDefinition(
            name="workspace_apply_patch",
            description="Replace an exact string in a conversation workspace file.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["path", "old_string", "new_string"],
            },
        ),
        ToolDefinition(
            name="workspace_delete_file",
            description="Delete a file from the conversation artifact workspace.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
    ]


def _content_type_for_path(path: str) -> str:
    return mimetypes.guess_type(path)[0] or "text/plain"


def _file_map(workspace: WorkspaceRoot) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in workspace.list_files():
        data, truncated = workspace.read_file(path)
        if truncated:
            data = (workspace.root / path).read_bytes()
        files[path] = data
    return files


def _artifact_display_payload(artifacts: Iterable[Artifact]) -> list[dict[str, Any]]:
    return [artifact_ref(artifact).model_dump(mode="json") for artifact in artifacts]


class ConversationWorkspaceService:
    """Hydrate one conversation's artifact workspace, execute, and persist."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        limits: WorkspaceLimits | None = None,
    ) -> None:
        self.db = db
        self.limits = limits or WorkspaceLimits()

    async def execute_tool(
        self,
        *,
        conversation: Conversation,
        tool_name: str,
        arguments: dict[str, Any],
        authorization_context: AuthorizationContext | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if tool_name not in CONVERSATION_WORKSPACE_TOOL_NAMES:
            raise ValueError(f"Unknown conversation workspace tool: {tool_name}")
        with tempfile.TemporaryDirectory(prefix="bifrost-chat-workspace-") as tmp:
            workspace = await self._hydrate(
                conversation,
                Path(tmp),
                authorization_context=authorization_context,
            )
            before = _file_map(workspace)
            result = await self._execute(workspace, tool_name, arguments)
            structured = result.structured_content or {}
            changed_artifacts = await self._persist_diff(
                conversation,
                before,
                _file_map(workspace),
            )
            if changed_artifacts:
                structured["artifacts"] = _artifact_display_payload(changed_artifacts)
            return result.display_content or result.content, structured

    async def _hydrate(
        self,
        conversation: Conversation,
        root: Path,
        *,
        authorization_context: AuthorizationContext | None = None,
    ) -> WorkspaceRoot:
        workspace = WorkspaceRoot(root, self.limits)
        user = conversation.user
        artifacts = await ArtifactService(self.db).list_workspace(
            conversation.id,
            user_id=conversation.user_id,
            organization_id=getattr(user, "organization_id", None),
            is_platform_admin=(
                authorization_context.has_capability("platform.superuser")
                if authorization_context is not None
                else False
            ),
        )
        service = ArtifactService(self.db)
        for artifact in artifacts:
            path = artifact.logical_path or artifact.filename
            workspace.write_file(path, await service.read(artifact))
        return workspace

    async def _execute(
        self,
        workspace: WorkspaceRoot,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> BuilderWorkspaceToolResult:
        neutral_name = _CONVERSATION_TO_NEUTRAL_TOOL_NAME.get(tool_name)
        if neutral_name is None:
            raise ValueError(f"Unknown conversation workspace tool: {tool_name}")
        return await execute_builder_workspace_tool(
            workspace=workspace,
            bundle_path=None,
            name=neutral_name,
            arguments=arguments,
        )

    async def _persist_diff(
        self,
        conversation: Conversation,
        before: dict[str, bytes],
        after: dict[str, bytes],
    ) -> list[Artifact]:
        service = ArtifactService(self.db)
        changed: list[Artifact] = []
        user = conversation.user
        organization_id = getattr(user, "organization_id", None)
        for path in sorted(set(before) - set(after)):
            await service.tombstone_workspace_path(
                conversation.id,
                path,
                created_by_user_id=conversation.user_id,
                organization_id=organization_id,
            )
        for path in sorted(after):
            if before.get(path) == after[path]:
                continue
            artifact = await service.store(
                filename=Path(path).name,
                content_type=_content_type_for_path(path),
                content=after[path],
                created_by_user_id=conversation.user_id,
                organization_id=organization_id,
                workspace_id=conversation.id,
                logical_path=path,
            )
            changed.append(artifact)
        return changed

__all__ = [
    "CONVERSATION_WORKSPACE_TOOL_NAMES",
    "WORKSPACE_INSTRUCTIONS",
    "ConversationWorkspaceService",
    "conversation_workspace_tool_definitions",
]
