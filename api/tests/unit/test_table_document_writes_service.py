"""Focused coverage for the shared table document write service.

Pins the extracted HTTP mutation orchestration: attribution privilege,
pre/post-image policy checks, replace-versus-merge semantics, transport-
neutral error mapping (422/403/409 details), commit-before-publish
ordering, atomic batch denial, duplicate IDs, and batch delete ordering.
Also covers thin HTTP adapter delegation + error mapping.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import shared.table_document_writes as writes
from shared.table_batch_writes import (
    BatchWriteResult,
    BatchPolicyDenied,
    ConcurrentBatchWrite,
    DuplicateBatchIds,
)
from src.core.constants import SYSTEM_USER_UUID
from src.core.principal import UserPrincipal
from src.models.orm.tables import Document, Table


def _user(**kwargs) -> UserPrincipal:
    base = {
        "user_id": uuid4(),
        "email": "writer@example.com",
        "organization_id": uuid4(),
    }
    base.update(kwargs)
    return UserPrincipal(**base)


def _engine_user() -> UserPrincipal:
    return UserPrincipal(
        user_id=SYSTEM_USER_UUID,
        email="engine@local",
        organization_id=None,
        is_superuser=True,
    )


def _table() -> Table:
    return Table(
        id=uuid4(),
        name="svc_writes",
        organization_id=uuid4(),
        created_by="test@example.com",
    )


def _doc(table: Table, doc_id: str = "doc-1", data: dict | None = None) -> Document:
    now = datetime.now(timezone.utc)
    return Document(
        id=doc_id,
        table_id=table.id,
        data=data if data is not None else {"status": "active"},
        created_by="writer",
        updated_by="writer",
        created_at=now,
        updated_at=now,
    )


class _FakeDb:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def commit(self) -> None:
        self.calls.append("commit")

    async def rollback(self) -> None:
        self.calls.append("rollback")


# --- attribution ---


def test_resolve_attribution_defaults_to_caller():
    user = _user()
    created_by, updated_by = writes.resolve_attribution(user, None, None)
    assert created_by == str(user.user_id)
    assert updated_by == str(user.user_id)


def test_resolve_attribution_created_by_mirrors_updated_by():
    user = _user(is_superuser=True)
    created_by, updated_by = writes.resolve_attribution(user, "someone", None)
    assert created_by == "someone"
    assert updated_by == "someone"


def test_resolve_attribution_override_forbidden_for_ordinary_caller():
    user = _user()
    with pytest.raises(writes.TableWriteForbidden) as exc_info:
        writes.resolve_attribution(user, None, "forged")
    assert exc_info.value.status_code == 403
    assert "engine or platform-admin" in str(exc_info.value.detail)


def test_resolve_attribution_override_allowed_for_engine():
    created_by, updated_by = writes.resolve_attribution(
        _engine_user(), "creator", "updater"
    )
    assert (created_by, updated_by) == ("creator", "updater")


def test_resolve_attribution_override_allowed_for_superuser():
    user = _user(is_superuser=True)
    created_by, updated_by = writes.resolve_attribution(
        user, "creator", "updater"
    )
    assert (created_by, updated_by) == ("creator", "updater")


# --- single insert ---


@pytest.mark.asyncio
async def test_insert_commits_before_single_publish(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()
    doc = _doc(table)
    events: list[tuple] = []

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, doc_id):
            return None

        async def insert(self, *args, **kwargs):
            return doc

    async def fake_check(*args, **kwargs):
        return None

    async def fake_publish(*, table_id, action, old_row, new_row):
        events.append((db.calls[:], action, old_row, new_row))

    monkeypatch.setattr(writes, "DocumentRepository", FakeRepo)
    monkeypatch.setattr(writes, "_check_action", fake_check)
    monkeypatch.setattr(writes, "publish_document_change", fake_publish)

    result = await writes.insert_table_document(
        db, table, user, doc_id=None, data={"a": 1},
        created_by=None, updated_by=None,
    )

    assert result is doc
    assert db.calls == ["commit"]
    assert len(events) == 1
    calls_before_publish, action, old_row, new_row = events[0]
    assert calls_before_publish == ["commit"]
    assert action == "insert"
    assert old_row is None
    assert new_row["id"] == "doc-1"


@pytest.mark.asyncio
async def test_insert_legacy_upsert_merges_and_publishes_update(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()
    existing = _doc(table, data={"keep": 1, "over": "old"})
    updated = _doc(table, data={"keep": 1, "over": "new"})
    seen = {}

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, doc_id):
            assert doc_id == "doc-1"
            return existing

        async def update(self, doc_id, data, *, updated_by=None):
            seen["data"] = data
            seen["updated_by"] = updated_by
            return updated

        async def insert(self, *args, **kwargs):
            raise AssertionError("insert must not run on the upsert branch")

    async def fake_require(table_arg, old_row, new_row, user_arg, *, db):
        seen["old"] = old_row
        seen["new"] = new_row

    async def fake_check(*args, **kwargs):
        raise AssertionError("create check must not run on the upsert branch")

    published = {}
    monkeypatch.setattr(writes, "DocumentRepository", FakeRepo)
    monkeypatch.setattr(writes, "require_update_policy", fake_require)
    monkeypatch.setattr(writes, "_check_action", fake_check)
    monkeypatch.setattr(
        writes, "publish_document_change", AsyncMock(side_effect=_capture(published))
    )

    result = await writes.insert_table_document(
        db, table, user, doc_id="doc-1", data={"over": "new"},
        created_by=None, updated_by=None, upsert=True,
    )

    assert result is updated
    # Merge semantics: untouched keys survive in the post-image.
    assert seen["new"]["keep"] == 1
    assert seen["new"]["over"] == "new"
    assert seen["old"]["over"] == "old"
    assert published["action"] == "update"
    assert published["old_row"]["over"] == "old"


def _capture(store: dict):
    async def _inner(*, table_id, action, old_row, new_row):
        store.update(
            {"table_id": table_id, "action": action, "old_row": old_row, "new_row": new_row}
        )

    return _inner


@pytest.mark.asyncio
async def test_upsert_replaces_data_on_conflict(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()
    existing = _doc(table, data={"keep": 1, "over": "old"})
    replaced = _doc(table, data={"over": "new"})
    seen = {}

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, doc_id):
            return existing

        async def upsert(self, doc_id, data, *, created_by, updated_by):
            seen["data"] = data
            return replaced, False

    async def fake_require(table_arg, old_row, new_row, user_arg, *, db):
        seen["new"] = new_row

    checks = []

    async def fake_check(action, *args, **kwargs):
        checks.append(action)

    published = {}
    monkeypatch.setattr(writes, "DocumentRepository", FakeRepo)
    monkeypatch.setattr(writes, "require_update_policy", fake_require)
    monkeypatch.setattr(writes, "_check_action", fake_check)
    monkeypatch.setattr(
        writes, "publish_document_change", AsyncMock(side_effect=_capture(published))
    )

    doc = await writes.upsert_table_document(
        db, table, user, doc_id="doc-1", data={"over": "new"},
        created_by=None, updated_by=None,
    )

    assert doc is replaced
    # Replace semantics: old keys are gone from the post-image.
    assert seen["new"] == {
        **{"over": "new"},
        "id": "doc-1",
        "table_id": seen["new"]["table_id"],
        "created_by": seen["new"]["created_by"],
        "created_at": seen["new"]["created_at"],
        "updated_by": seen["new"]["updated_by"],
        "updated_at": seen["new"]["updated_at"],
    }
    assert "keep" not in seen["new"]
    assert checks == ["create"]
    assert published["action"] == "update"
    assert published["old_row"]["over"] == "old"


@pytest.mark.asyncio
async def test_upsert_insert_path_publishes_insert_with_null_old(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()
    created = _doc(table)

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, doc_id):
            return None

        async def upsert(self, doc_id, data, *, created_by, updated_by):
            return created, True

    monkeypatch.setattr(writes, "DocumentRepository", FakeRepo)
    monkeypatch.setattr(writes, "_check_action", AsyncMock())
    published = {}
    monkeypatch.setattr(
        writes, "publish_document_change", AsyncMock(side_effect=_capture(published))
    )

    doc = await writes.upsert_table_document(
        db, table, user, doc_id="doc-1", data={"a": 1},
        created_by=None, updated_by=None,
    )

    assert doc is created
    assert published["action"] == "insert"
    assert published["old_row"] is None


@pytest.mark.asyncio
async def test_update_missing_document_404s(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, doc_id):
            return None

    monkeypatch.setattr(writes, "DocumentRepository", FakeRepo)

    with pytest.raises(writes.TableWriteNotFound) as exc_info:
        await writes.update_table_document(
            db, table, user, doc_id="missing", data={"a": 1}, updated_by=None
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_update_policy_deny_audits_commits_and_403s(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()
    existing = _doc(table)

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, doc_id):
            return existing

        async def update(self, *args, **kwargs):
            raise AssertionError("denied update must not write")

    monkeypatch.setattr(writes, "DocumentRepository", FakeRepo)
    monkeypatch.setattr(
        writes, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(writes, "preresolve_for_policies", AsyncMock())
    monkeypatch.setattr(writes, "evaluate_action", lambda *args: False)
    emit = AsyncMock()
    monkeypatch.setattr(writes, "emit_table_policy_deny", emit)
    publish = AsyncMock()
    monkeypatch.setattr(writes, "publish_document_change", publish)

    with pytest.raises(writes.TableWriteForbidden) as exc_info:
        await writes.update_table_document(
            db, table, user, doc_id="doc-1", data={"a": 1}, updated_by=None
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Access denied"
    emit.assert_awaited_once()
    assert db.calls == ["commit"]
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_missing_document_404s(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, doc_id):
            return None

    monkeypatch.setattr(writes, "DocumentRepository", FakeRepo)

    with pytest.raises(writes.TableWriteNotFound) as exc_info:
        await writes.delete_table_document(db, table, user, doc_id="missing")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_publishes_delete_after_commit(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()
    existing = _doc(table)
    events: list[tuple] = []

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, doc_id):
            return existing

        async def delete(self, doc_id):
            return True

    monkeypatch.setattr(writes, "DocumentRepository", FakeRepo)
    monkeypatch.setattr(writes, "_check_action", AsyncMock())

    async def fake_publish(*, table_id, action, old_row, new_row):
        events.append((db.calls[:], action, old_row, new_row))

    monkeypatch.setattr(writes, "publish_document_change", fake_publish)

    assert await writes.delete_table_document(db, table, user, doc_id="doc-1") is True
    assert db.calls == ["commit"]
    calls_before_publish, action, old_row, new_row = events[0]
    assert calls_before_publish == ["commit"]
    assert action == "delete"
    assert old_row["id"] == "doc-1"
    assert new_row is None


# --- batch writes ---


@pytest.mark.asyncio
async def test_batch_duplicate_ids_422_without_commit(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()
    monkeypatch.setattr(
        writes, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(writes, "preresolve_for_policies", AsyncMock())

    async def fake_write(*args, **kwargs):
        raise DuplicateBatchIds(["dup"])

    monkeypatch.setattr(writes, "write_table_batch", fake_write)

    with pytest.raises(writes.TableWriteUnprocessable) as exc_info:
        await writes.batch_write_table_documents(
            db, table, user,
            items=[writes.BatchDocumentInput(id="dup", data={}),
                   writes.BatchDocumentInput(id="dup", data={})],
            mode="insert",
        )
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == {"duplicate_ids": ["dup"]}
    assert db.calls == []


@pytest.mark.asyncio
async def test_batch_policy_denial_atomic_403_without_commit_or_publish(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()
    monkeypatch.setattr(
        writes, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(writes, "preresolve_for_policies", AsyncMock())

    async def fake_write(*args, **kwargs):
        raise BatchPolicyDenied([1])

    monkeypatch.setattr(writes, "write_table_batch", fake_write)
    publish = AsyncMock()
    monkeypatch.setattr(writes, "publish_table_invalidated", publish)

    with pytest.raises(writes.TableWriteForbidden) as exc_info:
        await writes.batch_write_table_documents(
            db, table, user,
            items=[writes.BatchDocumentInput(id="a", data={}),
                   writes.BatchDocumentInput(id="b", data={})],
            mode="insert",
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"denied_row_indices": [1]}
    assert db.calls == []
    publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_concurrent_conflict_rolls_back_and_409s(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()
    monkeypatch.setattr(
        writes, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(writes, "preresolve_for_policies", AsyncMock())

    async def fake_write(*args, **kwargs):
        raise ConcurrentBatchWrite("lost race")

    monkeypatch.setattr(writes, "write_table_batch", fake_write)

    with pytest.raises(writes.TableWriteConflict) as exc_info:
        await writes.batch_write_table_documents(
            db, table, user,
            items=[writes.BatchDocumentInput(id="a", data={})],
            mode="replace_upsert",
        )
    assert exc_info.value.status_code == 409
    assert "retry" in str(exc_info.value.detail)
    assert db.calls == ["rollback"]


@pytest.mark.asyncio
async def test_batch_commits_before_invalidate_and_preserves_order(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()
    alpha = _doc(table, "alpha")
    beta = _doc(table, "beta")
    events: list[list[str]] = []
    seen = {}

    monkeypatch.setattr(
        writes, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(writes, "preresolve_for_policies", AsyncMock())

    async def fake_write(session, tbl, rows, *, mode, policies, user):
        seen["mode"] = mode
        seen["ids"] = [row.id for row in rows]
        return BatchWriteResult(
            documents_by_index={0: alpha, 1: beta},
            previous_rows_by_index={},
            insert_conflicts=[],
        )

    async def fake_publish(table_id):
        events.append(db.calls[:])
        assert table_id == str(table.id)

    monkeypatch.setattr(writes, "write_table_batch", fake_write)
    monkeypatch.setattr(writes, "publish_table_invalidated", fake_publish)

    outcome = await writes.batch_write_table_documents(
        db, table, user,
        items=[writes.BatchDocumentInput(id="alpha", data={"v": 1}),
               writes.BatchDocumentInput(id="beta", data={"v": 2})],
        mode="insert",
    )

    assert [d.id for d in outcome.ordered_documents] == ["alpha", "beta"]
    assert outcome.inserted == 2
    assert seen["mode"] == "insert"
    assert seen["ids"] == ["alpha", "beta"]
    assert db.calls == ["commit"]
    assert events == [["commit"]]


@pytest.mark.asyncio
async def test_batch_privilege_override_denies_whole_batch(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()
    monkeypatch.setattr(
        writes, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(writes, "preresolve_for_policies", AsyncMock())
    called = False

    async def fake_write(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(writes, "write_table_batch", fake_write)

    with pytest.raises(writes.TableWriteForbidden) as exc_info:
        await writes.batch_write_table_documents(
            db, table, user,
            items=[writes.BatchDocumentInput(id="a", data={}, created_by="forged")],
            mode="insert",
        )
    assert exc_info.value.status_code == 403
    assert called is False
    assert db.calls == []


# --- batch delete ---


@pytest.mark.asyncio
async def test_batch_delete_skips_missing_and_returns_input_order(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()
    now = datetime.now(timezone.utc)
    docs = {
        "alpha": _doc(table, "alpha"),
        "beta": _doc(table, "beta"),
    }
    docs["alpha"].created_at = now
    events: list[list[str]] = []

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, doc_id):
            return docs.get(doc_id)

        async def delete(self, doc_id):
            return doc_id in docs

    monkeypatch.setattr(writes, "DocumentRepository", FakeRepo)
    monkeypatch.setattr(
        writes, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(writes, "preresolve_for_policies", AsyncMock())
    monkeypatch.setattr(writes, "evaluate_action", lambda *args: True)

    async def fake_publish(table_id):
        events.append(db.calls[:])

    monkeypatch.setattr(writes, "publish_table_invalidated", fake_publish)

    outcome = await writes.batch_delete_table_documents(
        db, table, user, ids=["beta", "missing", "alpha"]
    )

    assert outcome.deleted == 2
    assert outcome.deleted_ids == ["beta", "alpha"]
    assert db.calls == ["commit"]
    assert events == [["commit"]]


@pytest.mark.asyncio
async def test_batch_delete_denied_aborts_without_writes(monkeypatch):
    table = _table()
    user = _user()
    db = _FakeDb()
    docs = {"alpha": _doc(table, "alpha"), "beta": _doc(table, "beta")}

    class FakeRepo:
        def __init__(self, *args, **kwargs):
            pass

        async def get(self, doc_id):
            return docs.get(doc_id)

        async def delete(self, doc_id):
            raise AssertionError("denied batch must not delete")

    monkeypatch.setattr(writes, "DocumentRepository", FakeRepo)
    monkeypatch.setattr(
        writes, "load_resolved_table_policies", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(writes, "preresolve_for_policies", AsyncMock())
    monkeypatch.setattr(
        writes, "evaluate_action", lambda action, policies, row, u: row["id"] != "beta"
    )
    publish = AsyncMock()
    monkeypatch.setattr(writes, "publish_table_invalidated", publish)

    with pytest.raises(writes.TableWriteForbidden) as exc_info:
        await writes.batch_delete_table_documents(
            db, table, user, ids=["alpha", "beta"]
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"denied_row_indices": [1]}
    assert db.calls == []
    publish.assert_not_awaited()


# --- thin HTTP adapter parity ---


@pytest.mark.asyncio
async def test_router_insert_delegates_and_maps_document(monkeypatch):
    import src.routers.tables as router
    from src.models.contracts.tables import DocumentCreate

    table = SimpleNamespace(id=uuid4(), name="t")
    db = _FakeDb()
    user = _user()
    ctx = SimpleNamespace(db=db, user=user)
    doc = _doc(Table(id=table.id, name="t", organization_id=None), "doc-1")
    seen = {}

    async def fake_get_table_or_404(*args, **kwargs):
        return table

    async def fake_gate(*args, **kwargs):
        return None

    async def fake_insert(db_arg, table_arg, user_arg, **kwargs):
        seen.update(kwargs)
        assert db_arg is db
        assert table_arg is table
        assert user_arg is user
        return doc

    monkeypatch.setattr(router, "get_table_or_404", fake_get_table_or_404)
    monkeypatch.setattr(
        router, "_assert_solution_write_targets_owned_table", fake_gate
    )
    monkeypatch.setattr(router, "insert_table_document", fake_insert)

    response = await router.insert_document(
        "t", DocumentCreate(id="doc-1", data={"a": 1}, upsert=True), ctx=ctx, scope=None
    )

    assert response.id == "doc-1"
    assert seen["doc_id"] == "doc-1"
    assert seen["data"] == {"a": 1}
    assert seen["upsert"] is True


@pytest.mark.asyncio
async def test_router_batch_maps_neutral_forbidden_to_http_403(monkeypatch):
    from fastapi import HTTPException

    import src.routers.tables as router
    from src.models.contracts.tables import DocumentBatchCreate

    table = SimpleNamespace(id=uuid4(), name="t")
    ctx = SimpleNamespace(db=_FakeDb(), user=_user())

    async def fake_get_table_or_404(*args, **kwargs):
        return table

    async def fake_gate(*args, **kwargs):
        return None

    async def fake_batch(*args, **kwargs):
        raise writes.TableWriteForbidden({"denied_row_indices": [0]})

    monkeypatch.setattr(router, "get_table_or_404", fake_get_table_or_404)
    monkeypatch.setattr(
        router, "_assert_solution_write_targets_owned_table", fake_gate
    )
    monkeypatch.setattr(
        router, "_assert_explicit_scope_targets_table", AsyncMock()
    )
    monkeypatch.setattr(router, "batch_write_table_documents", fake_batch)

    with pytest.raises(HTTPException) as exc_info:
        await router.batch_documents(
            "t",
            DocumentBatchCreate(documents=[{"id": "a", "data": {}}]),
            ctx=ctx,
            scope=None,
        )
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == {"denied_row_indices": [0]}
