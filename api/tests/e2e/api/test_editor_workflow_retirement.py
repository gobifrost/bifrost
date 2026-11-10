"""Workspace deletion retires executable metadata without touching Solutions."""

import hashlib
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from src.core.module_cache import get_module, invalidate_module, set_module
from src.models.orm.file_index import FileIndex
from src.models.orm.solutions import Solution
from src.models.orm.workflows import Workflow
from src.services.file_index_service import FileIndexService
from src.services.execution.service import WorkflowNotFoundError, get_workflow_for_execution
from src.services.file_storage import FileStorageService
from src.services.repo_storage import RepoStorage

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def workflow_source(db_session):
    token = uuid4().hex
    path = f"delete-retirement-{token}/workflow.py"
    source = b"def run():\n    return 'source'\n"
    solution_id, workspace_id, installed_id = uuid4(), uuid4(), uuid4()
    db_session.add(Solution(id=solution_id, name=token, slug=token))
    await db_session.flush()
    for workflow_id, install in ((workspace_id, None), (installed_id, solution_id)):
        db_session.add(Workflow(
            id=workflow_id, name=f"delete_{workflow_id.hex}", function_name="run",
            path=path, solution_id=install,
        ))
    try:
        await FileIndexService(db_session).write(path, source)
        await set_module(path, source.decode(), hashlib.sha256(source).hexdigest())
        await db_session.commit()
        yield path, workspace_id, installed_id
    finally:
        await db_session.rollback()
        await db_session.execute(delete(Workflow).where(
            Workflow.id.in_([workspace_id, installed_id]),
        ))
        await db_session.execute(delete(Solution).where(Solution.id == solution_id))
        await db_session.execute(delete(FileIndex).where(FileIndex.path == path))
        await db_session.commit()
        await RepoStorage().delete(path)
        await invalidate_module(path)


async def _assert_retired(db, path, workspace_id, installed_id):
    db.expire_all()
    workspace = await db.get(Workflow, workspace_id)
    installed = await db.get(Workflow, installed_id)
    assert workspace.is_active is False
    assert workspace.is_orphaned is True
    assert installed.is_active is True
    assert installed.is_orphaned is False
    assert await db.scalar(select(FileIndex.path).where(FileIndex.path == path)) is None
    assert not await RepoStorage().exists(path)
    assert await get_module(path) is None
    with pytest.raises(WorkflowNotFoundError):
        await get_workflow_for_execution(str(workspace_id), db=db)


async def test_editor_delete_retires_workspace_workflow_only(
    e2e_client, platform_admin, db_session, workflow_source,
):
    path, workspace_id, installed_id = workflow_source

    response = e2e_client.delete(
        "/api/files/editor", params={"path": path}, headers=platform_admin.headers,
    )

    assert response.status_code == 204, response.text
    await _assert_retired(db_session, path, workspace_id, installed_id)


async def test_failed_retirement_is_reported_and_editor_retry_finishes_cleanup(
    e2e_client, platform_admin, db_session, workflow_source, monkeypatch,
):
    path, workspace_id, installed_id = workflow_source
    storage = FileStorageService(db_session)
    monkeypatch.setattr(
        storage._workflow_indexer, "delete_workflows_for_file",
        AsyncMock(side_effect=RuntimeError("retirement unavailable")),
    )

    with pytest.raises(RuntimeError, match="retirement unavailable"):
        await storage.delete_file(path)
    await db_session.rollback()
    assert not await RepoStorage().exists(path)
    assert await get_module(path) is None
    workspace = await db_session.get(Workflow, workspace_id)
    assert workspace.is_active is True

    # Object deletion is idempotent: the editor can finish cleanup even though
    # the earlier failure already removed the source bytes.
    response = e2e_client.delete(
        "/api/files/editor", params={"path": path}, headers=platform_admin.headers,
    )
    assert response.status_code == 204, response.text
    await _assert_retired(db_session, path, workspace_id, installed_id)
