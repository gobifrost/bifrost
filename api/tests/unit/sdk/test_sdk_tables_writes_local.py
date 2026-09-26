"""Stage 3b: HTTP/local parity for the full ``tables`` facade writes.

Covers the acceptance surface that does not need a forked child:

- metadata ``create/list/delete`` through the shared
  ``shared.sdk_table_metadata`` service (DTOs, 409/404 scoping,
  Solution gating, service-delete 403, no-actor fail-closed);
- document ``insert/upsert/update/delete_document/batch/batch_delete``
  through ``shared.table_document_writes`` (replace vs merge semantics,
  attribution, policy denials, batch modes/limits, Solution and
  explicit-scope write gates);
- the migrated facade methods now ride the shared
  ``BifrostClient.engine_request`` transport and never touch the dedicated
  channel: auto-create-once retries, 409 retry loops, and 404 mappings
  (``update``→None, ``delete_document``→False, ``delete_batch``→empty)
  without HTTP fallback.

Parity is asserted by invoking the real HTTP handlers and the parent
dispatcher against the same seeded rows and comparing outcomes. The
``sdk_local_dispatch`` parity classes still exercise the parent dispatcher
directly; the facade no longer calls it for these writes (see Gate C3b),
but the shared service it delegates to is the same one the HTTP routes run.
"""

from __future__ import annotations

import contextlib
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


