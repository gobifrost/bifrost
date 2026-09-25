"""Stage 1: parent dispatcher unit tests (no fork required).

Covers ``principal_from_context`` derivation, the ``config.get`` allowlist,
scope/key validation, short-session behavior, channel pumps (EOF, malformed,
oversized, unknown operation), per-request error isolation, shutdown
cancellation, and cross-channel isolation.
"""

import asyncio
import contextlib
import json
import multiprocessing
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from src.services.execution.sdk_local_dispatch import (
    LocalDispatchPrincipal,
    dispatch_frame,
    principal_from_context,
    serve_channel,
)


@contextlib.asynccontextmanager
async def _factory(session):
    yield session


def _frame(**overrides):
    frame = {"v": 1, "id": "req-1", "op": "config.get", "key": "k", "scope": None}
    frame.update(overrides)
    return frame


class TestPrincipalDerivation:
    def test_derives_from_parent_context(self):
        org_id = uuid4()
        principal = principal_from_context(
            {
                "organization": {
                    "id": str(org_id),
                    "name": "O",
                    "is_provider": True,
                },
                "is_platform_admin": True,
                "engine_token": "child-never-sees-this",
            }
        )
        assert principal.caller_org_id == org_id
        assert principal.is_platform_admin is True
        assert principal.is_provider_org is True
        assert principal.is_external is False

    def test_missing_org_means_global(self):
        principal = principal_from_context({"organization": None})
        assert principal.caller_org_id is None

    def test_malformed_org_id_raises(self):
        from src.services.execution.sdk_local_dispatch import LocalPrincipalError

        with pytest.raises(LocalPrincipalError, match="not a valid UUID"):
            principal_from_context({"organization": {"id": "junk"}})

    def test_non_string_org_id_raises(self):
        from src.services.execution.sdk_local_dispatch import LocalPrincipalError

        with pytest.raises(LocalPrincipalError, match="not a string"):
            principal_from_context({"organization": {"id": 12345}})

    def test_child_claims_are_not_read(self):
        # Extra keys a hostile child might stuff into the dispatch context
        # must not influence the principal.
        principal = principal_from_context(
            {
                "organization": {"id": str(uuid4())},
                "caller_org_id": "00000000-0000-0000-0000-000000000000",
                "is_external": True,
                "scope": "global",
            }
        )
        assert principal.is_external is False


@pytest.mark.asyncio
class TestDispatchValidation:
    async def _dispatch(self, db_session, frame, principal):
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=_empty_redis()),
        ):
            return await dispatch_frame(
                lambda: _factory(db_session), principal, frame
            )

    async def test_unknown_operation_rejected(self, db_session):
        principal = LocalDispatchPrincipal(caller_org_id=None)
        response = await self._dispatch(
            db_session, _frame(op="tables.drop"), principal
        )
        assert response["ok"] is False
        assert response["status"] == 404

    async def test_bad_version_rejected(self, db_session):
        principal = LocalDispatchPrincipal(caller_org_id=None)
        response = await self._dispatch(
            db_session, _frame(v=999), principal
        )
        assert response["ok"] is False
        assert response["status"] == 400

    async def test_non_string_key_rejected(self, db_session):
        principal = LocalDispatchPrincipal(caller_org_id=None)
        response = await self._dispatch(
            db_session, _frame(key={"nested": 1}), principal
        )
        assert response["ok"] is False
        assert response["status"] == 422

    async def test_long_missing_key_returns_null(self, db_session):
        principal = LocalDispatchPrincipal(caller_org_id=None)
        long_key = "k" * 2000
        with patch(
            "shared.sdk_config.get_sdk_config_dict",
            new=AsyncMock(return_value=None),
        ):
            response = await self._dispatch(
                db_session, _frame(key=long_key), principal
            )
        assert response["ok"] is True
        assert response["result"] is None

    async def test_cross_org_denied_without_bypass(self, db_session):
        principal = LocalDispatchPrincipal(caller_org_id=uuid4())
        response = await self._dispatch(
            db_session, _frame(scope=str(uuid4())), principal
        )
        assert response["ok"] is False
        assert response["status"] == 403

    async def test_cross_org_allowed_for_provider(self, db_session):
        principal = LocalDispatchPrincipal(
            caller_org_id=uuid4(), is_provider_org=True
        )
        with patch(
            "shared.sdk_config.get_sdk_config_dict",
            new=AsyncMock(return_value=None),
        ):
            response = await self._dispatch(
                db_session, _frame(scope=str(uuid4())), principal
            )
        assert response["ok"] is True
        assert response["result"] is None

    async def test_global_requires_bypass(self, db_session):
        principal = LocalDispatchPrincipal(caller_org_id=uuid4())
        response = await self._dispatch(
            db_session, _frame(scope="global"), principal
        )
        assert response["ok"] is False
        assert response["status"] == 403

    async def test_dispatcher_calls_shared_service(self, db_session):
        from shared import sdk_config as shared_service

        org_id = uuid4()
        principal = LocalDispatchPrincipal(caller_org_id=org_id)
        seen = {}

        async def _spy(session, *, key, org_id, external):
            seen["key"] = key
            seen["org_id"] = org_id
            seen["external"] = external
            return {"key": key, "value": 1, "config_type": "int"}

        with (
            patch.object(shared_service, "get_sdk_config_dict", new=_spy),
            patch(
                "src.core.cache.redis_client.get_shared_redis",
                new=AsyncMock(return_value=_empty_redis()),
            ),
        ):
            response = await dispatch_frame(
                lambda: _factory(db_session), principal, _frame(key="k")
            )
        assert response["ok"] is True
        assert seen == {"key": "k", "org_id": org_id, "external": False}

    async def test_one_short_session_per_operation(self, db_session):
        principal = LocalDispatchPrincipal(caller_org_id=None)
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

        with patch(
            "shared.sdk_config.get_sdk_config_dict",
            new=AsyncMock(return_value=None),
        ):
            for _ in range(3):
                response = await dispatch_frame(
                    _counting_factory, principal, _frame()
                )
                assert response["ok"] is True
        assert opened == 3
        assert closed == 3


