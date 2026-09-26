"""Stage 3b: HTTP/local parity for the full ``tables`` facade writes.

Covers the acceptance surface that does not need a forked child:

- metadata ``create/list/delete`` through the shared
  ``shared.sdk_table_metadata`` service (DTOs, 409/404 scoping,
  Solution gating, service-delete 403, no-actor fail-closed);
- document ``insert/upsert/update/delete_document/batch/batch_delete``
  through ``shared.table_document_writes`` (replace vs merge semantics,
  attribution, policy denials, batch modes/limits, Solution and
  explicit-scope write gates);
- the child transport performing real write round trips with zero HTTP
  requests: auto-create-once retries, 409 retry loops, and 404 mappings
  (``update``→None, ``delete_document``→False, ``delete_batch``→empty)
  without HTTP fallback.

Parity is asserted by invoking the real HTTP handlers and the parent
dispatcher against the same seeded rows and comparing outcomes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import multiprocessing
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    yield db_session


def _context_data(org_id=None, **kwargs):
    data = {
        "organization": {"id": str(org_id)} if org_id is not None else None,
        "is_platform_admin": kwargs.get("is_platform_admin", False),
        "execution_id": kwargs.get("execution_id", "exec-1"),
    }
    if kwargs.get("solution_id") is not None:
        data["solution_id"] = str(kwargs["solution_id"])
    if kwargs.get("service") is not None:
        data["service"] = kwargs["service"]
    return data


def _engine_principal(org_id=None, **kwargs):
    from src.services.execution.sdk_local_dispatch import principal_from_context

    return principal_from_context(_context_data(org_id, **kwargs))


def _service_principal(org_id, **kwargs):
    service_id = str(kwargs.get("service_id", uuid4()))
    attempt_id = str(kwargs.get("attempt_id", uuid4()))
    return _engine_principal(
        org_id,
        service={"service_id": service_id, "attempt_id": attempt_id},
        solution_id=kwargs.get("solution_id"),
        execution_id=attempt_id,
    )


def _table_user(principal):
    from src.services.execution.sdk_local_dispatch import (
        _table_user_for_principal,
    )

    return _table_user_for_principal(principal)


def _http_ctx(db_session, principal, *, org_id=None, solution_id=None):
    """Direct-call context carrying the token-equivalent user."""
    return SimpleNamespace(
        db=db_session,
        user=_table_user(principal),
        org_id=org_id,
        app_id=None,
        solution_id=solution_id,
        caller_solution_id=None,
    )


async def _seed_org(db_session, *, is_provider=False):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-tbl-wr-org-{uuid4().hex[:8]}",
        is_active=True,
        is_provider=is_provider,
        created_by="sdk-tables-writes-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _admin_bypass_access():
    from shared.policies.probe import make_seed_admin_bypass

    return make_seed_admin_bypass()


async def _seed_table(db_session, org_id, name, *, access=None, solution_id=None):
    from src.models.orm.tables import Table as TableModel

    table = TableModel(
        name=name,
        organization_id=org_id,
        solution_id=solution_id,
        schema={"columns": []},
        access=access if access is not None else _admin_bypass_access(),
        created_by="sdk-tables-writes-test",
    )
    db_session.add(table)
    await db_session.flush()
    return table


async def _seed_doc(db_session, table, doc_id, data, *, created_by="seed"):
    from src.models.orm.tables import Document as DocumentModel

    doc = DocumentModel(
        id=doc_id,
        table_id=table.id,
        data=data,
        created_by=created_by,
        updated_by=created_by,
    )
    db_session.add(doc)
    await db_session.flush()
    return doc


async def _seed_solution(db_session, org_id):
    from src.models.orm.solutions import Solution as SolutionModel

    sol = SolutionModel(
        slug=f"sol-{uuid4().hex[:8]}",
        name="Tables writes",
        organization_id=org_id,
        allow_inbound_access=True,
    )
    db_session.add(sol)
    await db_session.flush()
    return sol


async def _local(db_session, principal, frame):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


_VOLATILE_DOC_KEYS = ("table_id", "created_at", "updated_at")


def _stable(doc):
    """Document dict minus per-table volatile keys (ids, timestamps)."""
    return {k: v for k, v in doc.items() if k not in _VOLATILE_DOC_KEYS}


def _write_frame(op, frame_id="w-1", **fields):
    frame = {"v": 1, "id": frame_id, "op": op}
    frame.update(fields)
    return frame


@pytest.mark.asyncio
class TestCreateParity:
    async def test_create_matches_http_shape(self, db_session):
        from src.models.contracts.cli import SDKTableCreateRequest
        from src.routers.cli import cli_create_table

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        tag = uuid4().hex[:8]
        http_value = await cli_create_table(
            SDKTableCreateRequest(
                name=f"h{tag}", description="d", table_schema={"a": 1},
                scope=str(org.id),
            ),
            _http_ctx(db_session, principal, org_id=org.id),
            _table_user(principal),
            db_session,
        )
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.create", name=f"l{tag}", description="d",
                table_schema={"a": 1}, scope=str(org.id),
            ),
        )
        assert local["ok"] is True, local
        assert set(local["result"]) == set(http_value.model_dump(mode="json"))
        assert local["result"]["name"] == f"l{tag}"
        assert local["result"]["organization_id"] == str(org.id)
        assert local["result"]["table_schema"] == {"a": 1}

    async def test_duplicate_409_on_both(self, db_session):
        from src.models.contracts.cli import SDKTableCreateRequest
        from src.routers.cli import cli_create_table

        org = await _seed_org(db_session)
        name = f"dup_{uuid4().hex[:8]}"
        await _seed_table(db_session, org.id, name)
        principal = _engine_principal(org.id)
        with pytest.raises(HTTPException) as exc_info:
            await cli_create_table(
                SDKTableCreateRequest(name=name, scope=str(org.id)),
                _http_ctx(db_session, principal, org_id=org.id),
                _table_user(principal),
                db_session,
            )
        assert exc_info.value.status_code == 409
        local = await _local(
            db_session, principal,
            _write_frame("tables.create", name=name, scope=str(org.id)),
        )
        assert local["ok"] is False
        assert local["status"] == 409
        assert local["detail"] == f"Table '{name}' already exists"

    async def test_solution_context_404_on_both(self, db_session):
        from src.models.contracts.cli import SDKTableCreateRequest
        from src.routers.cli import cli_create_table

        org = await _seed_org(db_session)
        sol = await _seed_solution(db_session, org.id)
        principal = _engine_principal(org.id, solution_id=sol.id)
        with pytest.raises(HTTPException) as exc_info:
            await cli_create_table(
                SDKTableCreateRequest(
                    name=f"s{uuid4().hex[:8]}", scope=str(org.id)
                ),
                _http_ctx(db_session, principal, org_id=org.id,
                           solution_id=str(sol.id)),
                _table_user(principal),
                db_session,
            )
        assert exc_info.value.status_code == 404
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.create", name=f"s{uuid4().hex[:8]}",
                scope=str(org.id),
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 404

    async def test_malformed_scope_422_on_both(self, db_session):
        from src.models.contracts.cli import SDKTableCreateRequest
        from src.routers.cli import cli_create_table

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        with pytest.raises(HTTPException) as exc_info:
            await cli_create_table(
                SDKTableCreateRequest(
                    name=f"m{uuid4().hex[:8]}", scope="not-a-uuid"
                ),
                _http_ctx(db_session, principal, org_id=org.id),
                _table_user(principal),
                db_session,
            )
        assert exc_info.value.status_code == 422
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.create", name=f"m{uuid4().hex[:8]}",
                scope="not-a-uuid",
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 422

    async def test_cross_org_403_locally(self, db_session):
        """The parent re-validates the untrusted scope string itself.

        A non-admin initiator asking for another org fails closed at the
        parent gate — the same rule the facade's client-side
        ``resolve_scope`` enforces before any transport call, so the
        facade never reaches the parent with a forbidden scope on either
        path. (Over direct HTTP with the superuser engine token the same
        request is allowed; the facade-level denial precedes both.)
        """
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        principal = _engine_principal(org_a.id)
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.create", name=f"x{uuid4().hex[:8]}",
                scope=str(org_b.id),
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 403

    async def test_invalid_name_422_on_both(self, db_session):
        from pydantic import ValidationError

        from src.models.contracts.cli import SDKTableCreateRequest

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        # The same DTO guards both entries: direct construction fails
        # exactly as FastAPI request validation would over real HTTP.
        with pytest.raises(ValidationError):
            SDKTableCreateRequest(name="NOPE", scope=str(org.id))
        local = await _local(
            db_session, principal,
            _write_frame("tables.create", name="NOPE", scope=str(org.id)),
        )
        assert local["ok"] is False
        assert local["status"] == 422

    async def test_no_actor_fails_closed(self, db_session):
        from src.services.execution.sdk_local_dispatch import (
            LocalDispatchPrincipal,
        )

        org = await _seed_org(db_session)
        principal = LocalDispatchPrincipal(
            caller_org_id=org.id, actor_email=None
        )
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.create", name=f"n{uuid4().hex[:8]}",
                scope=str(org.id),
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 500


@pytest.mark.asyncio
class TestListDeleteParity:
    async def test_list_matches_http(self, db_session):
        from src.models.contracts.cli import SDKTableListRequest
        from src.routers.cli import cli_list_tables

        org = await _seed_org(db_session)
        tag = uuid4().hex[:8]
        await _seed_table(db_session, org.id, f"lb_{tag}")
        await _seed_table(db_session, None, f"lg_{tag}")
        principal = _engine_principal(org.id)
        http_items = await cli_list_tables(
            SDKTableListRequest(scope=str(org.id)),
            _table_user(principal),
            db_session,
        )
        local = await _local(
            db_session, principal,
            _write_frame("tables.list", scope=str(org.id)),
        )
        assert local["ok"] is True, local
        http_names = sorted(i.name for i in http_items if tag in i.name)
        local_names = sorted(i["name"] for i in local["result"]["items"]
                             if tag in i["name"])
        assert local_names == http_names == sorted([f"lb_{tag}", f"lg_{tag}"])

    async def test_delete_removes_row_on_both(self, db_session):
        from src.routers.tables import delete_table

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        tag = uuid4().hex[:8]
        http_row = await _seed_table(db_session, org.id, f"hd_{tag}")
        await delete_table(
            http_row.id, _http_ctx(db_session, principal, org_id=org.id),
            _table_user(principal),
        )
        local_row = await _seed_table(db_session, org.id, f"ld_{tag}")
        local = await _local(
            db_session, principal,
            _write_frame("tables.delete", table_id=str(local_row.id)),
        )
        assert local == {
            "v": 1, "id": "w-1", "ok": True, "result": True,
        }

    async def test_delete_absent_404_on_both(self, db_session):
        from src.routers.tables import delete_table

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        missing = uuid4()
        with pytest.raises(HTTPException) as exc_info:
            await delete_table(
                missing, _http_ctx(db_session, principal, org_id=org.id),
                _table_user(principal),
            )
        assert exc_info.value.status_code == 404
        local = await _local(
            db_session, principal,
            _write_frame("tables.delete", table_id=str(missing)),
        )
        assert local["ok"] is False
        assert local["status"] == 404

    async def test_delete_malformed_id_422(self, db_session):
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        local = await _local(
            db_session, principal,
            _write_frame("tables.delete", table_id="not-a-uuid"),
        )
        assert local["ok"] is False
        assert local["status"] == 422

    async def test_delete_solution_managed_409_on_both(self, db_session):
        from src.routers.tables import delete_table
        from src.services.solutions.guard import SOLUTION_MANAGED_MESSAGE

        org = await _seed_org(db_session)
        sol = await _seed_solution(db_session, org.id)
        principal = _engine_principal(org.id)
        tag = uuid4().hex[:8]
        http_row = await _seed_table(
            db_session, org.id, f"hs_{tag}", solution_id=sol.id
        )
        with pytest.raises(HTTPException) as exc_info:
            await delete_table(
                http_row.id, _http_ctx(db_session, principal, org_id=org.id),
                _table_user(principal),
            )
        assert exc_info.value.status_code == 409
        local_row = await _seed_table(
            db_session, org.id, f"ls_{tag}", solution_id=sol.id
        )
        local = await _local(
            db_session, principal,
            _write_frame("tables.delete", table_id=str(local_row.id)),
        )
        assert local["ok"] is False
        assert local["status"] == 409
        assert local["detail"] == SOLUTION_MANAGED_MESSAGE

    async def test_service_delete_is_403(self, db_session):
        """Route-level ``CurrentSuperuser`` is the HTTP gate; local matches."""
        org = await _seed_org(db_session)
        principal = _service_principal(org.id)
        row = await _seed_table(db_session, org.id, f"sd_{uuid4().hex[:8]}")
        local = await _local(
            db_session, principal,
            _write_frame("tables.delete", table_id=str(row.id)),
        )
        assert local["ok"] is False
        assert local["status"] == 403


@pytest.mark.asyncio
class TestInsertParity:
    async def test_insert_matches_http(self, db_session):
        from src.models.contracts.tables import DocumentCreate
        from src.routers.tables import insert_document

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        tag = uuid4().hex[:8]
        http_table = await _seed_table(db_session, org.id, f"hi_{tag}")
        local_table = await _seed_table(db_session, org.id, f"li_{tag}")
        http_value = await insert_document(
            str(http_table.id),
            DocumentCreate(id="doc-1", data={"v": 1}),
            _http_ctx(db_session, principal, org_id=None),
            scope=str(org.id),
        )
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.insert", table=str(local_table.id), doc_id="doc-1",
                data={"v": 1}, created_by=None, updated_by=None,
                scope=str(org.id), solution=None,
            ),
        )
        assert local["ok"] is True, local
        assert _stable(local["result"]) == _stable(
            http_value.model_dump(mode="json")
        )
        assert local["result"]["data"] == {"v": 1}

    async def test_insert_attribution_override(self, db_session):
        from sqlalchemy import select

        from src.models.orm.tables import Document as DocumentModel

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        table = await _seed_table(db_session, org.id, f"ia_{uuid4().hex[:8]}")
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.insert", table=str(table.id), doc_id="d1",
                data={"v": 1}, created_by="custom-actor", updated_by=None,
                scope=str(org.id), solution=None,
            ),
        )
        assert local["ok"] is True, local
        row = (
            await db_session.execute(
                select(DocumentModel).where(
                    DocumentModel.table_id == table.id,
                    DocumentModel.id == "d1",
                )
            )
        ).scalar_one()
        assert row.created_by == "custom-actor"

    async def test_insert_missing_table_404_on_both(self, db_session):
        from src.models.contracts.tables import DocumentCreate
        from src.routers.tables import insert_document

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        ghost = f"ghost_{uuid4().hex[:8]}"
        with pytest.raises(HTTPException) as exc_info:
            await insert_document(
                ghost,
                DocumentCreate(id="d", data={"v": 1}),
                _http_ctx(db_session, principal, org_id=None),
                scope=str(org.id),
            )
        assert exc_info.value.status_code == 404
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.insert", table=ghost, doc_id="d", data={"v": 1},
                created_by=None, updated_by=None,
                scope=str(org.id), solution=None,
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 404

    async def test_insert_policy_deny_403_on_both(self, db_session):
        from src.models.contracts.tables import DocumentCreate
        from src.routers.tables import insert_document

        org = await _seed_org(db_session)
        table = await _seed_table(db_session, org.id, f"deny_{uuid4().hex[:8]}")
        principal = _service_principal(org.id)
        with pytest.raises(HTTPException) as exc_info:
            await insert_document(
                str(table.id),
                DocumentCreate(id="d", data={"v": 1}),
                _http_ctx(db_session, principal, org_id=org.id),
                scope=None,
            )
        assert exc_info.value.status_code == 403
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.insert", table=str(table.id), doc_id="d",
                data={"v": 1}, created_by=None, updated_by=None,
                scope=None, solution=None,
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 403

    async def test_insert_missing_data_422(self, db_session):
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        table = await _seed_table(db_session, org.id, f"bad_{uuid4().hex[:8]}")
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.insert", table=str(table.id), doc_id="d",
                scope=str(org.id), solution=None,
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 422


@pytest.mark.asyncio
class TestUpsertParity:
    async def test_upsert_replaces_matches_http(self, db_session):
        from src.models.contracts.tables import DocumentUpsert
        from src.routers.tables import upsert_document

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        tag = uuid4().hex[:8]
        http_table = await _seed_table(db_session, org.id, f"hu_{tag}")
        local_table = await _seed_table(db_session, org.id, f"lu_{tag}")
        await _seed_doc(db_session, http_table, "k", {"v": 1, "gone": True})
        await _seed_doc(db_session, local_table, "k", {"v": 1, "gone": True})
        http_value = await upsert_document(
            str(http_table.id),
            DocumentUpsert(id="k", data={"v": 2}),
            _http_ctx(db_session, principal, org_id=None),
            scope=str(org.id),
        )
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.upsert", table=str(local_table.id), doc_id="k",
                data={"v": 2}, created_by=None, updated_by=None,
                scope=str(org.id),
            ),
        )
        assert local["ok"] is True, local
        assert _stable(local["result"]) == _stable(
            http_value.model_dump(mode="json")
        )
        # Replace semantics: the old key is gone on both paths.
        assert local["result"]["data"] == {"v": 2}

    async def test_upsert_new_row_inserts(self, db_session):
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        table = await _seed_table(db_session, org.id, f"un_{uuid4().hex[:8]}")
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.upsert", table=str(table.id), doc_id="new",
                data={"v": 1}, created_by=None, updated_by=None,
                scope=str(org.id),
            ),
        )
        assert local["ok"] is True, local
        assert local["result"]["id"] == "new"

    async def test_upsert_missing_id_422(self, db_session):
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        table = await _seed_table(db_session, org.id, f"um_{uuid4().hex[:8]}")
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.upsert", table=str(table.id),
                data={"v": 1}, scope=str(org.id),
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 422


@pytest.mark.asyncio
class TestUpdateDeleteParity:
    async def test_update_merges_matches_http(self, db_session):
        from src.models.contracts.tables import DocumentUpdate
        from src.routers.tables import update_document

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        tag = uuid4().hex[:8]
        http_table = await _seed_table(db_session, org.id, f"hp_{tag}")
        local_table = await _seed_table(db_session, org.id, f"lp_{tag}")
        await _seed_doc(db_session, http_table, "d", {"a": 1, "b": 1})
        await _seed_doc(db_session, local_table, "d", {"a": 1, "b": 1})
        http_value = await update_document(
            str(http_table.id), "d", DocumentUpdate(data={"b": 2}),
            _http_ctx(db_session, principal, org_id=None),
            scope=str(org.id),
        )
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.update", table=str(local_table.id), doc_id="d",
                data={"b": 2}, updated_by=None, scope=str(org.id),
            ),
        )
        assert local["ok"] is True, local
        assert _stable(local["result"]) == _stable(
            http_value.model_dump(mode="json")
        )
        assert local["result"]["data"] == {"a": 1, "b": 2}

    async def test_update_missing_404_on_both(self, db_session):
        from src.models.contracts.tables import DocumentUpdate
        from src.routers.tables import update_document

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        table = await _seed_table(db_session, org.id, f"pm_{uuid4().hex[:8]}")
        with pytest.raises(HTTPException) as exc_info:
            await update_document(
                str(table.id), "nope", DocumentUpdate(data={"b": 2}),
                _http_ctx(db_session, principal, org_id=None),
                scope=str(org.id),
            )
        assert exc_info.value.status_code == 404
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.update", table=str(table.id), doc_id="nope",
                data={"b": 2}, updated_by=None, scope=str(org.id),
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 404

    async def test_delete_document_matches_http(self, db_session):
        from src.routers.tables import delete_document

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        tag = uuid4().hex[:8]
        http_table = await _seed_table(db_session, org.id, f"hdd_{tag}")
        local_table = await _seed_table(db_session, org.id, f"ldd_{tag}")
        await _seed_doc(db_session, http_table, "d", {"v": 1})
        await _seed_doc(db_session, local_table, "d", {"v": 1})
        assert await delete_document(
            str(http_table.id), "d",
            _http_ctx(db_session, principal, org_id=None),
            scope=str(org.id),
        ) is None
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.delete_document", table=str(local_table.id),
                doc_id="d", scope=str(org.id),
            ),
        )
        assert local == {"v": 1, "id": "w-1", "ok": True, "result": True}

    async def test_delete_document_missing_404_on_both(self, db_session):
        from src.routers.tables import delete_document

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        table = await _seed_table(db_session, org.id, f"dm_{uuid4().hex[:8]}")
        with pytest.raises(HTTPException) as exc_info:
            await delete_document(
                str(table.id), "nope",
                _http_ctx(db_session, principal, org_id=None),
                scope=str(org.id),
            )
        assert exc_info.value.status_code == 404
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.delete_document", table=str(table.id),
                doc_id="nope", scope=str(org.id),
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 404


@pytest.mark.asyncio
class TestBatchParity:
    async def test_insert_batch_matches_http(self, db_session):
        from src.models.contracts.tables import DocumentBatchCreate
        from src.routers.tables import batch_documents

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        tag = uuid4().hex[:8]
        http_table = await _seed_table(db_session, org.id, f"hb_{tag}")
        local_table = await _seed_table(db_session, org.id, f"lb_{tag}")
        documents = [
            {"id": "a", "data": {"v": 1}},
            {"data": {"v": 2}},
        ]
        http_value = await batch_documents(
            str(http_table.id),
            DocumentBatchCreate.model_validate(
                {"documents": documents, "upsert": False}
            ),
            _http_ctx(db_session, principal, org_id=None),
            scope=str(org.id),
        )
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.batch", table=str(local_table.id),
                documents=documents, upsert=False,
                scope=str(org.id),
            ),
        )
        assert local["ok"] is True, local
        assert local["result"]["inserted"] == http_value.inserted == 2
        assert local["result"]["errors"] == []
        assert len(local["result"]["documents"]) == 2
        http_docs = {d.id: d.data for d in http_value.documents}
        local_docs = {d["id"]: d["data"] for d in local["result"]["documents"]}
        assert local_docs["a"] == http_docs["a"] == {"v": 1}
        assert len([i for i in local_docs if i != "a"]) == 1

    async def test_replace_upsert_count_only(self, db_session):
        from src.models.contracts.tables import DocumentBatchCreate
        from src.routers.tables import batch_documents, get_document

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        table = await _seed_table(db_session, org.id, f"ru_{uuid4().hex[:8]}")
        await _seed_doc(db_session, table, "k", {"v": 1, "gone": True})
        http_value = await batch_documents(
            str(table.id),
            DocumentBatchCreate.model_validate({
                "documents": [{"id": "k", "data": {"v": 2}}],
                "write_mode": "replace_upsert",
                "return_documents": False,
            }),
            _http_ctx(db_session, principal, org_id=None),
            scope=str(org.id),
        )
        assert http_value.inserted == 1
        assert http_value.documents == []
        table2 = await _seed_table(db_session, org.id, f"ru2_{uuid4().hex[:8]}")
        await _seed_doc(db_session, table2, "k", {"v": 1, "gone": True})
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.batch", table=str(table2.id),
                documents=[{"id": "k", "data": {"v": 2}}],
                write_mode="replace_upsert", return_documents=False,
                scope=str(org.id),
            ),
        )
        assert local["ok"] is True, local
        assert local["result"]["inserted"] == 1
        assert local["result"]["documents"] == []
        got = await get_document(
            str(table2.id), "k",
            _http_ctx(db_session, principal, org_id=None),
            scope=str(org.id),
        )
        assert got.data == {"v": 2}

    async def test_duplicate_ids_422_on_both(self, db_session):
        from src.models.contracts.tables import DocumentBatchCreate
        from src.routers.tables import batch_documents

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        table = await _seed_table(db_session, org.id, f"bd_{uuid4().hex[:8]}")
        dup = [
            {"id": "a", "data": {"v": 1}},
            {"id": "a", "data": {"v": 2}},
        ]
        with pytest.raises(HTTPException) as exc_info:
            await batch_documents(
                str(table.id),
                DocumentBatchCreate.model_validate({"documents": dup}),
                _http_ctx(db_session, principal, org_id=None),
                scope=str(org.id),
            )
        assert exc_info.value.status_code == 422
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.batch", table=str(table.id),
                documents=dup, scope=str(org.id),
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 422

    async def test_policy_deny_403_on_both(self, db_session):
        from src.models.contracts.tables import DocumentBatchCreate
        from src.routers.tables import batch_documents

        org = await _seed_org(db_session)
        table = await _seed_table(db_session, org.id, f"bp_{uuid4().hex[:8]}")
        await _seed_doc(db_session, table, "owned", {"v": 1},
                        created_by="someone-else")
        principal = _service_principal(org.id)
        denied = [{"id": "owned", "data": {"v": 9}}]
        with pytest.raises(HTTPException) as exc_info:
            await batch_documents(
                str(table.id),
                DocumentBatchCreate.model_validate(
                    {"documents": denied, "upsert": True}
                ),
                _http_ctx(db_session, principal, org_id=org.id),
                scope=None,
            )
        assert exc_info.value.status_code == 403
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.batch", table=str(table.id),
                documents=denied, upsert=True, scope=None,
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 403

    async def test_over_limit_422(self, db_session):
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        table = await _seed_table(db_session, org.id, f"bl_{uuid4().hex[:8]}")
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.batch", table=str(table.id),
                documents=[{"id": f"d{i}", "data": {}} for i in range(1001)],
                scope=str(org.id),
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 422

    async def test_explicit_scope_gate_404_on_both(self, db_session):
        from src.models.contracts.tables import DocumentBatchCreate
        from src.routers.tables import batch_documents

        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        table = await _seed_table(db_session, org_a.id, f"es_{uuid4().hex[:8]}")
        principal = _engine_principal(org_a.id)
        with pytest.raises(HTTPException) as exc_info:
            await batch_documents(
                str(table.id),
                DocumentBatchCreate.model_validate(
                    {"documents": [{"id": "a", "data": {}}]}
                ),
                _http_ctx(db_session, principal, org_id=org_a.id),
                scope=str(org_b.id),
            )
        assert exc_info.value.status_code == 404
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.batch", table=str(table.id),
                documents=[{"id": "a", "data": {}}],
                scope=str(org_b.id),
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 404


@pytest.mark.asyncio
class TestBatchDeleteParity:
    async def test_batch_delete_matches_http(self, db_session):
        from src.models.contracts.tables import DocumentBatchDeleteRequest
        from src.routers.tables import batch_delete_documents

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        tag = uuid4().hex[:8]
        http_table = await _seed_table(db_session, org.id, f"hbd_{tag}")
        local_table = await _seed_table(db_session, org.id, f"lbd_{tag}")
        for t in (http_table, local_table):
            await _seed_doc(db_session, t, "a", {"v": 1})
            await _seed_doc(db_session, t, "b", {"v": 2})
        http_value = await batch_delete_documents(
            str(http_table.id),
            DocumentBatchDeleteRequest(ids=["a", "ghost"]),
            _http_ctx(db_session, principal, org_id=None),
            scope=str(org.id),
        )
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.batch_delete", table=str(local_table.id),
                ids=["a", "ghost"], scope=str(org.id),
            ),
        )
        assert local["ok"] is True, local
        assert local["result"] == http_value.model_dump(mode="json")
        assert local["result"] == {"deleted": 1, "deleted_ids": ["a"]}

    async def test_batch_delete_missing_table_404_on_both(self, db_session):
        from src.models.contracts.tables import DocumentBatchDeleteRequest
        from src.routers.tables import batch_delete_documents

        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        ghost = f"ghost_{uuid4().hex[:8]}"
        with pytest.raises(HTTPException) as exc_info:
            await batch_delete_documents(
                ghost, DocumentBatchDeleteRequest(ids=["a"]),
                _http_ctx(db_session, principal, org_id=None),
                scope=str(org.id),
            )
        assert exc_info.value.status_code == 404
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.batch_delete", table=ghost, ids=["a"],
                scope=str(org.id),
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 404


@pytest.mark.asyncio
class TestSolutionWriteGate:
    async def test_solution_cannot_write_repo_table_on_both(self, db_session):
        from src.models.contracts.tables import DocumentCreate
        from src.routers.tables import insert_document

        org = await _seed_org(db_session)
        sol = await _seed_solution(db_session, org.id)
        table = await _seed_table(db_session, org.id, f"rw_{uuid4().hex[:8]}")
        principal = _engine_principal(org.id, solution_id=sol.id)
        with pytest.raises(HTTPException) as exc_info:
            await insert_document(
                str(table.id),
                DocumentCreate(id="d", data={"v": 1}),
                _http_ctx(db_session, principal, org_id=None,
                           solution_id=str(sol.id)),
                scope=str(org.id),
            )
        assert exc_info.value.status_code == 404
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.insert", table=str(table.id), doc_id="d",
                data={"v": 1}, created_by=None, updated_by=None,
                scope=str(org.id), solution=None,
            ),
        )
        assert local["ok"] is False
        assert local["status"] == 404

    async def test_own_install_write_succeeds_on_both(self, db_session):
        from src.models.contracts.tables import DocumentCreate
        from src.routers.tables import insert_document

        org = await _seed_org(db_session)
        sol = await _seed_solution(db_session, org.id)
        tag = uuid4().hex[:8]
        http_table = await _seed_table(
            db_session, org.id, f"ho_{tag}", solution_id=sol.id
        )
        local_table = await _seed_table(
            db_session, org.id, f"lo_{tag}", solution_id=sol.id
        )
        principal = _engine_principal(
            org.id, execution_id="exec-own", solution_id=sol.id
        )
        http_value = await insert_document(
            http_table.name,
            DocumentCreate(id="d", data={"v": 1}),
            _http_ctx(db_session, principal, org_id=None,
                       solution_id=str(sol.id)),
            scope=str(org.id),
        )
        local = await _local(
            db_session, principal,
            _write_frame(
                "tables.insert", table=local_table.name, doc_id="d",
                data={"v": 1}, created_by=None, updated_by=None,
                scope=str(org.id), solution=None,
            ),
        )
        assert local["ok"] is True, local
        assert local["result"]["data"] == http_value.data == {"v": 1}


@pytest.mark.asyncio
class TestMalformedWriteFrames:
    async def test_malformed_fields_are_422(self, db_session):
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        frames = [
            _write_frame("tables.create", name={"nested": 1},
                         scope=str(org.id)),
            _write_frame("tables.insert", table="t",
                         data="not-a-dict", scope=str(org.id)),
            _write_frame("tables.upsert", table="t", doc_id=42,
                         data={}, scope=str(org.id)),
            _write_frame("tables.update", table="t", doc_id="d",
                         scope=str(org.id)),
            _write_frame("tables.delete_document", table="",
                         doc_id="d", scope=str(org.id)),
            _write_frame("tables.batch", table="t", documents="nope",
                         scope=str(org.id)),
            _write_frame("tables.batch", table="t",
                         documents=[{"data": {}}],
                         write_mode="replace_upsert", scope=str(org.id)),
            _write_frame("tables.batch_delete", table="t",
                         ids="not-a-list", scope=str(org.id)),
        ]
        for frame in frames:
            response = await _local(db_session, principal, frame)
            assert response["ok"] is False, frame
            assert response["status"] == 422, frame


def _doc(table_id, doc_id="d1", data=None):
    return {
        "id": doc_id, "table_id": table_id, "data": data or {"v": 1},
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "created_by": "t", "updated_by": "t",
    }


def _table_info(name="t"):
    return {
        "id": str(uuid4()), "name": name, "organization_id": None,
        "table_schema": None, "description": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }


class TestChildTransportWrites:
    def _pair(self):
        req_recv, req_send = multiprocessing.Pipe(duplex=False)
        resp_recv, resp_send = multiprocessing.Pipe(duplex=False)
        return req_recv, req_send, resp_recv, resp_send

    def _close_all(self, conns):
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    async def _pump(self, req_conn, resp_conn, handler, count=1):
        for _ in range(count):
            raw = await asyncio.to_thread(req_conn.recv_bytes, 65537)
            frame = json.loads(raw.decode("utf-8"))
            response = handler(frame)
            await asyncio.to_thread(resp_conn.send_bytes, json.dumps(response).encode())

    def _ctx(self):
        from src.sdk.context import ExecutionContext

        return ExecutionContext(
            user_id="u1", email="e@e.com", name="T", scope="org-1",
            organization=None, is_platform_admin=False,
            is_function_key=False, execution_id="exec-1",
        )

    @pytest.mark.asyncio
    async def test_create_list_delete_use_shared_client_not_channel(self):
        from unittest.mock import AsyncMock, MagicMock

        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        info = _table_info("t")

        def _handler(frame):
            raise AssertionError(f"channel must not be used: {frame['op']}")

        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=[
            MagicMock(status_code=200, json=lambda: dict(info)),
            MagicMock(status_code=200, json=lambda: [dict(info)]),
            MagicMock(status_code=204),
        ])
        pump = asyncio.create_task(self._pump(req_recv, resp_send, _handler, count=1))
        try:
            from bifrost._context import (
                clear_execution_context,
                set_execution_context,
            )
            from bifrost.tables import tables

            set_execution_context(self._ctx())
            try:
                with patch("bifrost.tables.get_client", return_value=client):
                    created = await tables.create("t")
                    assert created.id == info["id"]
                    listed = await tables.list()
                    assert [t.id for t in listed] == [info["id"]]
                    assert await tables.delete(info["id"]) is True
            finally:
                clear_execution_context()
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
        # The migrated definition methods never read a channel frame: each
        # call went through the shared client's engine-local entry point.
        assert [
            (call.args[0], call.args[1])
            for call in client.engine_request.await_args_list
        ] == [
            ("POST", "/api/sdk/tables/create"),
            ("POST", "/api/sdk/tables/list"),
            ("DELETE", f"/api/tables/{info['id']}"),
        ]
        assert client.engine_request.await_args_list[0].kwargs["json"]["name"] == "t"

    @pytest.mark.asyncio
    async def test_insert_auto_creates_once_without_http(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        table_id = str(uuid4())
        ops = []

        def _handler(frame):
            ops.append(frame["op"])
            if frame["op"] == "tables.insert" and ops.count("tables.insert") == 1:
                return {"v": 1, "id": frame["id"], "ok": False,
                        "status": 404, "detail": "Table 't' not found"}
            if frame["op"] == "tables.create":
                return {"v": 1, "id": frame["id"], "ok": True,
                        "result": _table_info("t")}
            if frame["op"] == "tables.insert":
                return {"v": 1, "id": frame["id"], "ok": True,
                        "result": _doc(table_id, "d1")}
            raise AssertionError(frame["op"])

        pump = asyncio.create_task(self._pump(req_recv, resp_send, _handler, count=3))
        try:
            from bifrost._context import (
                clear_execution_context,
                set_execution_context,
            )
            from bifrost.tables import tables

            set_execution_context(self._ctx())
            try:
                with patch("bifrost.tables.get_client") as get_client:
                    get_client.side_effect = AssertionError("HTTP must not be used")
                    doc = await tables.insert("t", {"v": 1}, id="d1")
                    assert doc.id == "d1"
            finally:
                clear_execution_context()
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
        assert ops == ["tables.insert", "tables.create", "tables.insert"]

    @pytest.mark.asyncio
    async def test_insert_404_in_solution_never_creates(self):
        from bifrost import _local_transport as lt
        from bifrost.client import BifrostAPIError

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        ops = []

        def _handler(frame):
            ops.append(frame["op"])
            return {"v": 1, "id": frame["id"], "ok": False,
                    "status": 404, "detail": "Table 't' not found"}

        pump = asyncio.create_task(self._pump(req_recv, resp_send, _handler, count=1))
        try:
            from bifrost._context import (
                clear_execution_context,
                set_execution_context,
            )
            from bifrost.tables import tables

            ctx = self._ctx()
            ctx.solution_id = str(uuid4())
            set_execution_context(ctx)
            try:
                with patch("bifrost.tables.get_client") as get_client:
                    get_client.side_effect = AssertionError("HTTP must not be used")
                    with pytest.raises(BifrostAPIError):
                        await tables.insert("t", {"v": 1})
            finally:
                clear_execution_context()
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
        # No auto-create retry inside a Solution, and no HTTP fallback.
        assert ops == ["tables.insert"]

    @pytest.mark.asyncio
    async def test_update_delete_mappings_without_http(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        table_id = str(uuid4())

        def _handler(frame):
            if frame["op"] == "tables.update":
                if frame["doc_id"] == "missing":
                    return {"v": 1, "id": frame["id"], "ok": False,
                            "status": 404, "detail": "Document not found"}
                return {"v": 1, "id": frame["id"], "ok": True,
                        "result": _doc(table_id, "d1", {"a": 2})}
            if frame["op"] == "tables.delete_document":
                if frame["doc_id"] == "missing":
                    return {"v": 1, "id": frame["id"], "ok": False,
                            "status": 404, "detail": "Document not found"}
                return {"v": 1, "id": frame["id"], "ok": True, "result": True}
            if frame["op"] == "tables.batch_delete":
                if frame["table"] == "ghost":
                    return {"v": 1, "id": frame["id"], "ok": False,
                            "status": 404, "detail": "Table 'ghost' not found"}
                return {"v": 1, "id": frame["id"], "ok": True,
                        "result": {"deleted": 1, "deleted_ids": ["d1"]}}
            raise AssertionError(frame["op"])

        pump = asyncio.create_task(self._pump(req_recv, resp_send, _handler, count=5))
        try:
            from bifrost._context import (
                clear_execution_context,
                set_execution_context,
            )
            from bifrost.tables import tables

            set_execution_context(self._ctx())
            try:
                with patch("bifrost.tables.get_client") as get_client:
                    get_client.side_effect = AssertionError("HTTP must not be used")
                    updated = await tables.update("t", "d1", {"a": 2})
                    assert updated is not None and updated.data == {"a": 2}
                    assert await tables.update("t", "missing", {}) is None
                    assert await tables.delete_document("t", "d1") is True
                    assert await tables.delete_document("t", "missing") is False
                    deleted = await tables.delete_batch("ghost", ["d1"])
                    assert deleted.deleted_ids == [] and deleted.count == 0
            finally:
                clear_execution_context()
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_bulk_upsert_retries_409_without_http(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        calls = []

        def _handler(frame):
            assert frame["op"] == "tables.batch"
            calls.append(frame)
            if len(calls) == 1:
                return {"v": 1, "id": frame["id"], "ok": False,
                        "status": 409,
                        "detail": "Batch write conflicted with a concurrent insert"}
            return {"v": 1, "id": frame["id"], "ok": True,
                    "result": {"inserted": 2, "errors": [], "documents": []}}

        pump = asyncio.create_task(self._pump(req_recv, resp_send, _handler, count=2))
        try:
            from bifrost._context import (
                clear_execution_context,
                set_execution_context,
            )
            from bifrost.tables import tables

            set_execution_context(self._ctx())
            try:
                with patch("bifrost.tables.get_client") as get_client:
                    get_client.side_effect = AssertionError("HTTP must not be used")
                    result = await tables.bulk_upsert("t", [
                        {"id": "a", "data": {"v": 1}},
                        {"id": "b", "data": {"v": 2}},
                    ])
                    assert result.count == 2
            finally:
                clear_execution_context()
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
        assert len(calls) == 2
        assert calls[0]["write_mode"] == "replace_upsert"
        assert calls[0]["return_documents"] is False

    @pytest.mark.asyncio
    async def test_bulk_upsert_409_exhausted_raises(self):
        from bifrost import _local_transport as lt
        from bifrost.client import BifrostAPIError

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        calls = []

        def _handler(frame):
            calls.append(frame)
            return {"v": 1, "id": frame["id"], "ok": False,
                    "status": 409, "detail": "conflict"}

        pump = asyncio.create_task(self._pump(req_recv, resp_send, _handler, count=1))
        try:
            from bifrost._context import (
                clear_execution_context,
                set_execution_context,
            )
            from bifrost.tables import tables

            set_execution_context(self._ctx())
            try:
                with patch("bifrost.tables.get_client") as get_client:
                    get_client.side_effect = AssertionError("HTTP must not be used")
                    with pytest.raises(BifrostAPIError):
                        await tables.bulk_upsert(
                            "t", [{"id": "a", "data": {}}],
                            conflict_retries=0,
                        )
            finally:
                clear_execution_context()
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_write_error_never_falls_back_to_http(self):
        from bifrost import _local_transport as lt
        from bifrost.client import BifrostAuthorizationError

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)

        def _handler(frame):
            return {"v": 1, "id": frame["id"], "ok": False,
                    "status": 403, "detail": "Access denied"}

        pump = asyncio.create_task(self._pump(req_recv, resp_send, _handler, count=2))
        try:
            from bifrost._context import (
                clear_execution_context,
                set_execution_context,
            )
            from bifrost.tables import tables

            set_execution_context(self._ctx())
            try:
                with patch("bifrost.tables.get_client") as get_client:
                    get_client.side_effect = AssertionError("HTTP must not be used")
                    with pytest.raises(BifrostAuthorizationError):
                        await tables.insert("t", {"v": 1})
                    with pytest.raises(BifrostAuthorizationError):
                        await tables.upsert("t", "d", {"v": 1})
            finally:
                clear_execution_context()
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
