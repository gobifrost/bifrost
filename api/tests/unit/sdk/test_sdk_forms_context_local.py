"""Engine-local transport for form reads and client context.

Covers the acceptance surface that does not need a forked child:

- the parent dispatcher calls the shared ``shared.sdk_forms`` /
  ``shared.sdk_context`` services with a parent-derived
  token-equivalent user (engine superuser vs service non-superuser —
  never child fields) and maps list/get scoping, 404-before-403 error
  precedence, cross-org denial, malformed frames, and missing-org
  outcomes to HTTP-style statuses;
- ``forms.list``/``forms.get`` ride the async SDK channel while
  ``sdk.context`` rides the synchronous import channel (membership
  asserted on both allowlists, and cross-channel requests rejected);
- a synchronous context call completes while an async SDK request is
  held open on the other channel (separate pipes/locks — no deadlock);
- the migrated forms facade rides ``BifrostClient.engine_request`` with the
  exact HTTP paths, mapping results to the public surface (``FormPublic``
  list, ``ValueError``/``PermissionError`` on get) and preserving
  HTTP-shaped errors, while the context facade keeps its cached
  ``BifrostClient.context`` (with ``user``/``organization``/
  ``default_parameters`` views) on the import channel;
- external callers (no injected socket) keep the HTTP path unchanged.
"""

from __future__ import annotations

import asyncio
import contextlib
import httpx
import multiprocessing
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost._import_transport import OP_SDK_CONTEXT
from bifrost._local_transport import OP_FORMS_GET, OP_FORMS_LIST
from src.models.enums import FormAccessLevel


def test_server_form_response_parses_in_python_sdk():
    """The SDK accepts the actual HTTP form contract, including absent file_path."""
    from bifrost.models import FormPublic as SDKFormPublic
    from src.models.contracts.forms import FormPublic as ServerFormPublic

    response = ServerFormPublic(
        id=uuid4(), name="contract-form", is_active=True,
        access_level=FormAccessLevel.AUTHENTICATED,
    ).model_dump(mode="json")

    parsed = SDKFormPublic.model_validate(response)
    assert parsed.name == "contract-form"
    assert parsed.file_path is None


@pytest_asyncio.fixture
async def db_session(async_engine):
    """Exercise real commits while rolling back seeded rows after each test."""
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
    yield db_session


@contextlib.asynccontextmanager
async def _null_factory():
    yield None


def _context_data(org_id=None, **kwargs):
    data = {
        "organization": {"id": str(org_id)} if org_id is not None else None,
        "is_platform_admin": kwargs.get("is_platform_admin", False),
        "execution_id": kwargs.get("execution_id", "exec-forms-1"),
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
        execution_id=attempt_id,
    )


def _forms_user(principal):
    from src.services.execution.sdk_local_dispatch import (
        _forms_user_for_principal,
    )

    return _forms_user_for_principal(principal)


def _context_user(principal):
    from src.services.execution.sdk_local_dispatch import (
        _context_user_for_principal,
    )

    return _context_user_for_principal(principal)