def _empty_redis():
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    return redis


@pytest.mark.asyncio
class TestServeChannel:
    def _pair(self):
        req_recv, req_send = multiprocessing.Pipe(duplex=False)
        resp_recv, resp_send = multiprocessing.Pipe(duplex=False)
        return req_recv, req_send, resp_recv, resp_send

    def _close_all(self, conns):
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    async def test_eof_when_child_exits(self):
        req_recv, req_send, resp_recv, resp_send = self._pair()
        principal = LocalDispatchPrincipal(caller_org_id=None)
        pump = asyncio.create_task(
            serve_channel(
                recv_conn=req_recv,
                send_conn=resp_send,
                session_factory=None,
                principal=principal,
            )
        )
        # Child side goes away without a single request.
        req_send.close()
        resp_recv.close()
        try:
            assert await asyncio.wait_for(pump, timeout=10.0) == "eof"
        finally:
            self._close_all((req_recv, resp_send))

    async def test_idle_between_calls_does_not_close_channel(self):
        req_recv, req_send, resp_recv, resp_send = self._pair()
        pump = asyncio.create_task(
            serve_channel(
                recv_conn=req_recv,
                send_conn=resp_send,
                session_factory=None,
                principal=LocalDispatchPrincipal(caller_org_id=None),
            )
        )
        try:
            with patch(
                "src.services.execution.sdk_local_dispatch.CHANNEL_FRAME_IDLE_TIMEOUT_SECONDS",
                0.02,
            ):
                for request_id in ("first", "second"):
                    request = {"v": 1, "id": request_id, "op": "unknown"}
                    await asyncio.to_thread(
                        req_send.send_bytes, json.dumps(request).encode()
                    )
                    response = json.loads(
                        (await asyncio.to_thread(resp_recv.recv_bytes, 65537)).decode()
                    )
                    assert response["id"] == request_id
                    assert response["ok"] is False
                    if request_id == "first":
                        await asyncio.sleep(0.06)
                        assert not pump.done()
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_malformed_frame_closes_channel(self):
        req_recv, req_send, resp_recv, resp_send = self._pair()
        principal = LocalDispatchPrincipal(caller_org_id=None)
        pump = asyncio.create_task(
            serve_channel(
                recv_conn=req_recv,
                send_conn=resp_send,
                session_factory=None,
                principal=principal,
            )
        )
        try:
            await asyncio.to_thread(req_send.send_bytes, b"\xff\xfe-not-json")
            assert await asyncio.wait_for(pump, timeout=10.0) == "malformed"
        finally:
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_id_less_frame_closes_channel(self):
        req_recv, req_send, resp_recv, resp_send = self._pair()
        principal = LocalDispatchPrincipal(caller_org_id=None)
        pump = asyncio.create_task(
            serve_channel(
                recv_conn=req_recv,
                send_conn=resp_send,
                session_factory=None,
                principal=principal,
            )
        )
        try:
            await asyncio.to_thread(
                req_send.send_bytes, json.dumps({"v": 1, "op": "config.get"}).encode()
            )
            assert await asyncio.wait_for(pump, timeout=10.0) == "malformed"
        finally:
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_oversized_frame_closes_without_allocation(self):
        from bifrost._local_transport import MAX_FRAME_BYTES

        req_recv, req_send, resp_recv, resp_send = self._pair()
        principal = LocalDispatchPrincipal(caller_org_id=None)
        pump = asyncio.create_task(
            serve_channel(
                recv_conn=req_recv,
                send_conn=resp_send,
                session_factory=None,
                principal=principal,
            )
        )
        # The 64KB pipe buffer cannot hold the body the parent refuses to
        # drain, so the sender blocks: drive it in the background and close
        # underneath it after the parent reports "oversized".
        sender = asyncio.create_task(
            asyncio.to_thread(
                req_send.send_bytes, b'{"k": "' + b"x" * (MAX_FRAME_BYTES + 1) + b'"}'
            )
        )
        try:
            assert await asyncio.wait_for(pump, timeout=10.0) == "oversized"
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_error_response_keeps_channel_usable(self, db_session):
        req_recv, req_send, resp_recv, resp_send = self._pair()
        principal = LocalDispatchPrincipal(caller_org_id=uuid4())

        async def _child():
            bad = {"v": 1, "id": "bad-1", "op": "nope", "key": "k", "scope": None}
            good = {"v": 1, "id": "good-1", "op": "config.get", "key": "absent", "scope": None}
            await asyncio.to_thread(req_send.send_bytes, json.dumps(bad).encode())
            first = json.loads(
                (await asyncio.to_thread(resp_recv.recv_bytes, 65537)).decode()
            )
            await asyncio.to_thread(req_send.send_bytes, json.dumps(good).encode())
            second = json.loads(
                (await asyncio.to_thread(resp_recv.recv_bytes, 65537)).decode()
            )
            return first, second

        pump = asyncio.create_task(
            serve_channel(
                recv_conn=req_recv,
                send_conn=resp_send,
                session_factory=lambda: _factory(db_session),
                principal=principal,
            )
        )
        try:
            with patch(
                "src.core.cache.redis_client.get_shared_redis",
                new=AsyncMock(return_value=_empty_redis()),
            ):
                first, second = await asyncio.wait_for(_child(), timeout=15.0)
            assert first["ok"] is False and first["status"] == 404
            assert second["ok"] is True and second["result"] is None
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_parent_shutdown_cancels_pump(self):
        req_recv, req_send, resp_recv, resp_send = self._pair()
        principal = LocalDispatchPrincipal(caller_org_id=None)
        pump = asyncio.create_task(
            serve_channel(
                recv_conn=req_recv,
                send_conn=resp_send,
                session_factory=None,
                principal=principal,
            )
        )
        try:
            await asyncio.sleep(0.2)
            pump.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pump
        finally:
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_cross_channel_isolation(self, db_session):
        from uuid import uuid4 as _uuid

        async def _seed_org(db_session):
            from src.models.orm.organizations import Organization as OrganizationModel

            row = OrganizationModel(
                name=f"sdk-local-org-{_uuid().hex[:8]}",
                is_active=True,
                created_by="isolation-test",
            )
            db_session.add(row)
            await db_session.flush()
            return row

        async def _seed(key, value, org_id):
            from src.models.enums import ConfigType as ConfigTypeEnum
            from src.models.orm.config import Config as ConfigModel

            db_session.add(
                ConfigModel(
                    key=key,
                    value={"value": value},
                    config_type=ConfigTypeEnum.STRING,
                    organization_id=org_id,
                    updated_by="isolation-test",
                )
            )
            await db_session.flush()

        key = f"iso-{_uuid().hex[:8]}"
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        await _seed(key, "A", org_a.id)
        await _seed(key, "B", org_b.id)

        async def _roundtrip(org_id):
            req_recv, req_send, resp_recv, resp_send = self._pair()
            pump = asyncio.create_task(
                serve_channel(
                    recv_conn=req_recv,
                    send_conn=resp_send,
                    session_factory=lambda: _factory(db_session),
                    principal=LocalDispatchPrincipal(caller_org_id=org_id),
                )
            )
            try:
                frame = {"v": 1, "id": "r", "op": "config.get", "key": key, "scope": None}
                await asyncio.to_thread(req_send.send_bytes, json.dumps(frame).encode())
                raw = await asyncio.to_thread(resp_recv.recv_bytes, 65537)
                return json.loads(raw.decode())
            finally:
                pump.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
                for conn in (req_recv, req_send, resp_recv, resp_send):
                    with contextlib.suppress(Exception):
                        conn.close()

        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=_empty_redis()),
        ):
            # scope=None is UNSET → each channel resolves to its OWN org.
            ra, rb = await asyncio.gather(
                _roundtrip(org_a.id), _roundtrip(org_b.id)
            )
        assert ra["result"]["value"] == "A"
        assert rb["result"]["value"] == "B"


