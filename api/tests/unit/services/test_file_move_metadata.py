"""files.move metadata re-key: write-path keying, Core statements, write guard."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import insert, select

from src.models.orm.file_metadata import FileMetadata
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.services.file_policy_service import FilePolicyService
from src.services.solutions.guard import install_solution_write_guard


@pytest.fixture(autouse=True)
def _guard() -> None:
    install_solution_write_guard()


async def _org_and_solution(db_session):
    org = Organization(id=uuid4(), name=f"Move-{uuid4().hex[:8]}", created_by="test")
    db_session.add(org)
    await db_session.flush()
    solution = Solution(id=uuid4(), slug=f"move-{uuid4().hex[:8]}", name="Move", organization_id=org.id)
    db_session.add(solution)
    await db_session.flush()
    return org, solution


async def _row(db_session, **values) -> FileMetadata:
    row_id = uuid4()
    await db_session.execute(
        insert(FileMetadata).values(
            id=row_id,
            location="docs",
            s3_key=f"docs/x/{values['path']}",
            content_type="application/pdf",
            size_bytes=5,
            created_by=uuid4(),
            **values,
        )
    )
    return (await db_session.execute(select(FileMetadata).where(FileMetadata.id == row_id))).scalar_one()


async def test_solution_row_moves_in_place(db_session) -> None:
    org, solution = await _org_and_solution(db_session)
    org_id, solution_id = org.id, solution.id
    source = await _row(db_session, organization_id=org_id, solution_id=solution_id, path="a/r.pdf")
    source_id, created_by, created_at = source.id, source.created_by, source.created_at
    mover = uuid4()
    service = FilePolicyService(db_session)

    await service.move_metadata(
        organization_id=org_id, solution_id=solution_id, location="docs",
        source_row_id=source.id, destination_path="b/r.pdf", s3_key="docs/s/b/r.pdf",
        content_type="application/pdf", size_bytes=5, updated_by=mover,
    )

    db_session.expire_all()
    moved = await service.get_scoped_metadata(
        organization_id=org_id, solution_id=solution_id, location="docs", path="b/r.pdf"
    )
    assert moved is not None and moved.id == source_id
    assert (moved.created_by, moved.created_at) == (created_by, created_at)
    assert moved.updated_by == mover and moved.s3_key == "docs/s/b/r.pdf"
    assert await service.get_scoped_metadata(
        organization_id=org_id, solution_id=solution_id, location="docs", path="a/r.pdf"
    ) is None


async def test_stale_org_destination_row_removed_and_solution_row_untouched(db_session) -> None:
    org, solution = await _org_and_solution(db_session)
    source = await _row(db_session, organization_id=org.id, solution_id=None, path="a/r.pdf")
    stale = await _row(db_session, organization_id=org.id, solution_id=None, path="b/r.pdf")
    sibling = await _row(db_session, organization_id=org.id, solution_id=solution.id, path="b/r.pdf")
    source_id, stale_id, sibling_id = source.id, stale.id, sibling.id
    service = FilePolicyService(db_session)

    await service.move_metadata(
        organization_id=org.id, solution_id=None, location="docs",
        source_row_id=source.id, destination_path="b/r.pdf", s3_key="docs/o/b/r.pdf",
        content_type="application/pdf", size_bytes=5, updated_by=uuid4(),
    )

    db_session.expire_all()
    ids = set((await db_session.execute(
        select(FileMetadata.id).where(FileMetadata.location == "docs", FileMetadata.path == "b/r.pdf")
    )).scalars())
    assert ids == {source_id, sibling_id}
    assert stale_id not in ids


async def test_global_rows_key_on_null_org(db_session) -> None:
    source = await _row(db_session, organization_id=None, solution_id=None, path=f"g-{uuid4().hex}.pdf")
    source_id = source.id
    service = FilePolicyService(db_session)
    destination = f"g-{uuid4().hex}.pdf"
    await service.move_metadata(
        organization_id=None, solution_id=None, location="docs",
        source_row_id=source.id, destination_path=destination, s3_key=f"docs/global/{destination}",
        content_type="application/pdf", size_bytes=5, updated_by=uuid4(),
    )
    db_session.expire_all()
    moved = await service.get_scoped_metadata(
        organization_id=None, solution_id=None, location="docs", path=destination
    )
    assert moved is not None and moved.id == source_id


async def test_missing_source_row_inserts_destination_row(db_session) -> None:
    org, solution = await _org_and_solution(db_session)
    mover = uuid4()
    service = FilePolicyService(db_session)
    await service.move_metadata(
        organization_id=org.id, solution_id=solution.id, location="docs",
        source_row_id=None, destination_path="b/new.pdf", s3_key="docs/s/b/new.pdf",
        content_type="application/pdf", size_bytes=7, updated_by=mover,
    )
    row = await service.get_scoped_metadata(
        organization_id=org.id, solution_id=solution.id, location="docs", path="b/new.pdf"
    )
    assert row is not None
    assert (row.created_by, row.updated_by, row.size_bytes, row.sha256) == (mover, mover, 7, None)