class TestSharedClientTransport:
    """Gate C3b: the migrated writes ride the shared client, not the channel.

    ``tables.insert/upsert/update/delete_document`` and the batch writes now
    call ``BifrostClient.engine_request`` (the worker Unix socket inside an
    engine child, the network API elsewhere). A pipe transport is installed
    and must stay silent; route, body, retry, auto-create, and result mapping
    stay identical to the HTTP endpoints.
    """

    @pytest.fixture(autouse=True)
    def _clear_execution_context(self):
        from bifrost._context import clear_execution_context

        clear_execution_context()
        yield
        clear_execution_context()

    def _pair(self):
        req_recv, req_send = multiprocessing.Pipe(duplex=False)
        resp_recv, resp_send = multiprocessing.Pipe(duplex=False)
        return req_recv, req_send, resp_recv, resp_send

    def _close_all(self, conns):
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    @contextlib.contextmanager
    def _silent_channel(self):
        """Install a channel that must receive no frame from the facade."""
        from bifrost import _local_transport as lt

        conns = self._pair()
        lt.install(conns[1], conns[2])
        try:
            yield conns
        finally:
            lt.clear()
            self._close_all(conns)

    @staticmethod
    def _assert_channel_silent(conns):
        assert conns[0].poll(0.1) is False, "facade wrote to the local channel"

    @staticmethod
    def _response(status: int, payload=None):
        import httpx

        return httpx.Response(
            status,
            json=payload,
            request=httpx.Request("POST", "http://api/test"),
        )

    @staticmethod
    def _client(responses):
        from unittest.mock import AsyncMock, MagicMock

        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @pytest.mark.asyncio
    async def test_insert_auto_creates_once_over_shared_client(self):
        from bifrost.tables import tables

        table_id = str(uuid4())
        client = self._client(
            [
                self._response(404, {"detail": "Table 't' not found"}),
                self._response(201, _table_info("t")),
                self._response(201, _doc(table_id, "d1")),
            ]
        )
        with self._silent_channel() as conns:
            with patch("bifrost.tables.get_client", return_value=client):
                doc = await tables.insert("t", {"v": 1}, id="d1", scope="global")
            self._assert_channel_silent(conns)

        assert doc.id == "d1"
        calls = client.engine_request.await_args_list
        assert [(c.args[0], c.args[1]) for c in calls] == [
            ("POST", "/api/tables/t/documents?scope=global"),
            ("POST", "/api/tables?scope=global"),
            ("POST", "/api/tables/t/documents?scope=global"),
        ]
        assert calls[0].kwargs["json"] == {"id": "d1", "data": {"v": 1}}
        assert calls[1].kwargs["json"] == {"name": "t"}

    @pytest.mark.asyncio
    async def test_insert_in_solution_never_auto_creates(self):
        from bifrost._context import set_execution_context
        from bifrost.client import BifrostAPIError
        from bifrost.tables import tables
        from src.sdk.context import ExecutionContext

        ctx = ExecutionContext(
            user_id="u1", email="e@e.com", name="T", scope="org-1",
            organization=None, is_platform_admin=False,
            is_function_key=False, execution_id="exec-1",
        )
        ctx.solution_id = str(uuid4())
        set_execution_context(ctx)

        client = self._client(
            [self._response(404, {"detail": "Table 't' not found"})]
        )
        with self._silent_channel() as conns:
            with patch("bifrost.tables.get_client", return_value=client):
                with pytest.raises(BifrostAPIError):
                    await tables.insert("t", {"v": 1})
            self._assert_channel_silent(conns)
        # No auto-create retry inside a Solution.
        assert client.engine_request.await_count == 1

    @pytest.mark.asyncio
    async def test_upsert_uses_replace_route_with_transient_retry(self):
        from bifrost.tables import tables

        table_id = str(uuid4())
        client = self._client([self._response(200, _doc(table_id, "k"))])
        with self._silent_channel() as conns:
            with patch("bifrost.tables.get_client", return_value=client):
                doc = await tables.upsert("t", "k", {"v": 2}, scope="global")
            self._assert_channel_silent(conns)

        assert doc.id == "k"
        call = client.engine_request.await_args_list[0]
        assert call.args == (
            "POST", "/api/tables/t/documents/upsert?scope=global",
        )
        assert call.kwargs["json"] == {"id": "k", "data": {"v": 2}}
        assert call.kwargs["retry_transient"] is True

    @pytest.mark.asyncio
    async def test_update_maps_404_to_none_with_transient_retry(self):
        from bifrost.tables import tables

        client = self._client([self._response(404, {"detail": "not found"})])
        with self._silent_channel() as conns:
            with patch("bifrost.tables.get_client", return_value=client):
                updated = await tables.update("t", "d1", {"a": 2}, scope="global")
            self._assert_channel_silent(conns)

        assert updated is None
        call = client.engine_request.await_args_list[0]
        assert call.args == (
            "PATCH", "/api/tables/t/documents/d1?scope=global",
        )
        assert call.kwargs["json"] == {"data": {"a": 2}}
        assert call.kwargs["retry_transient"] is True

    @pytest.mark.asyncio
    async def test_delete_document_maps_404_to_false(self):
        from bifrost.tables import tables

        client = self._client([self._response(404, {"detail": "not found"})])
        with self._silent_channel() as conns:
            with patch("bifrost.tables.get_client", return_value=client):
                deleted = await tables.delete_document("t", "d1", scope="global")
            self._assert_channel_silent(conns)

        assert deleted is False
        call = client.engine_request.await_args_list[0]
        assert call.args == (
            "DELETE", "/api/tables/t/documents/d1?scope=global",
        )
        assert call.kwargs == {}

    @pytest.mark.asyncio
    async def test_insert_batch_posts_batch_without_transient_retry(self):
        from bifrost.tables import tables

        table_id = str(uuid4())
        client = self._client(
            [self._response(201, {"inserted": 1, "documents": [_doc(table_id, "a")]})]
        )
        with self._silent_channel() as conns:
            with patch("bifrost.tables.get_client", return_value=client):
                result = await tables.insert_batch(
                    "t", [{"id": "a", "data": {"v": 1}}], scope="global"
                )
            self._assert_channel_silent(conns)

        assert result.count == 1
        assert result.documents[0].id == "a"
        call = client.engine_request.await_args_list[0]
        assert call.args == (
            "POST", "/api/tables/t/documents/batch?scope=global",
        )
        assert call.kwargs["json"] == {
            "documents": [{"id": "a", "data": {"v": 1}}],
            "upsert": False,
        }
        assert call.kwargs["retry_transient"] is False

    @pytest.mark.asyncio
    async def test_upsert_batch_retries_transient_with_explicit_ids(self):
        from bifrost.tables import tables

        table_id = str(uuid4())
        client = self._client(
            [self._response(200, {"inserted": 1, "documents": [_doc(table_id, "a")]})]
        )
        with self._silent_channel() as conns:
            with patch("bifrost.tables.get_client", return_value=client):
                result = await tables.upsert_batch(
                    "t", [{"id": "a", "data": {"v": 1}}], scope="global"
                )
            self._assert_channel_silent(conns)

        assert result.count == 1
        call = client.engine_request.await_args_list[0]
        assert call.kwargs["json"]["upsert"] is True
        assert call.kwargs["retry_transient"] is True

    @pytest.mark.asyncio
    async def test_insert_batch_404_auto_creates_once(self):
        from bifrost.tables import tables

        table_id = str(uuid4())
        client = self._client(
            [
                self._response(404, {"detail": "Table 't' not found"}),
                self._response(201, _table_info("t")),
                self._response(
                    201, {"inserted": 1, "documents": [_doc(table_id, "a")]}
                ),
            ]
        )
        with self._silent_channel() as conns:
            with patch("bifrost.tables.get_client", return_value=client):
                result = await tables.insert_batch(
                    "t", [{"id": "a", "data": {"v": 1}}], scope="global"
                )
            self._assert_channel_silent(conns)

        assert result.count == 1
        calls = client.engine_request.await_args_list
        assert [(c.args[0], c.args[1]) for c in calls] == [
            ("POST", "/api/tables/t/documents/batch?scope=global"),
            ("POST", "/api/tables?scope=global"),
            ("POST", "/api/tables/t/documents/batch?scope=global"),
        ]

    @pytest.mark.asyncio
    async def test_bulk_upsert_retries_bounded_409_count_only(self):
        from bifrost.tables import tables

        client = self._client(
            [
                self._response(409, {"detail": "conflict"}),
                self._response(200, {"inserted": 2, "documents": []}),
            ]
        )
        with self._silent_channel() as conns:
            with patch("bifrost.tables.get_client", return_value=client):
                result = await tables.bulk_upsert(
                    "t",
                    [{"id": "a", "data": {}}, {"id": "b", "data": {}}],
                    scope="global",
                    conflict_retries=1,
                )
            self._assert_channel_silent(conns)

        assert result.count == 2
        calls = client.engine_request.await_args_list
        assert len(calls) == 2
        assert calls[0].args == (
            "POST", "/api/tables/t/documents/batch?scope=global",
        )
        assert calls[0].kwargs["json"]["write_mode"] == "replace_upsert"
        assert calls[0].kwargs["json"]["return_documents"] is False
        assert "retry_transient" not in calls[0].kwargs

    @pytest.mark.asyncio
    async def test_bulk_upsert_404_auto_creates_then_retries(self):
        from bifrost.tables import tables

        client = self._client(
            [
                self._response(404, {"detail": "Table 't' not found"}),
                self._response(201, _table_info("t")),
                self._response(200, {"inserted": 1, "documents": []}),
            ]
        )
        with self._silent_channel() as conns:
            with patch("bifrost.tables.get_client", return_value=client):
                result = await tables.bulk_upsert(
                    "t", [{"id": "a", "data": {}}], scope="global"
                )
            self._assert_channel_silent(conns)

        assert result.count == 1
        calls = client.engine_request.await_args_list
        assert [(c.args[0], c.args[1]) for c in calls] == [
            ("POST", "/api/tables/t/documents/batch?scope=global"),
            ("POST", "/api/tables?scope=global"),
            ("POST", "/api/tables/t/documents/batch?scope=global"),
        ]

    @pytest.mark.asyncio
    async def test_delete_batch_maps_404_to_empty(self):
        from bifrost.tables import tables

        client = self._client([self._response(404, {"detail": "not found"})])
        with self._silent_channel() as conns:
            with patch("bifrost.tables.get_client", return_value=client):
                result = await tables.delete_batch("t", ["a"], scope="global")
            self._assert_channel_silent(conns)

        assert result.deleted_ids == [] and result.count == 0
        call = client.engine_request.await_args_list[0]
        assert call.args == (
            "POST", "/api/tables/t/documents/batch-delete?scope=global",
        )
        assert call.kwargs["json"] == {"ids": ["a"]}

    @pytest.mark.asyncio
    async def test_delete_batch_maps_response(self):
        from bifrost.tables import tables

        client = self._client(
            [self._response(200, {"deleted": 1, "deleted_ids": ["a"]})]
        )
        with self._silent_channel() as conns:
            with patch("bifrost.tables.get_client", return_value=client):
                result = await tables.delete_batch("t", ["a", "ghost"], scope="global")
            self._assert_channel_silent(conns)

        assert result.deleted_ids == ["a"] and result.count == 1