async def _seed_org(db_session, **kwargs):
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=kwargs.get("name", f"sdk-forms-org-{uuid4().hex[:8]}"),
        is_active=kwargs.get("is_active", True),
        created_by="sdk-forms-local-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_form(db_session, name, *, org_id=None, **kwargs):
    from src.models.orm.forms import Form as FormModel

    row = FormModel(
        name=name,
        access_level=kwargs.get("access_level", FormAccessLevel.AUTHENTICATED),
        organization_id=org_id,
        is_active=kwargs.get("is_active", True),
        logo_data=kwargs.get("logo_data"),
        logo_content_type=kwargs.get("logo_content_type"),
        created_by="sdk-forms-local-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _dispatch(frame, principal, db_session):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


def _frame(op, frame_id="forms-1", **fields):
    return {"v": 1, "id": frame_id, "op": op, **fields}


@pytest.mark.asyncio
class TestFormsListDispatch:
    async def test_engine_principal_sees_all_including_inactive(
        self, db_session
    ):
        org = await _seed_org(db_session)
        stem = uuid4().hex[:8]
        await _seed_form(db_session, f"forms-global-{stem}")
        await _seed_form(db_session, f"forms-org-{stem}", org_id=org.id)
        await _seed_form(
            db_session, f"forms-inactive-{stem}", org_id=org.id, is_active=False
        )
        principal = _engine_principal()

        response = await _dispatch(_frame(OP_FORMS_LIST), principal, db_session)

        assert response["ok"] is True, response
        names = {f["name"] for f in response["result"]["items"]}
        assert f"forms-global-{stem}" in names
        assert f"forms-org-{stem}" in names
        assert f"forms-inactive-{stem}" in names

    async def test_service_principal_sees_own_org_plus_global(self, db_session):
        org = await _seed_org(db_session)
        other = await _seed_org(db_session)
        stem = uuid4().hex[:8]
        await _seed_form(db_session, f"forms-global-{stem}")
        await _seed_form(db_session, f"forms-own-{stem}", org_id=org.id)
        await _seed_form(db_session, f"forms-other-{stem}", org_id=other.id)
        await _seed_form(
            db_session, f"forms-own-inactive-{stem}", org_id=org.id, is_active=False
        )
        principal = _service_principal(org.id)

        response = await _dispatch(_frame(OP_FORMS_LIST), principal, db_session)

        assert response["ok"] is True, response
        names = {f["name"] for f in response["result"]["items"]}
        assert f"forms-global-{stem}" in names
        assert f"forms-own-{stem}" in names
        assert f"forms-other-{stem}" not in names
        assert f"forms-own-inactive-{stem}" not in names

    async def test_http_and_local_list_agree(self, db_session):
        from shared.sdk_forms import list_sdk_forms

        org = await _seed_org(db_session)
        stem = uuid4().hex[:8]
        await _seed_form(db_session, f"forms-parity-{stem}", org_id=org.id)
        principal = _engine_principal()

        expected = await list_sdk_forms(
            db_session, _forms_user(principal), scope=None
        )
        local = await _dispatch(_frame(OP_FORMS_LIST, "parity-l"), principal, db_session)

        assert local["ok"] is True, local
        assert local["result"] == {
            "items": [f.model_dump(mode="json") for f in expected]
        }

    async def test_list_is_sdk_channel_only(self):
        from src.services.execution.sdk_local_dispatch import (
            IMPORT_CHANNEL_ALLOWED_OPS,
            SDK_CHANNEL_ALLOWED_OPS,
        )

        assert OP_FORMS_LIST in SDK_CHANNEL_ALLOWED_OPS
        assert OP_FORMS_LIST not in IMPORT_CHANNEL_ALLOWED_OPS
        assert OP_FORMS_GET in SDK_CHANNEL_ALLOWED_OPS
        assert OP_FORMS_GET not in IMPORT_CHANNEL_ALLOWED_OPS


@pytest.mark.asyncio
class TestFormsGetDispatch:
    async def test_engine_get_returns_full_shape_with_logo(self, db_session):
        stem = uuid4().hex[:8]
        row = await _seed_form(
            db_session,
            f"forms-logo-{stem}",
            logo_data=b"fake-logo-bytes",
            logo_content_type="image/png",
        )
        principal = _engine_principal()

        response = await _dispatch(
            _frame(OP_FORMS_GET, "get-1", form_id=str(row.id)),
            principal,
            db_session,
        )

        assert response["ok"] is True, response
        result = response["result"]
        assert result["id"] == str(row.id)
        assert result["name"] == f"forms-logo-{stem}"
        # Full server shape, logo fields included like the HTTP JSON body.
        assert result["logo_url"] == f"/api/forms/{row.id}/logo"
        assert result["logo"] is not None
        assert result["logo"].startswith("data:image/png;base64,")

    async def test_engine_get_sees_inactive_form(self, db_session):
        row = await _seed_form(
            db_session, f"forms-inactive-get-{uuid4().hex[:8]}", is_active=False
        )
        principal = _engine_principal()

        response = await _dispatch(
            _frame(OP_FORMS_GET, "get-inactive", form_id=str(row.id)),
            principal,
            db_session,
        )

        assert response["ok"] is True, response
        assert response["result"]["id"] == str(row.id)

    async def test_missing_form_is_404(self, db_session):
        principal = _engine_principal()

        response = await _dispatch(
            _frame(OP_FORMS_GET, "get-missing", form_id=str(uuid4())),
            principal,
            db_session,
        )

        assert response["ok"] is False
        assert response["status"] == 404

    async def test_service_cross_org_get_is_403(self, db_session):
        org = await _seed_org(db_session)
        other = await _seed_org(db_session)
        row = await _seed_form(
            db_session, f"forms-foreign-{uuid4().hex[:8]}", org_id=other.id
        )
        principal = _service_principal(org.id)

        response = await _dispatch(
            _frame(OP_FORMS_GET, "get-foreign", form_id=str(row.id)),
            principal,
            db_session,
        )

        assert response["ok"] is False
        assert response["status"] == 403

    async def test_service_inactive_own_form_is_404(self, db_session):
        org = await _seed_org(db_session)
        row = await _seed_form(
            db_session,
            f"forms-own-inactive-{uuid4().hex[:8]}",
            org_id=org.id,
            is_active=False,
        )
        principal = _service_principal(org.id)

        response = await _dispatch(
            _frame(OP_FORMS_GET, "get-own-inactive", form_id=str(row.id)),
            principal,
            db_session,
        )

        assert response["ok"] is False
        assert response["status"] == 404

    async def test_malformed_form_id_is_422(self, db_session):
        principal = _engine_principal()

        for bad in ("not-a-uuid", "", None, 123):
            response = await _dispatch(
                _frame(OP_FORMS_GET, f"get-bad-{bad!r}", form_id=bad),
                principal,
                db_session,
            )
            assert response["ok"] is False, bad
            assert response["status"] == 422, bad

    async def test_http_and_local_get_agree(self, db_session):
        from shared.sdk_forms import get_sdk_form

        row = await _seed_form(db_session, f"forms-parity-{uuid4().hex[:8]}")
        principal = _engine_principal()

        expected = await get_sdk_form(db_session, _forms_user(principal), row.id)
        local = await _dispatch(
            _frame(OP_FORMS_GET, "parity-g", form_id=str(row.id)),
            principal,
            db_session,
        )

        assert local["ok"] is True, local
        assert local["result"] == expected.model_dump(mode="json")

    async def test_never_trusts_child_identity(self, db_session):
        org = await _seed_org(db_session)
        other = await _seed_org(db_session)
        row = await _seed_form(
            db_session, f"forms-forged-{uuid4().hex[:8]}", org_id=other.id
        )
        principal = _service_principal(org.id)

        # Child-supplied identity/scope claims must not widen access:
        # the dispatch principal alone decides.
        response = await _dispatch(
            _frame(
                OP_FORMS_GET,
                "get-forged",
                form_id=str(row.id),
                organization_id=str(other.id),
                is_superuser=True,
                actor_email="attacker@test.local",
            ),
            principal,
            db_session,
        )

        assert response["ok"] is False
        assert response["status"] == 403


@pytest.mark.asyncio
class TestSdkContextDispatch:
    async def test_engine_context_matches_http_shape(self, db_session):
        from src.core.security import ENGINE_SDK_ACTOR_EMAIL

        principal = _engine_principal()

        response = await _dispatch(_frame(OP_SDK_CONTEXT), principal, db_session)

        assert response["ok"] is True, response
        result = response["result"]
        assert result["user"]["email"] == ENGINE_SDK_ACTOR_EMAIL
        assert result["user"]["is_superuser"] is True
        assert result["organization"] is None
        assert result["default_parameters"] == {}
        assert result["track_executions"] is True

    async def test_service_context_matches_http_shape(self, db_session):
        org = await _seed_org(db_session)
        principal = _service_principal(org.id)

        response = await _dispatch(_frame(OP_SDK_CONTEXT), principal, db_session)

        assert response["ok"] is True, response
        result = response["result"]
        assert result["user"]["email"].startswith("service-")
        assert result["user"]["email"].endswith("@bifrost.internal")
        assert result["user"]["is_superuser"] is False
        assert result["organization"]["id"] == str(org.id)
        assert result["organization"]["name"] == org.name
        assert result["default_parameters"] == {}
        assert result["track_executions"] is True

    async def test_http_and_local_context_agree(self, db_session):
        from shared.sdk_context import get_sdk_context

        org = await _seed_org(db_session)
        for principal in (_engine_principal(), _service_principal(org.id)):
            expected = await get_sdk_context(
                db_session, _context_user(principal), org_id=None
            )
            local = await _dispatch(_frame(OP_SDK_CONTEXT), principal, db_session)

            assert local["ok"] is True, local
            assert local["result"] == expected

    async def test_missing_service_org_is_404(self, db_session):
        from src.services.execution.sdk_local_dispatch import (
            LocalDispatchPrincipal,
        )

        principal = _service_principal(uuid4())

        response = await _dispatch(_frame(OP_SDK_CONTEXT), principal, db_session)

        assert response["ok"] is False
        assert response["status"] == 404
        assert isinstance(principal, LocalDispatchPrincipal)

    async def test_never_trusts_child_identity(self, db_session):
        org = await _seed_org(db_session)
        other = await _seed_org(db_session)
        principal = _service_principal(org.id)

        # Child-supplied org/identity claims must not move the context.
        response = await _dispatch(
            _frame(
                OP_SDK_CONTEXT,
                "ctx-forged",
                org_id=str(other.id),
                organization_id=str(other.id),
                is_superuser=True,
            ),
            principal,
            db_session,
        )

        assert response["ok"] is True, response
        assert response["result"]["organization"]["id"] == str(org.id)

    async def test_context_is_import_channel_only(self):
        from src.services.execution.sdk_local_dispatch import (
            IMPORT_CHANNEL_ALLOWED_OPS,
            SDK_CHANNEL_ALLOWED_OPS,
        )

        assert OP_SDK_CONTEXT in IMPORT_CHANNEL_ALLOWED_OPS
        assert OP_SDK_CONTEXT not in SDK_CHANNEL_ALLOWED_OPS

    async def test_sdk_channel_rejects_context_op(self):
        import json

        from src.services.execution.sdk_local_dispatch import (
            SDK_CHANNEL_ALLOWED_OPS,
            serve_channel,
        )

        parent_recv, child_send = multiprocessing.Pipe(duplex=False)
        child_recv, parent_send = multiprocessing.Pipe(duplex=False)
        try:
            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=parent_recv,
                    send_conn=parent_send,
                    session_factory=lambda: _null_factory(),
                    principal=_engine_principal(),
                    allowed_ops=SDK_CHANNEL_ALLOWED_OPS,
                )
            )
            await asyncio.to_thread(
                child_send.send_bytes,
                json.dumps({"v": 1, "id": "wrong-channel", "op": OP_SDK_CONTEXT}).encode(),
            )
            response = json.loads(
                await asyncio.to_thread(child_recv.recv_bytes, 65537)
            )
            assert response["ok"] is False
            assert response["status"] == 404
            child_send.close()
            assert await asyncio.wait_for(pump, timeout=15.0) == "eof"
        finally:
            for conn in (child_send, child_recv, parent_recv, parent_send):
                with contextlib.suppress(Exception):
                    conn.close()


