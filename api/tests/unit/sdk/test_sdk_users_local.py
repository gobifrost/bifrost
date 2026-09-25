"""Engine-local transport for the fixed ``users`` facade.

Covers the acceptance surface that does not need a forked child:

- the five ``users`` operations ride the async SDK channel (membership
  asserted on the allowlist; the import channel rejects them);
- the parent dispatcher calls the same ``shared.sdk_users`` service the
  HTTP handlers call, with DTO validation (422), the platform-admin
  gate (403, before validation — like the HTTP ``CurrentSuperuser``
  dependency, which FastAPI solves before path/body validation),
  missing-user 404s, the self-delete 400 (before existence, like the
  service), and the system-user 403;
- the ``org_id``/``scope`` correction: the SDK facade sends its
  ``org_id`` as the ``scope`` filter on both transports, and the
  parent filters to the requested organization — including a no-match
  case. No compatibility alias or fallback;
- mutations commit explicitly (the shared service only flushes; HTTP
  commits via ``get_db``) while reads never commit;
- child frame actor, org, and Solution claims can never grant access;
- the SDK facades map local results to the public surface
  (``UserPublic`` objects, ``None``/``ValueError`` on 404) and never
  fall back to HTTP after a failed local call;
- external callers (no transport) keep the HTTP path unchanged.
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
    OP_USERS_CREATE,
    OP_USERS_DELETE,
    OP_USERS_GET,
    OP_USERS_LIST,
    OP_USERS_UPDATE,
)

ALL_USERS_OPS = (
    OP_USERS_LIST,
    OP_USERS_CREATE,
    OP_USERS_GET,
    OP_USERS_UPDATE,
    OP_USERS_DELETE,
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
        "execution_id": kwargs.get("execution_id", "exec-users-1"),
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
    return {"v": 1, "id": frame_id or f"users-{uuid4().hex}", "op": op, **fields}


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
    return f"users-local-{uuid4().hex[:8]}"


async def _seed_org(db_session, *, name=None):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=name or f"{_stem()}-org",
        is_active=True,
        created_by="users-local-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_user(db_session, *, email=None, org_id=None, superuser=False):
    from shared.sdk_users import create_user

    return await create_user(
        db_session,
        email=email or f"{_stem()}@example.com",
        name="Users Local Test User",
        is_active=True,
        is_superuser=superuser,
        is_external=False,
        organization_id=org_id,
        actor_user_id=uuid4(),
    )


# =============================================================================
# Allowlist and platform-admin gate
# =============================================================================


def test_users_ops_on_sdk_allowlist_only():
    from src.services.execution.sdk_local_dispatch import (
        IMPORT_CHANNEL_ALLOWED_OPS,
        SDK_CHANNEL_ALLOWED_OPS,
    )

    for op in ALL_USERS_OPS:
        assert op in SDK_CHANNEL_ALLOWED_OPS, op
        assert op not in IMPORT_CHANNEL_ALLOWED_OPS, op


@pytest.mark.asyncio
async def test_unknown_op_and_bad_version_rejected(db_session):
    denied = await _dispatch(
        db_session, _admin_principal(), _frame("users.bulk_update")
    )
    assert denied["ok"] is False
    assert denied["status"] == 404

    bad_version = _frame(OP_USERS_GET, user_id=str(uuid4()))
    bad_version["v"] = 999
    denied = await _dispatch(db_session, _admin_principal(), bad_version)
    assert denied["ok"] is False
    assert denied["status"] == 400


@pytest.mark.asyncio
async def test_users_ops_denied_for_service_token(db_session):
    """Every users op denies the non-superuser service token."""
    user_id = str(uuid4())
    frames = {
        OP_USERS_LIST: {"scope": None, "include_inactive": False},
        OP_USERS_CREATE: {"email": "x@y.zz", "name": "x"},
        OP_USERS_GET: {"user_id": user_id},
        OP_USERS_UPDATE: {"user_id": user_id, "updates": {"name": "y"}},
        OP_USERS_DELETE: {"user_id": user_id},
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
        db_session, _non_admin_principal(), _frame(OP_USERS_LIST)
    )
    assert listed["ok"] is True, listed


@pytest.mark.asyncio
async def test_gate_beats_malformed_id_like_http(db_session):
    """FastAPI solves the auth dependency before path parsing: 403 first."""
    denied = await _dispatch(
        db_session,
        _service_principal(),
        _frame(OP_USERS_GET),
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
            OP_USERS_CREATE,
            email="forged@example.com",
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
# List parity, including the org_id/scope correction
# =============================================================================


@pytest.mark.asyncio
async def test_list_scope_filters_to_requested_org_with_no_match(db_session):
    """Both transports filter to the requested org — no unfiltered leak."""
    org_a = await _seed_org(db_session)
    org_b = await _seed_org(db_session)
    tag = uuid4().hex[:8]
    member_a = await _seed_user(
        db_session, email=f"orga-{tag}@example.com", org_id=org_a.id
    )
    member_b = await _seed_user(
        db_session, email=f"orgb-{tag}@example.com", org_id=org_b.id
    )

    filtered = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_LIST, scope=str(org_a.id)),
    )
    assert filtered["ok"] is True, filtered
    envelope = filtered["result"]
    assert set(envelope) == {"items", "total"}
    assert envelope["total"] == 1
    assert [item["id"] for item in envelope["items"]] == [str(member_a.id)]
    assert str(member_b.id) not in {item["id"] for item in envelope["items"]}

    # A scope that matches nothing returns an empty envelope, never the
    # unfiltered list the old ignored-``org_id`` param produced.
    no_match = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_LIST, scope=str(uuid4())),
    )
    assert no_match["ok"] is True, no_match
    assert no_match["result"] == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_list_unfiltered_and_include_inactive(db_session):
    tag = uuid4().hex[:8]
    active = await _seed_user(
        db_session, email=f"lactive-{tag}@example.com", superuser=True
    )
    await _seed_user(
        db_session, email=f"linactive-{tag}@example.com", superuser=True
    )
    from src.models import User as UserORM
    from sqlalchemy import select

    inactive = (
        await db_session.execute(
            select(UserORM).where(UserORM.email == f"linactive-{tag}@example.com")
        )
    ).scalar_one()
    inactive.is_active = False
    await db_session.flush()

    default = await _dispatch(
        db_session, _admin_principal(), _frame(OP_USERS_LIST)
    )
    assert default["ok"] is True, default
    ids = {item["id"] for item in default["result"]["items"]}
    assert str(active.id) in ids
    assert str(inactive.id) not in ids

    everything = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_LIST, include_inactive=True),
    )
    assert everything["ok"] is True, everything
    ids = {item["id"] for item in everything["result"]["items"]}
    assert str(active.id) in ids
    assert str(inactive.id) in ids


@pytest.mark.asyncio
async def test_list_invalid_scope_422_and_malformed_fields(db_session):
    bad_scope = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_LIST, scope="not-a-scope"),
    )
    assert bad_scope["ok"] is False
    assert bad_scope["status"] == 422

    non_string_scope = await _dispatch(
        db_session, _admin_principal(), _frame(OP_USERS_LIST, scope=123)
    )
    assert non_string_scope["ok"] is False
    assert non_string_scope["status"] == 422

    non_bool_flag = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_LIST, include_inactive="true"),
    )
    assert non_bool_flag["ok"] is False
    assert non_bool_flag["status"] == 422


# =============================================================================
# Create/get parity (admin principal)
# =============================================================================


@pytest.mark.asyncio
async def test_create_get_round_trip_with_invite_and_commit(db_session):
    org = await _seed_org(db_session)
    email = f"{_stem()}@example.com"
    created, commits = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(
            OP_USERS_CREATE,
            email=email,
            name="Local Parity",
            is_superuser=False,
            organization_id=str(org.id),
            is_active=True,
        ),
    )
    assert created["ok"] is True, created
    body = created["result"]
    assert body["email"] == email
    assert body["is_verified"] is True
    assert body["is_registered"] is False
    assert body["invite_status"] == "pending"
    assert "/accept-invite?token=" in (body["registration_url"] or "")
    assert commits == 1

    from src.core.constants import SYSTEM_USER_UUID
    from src.models.orm.audit import AuditLog
    from src.services.audit_context import current_actor

    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "user.create",
            AuditLog.resource_id == UUID(body["id"]),
        )
    )
    assert audit is not None
    assert audit.user_id == SYSTEM_USER_UUID
    assert audit.source == "http"
    assert current_actor() is None

    fetched, read_commits = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_GET, user_id=body["id"]),
    )
    assert fetched["ok"] is True, fetched
    assert fetched["result"]["id"] == body["id"]
    assert fetched["result"]["email"] == email
    assert read_commits == 0

    by_email = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_GET, user_id=email),
    )
    assert by_email["ok"] is True, by_email
    assert by_email["result"]["id"] == body["id"]


@pytest.mark.asyncio
async def test_create_ignores_child_claims(db_session):
    created = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_USERS_CREATE,
            email=f"{_stem()}@example.com",
            name="Claims",
            is_superuser=True,
            actor_email="attacker@example.com",
            solution=str(uuid4()),
            organization=str(uuid4()),
        ),
    )
    assert created["ok"] is True, created
    assert created["result"]["is_superuser"] is True


@pytest.mark.asyncio
async def test_create_validation_422(db_session):
    bad_email = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_CREATE, email="not-an-email", name="x"),
    )
    assert bad_email["ok"] is False
    assert bad_email["status"] == 422

    bad_org = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_USERS_CREATE,
            email=f"{_stem()}@example.com",
            name="x",
            organization_id="not-a-uuid",
        ),
    )
    assert bad_org["ok"] is False
    assert bad_org["status"] == 422

    missing = await _dispatch(
        db_session, _admin_principal(), _frame(OP_USERS_CREATE, name="x")
    )
    assert missing["ok"] is False
    assert missing["status"] == 422


@pytest.mark.asyncio
async def test_get_missing_404_and_missing_id_422(db_session):
    missing = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_GET, user_id=str(uuid4())),
    )
    assert missing["ok"] is False
    assert missing["status"] == 404

    absent = await _dispatch(
        db_session, _admin_principal(), _frame(OP_USERS_GET)
    )
    assert absent["ok"] is False
    assert absent["status"] == 422


# =============================================================================
# Update/delete parity (admin principal)
# =============================================================================


@pytest.mark.asyncio
async def test_update_partial_promote_and_protection(db_session):
    from src.core.constants import PROVIDER_ORG_ID

    org = await _seed_org(db_session)
    created = await _seed_user(
        db_session, email=f"{_stem()}@example.com", org_id=org.id
    )
    user_id = str(created.id)

    updated, commits = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_UPDATE, user_id=user_id, updates={"name": "After"}),
    )
    assert updated["ok"] is True, updated
    assert updated["result"]["name"] == "After"
    assert updated["result"]["organization_id"] == str(org.id)
    assert commits == 1

    promoted = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_USERS_UPDATE, user_id=user_id, updates={"is_superuser": True}
        ),
    )
    assert promoted["ok"] is True, promoted
    assert promoted["result"]["is_superuser"] is True
    assert promoted["result"]["organization_id"] == str(PROVIDER_ORG_ID)

    missing = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_USERS_UPDATE, user_id=str(uuid4()), updates={"name": "x"}
        ),
    )
    assert missing["ok"] is False
    assert missing["status"] == 404

    malformed = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_USERS_UPDATE,
            user_id=user_id,
            updates={"email": "not-an-email"},
        ),
    )
    assert malformed["ok"] is False
    assert malformed["status"] == 422

    not_an_object = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_UPDATE, user_id=user_id, updates=["name"]),
    )
    assert not_an_object["ok"] is False
    assert not_an_object["status"] == 422

    from src.models import User as UserORM

    sys_user = UserORM(
        email=f"sysupd-{uuid4().hex[:8]}@example.com",
        is_system=True,
        is_superuser=True,
    )
    db_session.add(sys_user)
    await db_session.flush()
    protected = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_USERS_UPDATE, user_id=str(sys_user.id), updates={"name": "x"}
        ),
    )
    assert protected["ok"] is False
    assert protected["status"] == 403


@pytest.mark.asyncio
async def test_delete_self_400_before_existence(db_session):
    """The engine sentinel cannot delete itself — 400 before 404/403."""
    from src.core.constants import SYSTEM_USER_UUID
    from src.core.security import ENGINE_SDK_ACTOR_EMAIL

    by_id = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_DELETE, user_id=str(SYSTEM_USER_UUID)),
    )
    assert by_id["ok"] is False
    assert by_id["status"] == 400

    by_email = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_DELETE, user_id=ENGINE_SDK_ACTOR_EMAIL),
    )
    assert by_email["ok"] is False
    assert by_email["status"] == 400


@pytest.mark.asyncio
async def test_delete_missing_system_and_success(db_session):
    from src.models import User as UserORM

    missing = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_DELETE, user_id=str(uuid4())),
    )
    assert missing["ok"] is False
    assert missing["status"] == 404

    sys_user = UserORM(
        email=f"sysdel-{uuid4().hex[:8]}@example.com",
        is_system=True,
        is_superuser=True,
    )
    db_session.add(sys_user)
    await db_session.flush()
    protected = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_DELETE, user_id=str(sys_user.id)),
    )
    assert protected["ok"] is False
    assert protected["status"] == 403

    created = await _seed_user(
        db_session, email=f"{_stem()}@example.com", superuser=True
    )
    deleted, commits = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_DELETE, user_id=str(created.id)),
    )
    assert deleted["ok"] is True, deleted
    assert deleted["result"] is None
    assert commits == 1

    gone = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_GET, user_id=str(created.id)),
    )
    assert gone["ok"] is False
    assert gone["status"] == 404


@pytest.mark.asyncio
async def test_mutations_commit_and_reads_do_not(db_session):
    """The dispatcher commits exactly where the HTTP route commits."""
    created, n = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(
            OP_USERS_CREATE,
            email=f"{_stem()}@example.com",
            name="Commit",
            is_superuser=True,
        ),
    )
    assert created["ok"] is True, created
    assert n == 1
    user_id = created["result"]["id"]

    mutations = [
        _frame(
            OP_USERS_UPDATE, user_id=user_id, updates={"name": "Commit-2"}
        ),
    ]
    for frame in mutations:
        result, n = await _dispatch_counting_commits(
            db_session, _admin_principal(), frame
        )
        assert result["ok"] is True, (frame["op"], result)
        assert n == 1, frame["op"]

    reads = [
        _frame(OP_USERS_GET, user_id=user_id),
        _frame(OP_USERS_LIST),
    ]
    for frame in reads:
        result, n = await _dispatch_counting_commits(
            db_session, _admin_principal(), frame
        )
        assert result["ok"] is True, (frame["op"], result)
        assert n == 0, frame["op"]

    deleted, n = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(OP_USERS_DELETE, user_id=user_id),
    )
    assert deleted["ok"] is True, deleted
    assert n == 1


# =============================================================================
# SDK facade mapping (no DB)
# =============================================================================


def _user_body(user_id=None, email="facade@example.com"):
    uid = user_id or str(uuid4())
    return {
        "id": uid,
        "email": email,
        "name": "Facade User",
        "is_active": True,
        "is_superuser": False,
        "is_verified": True,
        "is_registered": False,
        "organization_id": None,
        "mfa_enabled": False,
        "created_at": "2026-09-25T00:00:00+00:00",
        "updated_at": "2026-09-25T00:00:00+00:00",
    }


def _local_error(status, detail="denied"):
    import httpx

    from bifrost.client import raise_for_status_with_detail

    request = httpx.Request("POST", "local://sdk/users/op")
    response = httpx.Response(
        status, json={"detail": detail}, request=request
    )
    try:
        raise_for_status_with_detail(response)
    except Exception as e:  # noqa: BLE001 - re-raised below by the fake
        return e
    raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_facade_maps_local_results_and_404s():
    from bifrost.users import users as users_facade

    body = _user_body()
    transport = AsyncMock()
    transport.call_users_list.return_value = {"items": [body], "total": 1}
    transport.call_users_create.return_value = body
    transport.call_users_get.return_value = body
    transport.call_users_update.return_value = body
    transport.call_users_delete.return_value = None

    with patch("bifrost._local_transport.get", return_value=transport):
        listed = await users_facade.list(org_id="org-123")
        assert [u.id for u in listed] == [body["id"]]
        transport.call_users_list.assert_awaited_once_with("org-123", False)

        created = await users_facade.create(
            "n@example.com", "N", org_id="org-123"
        )
        assert created.id == body["id"]
        transport.call_users_create.assert_awaited_once_with(
            "n@example.com", "N", False, "org-123", True
        )

        assert (await users_facade.get(body["id"])).email == body["email"]
        transport.call_users_get.assert_awaited_once_with(body["id"])

        updated = await users_facade.update(body["id"], name="New")
        assert updated.id == body["id"]
        transport.call_users_update.assert_awaited_once_with(
            body["id"], {"name": "New"}
        )

        assert await users_facade.delete(body["id"]) is True
        transport.call_users_delete.assert_awaited_once_with(body["id"])

    failing_get = AsyncMock()
    failing_get.call_users_get.side_effect = _local_error(404, "User not found")
    with (
        patch("bifrost._local_transport.get", return_value=failing_get),
        patch(
            "bifrost.users.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        assert await users_facade.get(str(uuid4())) is None

    for method, call in (
        ("update", lambda m, rid: m.update(rid, name="x")),
        ("delete", lambda m, rid: m.delete(rid)),
    ):
        failing = AsyncMock()
        getattr(failing, f"call_users_{method}").side_effect = _local_error(
            404, "User not found"
        )
        with (
            patch("bifrost._local_transport.get", return_value=failing),
            pytest.raises(ValueError, match="User not found"),
        ):
            await call(users_facade, str(uuid4()))


@pytest.mark.asyncio
async def test_facade_never_falls_back_to_http():
    """A failed local call raises loudly instead of retrying over HTTP."""
    from bifrost.users import users as users_facade
    from bifrost._local_transport import LocalTransportClosed

    transport = AsyncMock()
    transport.call_users_get.side_effect = LocalTransportClosed("closed")
    with (
        patch("bifrost._local_transport.get", return_value=transport),
        patch(
            "bifrost.users.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        with pytest.raises(LocalTransportClosed):
            await users_facade.get(str(uuid4()))


@pytest.mark.asyncio
async def test_facade_propagates_local_403_like_http():
    """Local 403s surface as authorization errors — no silent mapping."""
    from bifrost.users import users as users_facade
    from bifrost.client import BifrostAuthorizationError

    transport = AsyncMock()
    transport.call_users_create.side_effect = _local_error(
        403, "Superuser privileges required"
    )
    with patch("bifrost._local_transport.get", return_value=transport):
        with pytest.raises(BifrostAuthorizationError):
            await users_facade.create("d@example.com", "D")


@pytest.mark.asyncio
async def test_external_http_path_unchanged_with_scope_filter():
    """Without a transport the facade keeps the HTTP calls.

    Regression for the ``org_id``/``scope`` mismatch: the SDK sends its
    ``org_id`` as the ``scope`` query parameter the handler filters on —
    never the ignored ``org_id`` parameter.
    """
    from bifrost.users import users as users_facade

    body = _user_body()

    def _response(method, status, payload=None):
        request = httpx.Request(method, "http://test.local/api/users")
        return httpx.Response(status, json=payload, request=request)

    client = AsyncMock()
    client.get.return_value = _response("GET", 200, [body])
    client.post.return_value = _response("POST", 201, body)
    client.patch.return_value = _response("PATCH", 200, body)
    client.delete.return_value = _response("DELETE", 204, None)

    with (
        patch("bifrost._local_transport.get", return_value=None),
        patch("bifrost.users.get_client", return_value=client),
    ):
        await users_facade.list(org_id="org-123")
        args, kwargs = client.get.await_args_list[0]
        assert args[0] == "/api/users"
        assert kwargs["params"] == {"scope": "org-123"}

        await users_facade.list(org_id="org-123", include_inactive=True)
        args, kwargs = client.get.await_args_list[-1]
        assert kwargs["params"] == {
            "scope": "org-123",
            "include_inactive": "true",
        }

        await users_facade.list()
        args, kwargs = client.get.await_args_list[-1]
        assert kwargs["params"] == {}

        await users_facade.create("h@example.com", "H", org_id="org-123")
        args, kwargs = client.post.await_args_list[0]
        assert args[0] == "/api/users"
        assert kwargs["json"]["organization_id"] == "org-123"

        client.get.return_value = _response("GET", 200, body)
        assert (await users_facade.get(body["id"])).id == body["id"]

        await users_facade.update(body["id"], name="N2")
        args, kwargs = client.patch.await_args_list[0]
        assert args[0] == f"/api/users/{body['id']}"
        assert kwargs["json"] == {"name": "N2"}

        assert await users_facade.delete(body["id"]) is True
        client.delete.assert_awaited_with(f"/api/users/{body['id']}")

        client.get.return_value = _response("GET", 404, {"detail": "x"})
        assert await users_facade.get("missing") is None
        client.patch.return_value = _response("PATCH", 404, {"detail": "x"})
        with pytest.raises(ValueError, match="User not found"):
            await users_facade.update("missing", name="x")
        client.delete.return_value = _response("DELETE", 404, {"detail": "x"})
        with pytest.raises(ValueError, match="User not found"):
            await users_facade.delete("missing")
