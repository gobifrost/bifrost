from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.policies import TablePolicies
from src.models.contracts.tables import DocumentBatchCreate, DocumentBatchDeleteRequest
from src.models.orm.tables import Document
from shared.table_batch_writes import BatchWriteResult, ConcurrentBatchWrite


class _FakeDb:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.calls: list[str] = []

    async def commit(self) -> None:
        self.calls.append("commit")
        self.committed = True

    async def rollback(self) -> None:
        self.calls.append("rollback")
        self.rolled_back = True


@pytest.mark.asyncio
async def test_batch_upsert_rolls_back_and_409s_on_guarded_count_mismatch(monkeypatch):
    import shared.table_document_writes as writes
    import src.routers.tables as router

    table = SimpleNamespace(
        id=uuid4(),
        name="bulk_race",
        organization_id=None,
        solution_id=None,
    )
    db = _FakeDb()
    user = UserPrincipal(
        user_id=uuid4(),
        email="admin@example.com",
        organization_id=None,
        is_superuser=True,
        roles=["authenticated"],
    )
    ctx = SimpleNamespace(
        db=db,
        user=user,
        org_id=None,
        solution_id=None,
        app_id=None,
    )

    async def fake_get_table_or_404(*args, **kwargs):
        return table

    async def fake_solution_gate(*args, **kwargs):
        return None

    async def fake_load_policies(*args, **kwargs):
        return TablePolicies()

    async def fake_preresolve(*args, **kwargs):
        return None

    async def fake_write_table_batch(*args, **kwargs):
        raise ConcurrentBatchWrite("lost race")

    monkeypatch.setattr(router, "get_table_or_404", fake_get_table_or_404)
    monkeypatch.setattr(router, "_assert_solution_write_targets_owned_table", fake_solution_gate)
    monkeypatch.setattr(writes, "load_resolved_table_policies", fake_load_policies)
    monkeypatch.setattr(writes, "preresolve_for_policies", fake_preresolve)
    monkeypatch.setattr(writes, "write_table_batch", fake_write_table_batch)

    body = DocumentBatchCreate(
        write_mode="replace_upsert",
        return_documents=False,
        documents=[{"id": "new-row", "data": {"value": 1}}]
    )
    with pytest.raises(HTTPException) as exc_info:
        await router.batch_documents(
            "bulk_race",
            body,
            ctx=ctx,
            scope=None,
        )

    assert exc_info.value.status_code == 409
    assert db.rolled_back is True
    assert db.committed is False


@pytest.mark.asyncio
async def test_batch_documents_invalidates_table_once_after_commit(monkeypatch):
    import shared.table_document_writes as writes
    import src.routers.tables as router

    table = SimpleNamespace(
        id=uuid4(),
        name="batch_events",
        organization_id=None,
        solution_id=None,
    )
    db = _FakeDb()
    user = UserPrincipal(
        user_id=uuid4(),
        email="admin@example.com",
        organization_id=None,
        is_superuser=True,
        roles=["authenticated"],
    )
    ctx = SimpleNamespace(
        db=db,
        user=user,
        org_id=None,
        solution_id=None,
        app_id=None,
    )
    now = datetime.now(timezone.utc)
    alpha = Document(
        id="alpha",
        table_id=table.id,
        data={"value": "alpha"},
        created_by=str(user.user_id),
        updated_by=str(user.user_id),
        created_at=now,
        updated_at=now,
    )
    beta = Document(
        id="beta",
        table_id=table.id,
        data={"value": "beta"},
        created_by=str(user.user_id),
        updated_by=str(user.user_id),
        created_at=now,
        updated_at=now,
    )
    invalidated: list[str] = []

    async def fake_get_table_or_404(*args, **kwargs):
        return table

    async def fake_solution_gate(*args, **kwargs):
        return None

    async def fake_load_policies(*args, **kwargs):
        return TablePolicies()

    async def fake_preresolve(*args, **kwargs):
        return None

    async def fake_write_table_batch(*args, **kwargs):
        return BatchWriteResult(
            documents_by_index={0: alpha, 1: beta},
            previous_rows_by_index={1: {"id": "beta", "value": "old"}},
            insert_conflicts=[],
        )

    async def fake_publish_table_invalidated(table_id: str):
        db.calls.append("publish:invalidate")
        invalidated.append(table_id)

    monkeypatch.setattr(router, "get_table_or_404", fake_get_table_or_404)
    monkeypatch.setattr(router, "_assert_solution_write_targets_owned_table", fake_solution_gate)
    monkeypatch.setattr(writes, "load_resolved_table_policies", fake_load_policies)
    monkeypatch.setattr(writes, "preresolve_for_policies", fake_preresolve)
    monkeypatch.setattr(writes, "write_table_batch", fake_write_table_batch)
    monkeypatch.setattr(writes, "publish_table_invalidated", fake_publish_table_invalidated)

    body = DocumentBatchCreate(
        documents=[
            {"id": "alpha", "data": {"value": "alpha"}},
            {"id": "beta", "data": {"value": "beta"}},
        ]
    )

    response = await router.batch_documents("batch_events", body, ctx=ctx, scope=None)

    assert [doc.id for doc in response.documents] == ["alpha", "beta"]
    assert invalidated == [str(table.id)]
    assert db.calls == ["commit", "publish:invalidate"]
    assert db.committed is True


