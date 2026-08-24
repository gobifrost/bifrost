"""Conversation workspace tools persist through canonical artifacts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.models.contracts.agents import ToolCall, ToolResult
from src.models.contracts.artifacts import ArtifactRef
from src.services.agent_executor import AgentExecutor
from src.services.conversation_workspace import ConversationWorkspaceService


class _FakeArtifactService:
    artifacts: list[SimpleNamespace] = []
    tombstones: set[tuple] = set()

    def __init__(self, db) -> None:
        self.db = db

    async def list_workspace(
        self,
        workspace_id,
        *,
        user_id,
        organization_id,
        is_platform_admin=False,
    ):
        latest = {}
        for artifact in reversed(self.artifacts):
            if artifact.workspace_id != workspace_id:
                continue
            if (artifact.workspace_id, artifact.logical_path) in self.tombstones:
                continue
            if not is_platform_admin and artifact.created_by_user_id != user_id:
                continue
            if (
                not is_platform_admin
                and organization_id is not None
                and artifact.organization_id != organization_id
            ):
                continue
            latest.setdefault(artifact.logical_path, artifact)
        return list(latest.values())

    async def read(self, artifact):
        return artifact.content

    async def store(
        self,
        *,
        filename,
        content_type,
        content,
        created_by_user_id,
        organization_id,
        workspace_id,
        logical_path,
        **_kwargs,
    ):
        artifact = SimpleNamespace(
            id=uuid4(),
            filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            created_by_user_id=created_by_user_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
            logical_path=logical_path,
            content=content,
        )
        self.tombstones.discard((workspace_id, logical_path))
        self.artifacts.append(artifact)
        return artifact

    async def tombstone_workspace_path(
        self,
        workspace_id,
        path,
        *,
        created_by_user_id,
        organization_id,
    ):
        del created_by_user_id, organization_id
        self.tombstones.add((workspace_id, path))
        return SimpleNamespace(workspace_id=workspace_id, logical_path=path)


def _conversation(conversation_id=None, user_id=None, org_id=None):
    return SimpleNamespace(
        id=conversation_id or uuid4(),
        user_id=user_id or uuid4(),
        user=SimpleNamespace(
            id=user_id or uuid4(),
            organization_id=org_id,
            is_superuser=False,
        ),
    )


@pytest.fixture(autouse=True)
def fake_services(monkeypatch: pytest.MonkeyPatch):
    _FakeArtifactService.artifacts = []
    _FakeArtifactService.tombstones = set()
    monkeypatch.setattr(
        "src.services.conversation_workspace.ArtifactService",
        _FakeArtifactService,
    )


@pytest.mark.asyncio
async def test_hydrates_uploaded_artifact_and_searches_text() -> None:
    user_id = uuid4()
    org_id = uuid4()
    conversation = _conversation(user_id=user_id, org_id=org_id)
    _FakeArtifactService.artifacts.append(
        SimpleNamespace(
            id=uuid4(),
            filename="notes.md",
            content_type="text/markdown",
            size_bytes=13,
            created_by_user_id=user_id,
            organization_id=org_id,
            workspace_id=conversation.id,
            logical_path="notes.md",
            content=b"# Notes\nhello",
        )
    )

    content, structured = await ConversationWorkspaceService(MagicMock()).execute_tool(
        conversation=conversation,
        tool_name="workspace_search_text",
        arguments={"pattern": "hello"},
    )

    assert "notes.md:2: hello" in content
    assert structured["matches"] == [
        {"path": "notes.md", "line_number": 2, "line": "hello"}
    ]


@pytest.mark.asyncio
async def test_write_persists_artifact_ref_for_later_turn() -> None:
    user_id = uuid4()
    org_id = uuid4()
    conversation = _conversation(user_id=user_id, org_id=org_id)
    db = MagicMock()

    _content, structured = await ConversationWorkspaceService(db).execute_tool(
        conversation=conversation,
        tool_name="workspace_write_file",
        arguments={"path": "reports/summary.md", "content": "final"},
    )

    assert structured["artifacts"][0]["type"] == "bifrost_artifact"
    assert structured["artifacts"][0]["filename"] == "summary.md"
    assert _FakeArtifactService.artifacts[-1].logical_path == "reports/summary.md"
    assert _FakeArtifactService.artifacts[-1].content == b"final"

    content, structured = await ConversationWorkspaceService(db).execute_tool(
        conversation=conversation,
        tool_name="workspace_read_file",
        arguments={"path": "reports/summary.md"},
    )

    assert content == "final"
    assert structured["content"] == "final"


@pytest.mark.asyncio
async def test_workspace_does_not_leak_across_conversations() -> None:
    user_id = uuid4()
    org_id = uuid4()
    first = _conversation(user_id=user_id, org_id=org_id)
    second = _conversation(user_id=user_id, org_id=org_id)
    _FakeArtifactService.artifacts.append(
        SimpleNamespace(
            id=uuid4(),
            filename="private.txt",
            content_type="text/plain",
            size_bytes=6,
            created_by_user_id=user_id,
            organization_id=org_id,
            workspace_id=first.id,
            logical_path="private.txt",
            content=b"secret",
        )
    )

    content, structured = await ConversationWorkspaceService(MagicMock()).execute_tool(
        conversation=second,
        tool_name="workspace_list_files",
        arguments={},
    )

    assert content == "(workspace is empty)"
    assert structured == {"paths": []}


@pytest.mark.asyncio
async def test_workspace_does_not_use_legacy_superuser_bit_without_authorization_context() -> None:
    owner_id = uuid4()
    other_user_id = uuid4()
    org_id = uuid4()
    conversation = _conversation(user_id=owner_id, org_id=org_id)
    conversation.user.is_superuser = True
    _FakeArtifactService.artifacts.append(
        SimpleNamespace(
            id=uuid4(),
            filename="other.txt",
            content_type="text/plain",
            size_bytes=6,
            created_by_user_id=other_user_id,
            organization_id=org_id,
            workspace_id=conversation.id,
            logical_path="other.txt",
            content=b"secret",
        )
    )

    content, structured = await ConversationWorkspaceService(MagicMock()).execute_tool(
        conversation=conversation,
        tool_name="workspace_list_files",
        arguments={},
    )

    assert content == "(workspace is empty)"
    assert structured == {"paths": []}


@pytest.mark.asyncio
async def test_workspace_uses_canonical_platform_superuser_capability() -> None:
    owner_id = uuid4()
    other_user_id = uuid4()
    org_id = uuid4()
    conversation = _conversation(user_id=owner_id, org_id=org_id)
    authorization = SimpleNamespace(
        has_capability=lambda capability: capability == "platform.superuser",
    )
    _FakeArtifactService.artifacts.append(
        SimpleNamespace(
            id=uuid4(),
            filename="other.txt",
            content_type="text/plain",
            size_bytes=6,
            created_by_user_id=other_user_id,
            organization_id=org_id,
            workspace_id=conversation.id,
            logical_path="other.txt",
            content=b"secret",
        )
    )

    content, structured = await ConversationWorkspaceService(MagicMock()).execute_tool(
        conversation=conversation,
        tool_name="workspace_list_files",
        arguments={},
        authorization_context=authorization,
    )

    assert content == "other.txt"
    assert structured == {"paths": ["other.txt"]}


@pytest.mark.asyncio
async def test_delete_hides_path_without_deleting_old_artifact_ref() -> None:
    user_id = uuid4()
    conversation = _conversation(user_id=user_id)
    old = SimpleNamespace(
        id=uuid4(),
        filename="draft.txt",
        content_type="text/plain",
        size_bytes=3,
        created_by_user_id=user_id,
        organization_id=None,
        workspace_id=conversation.id,
        logical_path="draft.txt",
        content=b"old",
    )
    new = SimpleNamespace(
        id=uuid4(),
        filename="draft.txt",
        content_type="text/plain",
        size_bytes=3,
        created_by_user_id=user_id,
        organization_id=None,
        workspace_id=conversation.id,
        logical_path="draft.txt",
        content=b"new",
    )
    _FakeArtifactService.artifacts.extend([old, new])
    db = MagicMock()

    await ConversationWorkspaceService(db).execute_tool(
        conversation=conversation,
        tool_name="workspace_delete_file",
        arguments={"path": "draft.txt"},
    )

    assert _FakeArtifactService.artifacts == [old, new]
    assert await _FakeArtifactService(db).read(old) == b"old"
    assert await _FakeArtifactService(db).read(new) == b"new"

    content, structured = await ConversationWorkspaceService(db).execute_tool(
        conversation=conversation,
        tool_name="workspace_list_files",
        arguments={},
    )

    assert content == "(workspace is empty)"
    assert structured == {"paths": []}


@pytest.mark.asyncio
async def test_rewriting_tombstoned_path_restores_workspace_visibility() -> None:
    user_id = uuid4()
    conversation = _conversation(user_id=user_id)
    old = SimpleNamespace(
        id=uuid4(),
        filename="draft.txt",
        content_type="text/plain",
        size_bytes=3,
        created_by_user_id=user_id,
        organization_id=None,
        workspace_id=conversation.id,
        logical_path="draft.txt",
        content=b"old",
    )
    _FakeArtifactService.artifacts.append(old)
    db = MagicMock()

    await ConversationWorkspaceService(db).execute_tool(
        conversation=conversation,
        tool_name="workspace_delete_file",
        arguments={"path": "draft.txt"},
    )
    await ConversationWorkspaceService(db).execute_tool(
        conversation=conversation,
        tool_name="workspace_write_file",
        arguments={"path": "draft.txt", "content": "new"},
    )

    content, structured = await ConversationWorkspaceService(db).execute_tool(
        conversation=conversation,
        tool_name="workspace_read_file",
        arguments={"path": "draft.txt"},
    )

    assert content == "new"
    assert structured["content"] == "new"
    assert _FakeArtifactService.artifacts[0] is old


@pytest.mark.asyncio
async def test_executor_promotes_workspace_artifact_refs_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = ArtifactRef(
        id=str(uuid4()),
        filename="summary.md",
        content_type="text/markdown",
        size_bytes=5,
    )
    promoted = ArtifactRef(
        id=artifact.id,
        filename=artifact.filename,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
    )
    promote = AsyncMock(return_value=[promoted])
    monkeypatch.setattr("src.services.chat_artifacts.promote_artifact_refs", promote)
    executor = AgentExecutor(MagicMock())
    db = MagicMock()

    @asynccontextmanager
    async def _db():
        yield db

    executor._db = _db
    executor._update_tool_call_message = AsyncMock()
    executor._save_message = AsyncMock()
    conversation = SimpleNamespace(id=uuid4(), user_id=uuid4())
    message_id = uuid4()

    _model_content, chunks = await executor.complete_tool_call(
        agent=None,
        conversation=conversation,
        tool_call=ToolCall(
            id="call-1",
            name="workspace_write_file",
            arguments={"path": "summary.md", "content": "final"},
        ),
        message_id=message_id,
        execution_id="exec-1",
        tool_result=ToolResult(
            tool_call_id="call-1",
            tool_name="workspace_write_file",
            result={"artifacts": [artifact.model_dump(mode="json")]},
        ),
    )

    promote.assert_awaited_once()
    artifact_ready = [chunk for chunk in chunks if chunk.type == "artifact_ready"]
    assert len(artifact_ready) == 1
    assert artifact_ready[0].artifact == promoted
