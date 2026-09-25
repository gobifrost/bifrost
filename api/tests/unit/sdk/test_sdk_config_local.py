"""Stage 1: HTTP/local parity for ``config.get`` plus child transport behavior.

Covers the acceptance surface that does not need a forked child:

- the shared service (``shared.sdk_config``) resolves scope/cascade,
  external-user behavior, secret decryption, null/default, and type
  coercion exactly like the HTTP handler (the handler delegates to it);
- the parent dispatcher calls that same service with a parent-derived
  principal and maps scope/key failures to HTTP-style statuses;
- the child transport performs real ``config.get`` round trips over a
  dedicated channel with zero HTTP requests, safe concurrent calls,
  timeout/EOF handling, bounded frames, and no silent HTTP fallback.
"""

import asyncio
import contextlib
import json
import multiprocessing
import subprocess
import sys
import time
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from bifrost._local_transport import (
    MAX_FRAME_BYTES,
    ChildLocalTransport,
    decode_frame,
    encode_frame,
)
from src.models.contracts.cli import (
    CLIConfigDeleteRequest,
    CLIConfigGetRequest,
    CLIConfigListRequest,
    CLIConfigSetRequest,
)


def _no_cache():
    """Patch redis so merged_for_sdk falls through to the DB query path."""
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    return patch(
        "src.core.cache.redis_client.get_shared_redis",
        new=AsyncMock(return_value=redis),
    )


