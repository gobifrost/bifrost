"""Writers that used to bypass the source search index now keep it current.

These run against the test stack's real PostgreSQL + object storage, so a
writer that touches S3 without FileIndexService shows up as a stale row.
"""

from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.core.database import get_db_context
from src.models.orm.file_index import FileIndex, SolutionFileIndex
from src.models.orm.solutions import Solution
from src.services.file_index_service import FileIndexService


async def _content(db, path: str) -> str | None:
    return await db.scalar(select(FileIndex.content).where(FileIndex.path == path))


@pytest.mark.asyncio
async def test_ref_rewriter_updates_index_content(db_session):
    from src.services.solutions.ref_rewriter import WorkflowRefRewriter, WorkflowRename

    path = f"apps/rr-{uuid4().hex[:8]}/page.tsx"
    await FileIndexService(db_session).write(path, b'useWorkflow("old_wf")\n')
    rename = WorkflowRename(
        old_path="workflows/old.py", old_function_name="old_wf", old_name="old_wf",
        new_path="workflows/new.py", new_function_name="new_wf", new_name="new_wf",
    )
    updated = await WorkflowRefRewriter(db_session)._rewrite_sources([rename], [path])
    assert updated == [path]
    assert await _content(db_session, path) == 'useWorkflow("new_wf")\n'


@pytest.mark.asyncio
async def test_create_folder_marker_is_indexed(db_session):
    from src.services.file_storage import FileStorageService

    folder = f"fold-{uuid4().hex[:8]}"
    await FileStorageService(db_session).create_folder(folder, updated_by="me")
    row = (await db_session.execute(
        select(FileIndex).where(FileIndex.path == f"{folder}/.gitkeep")
    )).scalar_one()
    assert (row.content, row.updated_by) == ("", "me")


@pytest.mark.asyncio
async def test_write_python_indexes_and_unindexes_solution_source():
    from src.services.solutions.deploy import SolutionDeployer

    sid = uuid4()
    async with get_db_context() as db:
        db.add(Solution(id=sid, slug=f"wp-{sid.hex[:8]}", name="write_python index"))
        await db.commit()
    try:
        async with get_db_context() as db:
            deployer = SolutionDeployer(db)
            await deployer._write_python(sid, {"functions/a.py": "A = 1\n", "functions/b.py": "B = 1\n"})
            await deployer._write_python(sid, {"functions/a.py": "A = 2\n"})
        async with get_db_context() as db:
            result = await db.execute(
                select(SolutionFileIndex.path, SolutionFileIndex.content).where(
                    SolutionFileIndex.solution_id == sid
                )
            )
            rows = {path: content for path, content in result.all()}
        assert rows == {"functions/a.py": "A = 2\n"}
    finally:
        async with get_db_context() as db:
            await db.execute(delete(Solution).where(Solution.id == sid))
            await db.commit()


@pytest.mark.asyncio
async def test_object_uploaded_outside_the_service_is_indexed_on_completion(db_session):
    """Presigned PUTs land in S3 directly; completion indexes the real bytes."""
    from src.services.repo_storage import RepoStorage

    path = f"uploads-{uuid4().hex[:8]}/notes.md"
    await RepoStorage().write(path, b"# uploaded\n")
    await FileIndexService(db_session).index_existing_object(path, updated_by="me")
    assert await _content(db_session, path) == "# uploaded\n"


@pytest.mark.asyncio
async def test_oversized_upload_gets_a_path_only_row(db_session, monkeypatch):
    import src.services.file_index_service as fis
    from src.services.repo_storage import RepoStorage

    monkeypatch.setattr(fis, "MAX_INDEXABLE_TEXT_BYTES", 8)
    path = f"uploads-{uuid4().hex[:8]}/big.txt"
    await RepoStorage().write(path, b"0123456789")
    await FileIndexService(db_session).index_existing_object(path)
    row = (await db_session.execute(select(FileIndex).where(FileIndex.path == path))).scalar_one()
    assert row.content is None and len(row.content_hash) == 64
