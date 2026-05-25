from types import SimpleNamespace
from uuid import UUID

import pytest

from src.repositories.knowledge import KnowledgeRepository


ORG_ID = UUID("11111111-1111-1111-1111-111111111111")


class CapturingSession:
    def __init__(self):
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return SimpleNamespace(scalar_one_or_none=lambda: None)


@pytest.mark.asyncio
async def test_get_by_id_filters_to_repo_org_or_global():
    session = CapturingSession()
    repo = KnowledgeRepository(session=session, org_id=ORG_ID)

    await repo.get_by_id(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))

    compiled = str(session.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "knowledge_store.organization_id" in compiled
    assert (str(ORG_ID) in compiled) or (ORG_ID.hex in compiled)
    assert "IS NULL" in compiled


@pytest.mark.asyncio
async def test_get_by_id_global_repo_only_reads_global_documents():
    session = CapturingSession()
    repo = KnowledgeRepository(session=session, org_id=None)

    await repo.get_by_id(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))

    compiled = str(session.statement.compile(compile_kwargs={"literal_binds": True}))
    assert "knowledge_store.organization_id IS NULL" in compiled