async def _seed_org(db_session, *, is_provider=False):
    """Insert one organization row (configs FK-require it)."""
    from src.models.orm.organizations import Organization as OrganizationModel

    row = OrganizationModel(
        name=f"sdk-local-org-{uuid4().hex[:8]}",
        is_active=True,
        is_provider=is_provider,
        created_by="sdk-local-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_config(db_session, *, key, value, config_type="string", org_id=None):
    """Insert one config row visible to the current test session."""
    from src.models.enums import ConfigType as ConfigTypeEnum
    from src.models.orm.config import Config as ConfigModel

    stored = value
    if config_type == "secret":
        from src.core.security import encrypt_secret

        stored = encrypt_secret(value)
    row = ConfigModel(
        key=key,
        value={"value": stored},
        config_type=ConfigTypeEnum(config_type),
        organization_id=org_id,
        updated_by="sdk-local-test",
    )
    db_session.add(row)
    await db_session.flush()
    return row


@contextlib.asynccontextmanager
async def _db_factory(db_session):
    yield db_session


def _principal(org_id=None, **kwargs):
    from src.services.execution.sdk_local_dispatch import (
        LocalDispatchPrincipal,
    )

    return LocalDispatchPrincipal(
        caller_org_id=org_id,
        is_platform_admin=kwargs.get("is_platform_admin", False),
        is_provider_org=kwargs.get("is_provider_org", False),
        is_external=kwargs.get("is_external", False),
        actor_email=kwargs.get("actor_email", "sdk-local@test.local"),
    )


class _StubUser:
    def __init__(
        self,
        organization_id=None,
        is_superuser=False,
        is_external=False,
        email="sdk-local@test.local",
    ):
        self.organization_id = organization_id
        self.is_superuser = is_superuser
        self.is_external = is_external
        self.email = email


async def _http_get(db_session, *, key, scope, user):
    """Call the real HTTP handler function with a stub principal."""
    from src.routers.cli import cli_get_config

    return await cli_get_config(
        CLIConfigGetRequest(key=key, scope=scope),
        user,
        db_session,
    )


async def _local_get(db_session, *, key, scope, principal):
    """Serve one frame through the real parent dispatcher."""
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    frame = {"v": 1, "id": "parity-1", "op": "config.get", "key": key, "scope": scope}
    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


@pytest.mark.asyncio
class TestHttpLocalParity:
    async def test_string_int_bool_json_round_trip(self, db_session):
        tag = uuid4().hex[:8]
        await _seed_config(db_session, key=f"s-{tag}", value="hello")
        await _seed_config(db_session, key=f"i-{tag}", value=42, config_type="int")
        await _seed_config(db_session, key=f"b-{tag}", value=True, config_type="bool")
        await _seed_config(
            db_session, key=f"j-{tag}", value='{"a": 1}', config_type="json"
        )
        # Global scope needs the bypass the engine sentinel carries.
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        with _no_cache():
            for key, expected, ctype in [
                (f"s-{tag}", "hello", "string"),
                (f"i-{tag}", 42, "int"),
                (f"b-{tag}", True, "bool"),
                (f"j-{tag}", {"a": 1}, "json"),
            ]:
                http_value = await _http_get(
                    db_session, key=key, scope="global", user=user
                )
                local = await _local_get(
                    db_session, key=key, scope="global", principal=principal
                )
                assert local["ok"] is True, local
                assert local["result"] == http_value.model_dump()
                assert local["result"]["value"] == expected
                assert local["result"]["config_type"] == ctype

    async def test_org_cascade_global_fallback(self, db_session):
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        org_id = org.id
        await _seed_config(db_session, key=f"c-{tag}", value="from-global")
        await _seed_config(
            db_session, key=f"c-{tag}", value="from-org", org_id=org_id
        )
        user = _StubUser(organization_id=org_id)
        principal = _principal(org_id)
        with _no_cache():
            http_value = await _http_get(
                db_session, key=f"c-{tag}", scope=None, user=user
            )
            local = await _local_get(
                db_session, key=f"c-{tag}", scope=str(org_id), principal=principal
            )
            assert http_value.value == "from-org"
            assert local["ok"] is True
            assert local["result"]["value"] == "from-org"

            # An org with no override falls back to the global tier.
            other_org = await _seed_org(db_session)
            other = _principal(other_org.id)
            fallback = await _local_get(
                db_session, key=f"c-{tag}", scope=None, principal=other
            )
            # scope=None is UNSET → caller's own org → global fallback.
            assert fallback["ok"] is True
            assert fallback["result"]["value"] == "from-global"

    async def test_missing_key_is_null_not_error(self, db_session):
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        with _no_cache():
            http_value = await _http_get(
                db_session, key="no-such-key", scope="global", user=user
            )
            local = await _local_get(
                db_session, key="no-such-key", scope="global", principal=principal
            )
            assert http_value is None
            assert local["ok"] is True
            assert local["result"] is None

    async def test_long_missing_key_is_null_not_error(self, db_session):
        """Parity: a missing >1024-char key returns null via HTTP and local."""
        long_key = "k" * 2000
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        with _no_cache():
            http_value = await _http_get(
                db_session, key=long_key, scope="global", user=user
            )
            local = await _local_get(
                db_session, key=long_key, scope="global", principal=principal
            )
            assert http_value is None
            assert local["ok"] is True
            assert local["result"] is None

    async def test_secret_decrypted(self, db_session):
        tag = uuid4().hex[:8]
        await _seed_config(
            db_session, key=f"sec-{tag}", value="super-secret", config_type="secret"
        )
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        with _no_cache():
            http_value = await _http_get(
                db_session, key=f"sec-{tag}", scope="global", user=user
            )
            local = await _local_get(
                db_session, key=f"sec-{tag}", scope="global", principal=principal
            )
            assert http_value.value == "super-secret"
            assert http_value.config_type == "secret"
            assert local["ok"] is True
            assert local["result"]["value"] == "super-secret"

    async def test_external_caller_drops_global_tier(self, db_session):
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        org_id = org.id
        await _seed_config(db_session, key=f"x-{tag}", value="global-secret-value")
        user = _StubUser(organization_id=org_id, is_external=True)
        principal = _principal(org_id, is_external=True)
        with _no_cache():
            http_value = await _http_get(
                db_session, key=f"x-{tag}", scope=None, user=user
            )
            local = await _local_get(
                db_session, key=f"x-{tag}", scope=None, principal=principal
            )
            assert http_value is None
            assert local["ok"] is True
            assert local["result"] is None

    async def test_denied_cross_org_scope(self, db_session):
        tag = uuid4().hex[:8]
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        await _seed_config(
            db_session, key=f"d-{tag}", value="org-b-only", org_id=org_b.id
        )
        user = _StubUser(organization_id=org_a.id)
        principal = _principal(org_a.id)
        with _no_cache():
            import fastapi

            with pytest.raises(fastapi.HTTPException) as exc_info:
                await _http_get(
                    db_session, key=f"d-{tag}", scope=str(org_b.id), user=user
                )
            assert exc_info.value.status_code == 403
            local = await _local_get(
                db_session, key=f"d-{tag}", scope=str(org_b.id), principal=principal
            )
            assert local["ok"] is False
            assert local["status"] == 403

    async def test_malformed_scope_is_422(self, db_session):
        org = await _seed_org(db_session)
        principal = _principal(org.id)
        with _no_cache():
            local = await _local_get(
                db_session, key="k", scope="not-a-uuid", principal=principal
            )
            assert local["ok"] is False
            assert local["status"] == 422


class TestChildTransport:
    async def _pump(self, req_conn, resp_conn, handler, count=1):
        for _ in range(count):
            raw = await asyncio.to_thread(req_conn.recv_bytes, MAX_FRAME_BYTES + 1)
            frame = json.loads(raw.decode("utf-8"))
            response = handler(frame)
            await asyncio.to_thread(resp_conn.send_bytes, json.dumps(response).encode())

    def _pair(self):
        # req: child writes, parent reads. resp: parent writes, child reads.
        req_recv, req_send = multiprocessing.Pipe(duplex=False)
        resp_recv, resp_send = multiprocessing.Pipe(duplex=False)
        return req_recv, req_send, resp_recv, resp_send

    @pytest.mark.asyncio
    async def test_round_trip_without_http(self):
        from bifrost import _local_transport as lt
        from bifrost._context import (
            clear_execution_context,
            set_execution_context,
        )
        from src.sdk.context import ExecutionContext

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        ctx = ExecutionContext(
            user_id="u1", email="e@e.com", name="T", scope="org-1",
            organization=None, is_platform_admin=False,
            is_function_key=False, execution_id="exec-1",
        )
        set_execution_context(ctx)
        pump = asyncio.create_task(
            self._pump(
                req_recv, resp_send,
                lambda f: {
                    "v": 1, "id": f["id"], "ok": True,
                    "result": {"key": f["key"], "value": "v-secret", "config_type": "secret"},
                },
            )
        )
        try:
            from bifrost.config import config

            with patch("bifrost.config.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                value = await config.get("my_key")
            assert value == "v-secret"
            assert "v-secret" in ctx._collect_secret_values()
            await pump
        finally:
            lt.clear()
            clear_execution_context()
            for conn in (req_recv, req_send, resp_recv, resp_send):
                with contextlib.suppress(Exception):
                    conn.close()

    @pytest.mark.asyncio
    async def test_missing_key_returns_default(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        pump = asyncio.create_task(
            self._pump(
                req_recv, resp_send,
                lambda f: {"v": 1, "id": f["id"], "ok": True, "result": None},
            )
        )
        try:
            from bifrost.config import config

            with patch("bifrost.config.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                assert await config.get("absent", default="dflt") == "dflt"
            await pump
        finally:
            lt.clear()
            for conn in (req_recv, req_send, resp_recv, resp_send):
                with contextlib.suppress(Exception):
                    conn.close()

    @pytest.mark.asyncio
    async def test_local_error_never_falls_back_to_http(self):
        from bifrost import _local_transport as lt
        from bifrost.client import BifrostAuthorizationError

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        pump = asyncio.create_task(
            self._pump(
                req_recv, resp_send,
                lambda f: {"v": 1, "id": f["id"], "ok": False, "status": 403, "detail": "denied"},
            )
        )
        try:
            from bifrost.config import config

            with patch("bifrost.config.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                with pytest.raises(BifrostAuthorizationError):
                    await config.get("k")
            await pump
        finally:
            lt.clear()
            for conn in (req_recv, req_send, resp_recv, resp_send):
                with contextlib.suppress(Exception):
                    conn.close()

    @pytest.mark.asyncio
    async def test_concurrent_calls_share_one_channel(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        count = 20
        pump = asyncio.create_task(
            self._pump(
                req_recv, resp_send,
                lambda f: {
                    "v": 1, "id": f["id"], "ok": True,
                    "result": {"key": f["key"], "value": f"value-{f['key']}", "config_type": "string"},
                },
                count=count,
            )
        )
        try:
            from bifrost.config import config

            with patch("bifrost.config.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                values = await asyncio.gather(
                    *[config.get(f"k-{i}") for i in range(count)]
                )
            assert values == [f"value-k-{i}" for i in range(count)]
            await pump
        finally:
            lt.clear()
            for conn in (req_recv, req_send, resp_recv, resp_send):
                with contextlib.suppress(Exception):
                    conn.close()

    @pytest.mark.asyncio
    async def test_timeout_breaks_channel_without_fallback(self):
        req_recv, req_send, resp_recv, resp_send = self._pair()
        transport = ChildLocalTransport(req_send, resp_recv)
        try:
            # Parent never responds.
            with pytest.raises(TimeoutError):
                await transport.call_config_get("k", None, timeout=0.1)
            # The late-response hazard breaks the channel: the next call
            # fails immediately instead of reading a stale response.
            with pytest.raises(Exception):
                await transport.call_config_get("k", None, timeout=1.0)
        finally:
            for conn in (req_recv, req_send, resp_recv, resp_send):
                with contextlib.suppress(Exception):
                    conn.close()

    @pytest.mark.asyncio
    async def test_parent_close_raises_without_fallback(self):
        from bifrost._local_transport import LocalTransportClosed

        req_recv, req_send, resp_recv, resp_send = self._pair()
        transport = ChildLocalTransport(req_send, resp_recv)
        req_recv.close()
        resp_send.close()
        try:
            with pytest.raises(LocalTransportClosed):
                await transport.call_config_get("k", None, timeout=5.0)
        finally:
            for conn in (req_send, resp_recv):
                with contextlib.suppress(Exception):
                    conn.close()

    def test_frame_bounds(self):
        with pytest.raises(Exception):
            encode_frame({"blob": "x" * (MAX_FRAME_BYTES + 1)})
        with pytest.raises(Exception):
            decode_frame(b"not json{")
        with pytest.raises(Exception):
            decode_frame(json.dumps([1, 2]).encode())

    def test_child_module_imports_no_database(self):
        import os
        import textwrap
        from pathlib import Path

        probe = textwrap.dedent(
            """
            import multiprocessing
            import sys

            from bifrost import _local_transport as lt

            a, b = multiprocessing.Pipe(duplex=False)
            c, d = multiprocessing.Pipe(duplex=False)
            lt.install(b, c)
            forbidden = [
                m for m in sys.modules
                if m.startswith(("sqlalchemy", "asyncpg", "psycopg", "fastapi"))
                or m in ("src.models.orm", "src.core.database")
            ]
            assert not forbidden, forbidden
            print("clean")
            """
        )
        api_root = Path(__file__).resolve().parents[3]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(api_root)
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=api_root,
            env=env,
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "clean" in result.stdout


@pytest.mark.asyncio
class TestScopeParityMatrix:
    async def test_scope_outcomes_match_across_transports(self, db_session):
        """One contract: HTTP and local resolve every scope shape identically."""
        from fastapi import HTTPException

        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        provider_org = await _seed_org(db_session, is_provider=True)
        await _seed_config(db_session, key="scope-global-k", value="g")
        await _seed_config(db_session, key="scope-a-k", value="a", org_id=org_a.id)
        await _seed_config(db_session, key="scope-b-k", value="b", org_id=org_b.id)

        cases = [
            # (key, scope, user_kwargs, principal_kwargs)
            ("scope-a-k", None, {"org": org_a.id}, {"org": org_a.id}),
            ("scope-a-k", "", {"org": org_a.id}, {"org": org_a.id}),
            ("scope-a-k", str(org_a.id), {"org": org_a.id}, {"org": org_a.id}),
            ("scope-global-k", "global", {"admin": True}, {"admin": True}),
            ("scope-global-k", "global", {}, {}),
            ("scope-b-k", str(org_b.id), {"org": org_a.id}, {"org": org_a.id}),
            (
                "scope-b-k",
                str(org_b.id),
                {"org": provider_org.id},
                {"org": provider_org.id, "provider": True},
            ),
            ("scope-a-k", "not-a-uuid", {"org": org_a.id}, {"org": org_a.id}),
        ]

        async def _http_outcome(key, scope, user_kwargs):
            user = _StubUser(
                organization_id=user_kwargs.get("org"),
                is_superuser=user_kwargs.get("admin", False),
            )
            try:
                value = await _http_get(db_session, key=key, scope=scope, user=user)
            except HTTPException as e:
                return ("error", e.status_code)
            return ("value", value.value if value is not None else None)

        async def _local_outcome(key, scope, principal_kwargs):
            principal = _principal(
                principal_kwargs.get("org"),
                is_platform_admin=principal_kwargs.get("admin", False),
                is_provider_org=principal_kwargs.get("provider", False),
            )
            response = await _local_get(
                db_session, key=key, scope=scope, principal=principal
            )
            if not response["ok"]:
                return ("error", response["status"])
            result = response["result"]
            return ("value", result["value"] if result is not None else None)

        with _no_cache():
            for key, scope, user_kwargs, principal_kwargs in cases:
                http_result = await _http_outcome(key, scope, user_kwargs)
                local_result = await _local_outcome(key, scope, principal_kwargs)
                assert local_result == http_result, (key, scope)


@pytest.mark.asyncio
class TestChunkedTransfer:
    def _pair(self):
        req_recv, req_send = multiprocessing.Pipe(duplex=False)
        resp_recv, resp_send = multiprocessing.Pipe(duplex=False)
        return req_recv, req_send, resp_recv, resp_send

    def _close_all(self, conns):
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    @pytest.mark.asyncio
    async def test_chunked_round_trip_allows_regular_frame_progress_past_deadline(self):
        """Chunked unary traffic gets a timeout per stalled frame, not per batch."""
        from bifrost._local_transport import _CHUNK_RAW_BYTES
        from src.services.execution.sdk_local_dispatch import (
            LocalDispatchPrincipal,
            _ok_frames,
            serve_channel,
        )

        class _DelayedWriter:
            def __init__(self, conn):
                self._conn = conn

            def send_bytes(self, payload):
                # Each frame makes forward progress well inside the timeout.
                # The whole 20+ frame request and response intentionally do not.
                time.sleep(0.003)
                return self._conn.send_bytes(payload)

            def close(self):
                return self._conn.close()

        req_recv, req_send, resp_recv, resp_send = self._pair()
        transport = ChildLocalTransport(_DelayedWriter(req_send), resp_recv)
        received: dict[str, Any] = {}
        documents = [
            {"id": str(index), "data": {"payload": "x" * 4_000}}
            for index in range(250)
        ]
        response = {"inserted": len(documents), "errors": [], "documents": documents}
        assert len(json.dumps({"documents": documents}).encode()) > 20 * _CHUNK_RAW_BYTES
        assert len(json.dumps(response).encode()) > 20 * _CHUNK_RAW_BYTES

        async def _dispatch(_session_factory, _principal, frame):
            received["frame"] = frame
            return _ok_frames(frame["id"], response)

        pump = asyncio.create_task(
            serve_channel(
                recv_conn=req_recv,
                send_conn=_DelayedWriter(resp_send),
                session_factory=None,
                principal=LocalDispatchPrincipal(caller_org_id=None),
            )
        )
        try:
            with patch(
                "src.services.execution.sdk_local_dispatch.dispatch_frames",
                new=_dispatch,
            ):
                result = await transport.call_tables_batch(
                    "events",
                    documents,
                    upsert=True,
                    write_mode="replace",
                    return_documents=True,
                    scope=None,
                    timeout=0.05,
                )
            assert result == response
            assert received["frame"]["documents"] == documents
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_large_value_round_trip_over_pipe(self, db_session):
        """One contract: a >64KiB value resolves locally, byte-identical."""
        from bifrost import _local_transport as lt
        from src.services.execution.sdk_local_dispatch import dispatch_frames

        tag = uuid4().hex[:8]
        big = "y" * 100_000
        await _seed_config(db_session, key=f"big-{tag}", value=big)
        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        principal = _principal(is_platform_admin=True)

        async def _pump():
            raw = await asyncio.to_thread(req_recv.recv_bytes, MAX_FRAME_BYTES + 1)
            request = json.loads(raw.decode("utf-8"))
            with _no_cache():
                frames = await dispatch_frames(
                    lambda: _db_factory(db_session), principal, request
                )
                count = 0
                for frame in frames:
                    count += 1
                    encoded = json.dumps(frame, separators=(",", ":")).encode()
                    assert len(encoded) <= MAX_FRAME_BYTES
                    await asyncio.to_thread(resp_send.send_bytes, encoded)
                assert count > 1

        pump = asyncio.create_task(_pump())
        try:
            from bifrost.config import config

            with patch("bifrost.config.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                value = await config.get(f"big-{tag}", scope="global")
            assert value == big
            await asyncio.wait_for(pump, timeout=15.0)
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_inconsistent_chunk_header_breaks_channel(self):
        """One contract: a lying parts claim fails fast without hanging."""
        from bifrost import _local_transport as lt
        from bifrost._local_transport import LocalTransportError

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)

        async def _lying_pump():
            raw = await asyncio.to_thread(req_recv.recv_bytes, MAX_FRAME_BYTES + 1)
            request = json.loads(raw.decode("utf-8"))
            lie = {
                "v": 1,
                "id": request["id"],
                "ok": True,
                "chunked": True,
                "total": 100,
                "parts": 50,
            }
            await asyncio.to_thread(resp_send.send_bytes, json.dumps(lie).encode())

        pump = asyncio.create_task(_lying_pump())
        try:
            from bifrost.config import config

            with pytest.raises(LocalTransportError, match="chunk header"):
                await config.get("k")
            with pytest.raises(LocalTransportError):
                await config.get("k")
            await asyncio.wait_for(pump, timeout=15.0)
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_cancel_preserves_cancelled_error(self):
        """One contract: cancelling a call raises CancelledError, not Closed."""
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        transport = lt.install(req_send, resp_recv)

        async def _stalled_pump():
            await asyncio.to_thread(req_recv.recv_bytes, MAX_FRAME_BYTES + 1)
            await asyncio.sleep(60.0)

        pump = asyncio.create_task(_stalled_pump())
        try:
            task = asyncio.create_task(transport.call_config_get("k", None, timeout=30.0))
            await asyncio.sleep(0.3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    async def test_channel_broken_after_cancel(self):
        """One contract: post-cancel calls fail immediately, no HTTP."""
        from bifrost import _local_transport as lt
        from bifrost._local_transport import LocalTransportClosed

        req_recv, req_send, resp_recv, resp_send = self._pair()
        transport = lt.install(req_send, resp_recv)

        async def _stalled_pump():
            await asyncio.to_thread(req_recv.recv_bytes, MAX_FRAME_BYTES + 1)
            await asyncio.sleep(60.0)

        pump = asyncio.create_task(_stalled_pump())
        try:
            task = asyncio.create_task(transport.call_config_get("k", None, timeout=30.0))
            await asyncio.sleep(0.3)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            with patch("bifrost.config.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                with pytest.raises(LocalTransportClosed):
                    await transport.call_config_get("k", None, timeout=5.0)
        finally:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))


async def _http_set(db_session, *, key, value, is_secret, scope, user):
    """Call the real HTTP set handler with a stub principal."""
    from src.routers.cli import cli_set_config

    return await cli_set_config(
        CLIConfigSetRequest(
            key=key, value=value, is_secret=is_secret, scope=scope
        ),
        user,
        db_session,
    )


async def _http_list(db_session, *, scope, user):
    from src.routers.cli import cli_list_config

    return await cli_list_config(CLIConfigListRequest(scope=scope), user, db_session)


async def _http_delete(db_session, *, key, scope, user):
    from src.routers.cli import cli_delete_config

    return await cli_delete_config(
        CLIConfigDeleteRequest(key=key, scope=scope), user, db_session
    )


async def _local_set(db_session, *, key, value, is_secret, scope, principal):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    frame = {
        "v": 1,
        "id": "parity-set-1",
        "op": "config.set",
        "key": key,
        "value": value,
        "is_secret": is_secret,
        "scope": scope,
    }
    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


async def _local_list(db_session, *, scope, principal):
    import base64

    from src.services.execution.sdk_local_dispatch import dispatch_frames

    frame = {"v": 1, "id": "parity-list-1", "op": "config.list", "scope": scope}
    frames = await dispatch_frames(
        lambda: _db_factory(db_session), principal, frame
    )
    collected = list(frames)
    first = collected[0]
    if not first.get("chunked"):
        return first
    assert len(collected) == first["parts"] + 1
    raw = b"".join(base64.b64decode(part["data"]) for part in collected[1:])
    assert len(raw) == first["total"]
    return {"ok": True, "result": json.loads(raw)}


async def _local_delete(db_session, *, key, scope, principal):
    from src.services.execution.sdk_local_dispatch import dispatch_frame

    frame = {
        "v": 1,
        "id": "parity-del-1",
        "op": "config.delete",
        "key": key,
        "scope": scope,
    }
    return await dispatch_frame(lambda: _db_factory(db_session), principal, frame)


async def _row_for(db_session, *, key, org_id):
    from sqlalchemy import select

    from src.models import Config as ConfigModel

    result = await db_session.execute(
        select(ConfigModel).where(
            ConfigModel.key == key, ConfigModel.organization_id == org_id
        )
    )
    return result.scalar_one_or_none()


@pytest.mark.asyncio
class TestSetListDeleteParity:
    """Stage 2a: HTTP/local parity for config.set/list/delete."""

    async def test_set_types_round_trip(self, db_session):
        tag = uuid4().hex[:8]
        cases = [
            (f"ss-{tag}", "hello", False, "string", "hello"),
            (f"si-{tag}", 42, False, "int", 42),
            (f"sb-{tag}", True, False, "bool", True),
            (f"sj-{tag}", {"a": [1, 2]}, False, "json", {"a": [1, 2]}),
            (f"sl-{tag}", [1, "x"], False, "json", [1, "x"]),
            (f"se-{tag}", "super-secret", True, "secret", "super-secret"),
        ]
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        with _no_cache():
            for key, value, is_secret, ctype, expected in cases:
                await _http_set(
                    db_session, key=f"h-{key}", value=value,
                    is_secret=is_secret, scope="global", user=user,
                )
                http_value = await _http_get(
                    db_session, key=f"h-{key}", scope="global", user=user
                )
                response = await _local_set(
                    db_session, key=f"l-{key}", value=value,
                    is_secret=is_secret, scope="global", principal=principal,
                )
                assert response["ok"] is True, response
                assert response["result"] is None
                local_value = await _local_get(
                    db_session, key=f"l-{key}", scope="global",
                    principal=principal,
                )
                assert local_value["ok"] is True
                assert local_value["result"]["value"] == expected
                assert local_value["result"]["config_type"] == ctype
                assert http_value.value == expected
                assert http_value.config_type == ctype

    async def test_set_update_path_and_audit(self, db_session):
        tag = uuid4().hex[:8]
        key = f"upd-{tag}"
        user = _StubUser(is_superuser=True, email="writer@test.local")
        principal = _principal(
            is_platform_admin=True, actor_email="engine-actor@test.local"
        )
        with _no_cache():
            await _http_set(
                db_session, key=key, value="v1",
                is_secret=False, scope="global", user=user,
            )
            await _http_set(
                db_session, key=key, value="v2",
                is_secret=False, scope="global", user=user,
            )
            row = await _row_for(db_session, key=key, org_id=None)
            assert row is not None
            assert row.value == {"value": "v2"}
            assert row.updated_by == "writer@test.local"

            await _local_set(
                db_session, key=key, value="v3",
                is_secret=False, scope="global", principal=principal,
            )
            db_session.expire_all()
            row = await _row_for(db_session, key=key, org_id=None)
            assert row.value == {"value": "v3"}
            assert row.updated_by == "engine-actor@test.local"

    async def test_secret_stored_encrypted(self, db_session):
        tag = uuid4().hex[:8]
        key = f"enc-{tag}"
        principal = _principal(is_platform_admin=True)
        with _no_cache():
            response = await _local_set(
                db_session, key=key, value="plaintext-secret",
                is_secret=True, scope="global", principal=principal,
            )
            assert response["ok"] is True
            row = await _row_for(db_session, key=key, org_id=None)
            assert row is not None
            assert row.config_type.value == "secret"
            assert row.value["value"] != "plaintext-secret"

    async def test_list_parity_and_redaction(self, db_session):
        tag = uuid4().hex[:8]
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        with _no_cache():
            await _http_set(
                db_session, key=f"ls-{tag}", value="visible",
                is_secret=False, scope="global", user=user,
            )
            await _http_set(
                db_session, key=f"lx-{tag}", value="hidden",
                is_secret=True, scope="global", user=user,
            )
            await _http_set(
                db_session, key=f"li-{tag}", value=7,
                is_secret=False, scope="global", user=user,
            )
            http_list = await _http_list(db_session, scope="global", user=user)
            local = await _local_list(
                db_session, scope="global", principal=principal
            )
            assert local["ok"] is True, local
            assert local["result"][f"ls-{tag}"] == "visible"
            assert local["result"][f"lx-{tag}"] == "[SECRET]"
            assert local["result"][f"li-{tag}"] == 7
            assert http_list[f"ls-{tag}"] == "visible"
            assert http_list[f"lx-{tag}"] == "[SECRET]"
            assert http_list[f"li-{tag}"] == 7

    async def test_list_external_drops_global_tier(self, db_session):
        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        await _seed_config(db_session, key=f"g-{tag}", value="global-only")
        user = _StubUser(organization_id=org.id, is_external=True)
        principal = _principal(org.id, is_external=True)
        with _no_cache():
            http_list = await _http_list(db_session, scope=None, user=user)
            local = await _local_list(
                db_session, scope=None, principal=principal
            )
            assert local["ok"] is True
            assert f"g-{tag}" not in local["result"]
            assert f"g-{tag}" not in http_list

    async def test_delete_parity_and_missing_key(self, db_session):
        tag = uuid4().hex[:8]
        user = _StubUser(is_superuser=True)
        principal = _principal(is_platform_admin=True)
        with _no_cache():
            await _http_set(
                db_session, key=f"dh-{tag}", value="bye",
                is_secret=False, scope="global", user=user,
            )
            assert await _http_delete(
                db_session, key=f"dh-{tag}", scope="global", user=user
            ) is True
            assert await _http_delete(
                db_session, key=f"dh-{tag}", scope="global", user=user
            ) is False
            assert await _row_for(
                db_session, key=f"dh-{tag}", org_id=None
            ) is None

            await _local_set(
                db_session, key=f"dl-{tag}", value="bye",
                is_secret=False, scope="global", principal=principal,
            )
            first = await _local_delete(
                db_session, key=f"dl-{tag}", scope="global",
                principal=principal,
            )
            second = await _local_delete(
                db_session, key=f"dl-{tag}", scope="global",
                principal=principal,
            )
            assert first["ok"] is True and first["result"] is True
            assert second["ok"] is True and second["result"] is False
            assert await _row_for(
                db_session, key=f"dl-{tag}", org_id=None
            ) is None
            # The deleted key reads back as missing through both paths.
            assert await _http_get(
                db_session, key=f"dl-{tag}", scope="global", user=user
            ) is None

    async def test_scope_auth_parity(self, db_session):
        tag = uuid4().hex[:8]
        org_a = await _seed_org(db_session)
        org_b = await _seed_org(db_session)
        user = _StubUser(organization_id=org_a.id)
        principal = _principal(org_a.id)
        with _no_cache():
            import fastapi

            with pytest.raises(fastapi.HTTPException) as exc_info:
                await _http_set(
                    db_session, key=f"q-{tag}", value=1,
                    is_secret=False, scope=str(org_b.id), user=user,
                )
            assert exc_info.value.status_code == 403
            frame = await _local_set(
                db_session, key=f"q-{tag}", value=1,
                is_secret=False, scope=str(org_b.id),
                principal=principal,
            )
            assert frame["ok"] is False
            assert frame["status"] == 403

            with pytest.raises(fastapi.HTTPException) as exc_info:
                await _http_list(db_session, scope=str(org_b.id), user=user)
            assert exc_info.value.status_code == 403
            frame = await _local_list(
                db_session, scope=str(org_b.id), principal=principal
            )
            assert frame["ok"] is False
            assert frame["status"] == 403

            with pytest.raises(fastapi.HTTPException) as exc_info:
                await _http_delete(
                    db_session, key=f"q-{tag}",
                    scope=str(org_b.id), user=user,
                )
            assert exc_info.value.status_code == 403
            frame = await _local_delete(
                db_session, key=f"q-{tag}",
                scope=str(org_b.id), principal=principal,
            )
            assert frame["ok"] is False
            assert frame["status"] == 403

    async def test_malformed_scope_is_422(self, db_session):
        principal = _principal(is_platform_admin=True)
        with _no_cache():
            for frame in (
                await _local_set(
                    db_session, key="k", value=1, is_secret=False,
                    scope="not-a-uuid", principal=principal,
                ),
                await _local_list(
                    db_session, scope="not-a-uuid", principal=principal
                ),
                await _local_delete(
                    db_session, key="k", scope="not-a-uuid",
                    principal=principal,
                ),
            ):
                assert frame["ok"] is False
                assert frame["status"] == 422

    async def test_mutation_without_actor_email_fails_closed(self, db_session):
        tag = uuid4().hex[:8]
        key = f"noactor-{tag}"
        principal = _principal(is_platform_admin=True, actor_email=None)
        with _no_cache():
            refused_set = await _local_set(
                db_session, key=key, value=1, is_secret=False,
                scope="global", principal=principal,
            )
            refused_delete = await _local_delete(
                db_session, key=key, scope="global", principal=principal,
            )
            assert refused_set["ok"] is False
            assert refused_set["status"] == 500
            assert refused_delete["ok"] is False
            assert refused_delete["status"] == 500
            assert await _row_for(db_session, key=key, org_id=None) is None

    async def test_cache_side_effects(self, db_session):
        """Set upserts and delete invalidates the Redis cache like HTTP."""
        tag = uuid4().hex[:8]
        principal = _principal(is_platform_admin=True)
        with _no_cache():
            with (
                patch(
                    "src.core.cache.upsert_config",
                    new=AsyncMock(),
                ) as upsert_spy,
                patch(
                    "src.core.cache.invalidate_config",
                    new=AsyncMock(),
                ) as invalidate_spy,
            ):
                await _local_set(
                    db_session, key=f"c-{tag}", value="v",
                    is_secret=False, scope="global", principal=principal,
                )
                upsert_spy.assert_awaited_once()
                args, _ = upsert_spy.call_args
                assert args[0] is None and args[1] == f"c-{tag}"
                assert args[3] == "string"

                deleted = await _local_delete(
                    db_session, key=f"c-{tag}", scope="global",
                    principal=principal,
                )
                assert deleted["result"] is True
                invalidate_spy.assert_awaited_once_with(None, f"c-{tag}")


@pytest.mark.asyncio
class TestAuditParity:
    """Committed ``Config.updated_by`` agrees across HTTP and local paths.

    HTTP workflow calls authenticate as ``engine@bifrost.internal``
    (``mint_engine_token``) and service calls as
    ``service-<id>@bifrost.internal`` (``mint_service_token``). Local
    dispatch derives the same effective actor from the parent-owned
    execution/service identity — never from ``caller.email``.
    """

    def _workflow_context(self, org_id=None):
        # Admin-initiated so the global scope used below passes the gate;
        # the audit assertion is about the actor, not the gate.
        return {
            "execution_id": "audit-exec",
            "caller": {
                "user_id": "initiator",
                "email": "initiator@test.local",
                "name": "Initiator",
            },
            "organization": {"id": str(org_id)} if org_id else None,
            "is_platform_admin": True,
        }

    def _service_context(self, org_id, service_id):
        return {
            "execution_id": "audit-attempt",
            "caller": {
                "user_id": "00000000-0000-0000-0000-000000000001",
                "email": "stale-caller@test.local",
                "name": "stale",
            },
            "organization": {"id": str(org_id)},
            "service": {"service_id": service_id, "attempt_id": str(uuid4())},
        }

    async def test_workflow_updated_by_matches_engine_token(self, db_session):
        from src.core.security import ENGINE_SDK_ACTOR_EMAIL
        from src.services.execution.sdk_local_dispatch import (
            principal_from_context,
        )

        tag = uuid4().hex[:8]
        http_key = f"audit-h-{tag}"
        local_key = f"audit-l-{tag}"
        principal = principal_from_context(self._workflow_context())
        assert principal.actor_email == ENGINE_SDK_ACTOR_EMAIL
        engine_user = _StubUser(
            is_superuser=True, email=ENGINE_SDK_ACTOR_EMAIL
        )
        with _no_cache():
            await _http_set(
                db_session, key=http_key, value="v",
                is_secret=False, scope="global", user=engine_user,
            )
            response = await _local_set(
                db_session, key=local_key, value="v",
                is_secret=False, scope="global", principal=principal,
            )
            assert response["ok"] is True, response
            http_row = await _row_for(db_session, key=http_key, org_id=None)
            local_row = await _row_for(db_session, key=local_key, org_id=None)
            assert http_row is not None and local_row is not None
            assert http_row.updated_by == ENGINE_SDK_ACTOR_EMAIL
            assert local_row.updated_by == http_row.updated_by

    async def test_service_updated_by_matches_service_token(self, db_session):
        from src.core.security import service_sdk_actor_email
        from src.services.execution.sdk_local_dispatch import (
            principal_from_context,
        )

        tag = uuid4().hex[:8]
        org = await _seed_org(db_session)
        service_id = str(uuid4())
        expected = service_sdk_actor_email(service_id)
        principal = principal_from_context(
            self._service_context(org.id, service_id)
        )
        assert principal.actor_email == expected
        service_user = _StubUser(
            organization_id=org.id, email=expected
        )
        http_key = f"audit-sh-{tag}"
        local_key = f"audit-sl-{tag}"
        with _no_cache():
            await _http_set(
                db_session, key=http_key, value="v",
                is_secret=False, scope=str(org.id), user=service_user,
            )
            response = await _local_set(
                db_session, key=local_key, value="v",
                is_secret=False, scope=str(org.id), principal=principal,
            )
            assert response["ok"] is True, response
            http_row = await _row_for(
                db_session, key=http_key, org_id=org.id
            )
            local_row = await _row_for(
                db_session, key=local_key, org_id=org.id
            )
            assert http_row is not None and local_row is not None
            assert http_row.updated_by == expected
            assert local_row.updated_by == http_row.updated_by

    async def test_local_ignores_caller_email(self, db_session):
        from src.core.security import ENGINE_SDK_ACTOR_EMAIL
        from src.services.execution.sdk_local_dispatch import (
            principal_from_context,
        )

        tag = uuid4().hex[:8]
        key = f"audit-ig-{tag}"
        principal = principal_from_context(self._workflow_context())
        with _no_cache():
            response = await _local_set(
                db_session, key=key, value="v",
                is_secret=False, scope="global", principal=principal,
            )
            assert response["ok"] is True, response
            row = await _row_for(db_session, key=key, org_id=None)
            assert row is not None
            assert row.updated_by == ENGINE_SDK_ACTOR_EMAIL
            assert row.updated_by != "initiator@test.local"

    async def test_malformed_service_identity_fails_dispatch(self, db_session):
        from src.services.execution.sdk_local_dispatch import (
            LocalPrincipalError,
            principal_from_context,
        )

        with pytest.raises(LocalPrincipalError):
            principal_from_context(
                {
                    "organization": None,
                    "service": {"service_id": "junk"},
                }
            )


@pytest.mark.asyncio
class TestChildTransportMutations:
    """Child-side set/list/delete over the dedicated channel, no HTTP."""

    def _pair(self):
        req_recv, req_send = multiprocessing.Pipe(duplex=False)
        resp_recv, resp_send = multiprocessing.Pipe(duplex=False)
        return req_recv, req_send, resp_recv, resp_send

    def _close_all(self, conns):
        for conn in conns:
            with contextlib.suppress(Exception):
                conn.close()

    async def _pump_ops(self, req_conn, resp_conn, handlers):
        for _ in range(len(handlers)):
            raw = await asyncio.to_thread(req_conn.recv_bytes, MAX_FRAME_BYTES + 1)
            frame = json.loads(raw.decode("utf-8"))
            response = handlers[frame["op"]](frame)
            await asyncio.to_thread(resp_conn.send_bytes, json.dumps(response).encode())

    @pytest.mark.asyncio
    async def test_set_list_delete_round_trip_without_http(self):
        from bifrost import _local_transport as lt

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        handlers = {
            "config.set": lambda f: {"v": 1, "id": f["id"], "ok": True, "result": None},
            "config.list": lambda f: {
                "v": 1, "id": f["id"], "ok": True, "result": {"a": 1},
            },
            "config.delete": lambda f: {"v": 1, "id": f["id"], "ok": True, "result": True},
        }
        pump = asyncio.create_task(
            self._pump_ops(req_recv, resp_send, handlers)
        )
        try:
            from bifrost.config import config

            with patch("bifrost.config.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                assert await config.set("k", {"n": 1}) is None
                listed = await config.list()
                assert listed["a"] == 1
                assert listed.a == 1
                assert await config.delete("k") is True
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_set_error_never_falls_back_to_http(self):
        from bifrost import _local_transport as lt
        from bifrost.client import BifrostAuthorizationError

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        pump = asyncio.create_task(
            self._pump_ops(
                req_recv,
                resp_send,
                {
                    "config.set": lambda f: {
                        "v": 1, "id": f["id"], "ok": False,
                        "status": 403, "detail": "denied",
                    },
                },
            )
        )
        try:
            from bifrost.config import config

            with patch("bifrost.config.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                with pytest.raises(BifrostAuthorizationError):
                    await config.set("k", "v")
            await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_large_set_request_chunks_over_pipe(self, db_session):
        """A >64KiB set value reaches the parent via chunked request frames."""
        from bifrost import _local_transport as lt
        from src.services.execution.sdk_local_dispatch import serve_channel

        tag = uuid4().hex[:8]
        key = f"big-{tag}"
        big = {"blob": "w" * 100_000}
        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)
        principal = _principal(is_platform_admin=True)

        async def _pump():
            with _no_cache():
                return await serve_channel(
                    recv_conn=req_recv,
                    send_conn=resp_send,
                    session_factory=lambda: _db_factory(db_session),
                    principal=principal,
                )

        pump = asyncio.create_task(_pump())
        try:
            from bifrost.config import config

            with patch("bifrost.config.get_client") as get_client:
                get_client.side_effect = AssertionError("HTTP must not be used")
                await config.set(key, big)
                value = await config.get(key, scope="global")
            assert value == big
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_lying_response_chunk_header_breaks_channel(self):
        from bifrost import _local_transport as lt
        from bifrost._local_transport import LocalTransportError

        req_recv, req_send, resp_recv, resp_send = self._pair()
        lt.install(req_send, resp_recv)

        async def _lying_pump():
            raw = await asyncio.to_thread(req_recv.recv_bytes, MAX_FRAME_BYTES + 1)
            request = json.loads(raw.decode("utf-8"))
            lie = {
                "v": 1,
                "id": request["id"],
                "ok": True,
                "chunked": True,
                "total": 100,
                "parts": 50,
            }
            await asyncio.to_thread(resp_send.send_bytes, json.dumps(lie).encode())

        pump = asyncio.create_task(_lying_pump())
        try:
            from bifrost.config import config

            with pytest.raises(LocalTransportError, match="chunk header"):
                await config.list()
            with pytest.raises(LocalTransportError):
                await config.delete("k")
            await asyncio.wait_for(pump, timeout=15.0)
        finally:
            lt.clear()
            self._close_all((req_recv, req_send, resp_recv, resp_send))

    @pytest.mark.asyncio
    async def test_set_timeout_breaks_channel_without_fallback(self):
        req_recv, req_send, resp_recv, resp_send = self._pair()
        transport = ChildLocalTransport(req_send, resp_recv)
        try:
            with pytest.raises(TimeoutError):
                await transport.call_config_set("k", "v", False, None, timeout=0.1)
            with pytest.raises(Exception):
                await transport.call_config_list(None, timeout=1.0)
        finally:
            for conn in (req_recv, req_send, resp_recv, resp_send):
                with contextlib.suppress(Exception):
                    conn.close()

    @pytest.mark.asyncio
    async def test_delete_eof_raises_without_fallback(self):
        from bifrost._local_transport import LocalTransportClosed

        req_recv, req_send, resp_recv, resp_send = self._pair()
        transport = ChildLocalTransport(req_send, resp_recv)
        req_recv.close()
        resp_send.close()
        try:
            with pytest.raises(LocalTransportClosed):
                await transport.call_config_delete("k", None, timeout=5.0)
        finally:
            for conn in (req_send, resp_recv):
                with contextlib.suppress(Exception):
                    conn.close()