@pytest.mark.asyncio
class TestFailClosedRouting:
    async def test_route_execution_rejects_corrupt_identity_before_fork(self):
        """A malformed parent org id fails dispatch with no side effects."""
        from src.services.execution.process_pool import ProcessPoolManager
        from src.services.execution.sdk_local_dispatch import LocalPrincipalError

        pool = ProcessPoolManager(max_workers=5)
        forked: list = []
        pool._fork_process = lambda: forked.append(True)
        active = {
            "execution_id": "exec-bad",
            "workflow_id": "w",
            "workflow_name": "w",
            "org_id": None,
            "user_id": "u",
            "user_name": "U",
            "user_email": "u@e.com",
            "sync": False,
            "event": None,
        }
        with pytest.raises(LocalPrincipalError, match="not a valid UUID"):
            await pool.route_execution(
                "exec-bad",
                {"organization": {"id": "junk"}, "timeout_seconds": 300},
                active,
            )
        assert forked == []
        assert pool.processes == {}


@pytest.mark.asyncio
class TestChunkedParentFrames:
    async def test_large_result_splits_into_bounded_frames(self):
        """One contract: a >64KiB result returns bounded header+part frames."""
        import json

        from bifrost._local_transport import MAX_FRAME_BYTES
        from src.services.execution.sdk_local_dispatch import dispatch_frames

        big = "x" * 100_000
        payload = {"key": "big", "value": big, "config_type": "string"}

        @contextlib.asynccontextmanager
        async def _stub_factory():
            yield object()

        with patch(
            "shared.sdk_config.get_sdk_config_dict",
            new=AsyncMock(return_value=payload),
        ):
            frames = await dispatch_frames(
                _stub_factory,
                LocalDispatchPrincipal(caller_org_id=None, is_platform_admin=True),
                {"v": 1, "id": "big-1", "op": "config.get", "key": "big", "scope": "global"},
            )
            collected = list(frames)
        assert len(collected) > 1
        header, parts = collected[0], collected[1:]
        assert header["ok"] is True and header["chunked"] is True
        assert header["parts"] == len(parts) >= 2
        for i, part in enumerate(parts):
            assert part["id"] == "big-1" and part["part"] == i
            raw = json.dumps(part, separators=(",", ":")).encode()
            assert len(raw) <= MAX_FRAME_BYTES

    async def test_chunked_frames_are_lazy(self):
        """One contract: parts encode only as the iterable is consumed."""
        import base64 as _b64

        from src.services.execution.sdk_local_dispatch import dispatch_frames

        big = "x" * 100_000
        payload = {"key": "big", "value": big, "config_type": "string"}

        @contextlib.asynccontextmanager
        async def _stub_factory():
            yield object()

        with (
            patch(
                "shared.sdk_config.get_sdk_config_dict",
                new=AsyncMock(return_value=payload),
            ),
            patch("base64.b64encode", wraps=_b64.b64encode) as spy,
        ):
            frames = await dispatch_frames(
                _stub_factory,
                LocalDispatchPrincipal(caller_org_id=None, is_platform_admin=True),
                {"v": 1, "id": "big-lazy", "op": "config.get", "key": "big", "scope": "global"},
            )
            # Nothing encoded yet: the header + parts materialize on iteration.
            assert spy.call_count == 0
            assert not isinstance(frames, list)
            collected = list(frames)
            assert len(collected) > 1
            assert collected[0].get("chunked") is True
            assert spy.call_count == len(collected) - 1


