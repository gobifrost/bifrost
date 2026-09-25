"""Engine-local transport for the fixed ``roles`` facade.

Covers the acceptance surface that does not need a forked child:

- the nine ``roles`` operations ride the async SDK channel (membership
  asserted on the allowlist; the import channel rejects them);
- the parent dispatcher calls the same ``shared.sdk_roles`` service the
  HTTP handlers call, with DTO validation (422), the platform-admin
  gate (403, before validation — like the HTTP ``CurrentSuperuser``
  dependency, which FastAPI solves before path/body validation), UUID
  parsing (422), missing-role 404s, the solution-guard 409, and the
  malformed-form-id 500;
- mutations commit explicitly (the shared service only flushes; HTTP
  commits via ``get_db``) while reads never commit;
- child frame actor, org, and Solution claims can never grant access;
- the SDK facades map local results to the public surface (``Role``
  objects, ``user_ids``/``form_ids`` lists, ``ValueError`` on 404) and
  never fall back to HTTP after a failed local call;
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
    OP_ROLES_ASSIGN_FORMS,
    OP_ROLES_ASSIGN_USERS,
    OP_ROLES_CREATE,
    OP_ROLES_DELETE,
    OP_ROLES_GET,
    OP_ROLES_LIST,
    OP_ROLES_LIST_FORMS,
    OP_ROLES_LIST_USERS,
    OP_ROLES_UPDATE,
)

ALL_ROLES_OPS = (
    OP_ROLES_CREATE,
    OP_ROLES_GET,
    OP_ROLES_LIST,
    OP_ROLES_UPDATE,
    OP_ROLES_DELETE,
    OP_ROLES_LIST_USERS,
    OP_ROLES_LIST_FORMS,
    OP_ROLES_ASSIGN_USERS,
    OP_ROLES_ASSIGN_FORMS,
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
        "execution_id": kwargs.get("execution_id", "exec-roles-1"),
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
    return {"v": 1, "id": frame_id or f"roles-{uuid4().hex}", "op": op, **fields}


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
    return f"roles-local-{uuid4().hex[:8]}"


async def _seed_user(db_session, *, email=None):
    from src.models.orm.users import User as UserModel

    row = UserModel(
        email=email or f"{_stem()}@test.local",
        name="Roles Local Test User",
        is_active=True,
        # NULL-org users must be superuser
        # (``ck_users_org_requires_superuser``).
        is_superuser=True,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_form(db_session, *, name=None):
    from src.models.enums import FormAccessLevel
    from src.models.orm.forms import Form as FormModel

    row = FormModel(
        name=name or _stem(),
        access_level=FormAccessLevel.AUTHENTICATED,
        organization_id=None,
        is_active=True,
        created_by="roles-local-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


# =============================================================================
# Allowlist and platform-admin gate
# =============================================================================


def test_roles_ops_on_sdk_allowlist_only():
    from src.services.execution.sdk_local_dispatch import (
        IMPORT_CHANNEL_ALLOWED_OPS,
        SDK_CHANNEL_ALLOWED_OPS,
    )

    for op in ALL_ROLES_OPS:
        assert op in SDK_CHANNEL_ALLOWED_OPS, op
        assert op not in IMPORT_CHANNEL_ALLOWED_OPS, op


@pytest.mark.asyncio
async def test_unknown_op_and_bad_version_rejected(db_session):
    denied = await _dispatch(
        db_session, _admin_principal(), _frame("roles.bulk_unassign")
    )
    assert denied["ok"] is False
    assert denied["status"] == 404

    bad_version = _frame(OP_ROLES_GET, role_id=str(uuid4()))
    bad_version["v"] = 999
    denied = await _dispatch(db_session, _admin_principal(), bad_version)
    assert denied["ok"] is False
    assert denied["status"] == 400


@pytest.mark.asyncio
async def test_roles_ops_denied_for_service_token(db_session):
    """Every roles op denies the non-superuser service token."""
    role_id = str(uuid4())
    form_id = str(uuid4())
    frames = {
        OP_ROLES_CREATE: {"name": "x", "description": ""},
        OP_ROLES_GET: {"role_id": role_id},
        OP_ROLES_LIST: {},
        OP_ROLES_UPDATE: {"role_id": role_id, "updates": {"description": "y"}},
        OP_ROLES_DELETE: {"role_id": role_id},
        OP_ROLES_LIST_USERS: {"role_id": role_id},
        OP_ROLES_LIST_FORMS: {"role_id": role_id},
        OP_ROLES_ASSIGN_USERS: {"role_id": role_id, "user_ids": ["a@b.c"]},
        OP_ROLES_ASSIGN_FORMS: {"role_id": role_id, "form_ids": [form_id]},
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
        db_session, _non_admin_principal(), _frame(OP_ROLES_LIST)
    )
    assert listed["ok"] is True, listed


@pytest.mark.asyncio
async def test_gate_beats_malformed_id_like_http(db_session):
    """FastAPI solves the auth dependency before path parsing: 403 first."""
    denied = await _dispatch(
        db_session,
        _service_principal(),
        _frame(OP_ROLES_GET, role_id="not-a-uuid"),
    )
    assert denied["ok"] is False
    assert denied["status"] == 403


@pytest.mark.asyncio
async def test_service_principal_denied(db_session):
    """Supervised services are never platform admin: no roles access."""
    denied = await _dispatch(
        db_session,
        _service_principal(),
        _frame(OP_ROLES_CREATE, name="svc-role", description=""),
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
            OP_ROLES_CREATE,
            name="forged-role",
            description="",
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
# CRUD parity (admin principal)
# =============================================================================


@pytest.mark.asyncio
async def test_create_get_round_trip_with_actor_attribution(db_session):
    stem = _stem()
    created, commits = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_CREATE, name=stem, description="local parity"),
    )
    assert created["ok"] is True, created
    body = created["result"]
    assert body["name"] == stem
    assert body["description"] == "local parity"
    assert body["permissions"] == {}
    # Actor comes from the parent dispatch context, never child frames.
    assert body["created_by"] == "engine@bifrost.internal"
    assert commits == 1

    from src.core.constants import SYSTEM_USER_UUID
    from src.models.orm.audit import AuditLog

    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "role.create",
            AuditLog.resource_id == UUID(body["id"]),
        )
    )
    assert audit is not None
    assert audit.user_id == SYSTEM_USER_UUID
    assert audit.source == "http"

    fetched, read_commits = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_GET, role_id=body["id"]),
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
            OP_ROLES_CREATE,
            name=_stem(),
            description="",
            actor_email="attacker@example.com",
            solution=str(uuid4()),
            organization=str(uuid4()),
        ),
    )
    assert created["ok"] is True, created
    assert created["result"]["created_by"] == "engine@bifrost.internal"


@pytest.mark.asyncio
async def test_create_validation_422(db_session):
    too_long = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_CREATE, name="n" * 101, description=""),
    )
    assert too_long["ok"] is False
    assert too_long["status"] == 422

    missing = await _dispatch(
        db_session, _admin_principal(), _frame(OP_ROLES_CREATE, description="")
    )
    assert missing["ok"] is False
    assert missing["status"] == 422


@pytest.mark.asyncio
async def test_get_missing_404_and_malformed_422(db_session):
    missing = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_GET, role_id=str(uuid4())),
    )
    assert missing["ok"] is False
    assert missing["status"] == 404

    malformed = await _dispatch(
        db_session, _admin_principal(), _frame(OP_ROLES_GET, role_id="nope")
    )
    assert malformed["ok"] is False
    assert malformed["status"] == 422


@pytest.mark.asyncio
async def test_list_envelope_matches_http_shape(db_session):
    first, second = f"{_stem()}-a", f"{_stem()}-b"
    for name in (first, second):
        created = await _dispatch(
            db_session,
            _admin_principal(),
            _frame(OP_ROLES_CREATE, name=name, description=""),
        )
        assert created["ok"] is True, created

    listed = await _dispatch(
        db_session, _admin_principal(), _frame(OP_ROLES_LIST)
    )
    assert listed["ok"] is True, listed
    envelope = listed["result"]
    assert set(envelope) == {"items", "total"}
    assert envelope["total"] >= 2
    names = {item["name"] for item in envelope["items"]}
    assert {first, second} <= names


@pytest.mark.asyncio
async def test_update_partial_and_404(db_session):
    created = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_CREATE, name=_stem(), description="before"),
    )
    role_id = created["result"]["id"]

    updated = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ROLES_UPDATE,
            role_id=role_id,
            updates={"description": "after", "is_active": False},
        ),
    )
    assert updated["ok"] is True, updated
    assert updated["result"]["description"] == "after"
    # Only non-None known fields are applied; unknown fields ignored
    # exactly like the HTTP DTO (no ``is_active`` on roles).
    assert updated["result"]["name"] == created["result"]["name"]

    perms = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ROLES_UPDATE,
            role_id=role_id,
            updates={"permissions": {"tickets": ["read"]}},
        ),
    )
    assert perms["ok"] is True, perms
    assert perms["result"]["permissions"] == {"tickets": ["read"]}

    missing = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ROLES_UPDATE,
            role_id=str(uuid4()),
            updates={"description": "x"},
        ),
    )
    assert missing["ok"] is False
    assert missing["status"] == 404

    malformed = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_UPDATE, role_id="bad", updates={"description": "x"}),
    )
    assert malformed["ok"] is False
    assert malformed["status"] == 422

    not_an_object = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_UPDATE, role_id=role_id, updates=["description"]),
    )
    assert not_an_object["ok"] is False
    assert not_an_object["status"] == 422


@pytest.mark.asyncio
async def test_delete_removes_and_404s(db_session):
    created = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_CREATE, name=_stem(), description=""),
    )
    role_id = created["result"]["id"]

    deleted = await _dispatch(
        db_session, _admin_principal(), _frame(OP_ROLES_DELETE, role_id=role_id)
    )
    assert deleted["ok"] is True, deleted
    assert deleted["result"] is None

    gone = await _dispatch(
        db_session, _admin_principal(), _frame(OP_ROLES_GET, role_id=role_id)
    )
    assert gone["ok"] is False
    assert gone["status"] == 404

    again = await _dispatch(
        db_session, _admin_principal(), _frame(OP_ROLES_DELETE, role_id=role_id)
    )
    assert again["ok"] is False
    assert again["status"] == 404

    malformed = await _dispatch(
        db_session, _admin_principal(), _frame(OP_ROLES_DELETE, role_id="bad")
    )
    assert malformed["ok"] is False
    assert malformed["status"] == 422


@pytest.mark.asyncio
async def test_mutations_commit_and_reads_do_not(db_session):
    """The dispatcher commits exactly where the HTTP route commits."""
    created, n = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_CREATE, name=_stem(), description=""),
    )
    assert created["ok"] is True, created
    assert n == 1
    role_id = created["result"]["id"]

    user = await _seed_user(db_session)
    form = await _seed_form(db_session)

    mutations = [
        _frame(OP_ROLES_UPDATE, role_id=role_id, updates={"description": "c"}),
        _frame(
            OP_ROLES_ASSIGN_USERS, role_id=role_id, user_ids=[str(user.id)]
        ),
        _frame(
            OP_ROLES_ASSIGN_FORMS, role_id=role_id, form_ids=[str(form.id)]
        ),
    ]
    for frame in mutations:
        result, n = await _dispatch_counting_commits(
            db_session, _admin_principal(), frame
        )
        assert result["ok"] is True, (frame["op"], result)
        assert n == 1, frame["op"]

    reads = [
        _frame(OP_ROLES_GET, role_id=role_id),
        _frame(OP_ROLES_LIST),
        _frame(OP_ROLES_LIST_USERS, role_id=role_id),
        _frame(OP_ROLES_LIST_FORMS, role_id=role_id),
    ]
    for frame in reads:
        result, n = await _dispatch_counting_commits(
            db_session, _admin_principal(), frame
        )
        assert result["ok"] is True, (frame["op"], result)
        assert n == 0, frame["op"]

    # The database must cascade both assignments on role deletion.
    deleted, n = await _dispatch_counting_commits(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_DELETE, role_id=role_id),
    )
    assert deleted["ok"] is True, deleted
    assert n == 1


# =============================================================================
# Assignment parity (admin principal)
# =============================================================================


@pytest.mark.asyncio
async def test_assign_users_uuid_email_unknown_and_idempotent(db_session):
    from src.models import UserRole as UserRoleORM

    created = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_CREATE, name=_stem(), description=""),
    )
    role_id = created["result"]["id"]
    user = await _seed_user(db_session)

    assigned = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ROLES_ASSIGN_USERS,
            role_id=role_id,
            user_ids=[str(user.id), user.email, "ghost@example.com"],
        ),
    )
    assert assigned["ok"] is True, assigned
    assert assigned["result"] is None

    # Attribution uses the parent-derived actor, never child frames.
    from sqlalchemy import select

    rows = (
        await db_session.execute(
            select(UserRoleORM).where(UserRoleORM.role_id == role_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].assigned_by == "engine@bifrost.internal"

    members = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_LIST_USERS, role_id=role_id),
    )
    assert members["ok"] is True, members
    envelope = members["result"]
    assert set(envelope) >= {"user_ids", "users", "total"}
    assert envelope["user_ids"] == [str(user.id)]
    assert envelope["total"] == 1

    # Re-assignment is a no-op (no duplicate rows).
    repeat = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ROLES_ASSIGN_USERS, role_id=role_id, user_ids=[str(user.id)]
        ),
    )
    assert repeat["ok"] is True, repeat
    members = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_LIST_USERS, role_id=role_id),
    )
    assert members["result"]["user_ids"] == [str(user.id)]


@pytest.mark.asyncio
async def test_assign_users_empty_list_422(db_session):
    created = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_CREATE, name=_stem(), description=""),
    )
    empty = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ROLES_ASSIGN_USERS, role_id=created["result"]["id"], user_ids=[]
        ),
    )
    assert empty["ok"] is False
    assert empty["status"] == 422


@pytest.mark.asyncio
async def test_list_users_unknown_role_empty_and_malformed_422(db_session):
    empty = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_LIST_USERS, role_id=str(uuid4())),
    )
    assert empty["ok"] is True, empty
    assert empty["result"]["user_ids"] == []
    assert empty["result"]["total"] == 0

    malformed = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_LIST_USERS, role_id="bad"),
    )
    assert malformed["ok"] is False
    assert malformed["status"] == 422


@pytest.mark.asyncio
async def test_assign_forms_success_idempotent_404_500(db_session):
    created = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_CREATE, name=_stem(), description=""),
    )
    role_id = created["result"]["id"]
    form = await _seed_form(db_session)

    assigned = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ROLES_ASSIGN_FORMS, role_id=role_id, form_ids=[str(form.id)]
        ),
    )
    assert assigned["ok"] is True, assigned
    assert assigned["result"] is None

    forms = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_LIST_FORMS, role_id=role_id),
    )
    assert forms["ok"] is True, forms
    assert forms["result"] == {"form_ids": [str(form.id)]}

    repeat = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ROLES_ASSIGN_FORMS, role_id=role_id, form_ids=[str(form.id)]
        ),
    )
    assert repeat["ok"] is True, repeat
    forms = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_LIST_FORMS, role_id=role_id),
    )
    assert forms["result"] == {"form_ids": [str(form.id)]}

    missing = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(
            OP_ROLES_ASSIGN_FORMS, role_id=role_id, form_ids=[str(uuid4())]
        ),
    )
    assert missing["ok"] is False
    assert missing["status"] == 404

    # A malformed form id raises ValueError in the shared service — the
    # HTTP path surfaces it as a 500, and so does the local path.
    malformed = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_ASSIGN_FORMS, role_id=role_id, form_ids=["bad"]),
    )
    assert malformed["ok"] is False
    assert malformed["status"] == 500

    empty = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_ASSIGN_FORMS, role_id=role_id, form_ids=[]),
    )
    assert empty["ok"] is False
    assert empty["status"] == 422


@pytest.mark.asyncio
async def test_list_forms_unknown_role_empty_and_malformed_422(db_session):
    empty = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_LIST_FORMS, role_id=str(uuid4())),
    )
    assert empty["ok"] is True, empty
    assert empty["result"] == {"form_ids": []}

    malformed = await _dispatch(
        db_session,
        _admin_principal(),
        _frame(OP_ROLES_LIST_FORMS, role_id="bad"),
    )
    assert malformed["ok"] is False
    assert malformed["status"] == 422


# =============================================================================
# SDK facade mapping (no DB)
# =============================================================================


def _role_body(role_id=None, name="facade-role"):
    rid = role_id or str(uuid4())
    return {
        "id": rid,
        "name": name,
        "description": "d",
        "permissions": {},
        "created_by": "engine@bifrost.internal",
        "created_at": "2026-09-25T00:00:00+00:00",
        "updated_at": "2026-09-25T00:00:00+00:00",
    }


def _local_error(status, detail="denied"):
    import httpx

    from bifrost.client import raise_for_status_with_detail

    request = httpx.Request("POST", "local://sdk/roles/op")
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
    from bifrost.roles import roles as roles_facade

    body = _role_body()
    transport = AsyncMock()
    transport.call_roles_create.return_value = body
    transport.call_roles_get.return_value = body
    transport.call_roles_list.return_value = [body]
    transport.call_roles_update.return_value = body
    transport.call_roles_delete.return_value = None
    transport.call_roles_list_users.return_value = ["u-1"]
    transport.call_roles_list_forms.return_value = ["f-1"]
    transport.call_roles_assign_users.return_value = None
    transport.call_roles_assign_forms.return_value = None

    with patch("bifrost._local_transport.get", return_value=transport):
        role = await roles_facade.create("facade-role", description="d")
        assert role.id == body["id"]
        transport.call_roles_create.assert_awaited_once_with(
            "facade-role", "d"
        )

        assert (await roles_facade.get(body["id"])).name == "facade-role"
        transport.call_roles_get.assert_awaited_once_with(body["id"])

        listed = await roles_facade.list()
        assert [r.id for r in listed] == [body["id"]]
        transport.call_roles_list.assert_awaited_once_with()

        updated = await roles_facade.update(
            body["id"], description="d2"
        )
        assert updated.id == body["id"]
        transport.call_roles_update.assert_awaited_once_with(
            body["id"], {"description": "d2"}
        )

        assert await roles_facade.delete(body["id"]) is None
        assert await roles_facade.list_users(body["id"]) == ["u-1"]
        assert await roles_facade.list_forms(body["id"]) == ["f-1"]
        assert (
            await roles_facade.assign_users(body["id"], ["u-1"]) is None
        )
        assert (
            await roles_facade.assign_forms(body["id"], ["f-1"]) is None
        )

    for method, call in (
        ("get", lambda m, rid: m.get(rid)),
        ("update", lambda m, rid: m.update(rid, description="x")),
        ("delete", lambda m, rid: m.delete(rid)),
        ("list_users", lambda m, rid: m.list_users(rid)),
        ("list_forms", lambda m, rid: m.list_forms(rid)),
        ("assign_users", lambda m, rid: m.assign_users(rid, ["u-1"])),
        ("assign_forms", lambda m, rid: m.assign_forms(rid, ["f-1"])),
    ):
        failing = AsyncMock()
        getattr(failing, f"call_roles_{method}").side_effect = _local_error(
            404, "Role not found"
        )
        with (
            patch("bifrost._local_transport.get", return_value=failing),
            pytest.raises(ValueError, match="Role not found"),
        ):
            await call(roles_facade, str(uuid4()))


@pytest.mark.asyncio
async def test_facade_never_falls_back_to_http():
    """A failed local call raises loudly instead of retrying over HTTP."""
    from bifrost.roles import roles as roles_facade
    from bifrost._local_transport import LocalTransportClosed

    transport = AsyncMock()
    transport.call_roles_get.side_effect = LocalTransportClosed("closed")
    with (
        patch("bifrost._local_transport.get", return_value=transport),
        patch(
            "bifrost.roles.get_client",
            side_effect=AssertionError("HTTP fallback is forbidden"),
        ),
    ):
        with pytest.raises(LocalTransportClosed):
            await roles_facade.get(str(uuid4()))


@pytest.mark.asyncio
async def test_facade_propagates_local_403_like_http():
    """Local 403s surface as authorization errors — no silent mapping."""
    from bifrost.roles import roles as roles_facade
    from bifrost.client import BifrostAuthorizationError

    transport = AsyncMock()
    transport.call_roles_create.side_effect = _local_error(
        403, "Superuser privileges required"
    )
    with patch("bifrost._local_transport.get", return_value=transport):
        with pytest.raises(BifrostAuthorizationError):
            await roles_facade.create("denied-role")


@pytest.mark.asyncio
async def test_external_http_path_unchanged():
    """Without a transport the facade keeps the exact HTTP calls."""
    from bifrost.roles import roles as roles_facade

    body = _role_body()

    def _response(method, status, payload=None):
        request = httpx.Request(method, "http://test.local/api/roles")
        return httpx.Response(status, json=payload, request=request)

    client = AsyncMock()
    client.post.return_value = _response(
        "POST", 201, {**body, "consumer_counts": None}
    )
    client.get.return_value = _response("GET", 200, [body])
    client.patch.return_value = _response("PATCH", 200, body)
    client.delete.return_value = _response("DELETE", 204, None)

    with (
        patch("bifrost._local_transport.get", return_value=None),
        patch("bifrost.roles.get_client", return_value=client),
    ):
        await roles_facade.create("http-role", description="d")
        args, kwargs = client.post.await_args_list[0]
        assert args[0] == "/api/roles"
        assert kwargs["json"]["name"] == "http-role"

        await roles_facade.list()
        client.get.assert_any_await("/api/roles")

        await roles_facade.update(body["id"], description="d2")
        args, kwargs = client.patch.await_args_list[0]
        assert args[0] == f"/api/roles/{body['id']}"
        assert kwargs["json"] == {"description": "d2"}

        await roles_facade.delete(body["id"])
        client.delete.assert_awaited_with(f"/api/roles/{body['id']}")

        client.get.return_value = _response(
            "GET", 200, {"user_ids": ["u-1"], "users": [], "total": 1}
        )
        assert await roles_facade.list_users(body["id"]) == ["u-1"]
        client.get.return_value = _response(
            "GET", 200, {"form_ids": ["f-1"]}
        )
        assert await roles_facade.list_forms(body["id"]) == ["f-1"]

        client.post.return_value = _response("POST", 204, None)
        await roles_facade.assign_users(body["id"], ["u-1"])
        args, kwargs = client.post.await_args_list[-1]
        assert args[0] == f"/api/roles/{body['id']}/users"
        assert kwargs["json"] == {"user_ids": ["u-1"]}
        await roles_facade.assign_forms(body["id"], ["f-1"])
        args, kwargs = client.post.await_args_list[-1]
        assert args[0] == f"/api/roles/{body['id']}/forms"
        assert kwargs["json"] == {"form_ids": ["f-1"]}