@pytest.mark.asyncio
async def test_batch_documents_does_not_invalidate_for_conflict_only_no_change(monkeypatch):
    import shared.table_document_writes as writes
    import src.routers.tables as router

    table = SimpleNamespace(
        id=uuid4(),
        name="batch_no_change",
        organization_id=None,
        solution_id=None,
    )
    db = _FakeDb()
    user = UserPrincipal(
        user_id=uuid4(),
        email="admin@example.com",
        organization_id=None,
        is_superuser=True,
        roles=["authenticated"],
    )
    ctx = SimpleNamespace(
        db=db,
        user=user,
        org_id=None,
        solution_id=None,
        app_id=None,
    )
    invalidated: list[str] = []

    async def fake_get_table_or_404(*args, **kwargs):
        return table

    async def fake_solution_gate(*args, **kwargs):
        return None

    async def fake_load_policies(*args, **kwargs):
        return TablePolicies()

    async def fake_preresolve(*args, **kwargs):
        return None

    async def fake_write_table_batch(*args, **kwargs):
        return BatchWriteResult(
            documents_by_index={},
            previous_rows_by_index={},
            insert_conflicts=[SimpleNamespace(id="alpha")],
        )

    async def fake_publish_table_invalidated(table_id: str):
        invalidated.append(table_id)

    monkeypatch.setattr(router, "get_table_or_404", fake_get_table_or_404)
    monkeypatch.setattr(router, "_assert_solution_write_targets_owned_table", fake_solution_gate)
    monkeypatch.setattr(writes, "load_resolved_table_policies", fake_load_policies)
    monkeypatch.setattr(writes, "preresolve_for_policies", fake_preresolve)
    monkeypatch.setattr(writes, "write_table_batch", fake_write_table_batch)
    monkeypatch.setattr(writes, "publish_table_invalidated", fake_publish_table_invalidated)

    body = DocumentBatchCreate(
        write_mode="insert",
        documents=[{"id": "alpha", "data": {"value": "alpha"}}],
    )

    response = await router.batch_documents("batch_no_change", body, ctx=ctx, scope=None)

    assert response.inserted == 0
    assert response.errors == [{"id": "alpha", "error": "Document already exists"}]
    assert invalidated == []
    assert db.calls == ["commit"]
    assert db.committed is True


