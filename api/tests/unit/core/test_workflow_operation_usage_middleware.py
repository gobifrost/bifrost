"""R2a-2 Part 2 (post-review rework): the workflow-operation-usage Redis
counter increment.

Previously a synthetic per-route FastAPI dependency; now inline in the
request-context middleware (src/core/app_wiring.py), read from
``request.scope["route"]`` after ``call_next`` returns — routing has
necessarily happened by then. Driven through the worker-local SDK socket
app (the same middleware install_request_context_middleware wires onto the
main app too, so this one app's coverage is representative of the shared
code path).
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from src.core.security import mint_engine_token
from src.services.execution.worker_sdk_http import build_worker_sdk_app


def _fake_redis_pipe():
    fake_pipe = MagicMock()
    fake_pipe.execute = AsyncMock()
    fake_conn = MagicMock()
    fake_conn.pipeline = MagicMock(return_value=fake_pipe)
    fake_redis_client = MagicMock()
    fake_redis_client._get_redis = AsyncMock(return_value=fake_conn)
    return fake_redis_client, fake_pipe


class TestWorkflowOperationUsageMiddleware:
    @pytest.mark.asyncio
    async def test_catalogued_route_uses_operation_id_as_key(self):
        app = build_worker_sdk_app()

        @app.get(
            "/__test__/wf-usage-catalogued",
            operation_id="__test__.wf_usage_catalogued",
        )
        async def _catalogued():
            return {"ok": True}

        token, _ = mint_engine_token(
            execution_id=str(uuid4()),
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=300,
            engine_workflow_id="wf-mw-1",
        )

        fake_redis_client, fake_pipe = _fake_redis_pipe()
        transport = httpx.ASGITransport(app=app)
        with patch(
            "src.core.redis_client.get_redis_client", return_value=fake_redis_client
        ):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://bifrost-engine"
            ) as client:
                response = await client.get(
                    "/__test__/wf-usage-catalogued",
                    headers={"Authorization": f"Bearer {token}"},
                )
        assert response.status_code == 200, response.text

        fake_pipe.hincrby.assert_called_once()
        (key, field, amount), _ = fake_pipe.hincrby.call_args
        assert key.startswith("bifrost:wf_usage:")
        assert field == "wf-mw-1|__test__.wf_usage_catalogued"
        assert amount == 1
        fake_pipe.expire.assert_called_once()
        fake_pipe.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uncatalogued_route_uses_method_and_path_template(self):
        app = build_worker_sdk_app()

        @app.get("/__test__/wf-usage-uncatalogued/{item_id}")
        async def _uncatalogued(item_id: str):
            return {"ok": True, "item_id": item_id}

        token, _ = mint_engine_token(
            execution_id=str(uuid4()),
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=300,
            engine_workflow_id="wf-mw-2",
        )

        fake_redis_client, fake_pipe = _fake_redis_pipe()
        transport = httpx.ASGITransport(app=app)
        with patch(
            "src.core.redis_client.get_redis_client", return_value=fake_redis_client
        ):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://bifrost-engine"
            ) as client:
                response = await client.get(
                    "/__test__/wf-usage-uncatalogued/abc123",
                    headers={"Authorization": f"Bearer {token}"},
                )
        assert response.status_code == 200, response.text

        (_key, field, _amount), _ = fake_pipe.hincrby.call_args
        assert field == "wf-mw-2|GET /__test__/wf-usage-uncatalogued/{item_id}"

    @pytest.mark.asyncio
    async def test_no_engine_workflow_id_skips_increment_entirely(self):
        app = build_worker_sdk_app()

        @app.get("/__test__/wf-usage-no-claim")
        async def _no_claim():
            return {"ok": True}

        transport = httpx.ASGITransport(app=app)
        with patch("src.core.redis_client.get_redis_client") as get_redis:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://bifrost-engine"
            ) as client:
                response = await client.get("/__test__/wf-usage-no-claim")
        assert response.status_code == 200, response.text
        get_redis.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_failure_does_not_fail_the_request(self):
        """A Redis outage must never fail (or slow) the request it tags."""
        app = build_worker_sdk_app()

        @app.get("/__test__/wf-usage-redis-down")
        async def _redis_down():
            return {"ok": True}

        token, _ = mint_engine_token(
            execution_id=str(uuid4()),
            solution_id=None,
            global_repo_access=True,
            timeout_seconds=300,
            engine_workflow_id="wf-mw-3",
        )

        transport = httpx.ASGITransport(app=app)
        with patch(
            "src.core.redis_client.get_redis_client",
            side_effect=RuntimeError("redis is down"),
        ):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://bifrost-engine"
            ) as client:
                response = await client.get(
                    "/__test__/wf-usage-redis-down",
                    headers={"Authorization": f"Bearer {token}"},
                )
        assert response.status_code == 200, response.text