def _email_principal(org_id=None, **kwargs):
    kwargs.setdefault("actor_email", "dispatch-actor@test.local")
    return LocalDispatchPrincipal(
        caller_org_id=org_id,
        is_platform_admin=kwargs.get("is_platform_admin", False),
        is_provider_org=kwargs.get("is_provider_org", False),
        is_external=kwargs.get("is_external", False),
        actor_email=kwargs.get("actor_email"),
    )


@pytest.mark.asyncio
class TestSetListDeleteDispatch:
    async def _dispatch(self, db_session, frame, principal):
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=_empty_redis()),
        ):
            return await dispatch_frame(
                lambda: _factory(db_session), principal, frame
            )

    async def test_new_ops_are_allowlisted(self, db_session):
        from shared import sdk_config as shared_service

        principal = _email_principal(is_platform_admin=True)
        with (
            patch.object(
                shared_service, "set_sdk_config_value", new=AsyncMock()
            ),
            patch.object(
                shared_service,
                "list_sdk_config_values",
                new=AsyncMock(return_value={"a": 1}),
            ),
            patch.object(
                shared_service,
                "delete_sdk_config_value",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "src.core.cache.redis_client.get_shared_redis",
                new=AsyncMock(return_value=_empty_redis()),
            ),
        ):
            set_resp = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {
                    "v": 1, "id": "s-1", "op": "config.set",
                    "key": "k", "value": 1, "is_secret": False,
                    "scope": "global",
                },
            )
            list_resp = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {"v": 1, "id": "l-1", "op": "config.list", "scope": "global"},
            )
            del_resp = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {
                    "v": 1, "id": "d-1", "op": "config.delete",
                    "key": "k", "scope": "global",
                },
            )
        assert set_resp == {
            "v": 1, "id": "s-1", "ok": True, "result": None,
        }
        assert list_resp["ok"] is True and list_resp["result"] == {"a": 1}
        assert del_resp == {
            "v": 1, "id": "d-1", "ok": True, "result": True,
        }

    async def test_set_passes_parent_identity_to_service(self, db_session):
        from shared import sdk_config as shared_service

        org_id = uuid4()
        principal = _email_principal(org_id, actor_email="setter@test.local")
        seen = {}

        async def _spy(session, *, key, value, is_secret, org_id, actor_email):
            seen.update(
                key=key, value=value, is_secret=is_secret, org_id=org_id,
                actor_email=actor_email,
            )

        with (
            patch.object(shared_service, "set_sdk_config_value", new=_spy),
            patch(
                "src.core.cache.redis_client.get_shared_redis",
                new=AsyncMock(return_value=_empty_redis()),
            ),
        ):
            response = await dispatch_frame(
                lambda: _factory(db_session),
                principal,
                {
                    "v": 1, "id": "s-2", "op": "config.set",
                    "key": "k", "value": {"n": 2}, "is_secret": False,
                    "scope": str(org_id),
                },
            )
        assert response["ok"] is True
        assert seen == {
            "key": "k",
            "value": {"n": 2},
            "is_secret": False,
            "org_id": org_id,
            "actor_email": "setter@test.local",
        }

    async def test_set_rejects_malformed_fields(self, db_session):
        principal = _email_principal(is_platform_admin=True)
        bad_frames = [
            {"v": 1, "id": "b-1", "op": "config.set", "key": {"nested": 1},
             "value": 1, "is_secret": False, "scope": "global"},
            {"v": 1, "id": "b-2", "op": "config.set", "key": "k",
             "is_secret": False, "scope": "global"},
            # "maybe" is not bool-coercible (the HTTP DTO 422s it too);
            # coercible strings like "yes" follow the DTO and are accepted.
            {"v": 1, "id": "b-3", "op": "config.set", "key": "k",
             "value": 1, "is_secret": "maybe", "scope": "global"},
            {"v": 1, "id": "b-4", "op": "config.delete", "key": 42,
             "scope": "global"},
        ]
        for frame in bad_frames:
            response = await self._dispatch(db_session, frame, principal)
            assert response["ok"] is False, frame
            assert response["status"] == 422, frame

    async def test_set_bool_coercion_matches_http_dto(self, db_session):
        """is_secret follows the HTTP DTO: coercible strings coerce, junk 422s."""
        from src.models.contracts.cli import CLIConfigSetRequest

        # The DTO is the contract: "yes" coerces, "maybe" raises.
        assert CLIConfigSetRequest(
            key="k", value=1, is_secret="yes", scope="global"
        ).is_secret is True
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            CLIConfigSetRequest(
                key="k", value=1, is_secret="maybe", scope="global"
            )

        tag = uuid4().hex[:8]
        key = f"coerce-{tag}"
        principal = _email_principal(is_platform_admin=True)
        response = await self._dispatch(
            db_session,
            {"v": 1, "id": "co-1", "op": "config.set", "key": key,
             "value": "s3cr3t", "is_secret": "yes", "scope": "global"},
            principal,
        )
        assert response["ok"] is True, response
        from sqlalchemy import select

        from src.models import Config as ConfigModel

        row = (
            await db_session.execute(
                select(ConfigModel).where(
                    ConfigModel.key == key,
                    ConfigModel.organization_id.is_(None),
                )
            )
        ).scalar_one_or_none()
        assert row is not None
        assert row.config_type.value == "secret"
        assert row.value["value"] != "s3cr3t"

    async def test_cross_org_mutation_denied(self, db_session):
        principal = _email_principal(uuid4())
        for frame in (
            {"v": 1, "id": "c-1", "op": "config.set", "key": "k",
             "value": 1, "is_secret": False, "scope": str(uuid4())},
            {"v": 1, "id": "c-2", "op": "config.list", "scope": str(uuid4())},
            {"v": 1, "id": "c-3", "op": "config.delete", "key": "k",
             "scope": str(uuid4())},
        ):
            response = await self._dispatch(db_session, frame, principal)
            assert response["ok"] is False
            assert response["status"] == 403

    async def test_mutation_without_actor_email_fails_closed(self, db_session):
        from shared import sdk_config as shared_service

        principal = LocalDispatchPrincipal(
            caller_org_id=None, is_platform_admin=True, actor_email=None
        )
        with (
            patch.object(
                shared_service,
                "set_sdk_config_value",
                new=AsyncMock(
                    side_effect=AssertionError("must not reach the service")
                ),
            ),
            patch.object(
                shared_service,
                "delete_sdk_config_value",
                new=AsyncMock(
                    side_effect=AssertionError("must not reach the service")
                ),
            ),
        ):
            for frame in (
                {"v": 1, "id": "e-1", "op": "config.set", "key": "k",
                 "value": 1, "is_secret": False, "scope": "global"},
                {"v": 1, "id": "e-2", "op": "config.delete", "key": "k",
                 "scope": "global"},
            ):
                response = await self._dispatch(db_session, frame, principal)
                assert response["ok"] is False
                assert response["status"] == 500

    async def test_one_short_session_per_mutation(self, db_session):
        principal = _email_principal(is_platform_admin=True)
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
            {"v": 1, "id": "m-1", "op": "config.set", "key": f"sess-{uuid4().hex[:8]}",
             "value": 1, "is_secret": False, "scope": "global"},
            {"v": 1, "id": "m-2", "op": "config.list", "scope": "global"},
            {"v": 1, "id": "m-3", "op": "config.delete", "key": "absent",
             "scope": "global"},
        ]
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=_empty_redis()),
        ):
            for frame in frames:
                response = await dispatch_frame(
                    _counting_factory, principal, frame
                )
                assert response["ok"] is True, frame
        assert opened == 3
        assert closed == 3

    async def test_large_list_result_splits_into_bounded_frames(self):
        import json

        from bifrost._local_transport import MAX_FRAME_BYTES
        from src.services.execution.sdk_local_dispatch import dispatch_frames

        payload = {f"key-{i}": "x" * 2000 for i in range(100)}

        @contextlib.asynccontextmanager
        async def _stub_factory():
            yield object()

        with patch(
            "shared.sdk_config.list_sdk_config_values",
            new=AsyncMock(return_value=payload),
        ):
            frames = await dispatch_frames(
                _stub_factory,
                _email_principal(is_platform_admin=True),
                {"v": 1, "id": "big-list", "op": "config.list", "scope": "global"},
            )
            collected = list(frames)
        assert len(collected) > 1
        header, parts = collected[0], collected[1:]
        assert header["ok"] is True and header["chunked"] is True
        assert header["parts"] == len(parts) >= 2
        for i, part in enumerate(parts):
            assert part["id"] == "big-list" and part["part"] == i
            raw = json.dumps(part, separators=(",", ":")).encode()
            assert len(raw) <= MAX_FRAME_BYTES