@pytest.mark.asyncio
class TestDualChannelFormsContextConcurrency:
    """A held async forms call never blocks a synchronous context read.

    The child holds one ``forms.list`` on the async channel while
    ``sdk.context`` completes on the import channel — sharing one
    channel/lock would deadlock here.
    """

    async def test_context_finishes_while_forms_list_held(self):
        from bifrost._import_transport import ChildSyncImportTransport
        from bifrost._local_transport import ChildLocalTransport
        from src.services.execution.sdk_local_dispatch import (
            IMPORT_CHANNEL_ALLOWED_OPS,
            SDK_CHANNEL_ALLOWED_OPS,
            serve_channel,
        )

        sdk_parent_recv, sdk_child_send = multiprocessing.Pipe(duplex=False)
        sdk_child_recv, sdk_parent_send = multiprocessing.Pipe(duplex=False)
        imp_parent_recv, imp_child_send = multiprocessing.Pipe(duplex=False)
        imp_child_recv, imp_parent_send = multiprocessing.Pipe(duplex=False)
        conns = [
            sdk_child_send,
            sdk_parent_recv,
            sdk_parent_send,
            sdk_child_recv,
            imp_child_send,
            imp_parent_recv,
            imp_parent_send,
            imp_child_recv,
        ]
        release = asyncio.Event()
        principal = _engine_principal()

        async def _blocked_forms_list(*args, **kwargs):
            await release.wait()
            return []

        async def _fast_context(*args, **kwargs):
            return {
                "user": {"id": "u", "email": "e", "is_superuser": True},
                "organization": None,
                "default_parameters": {},
                "track_executions": True,
            }

        sdk_pump = imp_pump = sdk_call = None
        try:
            with (
                patch(
                    "shared.sdk_forms.list_sdk_forms",
                    new=AsyncMock(side_effect=_blocked_forms_list),
                ),
                patch(
                    "shared.sdk_context.get_sdk_context",
                    new=AsyncMock(side_effect=_fast_context),
                ),
            ):
                sdk_pump = asyncio.create_task(
                    serve_channel(
                        recv_conn=sdk_parent_recv,
                        send_conn=sdk_parent_send,
                        session_factory=lambda: _null_factory(),
                        principal=principal,
                        allowed_ops=SDK_CHANNEL_ALLOWED_OPS,
                    )
                )
                imp_pump = asyncio.create_task(
                    serve_channel(
                        recv_conn=imp_parent_recv,
                        send_conn=imp_parent_send,
                        session_factory=lambda: _null_factory(),
                        principal=principal,
                        allowed_ops=IMPORT_CHANNEL_ALLOWED_OPS,
                    )
                )
                child_sdk = ChildLocalTransport(sdk_child_send, sdk_child_recv)
                child_imp = ChildSyncImportTransport(imp_child_send, imp_child_recv)
                sdk_call = asyncio.create_task(child_sdk.call_forms_list())
                await asyncio.sleep(0.2)
                assert not sdk_call.done()
                context = await asyncio.to_thread(child_imp.call_sdk_context)
                assert context["track_executions"] is True
                release.set()
                items = await asyncio.wait_for(sdk_call, timeout=15.0)
                assert items == []
                child_sdk._send.close()
                child_imp._send.close()
                assert await asyncio.wait_for(sdk_pump, timeout=15.0) == "eof"
                assert await asyncio.wait_for(imp_pump, timeout=15.0) == "eof"
                sdk_pump = imp_pump = sdk_call = None
        finally:
            release.set()
            for conn in conns:
                with contextlib.suppress(Exception):
                    conn.close()
            for task in (sdk_pump, imp_pump, sdk_call):
                if task is not None:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task


