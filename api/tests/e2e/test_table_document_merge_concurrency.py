"""Ordinary merges serialize against current PostgreSQL state before policy checks.

Direct fixtures, two overlapping writer transactions, no jobs. Publication is
stubbed, but repository SQL and pre/post-image policy evaluation are real.
"""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, text

import shared.table_document_writes as writes
from shared.table_documents import DocumentRepository
from src.core.principal import UserPrincipal
from src.models.orm.tables import Document, Table


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_upsert", [False, True], ids=["partial-update", "legacy-merge-upsert"])
async def test_overlapping_document_merges_preserve_disjoint_fields(
    async_session_factory, monkeypatch, legacy_upsert,
):
    table_id = uuid4()
    doc_id = "merge-target"
    preimage = {"left": 0, "right": 0}
    user = UserPrincipal(user_id=uuid4(), email="merge@example.com", organization_id=None)
    policy_images = []
    require_policy = writes.require_update_policy

    async def capture_policy(table, old_row, new_row, user, *, db):
        policy_images.append((dict(old_row), dict(new_row)))
        await require_policy(table, old_row, new_row, user, db=db)

    monkeypatch.setattr(writes, "require_update_policy", capture_policy)
    monkeypatch.setattr(writes, "publish_document_change", AsyncMock())
    try:
        async with async_session_factory() as setup:
            setup.add(Table(
                id=table_id, name=f"merge-{table_id}",
                access={"policies": [{"name": "update", "actions": ["update"], "when": None}]},
            ))
            await setup.flush()
            setup.add(Document(table_id=table_id, id=doc_id, data=preimage))
            await setup.commit()

        async with async_session_factory() as a, async_session_factory() as b:
            table_a = await a.get(Table, table_id)
            table_b = await b.get(Table, table_id)
            assert table_a is not None and table_b is not None
            repo_a = DocumentRepository(a, table_a)
            # Retain B's stale identity across the wait, not just its transaction.
            stale_b = await DocumentRepository(b, table_b).get(doc_id)
            assert stale_b is not None and stale_b.data == preimage
            initial_updated_at = stale_b.updated_at
            blocker = await a.scalar(text("SELECT pg_backend_pid()"))
            waiter = await b.scalar(text("SELECT pg_backend_pid()"))
            updated_a = await repo_a.update(doc_id, {"left": 1}, updated_by="writer-a")
            assert updated_a is not None
            first_updated_at = updated_a.updated_at

            if legacy_upsert:
                operation = writes.insert_table_document(
                    b, table_b, user, doc_id=doc_id, data={"right": 1},
                    created_by=None, updated_by=None, upsert=True,
                )
            else:
                operation = writes.update_table_document(
                    b, table_b, user, doc_id=doc_id, data={"right": 1}, updated_by=None,
                )
            task = asyncio.create_task(operation)
            try:
                # Wait for an actual database lock dependency, not a timed guess.
                async with async_session_factory() as observer:
                    async with asyncio.timeout(5):
                        while not await observer.scalar(
                            text("SELECT CAST(:blocker AS integer) = ANY("
                                 "pg_blocking_pids(CAST(:waiter AS integer)))"),
                            {"blocker": blocker, "waiter": waiter},
                        ):
                            if task.done():
                                assert (await task).id == doc_id
                                pytest.fail("second writer finished before first writer committed")
                await a.commit()
                async with asyncio.timeout(5):
                    assert (await task).data == {"left": 1, "right": 1}
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        async with async_session_factory() as verify:
            final = await verify.get(Document, (table_id, doc_id))
            assert final is not None
            assert final.data == {"left": 1, "right": 1}
            assert initial_updated_at < first_updated_at < final.updated_at
            assert final.updated_by == str(user.user_id)
        assert len(policy_images) == 1
        old_row, new_row = policy_images[0]
        assert (old_row["left"], old_row["right"]) == (1, 0)
        assert (new_row["left"], new_row["right"]) == (1, 1)
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(delete(Document).where(Document.table_id == table_id))
            await cleanup.execute(delete(Table).where(Table.id == table_id))
            await cleanup.commit()
