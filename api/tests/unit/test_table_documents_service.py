"""Focused orchestration coverage for the shared table document read service."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared import table_documents
from src.core.principal import UserPrincipal
from src.models.contracts.tables import DocumentQuery
from src.models.orm.tables import Document, Table


def _user() -> UserPrincipal:
    uid = uuid4()
    return UserPrincipal(
        user_id=uid,
        email="reader@example.com",
        organization_id=uuid4(),
    )


def _table() -> Table:
    return Table(
        id=uuid4(),
        name="svc_docs",
        organization_id=uuid4(),
        created_by="test@example.com",
    )


def _doc(table: Table, doc_id: str = "doc-1") -> Document:
    now = datetime.now(timezone.utc)
    return Document(
        id=doc_id,
        table_id=table.id,
        data={"status": "active"},
        created_by="test@example.com",
        updated_by="test@example.com",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_query_returns_empty_without_touching_repo(monkeypatch):
    table = _table()
    user = _user()
    db = AsyncMock()
    query = DocumentQuery(limit=7, offset=3)

    monkeypatch.setattr(
        table_documents, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(table_documents, "preresolve_for_policies", AsyncMock())
    monkeypatch.setattr(table_documents, "compile_read_filter", lambda policies, u: None)

    called = False

    class ExplodingRepo:
        def __init__(self, *args, **kwargs):
            nonlocal called
            called = True

    monkeypatch.setattr(table_documents, "DocumentRepository", ExplodingRepo)

    result = await table_documents.query_table_documents(db, table, query, user)

    assert called is False
    assert result.table_id == table.id
    assert result.documents == []
    assert result.total == 0
    assert result.limit == 7
    assert result.offset == 3


@pytest.mark.asyncio
async def test_query_delegates_to_repo_and_maps_dtos(monkeypatch):
    table = _table()
    user = _user()
    db = AsyncMock()
    query = DocumentQuery(limit=5, offset=0)
    docs = [_doc(table, "doc-1"), _doc(table, "doc-2")]
    sentinel_filter = object()
    seen = {}

    monkeypatch.setattr(
        table_documents, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(table_documents, "preresolve_for_policies", AsyncMock())
    monkeypatch.setattr(
        table_documents, "compile_read_filter", lambda policies, u: sentinel_filter
    )

    class FakeRepo:
        def __init__(self, session, tbl):
            seen["session"] = session
            seen["table"] = tbl

        async def query(self, params, *, extra_where=None):
            seen["params"] = params
            seen["extra_where"] = extra_where
            return docs, 2

    monkeypatch.setattr(table_documents, "DocumentRepository", FakeRepo)

    result = await table_documents.query_table_documents(db, table, query, user)

    assert seen["session"] is db
    assert seen["table"] is table
    assert seen["params"] is query
    assert seen["extra_where"] is sentinel_filter
    assert [d.id for d in result.documents] == ["doc-1", "doc-2"]
    assert result.total == 2


@pytest.mark.asyncio
async def test_count_returns_zero_without_touching_repo(monkeypatch):
    table = _table()
    user = _user()
    db = AsyncMock()

    monkeypatch.setattr(
        table_documents, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(table_documents, "preresolve_for_policies", AsyncMock())
    monkeypatch.setattr(table_documents, "compile_read_filter", lambda policies, u: None)

    async def explode(*args, **kwargs):
        raise AssertionError("repo must not be called")

    monkeypatch.setattr(table_documents.DocumentRepository, "count", explode)

    result = await table_documents.count_table_documents(db, table, user)

    assert result.count == 0


@pytest.mark.asyncio
async def test_count_delegates_where_and_filter(monkeypatch):
    table = _table()
    user = _user()
    db = AsyncMock()
    sentinel_filter = object()
    seen = {}
    where = {"status": "active"}

    monkeypatch.setattr(
        table_documents, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(table_documents, "preresolve_for_policies", AsyncMock())
    monkeypatch.setattr(
        table_documents, "compile_read_filter", lambda policies, u: sentinel_filter
    )

    class FakeRepo:
        def __init__(self, session, tbl):
            pass

        async def count(self, w=None, *, extra_where=None):
            seen["where"] = w
            seen["extra_where"] = extra_where
            return 4

    monkeypatch.setattr(table_documents, "DocumentRepository", FakeRepo)

    result = await table_documents.count_table_documents(db, table, user, where)

    assert result.count == 4
    assert seen["where"] == where
    assert seen["extra_where"] is sentinel_filter


@pytest.mark.asyncio
async def test_get_missing_document_raises_404_and_skips_check(monkeypatch):
    table = _table()
    user = _user()
    db = AsyncMock()

    class FakeRepo:
        def __init__(self, session, tbl):
            pass

        async def get(self, doc_id):
            assert doc_id == "missing"
            return None

    monkeypatch.setattr(table_documents, "DocumentRepository", FakeRepo)
    check = AsyncMock()
    monkeypatch.setattr(table_documents, "check_table_action_or_403", check)

    with pytest.raises(HTTPException) as exc_info:
        await table_documents.get_table_document(db, table, "missing", user)

    assert exc_info.value.status_code == 404
    check.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_enforces_read_check_and_returns_dto(monkeypatch):
    table = _table()
    user = _user()
    db = AsyncMock()
    doc = _doc(table, "doc-9")
    seen = {}

    class FakeRepo:
        def __init__(self, session, tbl):
            pass

        async def get(self, doc_id):
            assert doc_id == "doc-9"
            return doc

    async def fake_check(action, tbl, row, u, *, db):
        seen["action"] = action
        seen["row"] = row
        assert tbl is table
        assert u is user

    monkeypatch.setattr(table_documents, "DocumentRepository", FakeRepo)
    monkeypatch.setattr(table_documents, "check_table_action_or_403", fake_check)

    result = await table_documents.get_table_document(db, table, "doc-9", user)

    assert seen["action"] == "read"
    assert seen["row"]["id"] == "doc-9"
    assert result.id == "doc-9"


@pytest.mark.asyncio
async def test_check_denied_emits_audit_commits_and_raises_403(monkeypatch):
    table = _table()
    user = _user()
    db = AsyncMock()
    db.commit = AsyncMock()

    monkeypatch.setattr(
        table_documents, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(table_documents, "preresolve_for_policies", AsyncMock())
    monkeypatch.setattr(table_documents, "evaluate_action", lambda *args: False)
    emit = AsyncMock()
    monkeypatch.setattr(table_documents, "emit_table_policy_deny", emit)

    with pytest.raises(HTTPException) as exc_info:
        await table_documents.check_table_action_or_403(
            "read", table, {"id": "doc-1"}, user, db=db
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Access denied"
    emit.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_allowed_skips_audit_and_commit(monkeypatch):
    table = _table()
    user = _user()
    db = AsyncMock()
    db.commit = AsyncMock()

    monkeypatch.setattr(
        table_documents, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(table_documents, "preresolve_for_policies", AsyncMock())
    monkeypatch.setattr(table_documents, "evaluate_action", lambda *args: True)
    emit = AsyncMock()
    monkeypatch.setattr(table_documents, "emit_table_policy_deny", emit)

    await table_documents.check_table_action_or_403(
        "read", table, {"id": "doc-1"}, user, db=db
    )

    emit.assert_not_awaited()
    db.commit.assert_not_awaited()
