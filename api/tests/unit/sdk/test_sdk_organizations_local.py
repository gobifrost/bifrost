"""Engine-local transport for the fixed ``organizations`` facade.

Covers the acceptance surface that does not need a forked child:

- the five ``organizations`` operations ride the async SDK channel
  (membership asserted on the allowlist; the import channel rejects
  them);
- the parent dispatcher calls the same ``shared.sdk_organizations``
  service the HTTP handlers call, with DTO validation (422), the
  platform-admin gate (403, before validation — like the HTTP
  ``CurrentSuperuser`` dependency, which FastAPI solves before path/body
  validation), UUID parsing (422), missing-organization 404s, and the
  provider-organization 403s;
- token-equivalent authority: an ordinary workflow engine token passes
  even when the initiating user is not a platform admin, while a
  supervised service token does not (``is_platform_admin`` is never the
  gate);
- mutations commit explicitly (the shared service only flushes; HTTP
  commits via ``get_db``) while reads never commit; creation/update/
  deletion side effects (audit actor, cache updates/invalidation,
  provider-org protection, soft-disable) match HTTP;
- child frame actor, org, and Solution claims can never grant access;
- the SDK facades ride ``BifrostClient.engine_request`` with the exact
  HTTP method/path/body and map statuses to the same public exceptions
  (``Organization`` objects, ``ValueError`` on 404) with no network
  fallback after a failed local call.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost._local_transport import (
    OP_ORGANIZATIONS_CREATE,
    OP_ORGANIZATIONS_DELETE,
    OP_ORGANIZATIONS_GET,
    OP_ORGANIZATIONS_LIST,
    OP_ORGANIZATIONS_UPDATE,
)

ALL_ORGANIZATIONS_OPS = (
    OP_ORGANIZATIONS_CREATE,
    OP_ORGANIZATIONS_GET,
    OP_ORGANIZATIONS_LIST,
    OP_ORGANIZATIONS_UPDATE,
    OP_ORGANIZATIONS_DELETE,
)


@pytest_asyncio.fixture
async def db_session(async_engine):
    """Exercise real flushes while rolling back seeded rows after each test."""
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            try:
                yield session
            finally:
                await session.rollback()
                await transaction.rollback()


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    # Unit cases share the fixture transaction (savepoint-joined, rolled
    # back at teardown), so real local commits never leak across tests.
    yield db_session


def _context_data(**kwargs):
    data = {
        "organization": None,
        "is_platform_admin": kwargs.get("is_platform_admin", True),
        "execution_id": kwargs.get("execution_id", "exec-orgs-1"),
    }
    if kwargs.get("service") is not None:
        data["service"] = kwargs["service"]
    return data


def _admin_principal(**kwargs):
    from src.services.execution.sdk_local_dispatch import principal_from_context

    return principal_from_context(_context_data(**kwargs))


def _non_admin_principal(**kwargs):
    kwargs.setdefault("is_platform_admin", False)
    from src.services.execution.sdk_local_dispatch import principal_from_context

    return principal_from_context(_context_data(**kwargs))


def _service_principal(**kwargs):
    service_id = str(kwargs.get("service_id", uuid4()))
    attempt_id = str(kwargs.get("attempt_id", uuid4()))
    from src.services.execution.sdk_local_dispatch import principal_from_context

    return principal_from_context(
        _context_data(
            is_platform_admin=False,
            service={"service_id": service_id, "attempt_id": attempt_id},
            execution_id=attempt_id,
        )
    )


def _frame(op, frame_id=None, **fields):
    return {"v": 1, "id": frame_id or f"orgs-{uuid4().hex}", "op": op, **fields}


async def _dispatch(db_session, principal, frame):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


async def _dispatch_counting_commits(db_session, principal, frame):
    """Dispatch one frame, returning ``(response, commit_count)``.

    Patches the session commit for exactly this call (flushes still
    apply, so same-session re-reads work) to prove the dispatcher —
    not ``_run_short`` — commits mutations and never commits reads.
    """
    with patch.object(db_session, "commit", new_callable=AsyncMock) as commits:
        result = await _dispatch(db_session, principal, frame)
    return result, commits.await_count


def _stem():
    return f"orgs-local-{uuid4().hex[:8]}"


async def _make_provider(db_session, org_id):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = (
        await db_session.execute(
            select(OrganizationModel).where(OrganizationModel.id == org_id)
        )
    ).scalar_one()
    row.is_provider = True
    db_session.add(row)
    await db_session.flush()
    await db_session.refresh(row)
    return row


# =============================================================================
# Allowlist and platform-admin gate
# =============================================================================


def test_organizations_ops_on_sdk_allowlist_only():
    from src.services.execution.sdk_local_dispatch import (
        IMPORT_CHANNEL_ALLOWED_OPS,
        SDK_CHANNEL_ALLOWED_OPS,
    )

    for op in ALL_ORGANIZATIONS_OPS:
        assert op in SDK_CHANNEL_ALLOWED_OPS, op
        assert op not in IMPORT_CHANNEL_ALLOWED_OPS, op


@pytest.mark.asyncio
async def test_unknown_op_and_bad_version_rejected(db_session):
    denied = await _dispatch(
        db_session, _admin_principal(), _frame("organizations.bulk_update")
    )
    assert denied["ok"] is False
    assert denied["status"] == 404

    bad_version = _frame(OP_ORGANIZATIONS_GET, org_id=str(uuid4()))
    bad_version["v"] = 999
    denied = await _dispatch(db_session, _admin_principal(), bad_version)
    assert denied["ok"] is False
    assert denied["status"] == 400


@pytest.mark.asyncio
async def test_organizations_ops_denied_for_service_token(db_session):
    """Every organizations op denies the non-superuser service token."""
    org_id = str(uuid4())
    frames = {
        OP_ORGANIZATIONS_CREATE: {"name": "x"},
        OP_ORGANIZATIONS_GET: {"org_id": org_id},
        OP_ORGANIZATIONS_LIST: {},
        OP_ORGANIZATIONS_UPDATE: {"org_id": org_id, "updates": {"name": "y"}},
        OP_ORGANIZATIONS_DELETE: {"org_id": org_id},
    }
    principal = _service_principal()
    for op, fields in frames.items():
        denied = await _dispatch(db_session, principal, _frame(op, **fields))
        assert denied["ok"] is False, op
        assert denied["status"] == 403, op
        assert denied["detail"] == "Superuser privileges required", op


@pytest.mark.asyncio
async def test_non_admin_initiator_keeps_workflow_engine_authority(db_session):
    """HTTP workflow SDK calls use the engine superuser token."""
    listed = await _dispatch(
        db_session, _non_admin_principal(), _frame(OP_ORGANIZATIONS_LIST)
    )
    assert listed["ok"] is True, listed


@pytest.mark.asyncio
async def test_gate_beats_malformed_id_like_http(db_session):
    """FastAPI solves the auth dependency before path parsing: 403 first."""
    denied = await _dispatch(
        db_session,
        _service_principal(),
        _frame(OP_ORGANIZATIONS_GET),
    )
    assert denied["ok"] is False
    assert denied["status"] == 403


@pytest.mark.asyncio
async def test_child_claims_cannot_grant_access(db_session):
    """Forged actor/org/Solution frame fields do not bypass the gate."""
    denied = await _dispatch(
        db_session,
        _service_principal(),
        _frame(
            OP_ORGANIZATIONS_CREATE,
            name="forged",
            actor_email="admin@bifrost.internal",
            organization="global",
            scope="global",
            solution=str(uuid4()),
            is_platform_admin=True,
        ),
    )
    assert denied["ok"] is False
    assert denied["status"] == 403


# =============================================================================
# Create/get/list parity (admin principal)
# =============================================================================


@pytest.mark.asyncio
async def test_create_get_round_trip_with_actor_and_commit(db_session):
    from src.core.constants import SYSTEM_USER_UUID
    from src.models.orm.audit import AuditLog

    stem = _stem()
    created, commits = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(
            OP_ORGANIZATIONS_CREATE,
            name=stem,
            domain="ACME.COM",
            is_active=True,
        ),
    )
    assert created["ok"] is True, created
    body = created["result"]
    assert body["name"] == stem
    assert body["domain"] == "acme.com"
    assert body["is_active"] is True
    assert body["is_provider"] is False
    # Actor comes from the parent dispatch context, never child frames.
    assert body["created_by"] == "engine@bifrost.internal"
    assert commits == 1

    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "organization.create",
            AuditLog.resource_id == UUID(body["id"]),
        )
    )
    assert audit is not None
    assert audit.user_id == SYSTEM_USER_UUID
    assert audit.source == "http"

    fetched, read_commits = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_GET, org_id=body["id"]),
    )
    assert fetched["ok"] is True, fetched
    assert fetched["result"]["id"] == body["id"]
    assert fetched["result"]["name"] == stem
    assert read_commits == 0


@pytest.mark.asyncio
async def test_create_ignores_child_claims(db_session):
    created = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ORGANIZATIONS_CREATE,
            name=_stem(),
            actor_email="attacker@example.com",
            solution=str(uuid4()),
            organization=str(uuid4()),
            is_provider=True,
            settings={"forged": True},
        ),
    )
    assert created["ok"] is True, created
    assert created["result"]["created_by"] == "engine@bifrost.internal"
    assert created["result"]["is_provider"] is False
    assert created["result"]["settings"] == {}


@pytest.mark.asyncio
async def test_create_validation_422(db_session):
    missing = await _dispatch(
        db_session, _admin_principal(), _frame(OP_ORGANIZATIONS_CREATE)
    )
    assert missing["ok"] is False
    assert missing["status"] == 422

    too_long = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_CREATE, name="n" * 300),
    )
    assert too_long["ok"] is False
    assert too_long["status"] == 422

    bad_flag = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ORGANIZATIONS_CREATE, name=_stem(), is_active="yes-please"
        ),
    )
    assert bad_flag["ok"] is False
    assert bad_flag["status"] == 422


@pytest.mark.asyncio
async def test_get_missing_404_and_malformed_422(db_session):
    missing = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_GET, org_id=str(uuid4())),
    )
    assert missing["ok"] is False
    assert missing["status"] == 404

    malformed = await _dispatch(
        db_session, _admin_principal(), _frame(OP_ORGANIZATIONS_GET, org_id="nope")
    )
    assert malformed["ok"] is False
    assert malformed["status"] == 422

    absent = await _dispatch(
        db_session, _admin_principal(), _frame(OP_ORGANIZATIONS_GET)
    )
    assert absent["ok"] is False
    assert absent["status"] == 422


@pytest.mark.asyncio
async def test_list_envelope_and_ordering(db_session):
    tag = uuid4().hex[:8]
    prefix = f"orgs-local-{tag}"
    first = f"{prefix}-b"
    second = f"{prefix}-a"
    for name in (first, second):
        created = await _dispatch(
            db_session,
            _admin_principal(),
            _frame(OP_ORGANIZATIONS_CREATE, name=name),
        )
        assert created["ok"] is True, created

    listed = await _dispatch(
        db_session, _admin_principal(), _frame(OP_ORGANIZATIONS_LIST)
    )
    assert listed["ok"] is True, listed
    envelope = listed["result"]
    assert set(envelope) == {"items"}
    names = [item["name"] for item in envelope["items"]]
    # Active-only route default: both fresh rows present, alphabetical.
    assert second in names and first in names
    assert names.index(second) < names.index(first)


# =============================================================================
# Update/delete parity (admin principal)
# =============================================================================


@pytest.mark.asyncio
async def test_update_partial_and_provider_protection(db_session):
    from src.models.orm.audit import AuditLog

    created = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_CREATE, name=_stem(), domain="OLD.COM"),
    )
    assert created["ok"] is True, created
    org_id = created["result"]["id"]

    updated, commits = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(
            OP_ORGANIZATIONS_UPDATE, org_id=org_id, updates={"name": "After"}
        ),
    )
    assert updated["ok"] is True, updated
    assert updated["result"]["name"] == "After"
    assert updated["result"]["domain"] == "old.com"
    assert commits == 1

    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "organization.update",
            AuditLog.resource_id == UUID(org_id),
        )
    )
    assert audit is not None

    missing = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ORGANIZATIONS_UPDATE,
            org_id=str(uuid4()),
            updates={"name": "x"},
        ),
    )
    assert missing["ok"] is False
    assert missing["status"] == 404

    # 404 precedes the provider guard, like the shared service.
    malformed_updates = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ORGANIZATIONS_UPDATE, org_id=org_id, updates={"domain": 123}
        ),
    )
    assert malformed_updates["ok"] is False
    assert malformed_updates["status"] == 422

    not_an_object = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_UPDATE, org_id=org_id, updates=["name"]),
    )
    assert not_an_object["ok"] is False
    assert not_an_object["status"] == 422

    # Provider organization cannot be disabled (403, like HTTP).
    provider = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_CREATE, name=f"{_stem()}-prov"),
    )
    assert provider["ok"] is True, provider
    await _make_provider(db_session, UUID(provider["result"]["id"]))
    denied = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ORGANIZATIONS_UPDATE,
            org_id=provider["result"]["id"],
            updates={"is_active": False},
        ),
    )
    assert denied["ok"] is False
    assert denied["status"] == 403


@pytest.mark.asyncio
async def test_delete_soft_disables_and_provider_403(db_session):
    from src.models.orm.audit import AuditLog
    from src.models.orm.organizations import Organization as OrganizationModel

    created = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_CREATE, name=_stem()),
    )
    assert created["ok"] is True, created
    org_id = created["result"]["id"]

    deleted, commits = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_DELETE, org_id=org_id),
    )
    assert deleted["ok"] is True, deleted
    assert deleted["result"] is None
    assert commits == 1

    row = (
        await db_session.execute(
            select(OrganizationModel).where(OrganizationModel.id == UUID(org_id))
        )
    ).scalar_one()
    assert row.is_active is False

    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "organization.delete",
            AuditLog.resource_id == UUID(org_id),
        )
    )
    assert audit is not None

    # Soft-deleted rows still read (like HTTP get), but leave the list.
    fetched = await _dispatch(
        db_session, _admin_principal(), _frame(OP_ORGANIZATIONS_GET, org_id=org_id)
    )
    assert fetched["ok"] is True, fetched
    assert fetched["result"]["is_active"] is False

    listed = await _dispatch(
        db_session, _admin_principal(), _frame(OP_ORGANIZATIONS_LIST)
    )
    assert listed["ok"] is True, listed
    assert org_id not in {item["id"] for item in listed["result"]["items"]}

    missing = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_DELETE, org_id=str(uuid4())),
    )
    assert missing["ok"] is False
    assert missing["status"] == 404

    malformed = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_DELETE, org_id="nope"),
    )
    assert malformed["ok"] is False
    assert malformed["status"] == 422

    provider = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_CREATE, name=f"{_stem()}-prov-del"),
    )
    assert provider["ok"] is True, provider
    await _make_provider(db_session, UUID(provider["result"]["id"]))
    protected = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_DELETE, org_id=provider["result"]["id"]),
    )
    assert protected["ok"] is False
    assert protected["status"] == 403


@pytest.mark.asyncio
async def test_mutations_commit_and_reads_do_not(db_session):
    """The dispatcher commits exactly where the HTTP route commits."""
    created, n = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_CREATE, name=_stem()),
    )
    assert created["ok"] is True, created
    assert n == 1
    org_id = created["result"]["id"]

    updated, n = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(
            OP_ORGANIZATIONS_UPDATE, org_id=org_id, updates={"name": "Commit-2"}
        ),
    )
    assert updated["ok"] is True, updated
    assert n == 1

    for frame in (
        _frame(OP_ORGANIZATIONS_GET, org_id=org_id),
        _frame(OP_ORGANIZATIONS_LIST),
    ):
        result, n = await _dispatch_counting_commits(
            db_session, _admin_principal(), frame
        )
        assert result["ok"] is True, (frame["op"], result)
        assert n == 0, frame["op"]

    deleted, n = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(OP_ORGANIZATIONS_DELETE, org_id=org_id),
    )
    assert deleted["ok"] is True, deleted
    assert n == 1


# =============================================================================
# SDK facade mapping (no DB)
# =============================================================================


def _org_body(org_id=None, name="Facade Org"):
    uid = org_id or str(uuid4())
    return {
        "id": uid,
        "name": name,
        "domain": None,
        "is_active": True,
        "is_provider": False,
        "settings": {},
        "created_at": "2026-09-25T00:00:00+00:00",
        "created_by": "engine@bifrost.internal",
        "updated_at": "2026-09-25T00:00:00+00:00",
    }


def _response(method, status, payload=None):
    request = httpx.Request(method, "http://engine.local")
    return httpx.Response(status, json=payload, request=request)


class TestEngineRequestOrganizationsFacade:
    """Gate C5d: the migrated organizations facade rides ``engine_request``.

    Each method sends the exact HTTP method/path/body the external path
    used, so the socket-served and network calls stay identical; statuses
    map to the same public exceptions with no silent fallback.
    """

    def _client(self, *responses):
        client = AsyncMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    @pytest.mark.asyncio
    async def test_all_five_methods_use_exact_http_calls(self):
        from bifrost.organizations import organizations as organizations_facade

        body = _org_body()
        client = self._client(
            _response("POST", 201, body),
            _response("GET", 200, body),
            _response("GET", 200, [body]),
            _response("PATCH", 200, body),
            _response("DELETE", 204, None),
        )
        with patch("bifrost.organizations.get_client", return_value=client):
            created = await organizations_facade.create(
                body["name"], domain="acme.com"
            )
            assert created.id == body["id"]
            args, kwargs = client.engine_request.await_args_list[0]
            assert args == ("POST", "/api/organizations")
            assert kwargs["json"] == {
                "name": body["name"],
                "domain": "acme.com",
                "is_active": True,
            }

            assert (await organizations_facade.get(body["id"])).id == body["id"]
            args, _ = client.engine_request.await_args_list[1]
            assert args == ("GET", f"/api/organizations/{body['id']}")

            listed = await organizations_facade.list()
            assert [o.id for o in listed] == [body["id"]]
            args, _ = client.engine_request.await_args_list[2]
            assert args == ("GET", "/api/organizations")

            updated = await organizations_facade.update(body["id"], name="New")
            assert updated.id == body["id"]
            args, kwargs = client.engine_request.await_args_list[3]
            assert args == ("PATCH", f"/api/organizations/{body['id']}")
            assert kwargs["json"] == {"name": "New"}

            assert await organizations_facade.delete(body["id"]) is True
            args, _ = client.engine_request.await_args_list[4]
            assert args == ("DELETE", f"/api/organizations/{body['id']}")

    @pytest.mark.asyncio
    async def test_status_mapping_matches_http(self):
        from bifrost.client import BifrostAPIError, BifrostAuthorizationError
        from bifrost.organizations import organizations as organizations_facade

        request = httpx.Request("GET", "http://engine.local/api/organizations/x")
        for call in (
            lambda m: m.get("missing"),
            lambda m: m.update("missing", name="x"),
            lambda m: m.delete("missing"),
        ):
            client = self._client(
                httpx.Response(404, json={"detail": "x"}, request=request)
            )
            with patch("bifrost.organizations.get_client", return_value=client):
                with pytest.raises(ValueError, match="Organization not found"):
                    await call(organizations_facade)

        # 403 (platform-admin gate) and 422 (DTO validation) surface as the
        # same public exceptions as the external path.
        client = self._client(
            httpx.Response(403, json={"detail": "denied"}, request=request),
            httpx.Response(422, json={"detail": "bad"}, request=request),
        )
        with patch("bifrost.organizations.get_client", return_value=client):
            with pytest.raises(BifrostAuthorizationError):
                await organizations_facade.create("denied")
            with pytest.raises(BifrostAPIError):
                await organizations_facade.list()
