"""Stage 3a: HTTP/local parity for ``tables.get/query/count``.

Covers the acceptance surface that does not need a forked child:

- the shared resolution (``shared.table_resolution``) + read service
  (``shared.table_documents``) behave identically behind the HTTP handlers
  and the parent dispatcher (DTOs, pagination, 404/403, deny audit);
- the parent dispatcher builds token-equivalent identities (engine
  superuser vs service non-superuser — never the initiating user's admin
  flag), keeps the service's 404/403 statuses, ignores forged child
  install claims, and serves each request on one short session;
- the migrated facade methods (``get/query/count``) ride the shared
  ``BifrostClient.engine_request`` transport and never touch the dedicated
  channel, map 404s to the facade's method-specific results, and compose
  filtered counts through ``tables.query``.

The ``sdk_local_dispatch`` parity classes still exercise the parent
dispatcher directly; the facade no longer calls it for these reads (see
Gate C3a), but the shared service it delegates to is the same one the HTTP
routes run.
"""

from __future__ import annotations

import base64
import contextlib
import json
import multiprocessing
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.tables import DocumentQuery


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    yield db_session


def _context_data(org_id=None, **kwargs):
    """Parent-owned dispatch context shaped like the consumer's."""
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
    """Real HTTP ExecutionContext type carrying the token-equivalent user."""
    from src.core.auth import ExecutionContext

    return ExecutionContext(
        user=_table_user(principal),
        org_id=org_id,
        db=db_session,
        solution_id=solution_id,
    )


async def _seed_org(db_session, *, is_provider=False):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-tables-org-{uuid4().hex[:8]}",
        is_active=True,
        is_provider=is_provider,
        created_by="sdk-tables-test",
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
        created_by="sdk-tables-test",
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


async def _seed_solution(db_session, org_id, *, inbound=True):
    from src.models.orm.solutions import Solution as SolutionModel

    sol = SolutionModel(
        slug=f"sol-{uuid4().hex[:8]}",
        name="Tables E2E",
        organization_id=org_id,
        allow_inbound_access=inbound,
    )
    db_session.add(sol)
    await db_session.flush()
    return sol


# ---------------------------------------------------------------------------
# Local dispatch entry points
# ---------------------------------------------------------------------------


async def _local_get(db_session, *, table, doc_id, scope, solution, principal):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(
        lambda: _db_factory(db_session),
        principal,
        {
            "v": 1, "id": "get-1", "op": "tables.get",
            "table": table, "doc_id": doc_id,
            "scope": scope, "solution": solution,
        },
    )


async def _local_query(db_session, *, table, query, scope, solution, principal):
    from src.services.execution.sdk_local_dispatch import dispatch_frames

    frames = await dispatch_frames(
        lambda: _db_factory(db_session),
        principal,
        {
            "v": 1, "id": "query-1", "op": "tables.query",
            "table": table, "query": query,
            "scope": scope, "solution": solution,
        },
    )
    collected = list(frames)
    first = collected[0]
    if first.get("chunked"):
        raw = b"".join(base64.b64decode(part["data"]) for part in collected[1:])
        assert len(raw) == first["total"]
        return {"ok": True, "result": json.loads(raw)}
    return first


async def _local_count(db_session, *, table, scope, solution, principal):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(
        lambda: _db_factory(db_session),
        principal,
        {
            "v": 1, "id": "count-1", "op": "tables.count",
            "table": table, "scope": scope, "solution": solution,
        },
    )


async def _http_get(db_session, *, table, doc_id, scope, ctx):
    from src.routers.tables import get_document

    return await get_document(table, doc_id, ctx, scope=scope)


async def _http_query(db_session, *, table, query, scope, ctx):
    from src.routers.tables import query_documents

    return await query_documents(table, DocumentQuery.model_validate(query), ctx, scope=scope)


async def _http_count(db_session, *, table, scope, ctx):
    from src.routers.tables import count_documents

    return await count_documents(table, ctx, scope=scope)