@pytest.mark.asyncio
async def test_batch_delete_documents_invalidates_table_once_after_commit(monkeypatch):
    import shared.table_document_writes as writes
    import src.routers.tables as router

    table = SimpleNamespace(
        id=uuid4(),
        name="batch_delete_events",
        organization_id=None,
        solution_id=None,
    )
    db = _FakeDb()
    user = UserPrincipal(
        user_id=uuid4(),
        email="admin@example.com",
        organization_id=None,
        is_superuser=True,
        roles=["authenticated"],
    )
    ctx = SimpleNamespace(
        db=db,
        user=user,
        org_id=None,
        solution_id=None,
        app_id=None,
    )
    now = datetime.now(timezone.utc)
    docs = {
        "alpha": Document(
            id="alpha",
            table_id=table.id,
            data={"value": "alpha"},
            created_by=str(user.user_id),
            updated_by=str(user.user_id),
            created_at=now,
            updated_at=now,
        ),
        "beta": Document(
            id="beta",
            table_id=table.id,
            data={"value": "beta"},
            created_by=str(user.user_id),
            updated_by=str(user.user_id),
            created_at=now,
            updated_at=now,
        ),
    }
    invalidated: list[str] = []

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, doc_id: str):
            return docs.get(doc_id)

        async def delete(self, doc_id: str):
            return doc_id in docs

    async def fake_get_table_or_404(*args, **kwargs):
        return table

    async def fake_solution_gate(*args, **kwargs):
        return None

    async def fake_load_policies(*args, **kwargs):
        return TablePolicies()

    async def fake_preresolve(*args, **kwargs):
        return None

    async def fake_publish_table_invalidated(table_id: str):
        db.calls.append("publish:invalidate")
        invalidated.append(table_id)

    monkeypatch.setattr(router, "get_table_or_404", fake_get_table_or_404)
    monkeypatch.setattr(router, "_assert_solution_write_targets_owned_table", fake_solution_gate)
    monkeypatch.setattr(writes, "DocumentRepository", FakeRepo)
    monkeypatch.setattr(writes, "load_resolved_table_policies", fake_load_policies)
    monkeypatch.setattr(writes, "preresolve_for_policies", fake_preresolve)
    monkeypatch.setattr(writes, "evaluate_action", lambda *args, **kwargs: True)
    monkeypatch.setattr(writes, "publish_table_invalidated", fake_publish_table_invalidated)

    body = DocumentBatchDeleteRequest(ids=["alpha", "beta"])

    response = await router.batch_delete_documents(
        "batch_delete_events",
        body,
        ctx=ctx,
        scope=None,
    )

    assert response.deleted == 2
    assert response.deleted_ids == ["alpha", "beta"]
    assert invalidated == [str(table.id)]
    assert db.calls == ["commit", "publish:invalidate"]
    assert db.committed is True


@pytest.mark.asyncio
async def test_batch_delete_documents_does_not_invalidate_noop(monkeypatch):
    import shared.table_document_writes as writes
    import src.routers.tables as router

    table = SimpleNamespace(
        id=uuid4(),
        name="batch_delete_noop",
        organization_id=None,
        solution_id=None,
    )
    db = _FakeDb()
    user = UserPrincipal(
        user_id=uuid4(),
        email="admin@example.com",
        organization_id=None,
        is_superuser=True,
        roles=["authenticated"],
    )
    ctx = SimpleNamespace(
        db=db,
        user=user,
        org_id=None,
        solution_id=None,
        app_id=None,
    )
    invalidated: list[str] = []

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, doc_id: str):
            return None

        async def delete(self, doc_id: str):
            raise AssertionError("delete should not be called for missing rows")

    async def fake_get_table_or_404(*args, **kwargs):
        return table

    async def fake_solution_gate(*args, **kwargs):
        return None

    async def fake_load_policies(*args, **kwargs):
        return TablePolicies()

    async def fake_preresolve(*args, **kwargs):
        return None

    async def fake_publish_table_invalidated(table_id: str):
        invalidated.append(table_id)

    monkeypatch.setattr(router, "get_table_or_404", fake_get_table_or_404)
    monkeypatch.setattr(router, "_assert_solution_write_targets_owned_table", fake_solution_gate)
    monkeypatch.setattr(writes, "DocumentRepository", FakeRepo)
    monkeypatch.setattr(writes, "load_resolved_table_policies", fake_load_policies)
    monkeypatch.setattr(writes, "preresolve_for_policies", fake_preresolve)
    monkeypatch.setattr(writes, "publish_table_invalidated", fake_publish_table_invalidated)

    body = DocumentBatchDeleteRequest(ids=["missing"])

    response = await router.batch_delete_documents(
        "batch_delete_noop",
        body,
        ctx=ctx,
        scope=None,
    )

    assert response.deleted == 0
    assert response.deleted_ids == []
    assert invalidated == []
    assert db.calls == ["commit"]
    assert db.committed is True
