"""Orphan recreation must carry verified durable bytes without early commit."""
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.orm.file_index import FileIndex
from src.models.orm.workflows import Workflow
from src.services.workflow_orphan import WorkflowOrphanService


@pytest.mark.parametrize("digest", [None, "0" * 64, "matching"])
async def test_recreation_checks_durable_bytes_and_keeps_transaction_open(monkeypatch, digest):
    content = b"def main(): return 'durable'\n"
    wf = SimpleNamespace(id=uuid4(), solution_id=None, is_orphaned=True, path="workflows/restore.py")
    indexed = SimpleNamespace(content_hash=hashlib.sha256(content).hexdigest() if digest == "matching" else digest)
    db = SimpleNamespace(get=AsyncMock(side_effect=[wf, indexed]), commit=AsyncMock())
    read = AsyncMock(return_value=content)
    monkeypatch.setattr("src.services.repo_storage.RepoStorage.read", read)
    if digest == "matching":
        result = await WorkflowOrphanService(db).recreate_file(wf.id)
        assert result.content is content
        assert result.workflow is wf
        assert wf.is_orphaned is False
    else:
        with pytest.raises(ValueError, match="fingerprint|changed"):
            await WorkflowOrphanService(db).recreate_file(wf.id)
        assert wf.is_orphaned is True
    db.commit.assert_not_awaited()
    assert db.get.await_args_list[0].args == (Workflow, wf.id)
    assert db.get.await_args_list[0].kwargs == {"with_for_update": True}
    assert db.get.await_args_list[1].args == (FileIndex, wf.path)


@pytest.mark.parametrize("write_fails", [False, True])
async def test_recreation_router_commits_only_after_persistence(monkeypatch, write_fails):
    from fastapi import HTTPException
    from src.routers import workflows as router
    from src.services.workflow_orphan import RecreatedWorkflowSource

    wf = SimpleNamespace(id=uuid4(), path="workflows/recover.py")
    content = b"def recover(): pass\n"
    prepared = RecreatedWorkflowSource(wf, content)
    db = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr(router, "_get_workflow_or_404", AsyncMock(return_value=wf))
    monkeypatch.setattr(router, "_require_workflow_permission", AsyncMock())
    monkeypatch.setattr(router, "assert_entity_id_not_solution_managed", AsyncMock())
    monkeypatch.setattr(WorkflowOrphanService, "recreate_file", AsyncMock(return_value=prepared))

    async def write(**kwargs):
        db.commit.assert_not_awaited()
        assert kwargs["content"] is content
        if write_fails:
            raise RuntimeError("source store unavailable")
        return SimpleNamespace(pending_deactivations=None)

    monkeypatch.setattr("src.services.file_storage.FileStorageService.write_file", AsyncMock(side_effect=write))
    user = SimpleNamespace(email="recovery@example.test")
    if write_fails:
        with pytest.raises(HTTPException) as exc:
            await router.recreate_workflow_file(wf.id, None, user, db)
        assert exc.value.status_code == 500
        db.commit.assert_not_awaited()
    else:
        result = await router.recreate_workflow_file(wf.id, None, user, db)
        assert result.success is True
        db.commit.assert_awaited_once()