@pytest.mark.asyncio
class TestFormsFacadeEngineRequest:
    """Gate C5c: the migrated forms facade rides ``engine_request``.

    ``list`` reads ``GET /api/forms`` and validates the list into
    ``FormPublic``; ``get`` reads ``GET /api/forms/{id}`` and maps 404/403 to
    the same public exceptions as the HTTP path — no dedicated-channel frames
    and no silent network fallback.
    """

    def _client(self, *responses):
        client = MagicMock()
        client.engine_request = AsyncMock(side_effect=responses)
        return client

    def _form_body(self, form_id):
        return {
            "id": form_id,
            "name": "engine-form",
            "confirmation_markdown": "Thanks",
            "workflow_id": None,
            "launch_workflow_id": None,
            "default_launch_params": None,
            "allowed_query_params": None,
            "form_schema": None,
            "access_level": "authenticated",
            "organization_id": None,
            "role_ids": [],
            "is_active": True,
            "created_at": None,
            "updated_at": None,
        }

    async def test_list_reads_exact_path_and_parses_models(self):
        from bifrost.forms import forms

        form_id = str(uuid4())
        client = self._client(
            httpx.Response(200, json=[self._form_body(form_id)])
        )
        with patch("bifrost.forms.get_client", return_value=client):
            result = await forms.list()

        assert [f.name for f in result] == ["engine-form"]
        assert result[0].id == form_id
        call = client.engine_request.await_args
        assert call.args == ("GET", "/api/forms")
        assert call.kwargs == {}

    async def test_get_reads_exact_path_and_parses_model(self):
        from bifrost.forms import forms

        form_id = str(uuid4())
        client = self._client(
            httpx.Response(200, json=self._form_body(form_id))
        )
        with patch("bifrost.forms.get_client", return_value=client):
            detail = await forms.get(form_id)

        assert detail.id == form_id
        assert detail.name == "engine-form"
        assert client.engine_request.await_args.args == (
            "GET",
            f"/api/forms/{form_id}",
        )

    async def test_get_maps_404_and_403_to_public_errors(self):
        from bifrost.forms import forms

        request = httpx.Request("GET", "http://engine/api/forms/x")
        for status, exc_type in ((404, ValueError), (403, PermissionError)):
            client = self._client(
                httpx.Response(status, json={"detail": "no"}, request=request)
            )
            with patch("bifrost.forms.get_client", return_value=client):
                with pytest.raises(exc_type):
                    await forms.get(str(uuid4()))

    async def test_list_error_statuses_surface(self):
        from bifrost.client import BifrostAPIError
        from bifrost.forms import forms

        request = httpx.Request("GET", "http://engine/api/forms")
        for status in (401, 403, 500):
            client = self._client(
                httpx.Response(status, json={"detail": "denied"}, request=request)
            )
            with patch("bifrost.forms.get_client", return_value=client):
                with pytest.raises(BifrostAPIError) as exc_info:
                    await forms.list()
            assert exc_info.value.response.status_code == status