@pytest.mark.asyncio
class TestChunkedRequests:
    """Bounded chunked child→parent requests (large config.set values)."""

    def _pair(self):
        req_recv, req_send = multiprocessing.Pipe(duplex=False)
        resp_recv, resp_send = multiprocessing.Pipe(duplex=False)
        return req_recv, req_send, resp_recv, resp_send

    def _close_all(self, conns):
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    def _chunked_raw(self, request):
        """Encode one request exactly like the child transport does."""
        import base64 as _b64

        from bifrost._local_transport import _CHUNK_RAW_BYTES, MAX_FRAME_BYTES

        raw = json.dumps(request, separators=(",", ":")).encode("utf-8")
        assert len(raw) > MAX_FRAME_BYTES
        total = len(raw)
        parts = -(-total // _CHUNK_RAW_BYTES)
        frames = [
            json.dumps(
                {
                    "v": 1, "id": request["id"], "op": request["op"],
                    "chunked": True, "total": total, "parts": parts,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        ]
        for i in range(parts):
            chunk = raw[i * _CHUNK_RAW_BYTES : (i + 1) * _CHUNK_RAW_BYTES]
            frames.append(
                json.dumps(
                    {
                        "v": 1, "id": request["id"], "part": i,
                        "data": _b64.b64encode(chunk).decode("ascii"),
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        for encoded in frames:
            assert len(encoded) <= MAX_FRAME_BYTES
        return frames

    async def test_chunked_set_request_served(self, db_session):
        from sqlalchemy import select

        from src.models import Config as ConfigModel

        tag = uuid4().hex[:8]
        key = f"chunked-{tag}"
        big = "q" * 100_000
        request = {
            "v": 1, "id": "chunk-1", "op": "config.set",
            "key": key, "value": big, "is_secret": False, "scope": "global",
        }
        frames = self._chunked_raw(request)

        req_recv, req_send, resp_recv, resp_send = self._pair()
        principal = _email_principal(
            is_platform_admin=True, actor_email="chunk-actor@test.local"
        )
        pump = asyncio.create_task(
            serve_channel(
                recv_conn=req_recv,
                send_conn=resp_send,
                session_factory=lambda: _factory(db_session),
                principal=principal,
            )
        )
        try:
            with patch(
                "src.core.cache.redis_client.get_shared_redis",
                new=AsyncMock(return_value=_empty_redis()),
            ):
                for encoded in frames:
                    await asyncio.to_thread(req_send.send_bytes, encoded)
                raw = await asyncio.to_thread(resp_recv.recv_bytes, 65537)
            response = json.loads(raw.decode("utf-8"))
            assert response == {
                "v": 1, "id": "chunk-1", "ok": True, "result": None,
            }
            row = (
                await db_session.execute(
                    select(ConfigModel).where(
                        ConfigModel.key == key,
                        ConfigModel.organization_id.is_(None),
                    )
                )
            ).scalar_one_or_none()
            assert row is not None
            assert row.value == {"value": big}
            assert row.updated_by == "chunk-actor@test.local"
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_lying_chunk_header_closes_malformed(self):
        req_recv, req_send, resp_recv, resp_send = self._pair()
        principal = _email_principal(is_platform_admin=True)
        pump = asyncio.create_task(
            serve_channel(
                recv_conn=req_recv,
                send_conn=resp_send,
                session_factory=None,
                principal=principal,
            )
        )
        try:
            lie = {
                "v": 1, "id": "lie-1", "op": "config.set",
                "chunked": True, "total": 100, "parts": 50,
            }
            await asyncio.to_thread(
                req_send.send_bytes, json.dumps(lie).encode()
            )
            assert await asyncio.wait_for(pump, timeout=10.0) == "malformed"
        finally:
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_part_order_violation_closes_malformed(self):
        import base64 as _b64

        from bifrost._local_transport import _CHUNK_RAW_BYTES

        req_recv, req_send, resp_recv, resp_send = self._pair()
        principal = _email_principal(is_platform_admin=True)
        pump = asyncio.create_task(
            serve_channel(
                recv_conn=req_recv,
                send_conn=resp_send,
                session_factory=None,
                principal=principal,
            )
        )
        try:
            raw = json.dumps(
                {"v": 1, "id": "ord-1", "op": "config.set", "key": "k",
                 "value": "x" * 100_000, "is_secret": False, "scope": None}
            ).encode()
            total, parts = len(raw), -(-len(raw) // _CHUNK_RAW_BYTES)
            assert parts >= 2
            header = {
                "v": 1, "id": "ord-1", "op": "config.set",
                "chunked": True, "total": total, "parts": parts,
            }
            await asyncio.to_thread(
                req_send.send_bytes, json.dumps(header).encode()
            )
            # Send part 1 before part 0.
            for i in (1, 0):
                chunk = raw[i * _CHUNK_RAW_BYTES : (i + 1) * _CHUNK_RAW_BYTES]
                part = {
                    "v": 1, "id": "ord-1", "part": i,
                    "data": _b64.b64encode(chunk).decode("ascii"),
                }
                await asyncio.to_thread(
                    req_send.send_bytes, json.dumps(part).encode()
                )
            assert await asyncio.wait_for(pump, timeout=10.0) == "malformed"
        finally:
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_child_gone_mid_request_returns_eof(self):
        req_recv, req_send, resp_recv, resp_send = self._pair()
        principal = _email_principal(is_platform_admin=True)
        pump = asyncio.create_task(
            serve_channel(
                recv_conn=req_recv,
                send_conn=resp_send,
                session_factory=None,
                principal=principal,
            )
        )
        try:
            header = {
                "v": 1, "id": "gone-1", "op": "config.set",
                "chunked": True, "total": 100_000, "parts": 3,
            }
            await asyncio.to_thread(
                req_send.send_bytes, json.dumps(header).encode()
            )
            req_send.close()
            assert await asyncio.wait_for(pump, timeout=10.0) == "eof"
        finally:
            self._close_all((req_recv, resp_recv, resp_send))


class TestActorDerivation:
    def test_workflow_derives_engine_sentinel_not_caller(self):
        """Workflows attribute to the engine token address, not the user."""
        from src.core.security import ENGINE_SDK_ACTOR_EMAIL

        org_id = uuid4()
        principal = principal_from_context(
            {
                "organization": {"id": str(org_id)},
                "caller": {"user_id": "u", "email": "initiator@test.local"},
            }
        )
        assert principal.actor_email == ENGINE_SDK_ACTOR_EMAIL
        assert principal.is_service is False

    def test_workflow_ignores_caller_email_entirely(self):
        from src.core.security import ENGINE_SDK_ACTOR_EMAIL

        for caller in (
            {"user_id": "u", "email": "actor@test.local"},
            {"user_id": "u", "email": ""},
            {"user_id": "u", "email": 12345},
            "not-a-mapping",
            None,
        ):
            principal = principal_from_context(
                {"organization": None, "caller": caller}
            )
            assert principal.actor_email == ENGINE_SDK_ACTOR_EMAIL

    def test_service_derives_shared_service_email(self):
        from src.core.security import service_sdk_actor_email

        service_id = str(uuid4())
        org_id = uuid4()
        principal = principal_from_context(
            {
                "organization": {"id": str(org_id)},
                "caller": {"user_id": "u", "email": "stale@test.local"},
                "service": {
                    "service_id": service_id,
                    "attempt_id": str(uuid4()),
                },
            }
        )
        assert principal.actor_email == service_sdk_actor_email(service_id)
        assert principal.is_service is True
        # Service tokens are never superuser, even if the context claims it.
        assert principal.is_platform_admin is False

    def test_service_email_matches_mint_service_token(self):
        """One helper serves token minting and local dispatch alike."""
        from src.core.security import decode_token, mint_service_token

        service_id = str(uuid4())
        attempt_id = str(uuid4())
        org_id = str(uuid4())
        token, _ = mint_service_token(
            service_id=service_id,
            attempt_id=attempt_id,
            organization_id=org_id,
            solution_id=None,
            global_repo_access=False,
        )
        payload = decode_token(token)
        assert payload is not None
        principal = principal_from_context(
            {
                "organization": {"id": org_id},
                "service": {"service_id": service_id, "attempt_id": attempt_id},
            }
        )
        assert principal.actor_email == payload["email"]

    def test_malformed_service_identity_fails_closed(self):
        from src.services.execution.sdk_local_dispatch import LocalPrincipalError

        bad_contexts = [
            {"organization": None, "service": "not-a-mapping"},
            {"organization": None, "service": {}},
            {"organization": None, "service": {"service_id": ""}},
            {"organization": None, "service": {"service_id": "junk"}},
            {"organization": None, "service": {"service_id": 12345}},
            {"organization": None, "service": {"attempt_id": "x"}},
        ]
        for context in bad_contexts:
            with pytest.raises(LocalPrincipalError):
                principal_from_context(context)

    def test_child_frames_cannot_set_actor(self):
        """No frame field flows into the audit email (dispatch ignores it)."""
        from src.core.security import ENGINE_SDK_ACTOR_EMAIL

        principal = principal_from_context({"organization": None})
        assert principal.actor_email == ENGINE_SDK_ACTOR_EMAIL


@pytest.mark.asyncio
class TestServiceProviderRevocation:
    """Live provider check for long-lived services (HTTP parity).

    HTTP ``_resolve_sdk_org_id`` queries ``Organization.is_provider``
    when a bypass is requested; a service started as a provider must lose
    cross-org/global access after revocation. Own-org calls need no
    lookup at all.
    """

    async def _seed_org(self, db_session, *, is_provider):
        from src.models.orm.organizations import Organization as OrganizationModel

        row = OrganizationModel(
            name=f"sdk-rev-{uuid4().hex[:8]}",
            is_active=True,
            is_provider=is_provider,
            created_by="revocation-test",
        )
        db_session.add(row)
        await db_session.flush()
        return row

    def _service_principal(self, org_id):
        # Snapshot at service start claimed provider membership.
        return principal_from_context(
            {
                "organization": {"id": str(org_id), "is_provider": True},
                "service": {
                    "service_id": str(uuid4()),
                    "attempt_id": str(uuid4()),
                },
            }
        )

    async def _dispatch(self, db_session, frame, principal):
        with patch(
            "src.core.cache.redis_client.get_shared_redis",
            new=AsyncMock(return_value=_empty_redis()),
        ):
            return await dispatch_frame(
                lambda: _factory(db_session), principal, frame
            )

    async def test_live_lookup_allows_before_revocation(self, db_session):
        provider = await self._seed_org(db_session, is_provider=True)
        other = await self._seed_org(db_session, is_provider=False)
        principal = self._service_principal(provider.id)
        with patch(
            "shared.sdk_config.get_sdk_config_dict",
            new=AsyncMock(return_value=None),
        ):
            response = await self._dispatch(
                db_session, _frame(scope=str(other.id)), principal
            )
        assert response["ok"] is True, response
        assert response["result"] is None

    async def test_revoked_provider_denied_cross_org_and_global(
        self, db_session
    ):
        provider = await self._seed_org(db_session, is_provider=True)
        other = await self._seed_org(db_session, is_provider=False)
        principal = self._service_principal(provider.id)
        # Revoke after the service started: the snapshot still claims
        # provider, but the live row no longer does.
        provider.is_provider = False
        await db_session.flush()

        cross = await self._dispatch(
            db_session, _frame(scope=str(other.id)), principal
        )
        assert cross["ok"] is False
        assert cross["status"] == 403

        glob = await self._dispatch(
            db_session, _frame(scope="global"), principal
        )
        assert glob["ok"] is False
        assert glob["status"] == 403

    async def test_own_org_needs_no_lookup_after_revocation(
        self, db_session
    ):
        provider = await self._seed_org(db_session, is_provider=True)
        principal = self._service_principal(provider.id)
        provider.is_provider = False
        await db_session.flush()

        # Own-org (UNSET) resolves without consulting membership at all:
        # even a broken lookup path cannot fail it.
        with (
            patch(
                "src.services.execution.sdk_local_dispatch."
                "_live_provider_membership",
                side_effect=AssertionError("no lookup for own-org"),
            ),
            patch(
                "shared.sdk_config.get_sdk_config_dict",
                new=AsyncMock(return_value=None),
            ),
        ):
            response = await self._dispatch(
                db_session, _frame(scope=None), principal
            )
        assert response["ok"] is True, response
