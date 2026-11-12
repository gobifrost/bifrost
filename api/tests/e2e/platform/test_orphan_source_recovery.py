"""Recovery verifies durable source rather than resurrecting stale cached bytes."""
import hashlib
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.core.module_cache import set_module, invalidate_module
from src.models.orm.file_index import FileIndex
from src.models.orm.workflows import Workflow
from src.services.repo_storage import RepoStorage
from tests.e2e.conftest import write_and_register

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("corrupt_digest", [False, True])
async def test_recovery_verifies_durable_bytes(e2e_client, platform_admin, db_session, corrupt_digest):
    path = f"workflows/orphan_{uuid4().hex}.py"
    source = b"from bifrost import workflow\n@workflow\nasync def recover(): return 'durable'\n"
    workflow = write_and_register(e2e_client, platform_admin.headers, path,
                                  source.decode(), "recover", organization_id=None)
    workflow_id = UUID(workflow["id"])
    try:
        wf = await db_session.get(Workflow, workflow_id)
        wf.is_orphaned = True
        if corrupt_digest:
            indexed = await db_session.get(FileIndex, path)
            indexed.content_hash = '0' * 64
        await db_session.commit()
        stale = "raise RuntimeError('stale cache must not be restored')\n"
        await set_module(path, stale, hashlib.sha256(stale.encode()).hexdigest())
        response = e2e_client.post(f"/api/workflows/{workflow_id}/recreate", headers=platform_admin.headers)
        assert response.status_code == (400 if corrupt_digest else 200), response.text
        assert await RepoStorage().read(path) == source
        await db_session.refresh(wf)
        assert wf.is_orphaned is corrupt_digest
    finally:
        await db_session.execute(delete(Workflow).where(Workflow.id == workflow_id))
        await db_session.execute(delete(FileIndex).where(FileIndex.path == path))
        await db_session.commit()
        await RepoStorage().delete(path)
        await invalidate_module(path)