@pytest.mark.asyncio
class TestContextFacadeLocalMapping:
    def _client(self):
        from bifrost.client import BifrostClient

        return BifrostClient("http://127.0.0.1:9", "dead-token")

    def _payload(self, **overrides):
        data = {
            "user": {
                "id": str(uuid4()),
                "email": "engine@bifrost.internal",
                "name": "Bifrost Engine",
                "is_superuser": True,
            },
            "organization": None,
            "default_parameters": {},
            "track_executions": True,
        }
        data.update(overrides)
        return data

    def _install_fake_import_transport(self, monkeypatch, fake):
        import bifrost._import_transport as import_transport

        monkeypatch.setattr(import_transport, "_installed", fake)
        return import_transport

    async def test_sync_property_uses_local_and_caches(self, monkeypatch):
        calls = []

        class FakeImportTransport:
            def call_sdk_context(self, timeout=30.0):
                calls.append(timeout)
                return self.payload

        fake = FakeImportTransport()
        fake.payload = self._payload()
        self._install_fake_import_transport(monkeypatch, fake)

        client = self._client()
        first = client.context
        second = client.context

        assert first == fake.payload
        assert second == fake.payload
        assert len(calls) == 1
        assert client.user == fake.payload["user"]
        assert client.organization is None
        assert client.default_parameters == {}

    async def test_sync_property_views_service_payload(self, monkeypatch):
        org_id = str(uuid4())
        payload = self._payload(
            user={
                "id": str(uuid4()),
                "email": "service-abc@bifrost.internal",
                "name": "service-abc",
                "is_superuser": False,
            },
            organization={"id": org_id, "name": "Svc Org"},
        )

        class FakeImportTransport:
            def call_sdk_context(self, timeout=30.0):
                return payload

        self._install_fake_import_transport(monkeypatch, FakeImportTransport())

        client = self._client()
        assert client.organization == {"id": org_id, "name": "Svc Org"}
        assert client.user["email"] == "service-abc@bifrost.internal"

    async def test_async_fetch_uses_local_too(self, monkeypatch):
        calls = []

        class FakeImportTransport:
            def call_sdk_context(self, timeout=30.0):
                calls.append(timeout)
                return self.payload

        fake = FakeImportTransport()
        fake.payload = self._payload()
        self._install_fake_import_transport(monkeypatch, fake)

        client = self._client()

        def _dead_sync(*args, **kwargs):
            raise AssertionError("context HTTP must not be used in the engine path")

        async def _dead_async(*args, **kwargs):
            raise AssertionError("context HTTP must not be used in the engine path")

        monkeypatch.setattr(client, "get_sync", _dead_sync)
        monkeypatch.setattr(client, "get", _dead_async)

        result = await client._fetch_context()

        assert result == fake.payload
        assert len(calls) == 1
        # Cached: the sync property reuses the async fetch.
        assert client.context == fake.payload
        assert len(calls) == 1

    async def test_local_errors_match_http_mapping(self, monkeypatch):
        from bifrost._import_transport import (
            ImportNotFound,
            ImportServiceError,
        )
        from bifrost.client import (
            BifrostAPIError,
            BifrostAuthorizationError,
        )

        class FailContext:
            def __init__(self, error):
                self.error = error

            def call_sdk_context(self, timeout=30.0):
                raise self.error

        for error, expected in (
            (ImportNotFound("missed", status_code=404, detail="nope"), BifrostAPIError),
            (
                ImportServiceError("denied", status_code=403, detail="denied"),
                BifrostAuthorizationError,
            ),
            (
                ImportServiceError("boom", status_code=500, detail="boom"),
                BifrostAPIError,
            ),
        ):
            self._install_fake_import_transport(monkeypatch, FailContext(error))
            client = self._client()
            with pytest.raises(expected) as exc_info:
                client.context
            assert exc_info.value.response.status_code == error.status_code

    async def test_external_callers_keep_http(self, monkeypatch):
        import bifrost._import_transport as import_transport

        monkeypatch.setattr(import_transport, "_installed", None)

        payload = self._payload()
        sync_response = MagicMock()
        sync_response.is_success = True
        sync_response.json = lambda: payload
        async_response = MagicMock()
        async_response.is_success = True
        async_response.json = lambda: payload

        client = self._client()
        monkeypatch.setattr(
            client, "get_sync", lambda path, **kwargs: sync_response
        )
        calls = []

        async def _fake_get(path, **kwargs):
            calls.append(path)
            return async_response

        monkeypatch.setattr(client, "get", _fake_get)

        assert client.context == payload
        fresh = self._client()
        monkeypatch.setattr(fresh, "get", _fake_get)
        assert await fresh._fetch_context() == payload
        assert calls == ["/api/sdk/context"]

    async def test_broken_channel_raises_loudly_without_http(self, monkeypatch):
        from bifrost._import_transport import ImportTransportClosed

        class BrokenTransport:
            def call_sdk_context(self, timeout=30.0):
                raise ImportTransportClosed("parent is gone")

        self._install_fake_import_transport(monkeypatch, BrokenTransport())

        client = self._client()
        with pytest.raises(ImportTransportClosed):
            client.context