@pytest.mark.asyncio
class TestGetParity:
    async def test_open_table_round_trip_matches_http(self, db_session):
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        table = await _seed_table(db_session, org.id, f"customers_{tag}")
        await _seed_doc(db_session, table, "acme-1", {"status": "active"})
        principal = _engine_principal(org.id)
        http_value = await _http_get(
            db_session, table=str(table.id), doc_id="acme-1",
            scope=str(org.id),
            ctx=_http_ctx(db_session, principal, org_id=None),
        )
        local = await _local_get(
            db_session, table=str(table.id), doc_id="acme-1",
            scope=str(org.id), solution=None, principal=principal,
        )
        assert local["ok"] is True, local
        assert local["result"] == http_value.model_dump(mode="json")
        assert local["result"]["data"] == {"status": "active"}

    async def test_missing_row_404_on_both(self, db_session):
        org = await _seed_org(db_session)
        table = await _seed_table(db_session, org.id, f"t_{uuid4().hex[:8]}")
        principal = _engine_principal(org.id)
        with pytest.raises(HTTPException) as exc_info:
            await _http_get(
                db_session, table=str(table.id), doc_id="nope",
                scope=str(org.id),
                ctx=_http_ctx(db_session, principal, org_id=None),
            )
        assert exc_info.value.status_code == 404
        local = await _local_get(
            db_session, table=str(table.id), doc_id="nope",
            scope=str(org.id), solution=None, principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 404

    async def test_missing_table_404_on_both(self, db_session):
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        with pytest.raises(HTTPException) as exc_info:
            await _http_get(
                db_session, table=f"ghost_{uuid4().hex[:8]}", doc_id="x",
                scope=str(org.id),
                ctx=_http_ctx(db_session, principal, org_id=None),
            )
        assert exc_info.value.status_code == 404
        local = await _local_get(
            db_session, table=f"ghost_{uuid4().hex[:8]}", doc_id="x",
            scope=str(org.id), solution=None, principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 404

    async def test_name_lookup_matches_http(self, db_session):
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        table = await _seed_table(db_session, org.id, f"named_{tag}")
        await _seed_doc(db_session, table, "d1", {"v": 1})
        principal = _engine_principal(org.id)
        http_value = await _http_get(
            db_session, table=f"named_{tag}", doc_id="d1",
            scope=str(org.id),
            ctx=_http_ctx(db_session, principal, org_id=None),
        )
        local = await _local_get(
            db_session, table=f"named_{tag}", doc_id="d1",
            scope=str(org.id), solution=None, principal=principal,
        )
        assert local["ok"] is True, local
        assert local["result"] == http_value.model_dump(mode="json")


@pytest.mark.asyncio
class TestQueryParity:
    async def _seed(self, db_session, n=5):
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        table = await _seed_table(db_session, org.id, f"q_{tag}")
        for i in range(n):
            await _seed_doc(
                db_session, table, f"doc-{i:02d}",
                {"status": "active" if i % 2 == 0 else "archived", "n": i},
            )
        return org, table

    async def test_full_query_matches_http(self, db_session):
        org, table = await self._seed(db_session)
        principal = _engine_principal(org.id)
        query = {"where": {"status": "active"}, "order_by": "n",
                 "order_dir": "desc", "limit": 2, "offset": 1}
        http_value = await _http_query(
            db_session, table=str(table.id), query=query,
            scope=str(org.id),
            ctx=_http_ctx(db_session, principal, org_id=None),
        )
        local = await _local_query(
            db_session, table=str(table.id), query=query,
            scope=str(org.id), solution=None, principal=principal,
        )
        assert local["ok"] is True, local
        assert local["result"] == http_value.model_dump(mode="json")
        assert local["result"]["total"] == 3
        assert [d["id"] for d in local["result"]["documents"]] == ["doc-02", "doc-00"]

    async def test_dto_defaults_apply_locally(self, db_session):
        org, table = await self._seed(db_session, n=3)
        principal = _engine_principal(org.id)
        local = await _local_query(
            db_session, table=str(table.id), query={},
            scope=str(org.id), solution=None, principal=principal,
        )
        assert local["ok"] is True, local
        assert local["result"]["limit"] == 100
        assert local["result"]["offset"] == 0
        assert local["result"]["total"] == 3
        assert len(local["result"]["documents"]) == 3

    async def test_pagination_modes_match_http(self, db_session):
        org, table = await self._seed(db_session, n=4)
        principal = _engine_principal(org.id)
        for query in (
            {"after_document_id": "doc-01"},
            {"document_id_prefix": "doc-0"},
            {"document_ids": ["doc-03", "doc-00", "doc-03"]},
            {"skip_count": True, "limit": 2},
        ):
            http_value = await _http_query(
                db_session, table=str(table.id), query=query,
                scope=str(org.id),
                ctx=_http_ctx(db_session, principal, org_id=None),
            )
            local = await _local_query(
                db_session, table=str(table.id), query=query,
                scope=str(org.id), solution=None, principal=principal,
            )
            assert local["ok"] is True, (query, local)
            assert local["result"] == http_value.model_dump(mode="json"), query

    async def test_invalid_query_is_422(self, db_session):
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        local = await _local_query(
            db_session, table="t", query={"limit": 0},
            scope=str(org.id), solution=None, principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 422

    async def test_missing_table_query_404_on_both(self, db_session):
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        with pytest.raises(HTTPException) as exc_info:
            await _http_query(
                db_session, table=f"ghost_{uuid4().hex[:8]}", query={},
                scope=str(org.id),
                ctx=_http_ctx(db_session, principal, org_id=None),
            )
        assert exc_info.value.status_code == 404
        local = await _local_query(
            db_session, table=f"ghost_{uuid4().hex[:8]}", query={},
            scope=str(org.id), solution=None, principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 404


@pytest.mark.asyncio
class TestCountParity:
    async def test_unfiltered_count_matches_http(self, db_session):
        org = await _seed_org(db_session)
        table = await _seed_table(db_session, org.id, f"c_{uuid4().hex[:8]}")
        for i in range(3):
            await _seed_doc(db_session, table, f"d{i}", {"v": i})
        principal = _engine_principal(org.id)
        http_value = await _http_count(
            db_session, table=str(table.id), scope=str(org.id),
            ctx=_http_ctx(db_session, principal, org_id=None),
        )
        local = await _local_count(
            db_session, table=str(table.id),
            scope=str(org.id), solution=None, principal=principal,
        )
        assert local["ok"] is True, local
        assert local["result"] == http_value.model_dump(mode="json") == {"count": 3}

    async def test_missing_table_count_404_on_both(self, db_session):
        org = await _seed_org(db_session)
        principal = _engine_principal(org.id)
        with pytest.raises(HTTPException) as exc_info:
            await _http_count(
                db_session, table=f"ghost_{uuid4().hex[:8]}",
                scope=str(org.id),
                ctx=_http_ctx(db_session, principal, org_id=None),
            )
        assert exc_info.value.status_code == 404
        local = await _local_count(
            db_session, table=f"ghost_{uuid4().hex[:8]}",
            scope=str(org.id), solution=None, principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 404


@pytest.mark.asyncio
class TestPoliciesAndDenyAudit:
    async def test_service_get_denied_403_with_audit_on_both(
        self, db_session, monkeypatch
    ):
        """Deny parity: 403 on both paths via the same shared deny emission.

        Direct handler invocation carries no HTTP actor context, so the
        audit row itself only materializes over real HTTP (covered by
        ``test_policies.py::test_denial_writes_audit_row``). Here we spy
        the shared ``emit_table_policy_deny`` call both paths funnel
        through and assert identical emission arguments.
        """
        from unittest.mock import AsyncMock

        import shared.table_documents as table_documents

        org = await _seed_org(db_session)
        table = await _seed_table(db_session, org.id, f"deny_{uuid4().hex[:8]}")
        await _seed_doc(db_session, table, "owned", {"v": 1}, created_by="someone-else")
        principal = _service_principal(org.id)

        emitted = AsyncMock()
        monkeypatch.setattr(
            table_documents, "emit_table_policy_deny", emitted
        )
        with pytest.raises(HTTPException) as exc_info:
            await _http_get(
                db_session, table=str(table.id), doc_id="owned",
                scope=None,
                ctx=_http_ctx(db_session, principal, org_id=org.id),
            )
        assert exc_info.value.status_code == 403
        local = await _local_get(
            db_session, table=str(table.id), doc_id="owned",
            scope=None, solution=None, principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 403
        # One shared deny emission per path, with identical arguments.
        assert emitted.await_count == 2
        for call in emitted.await_calls:
            assert call.kwargs["policy_action"] == "read"
            assert call.kwargs["table_id"] == table.id
            assert call.kwargs["table_name"] == table.name

    async def test_service_query_without_grant_is_empty_on_both(self, db_session):
        org = await _seed_org(db_session)
        table = await _seed_table(db_session, org.id, f"qdeny_{uuid4().hex[:8]}")
        await _seed_doc(db_session, table, "d1", {"v": 1})
        principal = _service_principal(org.id)
        http_value = await _http_query(
            db_session, table=str(table.id), query={},
            scope=None,
            ctx=_http_ctx(db_session, principal, org_id=org.id),
        )
        local = await _local_query(
            db_session, table=str(table.id), query={},
            scope=None, solution=None, principal=principal,
        )
        assert http_value.documents == []
        assert local["ok"] is True, local
        assert local["result"]["documents"] == []
        assert local["result"]["total"] == 0

    async def test_service_count_without_grant_is_zero_on_both(self, db_session):
        org = await _seed_org(db_session)
        table = await _seed_table(db_session, org.id, f"cdeny_{uuid4().hex[:8]}")
        await _seed_doc(db_session, table, "d1", {"v": 1})
        principal = _service_principal(org.id)
        http_value = await _http_count(
            db_session, table=str(table.id), scope=None,
            ctx=_http_ctx(db_session, principal, org_id=org.id),
        )
        local = await _local_count(
            db_session, table=str(table.id),
            scope=None, solution=None, principal=principal,
        )
        assert http_value.count == 0
        assert local["ok"] is True, local
        assert local["result"] == {"count": 0}

    async def test_service_reads_own_row_on_both(self, db_session):
        from src.core.constants import SYSTEM_USER_UUID

        org = await _seed_org(db_session)
        service_id = str(uuid4())
        access = {"policies": [{
            "name": "own_row",
            "actions": ["read"],
            "when": {"eq": [{"row": "created_by"}, {"user": "user_id"}]},
        }]}
        table = await _seed_table(
            db_session, org.id, f"own_{uuid4().hex[:8]}", access=access
        )
        # Attribution stores the caller id (UUID string), which the own-row
        # policy compares against ``user.user_id`` — the service token's
        # system-user id on both paths.
        await _seed_doc(
            db_session, table, "mine", {"v": 1},
            created_by=str(SYSTEM_USER_UUID),
        )
        await _seed_doc(db_session, table, "theirs", {"v": 2}, created_by="other")
        principal = _service_principal(org.id, service_id=service_id)
        http_value = await _http_query(
            db_session, table=str(table.id), query={},
            scope=None,
            ctx=_http_ctx(db_session, principal, org_id=org.id),
        )
        local = await _local_query(
            db_session, table=str(table.id), query={},
            scope=None, solution=None, principal=principal,
        )
        assert [d.id for d in http_value.documents] == ["mine"]
        assert local["ok"] is True, local
        assert [d["id"] for d in local["result"]["documents"]] == ["mine"]


@pytest.mark.asyncio
class TestServiceIdentityDiffersFromInitiator:
    async def test_service_ignores_initiator_platform_admin(self, db_session):
        """The initiator's admin flag must not leak into table access."""
        org = await _seed_org(db_session)
        table = await _seed_table(db_session, org.id, f"adm_{uuid4().hex[:8]}")
        await _seed_doc(db_session, table, "d1", {"v": 1})
        # Initiator is a platform admin, but the service token is not.
        principal = _engine_principal(
            org.id,
            is_platform_admin=True,
            service={
                "service_id": str(uuid4()),
                "attempt_id": str(uuid4()),
            },
        )
        assert principal.is_platform_admin is False
        user = _table_user(principal)
        assert user.is_superuser is False
        local = await _local_get(
            db_session, table=str(table.id), doc_id="d1",
            scope=None, solution=None, principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 403

    async def test_workflow_engine_is_superuser_without_initiator_admin(
        self, db_session
    ):
        org = await _seed_org(db_session)
        table = await _seed_table(db_session, org.id, f"eng_{uuid4().hex[:8]}")
        await _seed_doc(db_session, table, "d1", {"v": 1})
        principal = _engine_principal(org.id, is_platform_admin=False)
        user = _table_user(principal)
        assert user.is_superuser is True
        local = await _local_get(
            db_session, table=str(table.id), doc_id="d1",
            scope=str(org.id), solution=None, principal=principal,
        )
        assert local["ok"] is True, local

    async def test_engine_user_carries_execution_and_solution_claims(self):
        from src.core.constants import SYSTEM_USER_UUID
        from src.core.security import ENGINE_SDK_ACTOR_EMAIL

        org_id = uuid4()
        solution_id = uuid4()
        principal = _engine_principal(
            org_id, execution_id="exec-9", solution_id=solution_id
        )
        assert principal.execution_id == "exec-9"
        user = _table_user(principal)
        assert user.user_id == SYSTEM_USER_UUID
        assert user.email == ENGINE_SDK_ACTOR_EMAIL
        assert user.is_superuser is True
        assert user.engine_execution_id == "exec-9"
        assert user.engine_solution_id == str(solution_id)

    async def test_service_user_carries_service_claims(self):
        from src.core.constants import SYSTEM_USER_UUID
        from src.core.security import service_sdk_actor_email

        org_id = uuid4()
        service_id = str(uuid4())
        attempt_id = str(uuid4())
        principal = _service_principal(
            org_id, service_id=service_id, attempt_id=attempt_id
        )
        user = _table_user(principal)
        assert user.user_id == SYSTEM_USER_UUID
        assert user.email == service_sdk_actor_email(service_id)
        assert user.is_superuser is False
        assert user.organization_id == org_id
        assert user.service_id == service_id
        assert user.service_attempt_id == attempt_id
        assert user.engine_execution_id == attempt_id
        from src.services.solution_scope import is_service_principal

        assert is_service_principal(user) is True


@pytest.mark.asyncio
class TestSolutionGating:
    async def _install(self, db_session, org, *, inbound=True):
        sol = await _seed_solution(db_session, org.id, inbound=inbound)
        table = await _seed_table(
            db_session, org.id, f"sol_{uuid4().hex[:8]}", solution_id=sol.id
        )
        await _seed_doc(db_session, table, "r1", {"v": 1})
        return sol, table

    async def test_own_install_name_resolves_locally(self, db_session):
        org = await _seed_org(db_session)
        sol, table = await self._install(db_session, org)
        principal = _engine_principal(
            org.id, execution_id="exec-own", solution_id=sol.id
        )
        local = await _local_get(
            db_session, table=table.name, doc_id="r1",
            scope=str(org.id), solution=None, principal=principal,
        )
        assert local["ok"] is True, local
        assert local["result"]["data"] == {"v": 1}

    async def test_explicit_own_target_resolves(self, db_session):
        org = await _seed_org(db_session)
        sol, table = await self._install(db_session, org)
        principal = _engine_principal(
            org.id, execution_id="exec-own", solution_id=sol.id
        )
        local = await _local_get(
            db_session, table=table.name, doc_id="r1",
            scope=str(org.id), solution=str(sol.id), principal=principal,
        )
        assert local["ok"] is True, local

    async def test_slug_target_resolves(self, db_session):
        org = await _seed_org(db_session)
        sol, table = await self._install(db_session, org)
        principal = _engine_principal(
            org.id, execution_id="exec-own", solution_id=sol.id
        )
        local = await _local_get(
            db_session, table=table.name, doc_id="r1",
            scope=str(org.id), solution=sol.slug, principal=principal,
        )
        assert local["ok"] is True, local

    async def test_cross_install_sealed_target_404s(self, db_session):
        org = await _seed_org(db_session)
        sealed, sealed_table = await self._install(db_session, org, inbound=False)
        other = await _seed_solution(db_session, org.id, inbound=True)
        principal = _engine_principal(
            org.id, execution_id="exec-other", solution_id=other.id
        )
        local = await _local_get(
            db_session, table=sealed_table.name, doc_id="r1",
            scope=str(org.id), solution=str(sealed.id), principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 404

    async def test_forged_caller_claims_are_ignored(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        org = await _seed_org(db_session)
        sealed, sealed_table = await self._install(db_session, org, inbound=False)
        other = await _seed_solution(db_session, org.id, inbound=True)
        principal = _engine_principal(
            org.id, execution_id="exec-other", solution_id=other.id
        )
        # A hostile child stuffing trusted-claim fields into the frame must
        # not escalate: they are never read by the dispatcher.
        response = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {
                "v": 1, "id": "forge-1", "op": "tables.get",
                "table": sealed_table.name, "doc_id": "r1",
                "scope": str(org.id), "solution": str(sealed.id),
                "caller_solution": str(sealed.id),
                "app_id": "whatever",
                "user": {"is_superuser": True},
            },
        )
        assert response["ok"] is False
        assert response["status"] == 404

    async def test_cross_install_uuid_direct_access_gated(self, db_session):
        org = await _seed_org(db_session)
        sealed, sealed_table = await self._install(db_session, org, inbound=False)
        other = await _seed_solution(db_session, org.id, inbound=True)
        principal = _engine_principal(
            org.id, execution_id="exec-other", solution_id=other.id
        )
        local = await _local_get(
            db_session, table=str(sealed_table.id), doc_id="r1",
            scope=str(org.id), solution=str(sealed.id), principal=principal,
        )
        assert local["ok"] is False
        assert local["status"] == 404

    async def test_open_install_cross_target_resolves(self, db_session):
        org = await _seed_org(db_session)
        target, target_table = await self._install(db_session, org, inbound=True)
        other = await _seed_solution(db_session, org.id, inbound=True)
        principal = _engine_principal(
            org.id, execution_id="exec-other", solution_id=other.id
        )
        local = await _local_get(
            db_session, table=target_table.name, doc_id="r1",
            scope=str(org.id), solution=str(target.id), principal=principal,
        )
        assert local["ok"] is True, local

    async def test_own_install_uuid_matches_http(self, db_session):
        org = await _seed_org(db_session)
        sol, table = await self._install(db_session, org)
        principal = _engine_principal(
            org.id, execution_id="exec-own", solution_id=sol.id
        )
        http_value = await _http_get(
            db_session, table=str(table.id), doc_id="r1",
            scope=str(org.id),
            ctx=_http_ctx(
                db_session, principal, org_id=None, solution_id=str(sol.id)
            ),
        )
        local = await _local_get(
            db_session, table=str(table.id), doc_id="r1",
            scope=str(org.id), solution=str(sol.id), principal=principal,
        )
        assert local["ok"] is True, local
        assert local["result"] == http_value.model_dump(mode="json")


@pytest.mark.asyncio
class TestMalformedFrames:
    async def test_unknown_operation_rejected(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        principal = _engine_principal(is_platform_admin=True)
        response = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {"v": 1, "id": "bad-op", "op": "tables.drop"},
        )
        assert response["ok"] is False
        assert response["status"] == 404

    async def test_bad_version_rejected(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        principal = _engine_principal(is_platform_admin=True)
        response = await dispatch_frame(
            lambda: _db_factory(db_session),
            principal,
            {"v": 999, "id": "bad-v", "op": "tables.get",
             "table": "t", "doc_id": "d"},
        )
        assert response["ok"] is False
        assert response["status"] == 400

    async def test_malformed_fields_are_422(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        principal = _engine_principal(is_platform_admin=True)
        frames = [
            {"v": 1, "id": "m-1", "op": "tables.get",
             "table": {"nested": 1}, "doc_id": "d"},
            {"v": 1, "id": "m-2", "op": "tables.get",
             "table": "t", "doc_id": 42},
            {"v": 1, "id": "m-3", "op": "tables.get",
             "table": "", "doc_id": "d"},
            {"v": 1, "id": "m-4", "op": "tables.query",
             "table": "t", "query": [1, 2]},
            {"v": 1, "id": "m-5", "op": "tables.query",
             "table": "t", "query": {"order_dir": "sideways"}},
            {"v": 1, "id": "m-6", "op": "tables.count",
             "table": "t", "scope": {"nested": 1}},
            {"v": 1, "id": "m-7", "op": "tables.count",
             "table": "t", "solution": 42},
        ]
        for frame in frames:
            response = await dispatch_frame(
                lambda: _db_factory(db_session), principal, frame
            )
            assert response["ok"] is False, frame
            assert response["status"] == 422, frame


@pytest.mark.asyncio
class TestSessionsAndChunking:
    async def test_one_short_session_per_table_request(self, db_session):
        from src.services.execution.sdk_local_dispatch import dispatch_frame

        org = await _seed_org(db_session)
        table = await _seed_table(db_session, org.id, f"sess_{uuid4().hex[:8]}")
        await _seed_doc(db_session, table, "d1", {"v": 1})
        principal = _engine_principal(org.id)
        opened = 0
        closed = 0

        @contextlib.asynccontextmanager
        async def _counting_factory():
            nonlocal opened, closed
            opened += 1
            try:
                yield db_session
            finally:
                closed += 1

        frames = [
            {"v": 1, "id": "s-1", "op": "tables.get",
             "table": str(table.id), "doc_id": "d1",
             "scope": str(org.id), "solution": None},
            {"v": 1, "id": "s-2", "op": "tables.query",
             "table": str(table.id), "query": {},
             "scope": str(org.id), "solution": None},
            {"v": 1, "id": "s-3", "op": "tables.count",
             "table": str(table.id),
             "scope": str(org.id), "solution": None},
        ]
        for frame in frames:
            response = await dispatch_frame(_counting_factory, principal, frame)
            assert response["ok"] is True, (frame, response)
        assert opened == 3
        assert closed == 3

    async def test_large_query_result_splits_into_bounded_frames(self):
        import json as _json

        from bifrost._local_transport import MAX_FRAME_BYTES
        from src.services.execution.sdk_local_dispatch import dispatch_frames

        big = "x" * 2000
        payload = {
            "table_id": str(uuid4()),
            "documents": [
                {"id": f"d{i}", "table_id": str(uuid4()), "data": {"blob": big},
                 "created_at": "2026-01-01T00:00:00+00:00",
                 "updated_at": "2026-01-01T00:00:00+00:00",
                 "created_by": "t", "updated_by": "t"}
                for i in range(100)
            ],
            "total": 100, "limit": 100, "offset": 0,
        }

        @contextlib.asynccontextmanager
        async def _stub_factory():
            yield object()

        from unittest.mock import AsyncMock

        principal = _engine_principal(is_platform_admin=True)
        with (
            patch(
                "shared.table_documents.query_table_documents",
                new=AsyncMock(
                    side_effect=lambda db, table, params, user: _resp(payload)
                ),
            ),
            patch(
                "shared.table_resolution.get_table_or_404",
                new=AsyncMock(return_value=object()),
            ),
        ):
            frames = await dispatch_frames(
                _stub_factory,
                principal,
                {"v": 1, "id": "big-q", "op": "tables.query",
                 "table": "t", "query": {}, "scope": "global",
                 "solution": None},
            )
            collected = list(frames)
        assert len(collected) > 1
        header, parts = collected[0], collected[1:]
        assert header["ok"] is True and header["chunked"] is True
        assert header["parts"] == len(parts) >= 2
        for i, part in enumerate(parts):
            assert part["id"] == "big-q" and part["part"] == i
            raw = _json.dumps(part, separators=(",", ":")).encode()
            assert len(raw) <= MAX_FRAME_BYTES


def _resp(payload):
    from src.models.contracts.tables import DocumentListResponse

    return DocumentListResponse.model_validate(payload)


class TestSharedClientTransport:
    """Gate C3a: the migrated reads ride the shared client, not the channel.

    ``tables.get/query/count`` now go through
    ``BifrostClient.engine_request`` (the worker Unix socket inside an engine
    child, the network API elsewhere). A pipe transport is installed and
    every migrated call must ignore it; the extracted document, 404, and
    error mapping stays identical to the HTTP endpoints.
    """

    def _pair(self):
        req_recv, req_send = multiprocessing.Pipe(duplex=False)
        resp_recv, resp_send = multiprocessing.Pipe(duplex=False)
        return req_recv, req_send, resp_recv, resp_send

    def _close_all(self, conns):
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    def _client(self, responses):
        from unittest.mock import AsyncMock, MagicMock

        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @pytest.mark.asyncio
    async def test_get_query_count_use_shared_client_not_channel(self):
        import httpx

        from bifrost import _local_transport as lt
        from bifrost._context import (
            clear_execution_context,
            set_execution_context,
        )
        from bifrost.tables import tables
        from src.sdk.context import ExecutionContext

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        table_id = str(uuid4())
        doc = {
            "id": "d1", "table_id": table_id, "data": {"v": 1},
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "created_by": "t", "updated_by": "t",
        }
        document_list = {
            "table_id": table_id, "documents": [doc],
            "total": 1, "limit": 100, "offset": 0,
        }
        client = self._client([
            httpx.Response(200, json=doc),
            httpx.Response(200, json=document_list),
            httpx.Response(200, json={"count": 7}),
            httpx.Response(200, json=document_list),
        ])
        try:
            set_execution_context(
                ExecutionContext(
                    user_id="u1", email="e@e.com", name="T", scope="org-1",
                    organization=None, is_platform_admin=False,
                    is_function_key=False, execution_id="exec-1",
                )
            )
            try:
                with patch("bifrost.tables.get_client", return_value=client):
                    got = await tables.get("t", "d1")
                    assert got is not None and got.id == "d1"
                    queried = await tables.query("t", where={"v": 1})
                    assert queried.total == 1
                    assert [d.id for d in queried.documents] == ["d1"]
                    assert await tables.count("t") == 7
                    assert await tables.count("t", where={"v": 1}) == 1
            finally:
                clear_execution_context()
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))
        # The migrated reads never read a channel frame: every call went
        # through the shared client's engine-local entry point.
        assert [
            (call.args[0], call.args[1])
            for call in client.engine_request.await_args_list
        ] == [
            ("GET", "/api/tables/t/documents/d1"),
            ("POST", "/api/tables/t/documents/query"),
            ("GET", "/api/tables/t/documents/count"),
            ("POST", "/api/tables/t/documents/query"),
        ]
        # Filtered count composes through query(limit=1): no count frame.
        assert (
            client.engine_request.await_args_list[3].kwargs["json"]["limit"] == 1
        )

    @pytest.mark.asyncio
    async def test_local_404_maps_to_method_results_without_http(self):
        import httpx

        from bifrost import _local_transport as lt
        from bifrost.tables import tables

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        client = self._client([
            httpx.Response(404, json={"detail": "not found"}),
            httpx.Response(404, json={"detail": "not found"}),
            httpx.Response(404, json={"detail": "not found"}),
        ])
        try:
            with patch("bifrost.tables.get_client", return_value=client):
                assert await tables.get("ghost", "d1") is None
                queried = await tables.query("ghost")
                assert queried.documents == [] and queried.total == 0
                assert await tables.count("ghost") == 0
            assert client.engine_request.await_count == 3
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_error_status_surfaces_without_channel(self):
        import httpx

        from bifrost import _local_transport as lt
        from bifrost.client import BifrostAPIError, BifrostAuthorizationError
        from bifrost.tables import tables

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        client = self._client([
            httpx.Response(
                403, json={"detail": "denied"},
                request=httpx.Request(
                    "GET", "http://api/api/tables/t/documents/d1"
                ),
            ),
            httpx.Response(
                500, json={"detail": "boom"},
                request=httpx.Request(
                    "POST", "http://api/api/tables/t/documents/query"
                ),
            ),
        ])
        try:
            with patch("bifrost.tables.get_client", return_value=client):
                with pytest.raises(BifrostAuthorizationError):
                    await tables.get("t", "d1")
                with pytest.raises(BifrostAPIError):
                    await tables.query("t")
            assert client.engine_request.await_count == 2
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))


class TestPrincipalContextPlumbing:
    def test_execution_id_flows_from_parent_context(self):
        from src.services.execution.sdk_local_dispatch import principal_from_context

        principal = principal_from_context(
            {"organization": None, "execution_id": "exec-42"}
        )
        assert principal.execution_id == "exec-42"

    def test_missing_execution_id_degrades_to_none(self):
        from src.services.execution.sdk_local_dispatch import principal_from_context

        principal = principal_from_context({"organization": None})
        assert principal.execution_id is None
        user = _table_user(principal)
        assert user.engine_execution_id is None

    def test_service_attempt_id_flows_from_parent_context(self):
        service_id = str(uuid4())
        principal = _service_principal(
            uuid4(), service_id=service_id, attempt_id="attempt-7"
        )
        assert principal.service_id == service_id
        assert principal.service_attempt_id == "attempt-7"

    def test_malformed_solution_id_still_fails_dispatch_closed(self):
        from src.services.execution.sdk_local_dispatch import LocalPrincipalError

        with pytest.raises(LocalPrincipalError):
            _engine_principal(uuid4(), solution_id="not-a-uuid")

    def test_child_frames_cannot_set_table_identity(self):
        """No table frame field flows into the token-equivalent user."""
        from src.core.constants import SYSTEM_USER_UUID

        principal = _engine_principal(uuid4(), execution_id="exec-1")
        user = _table_user(principal)
        assert user.user_id == SYSTEM_USER_UUID
        assert user.is_superuser is True
