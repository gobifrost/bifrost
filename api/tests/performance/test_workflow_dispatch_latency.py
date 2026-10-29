"""Direct first/warm workflow timings; run explicitly on both runtime variants.

Each kind pays one first execution and ten warm executions. The SDK case does
one real authorized config read; the echo case isolates runner overhead. The
sync HTTP path measures caller latency without a polling interval floor.
"""

import asyncio
import json
import time
from uuid import UUID, uuid4

import httpx
import pytest

from src.models.orm.executions import Execution
from tests.e2e.conftest import E2E_API_URL, execute_workflow_sync, write_and_register

pytestmark = pytest.mark.slow


@pytest.fixture(scope="session")
def e2e_client():
    with httpx.Client(base_url=E2E_API_URL, timeout=120) as client:
        yield client


def test_first_and_warm_workflow_dispatch(e2e_client, platform_admin, async_session_factory):
    tag = uuid4().hex[:12]
    path = f"workflow_latency_{tag}.py"
    names = {kind: f"latency_{kind}_{tag}" for kind in ("echo", "sdk")}
    content = f'''from bifrost import workflow
from bifrost.config import config
from bifrost.client import get_engine_socket_path
import time

@workflow(name="{names['echo']}")
async def {names['echo']}(value: int = 1):
    started = time.perf_counter()
    return {{"value": value, "used_socket": get_engine_socket_path() is not None,
            "body_seconds": time.perf_counter() - started}}

@workflow(name="{names['sdk']}")
async def {names['sdk']}(value: int = 1):
    started = time.perf_counter()
    configs = await config.list()
    return {{"value": value, "config_count": len(configs.data),
            "used_socket": get_engine_socket_path() is not None,
            "body_seconds": time.perf_counter() - started}}
'''

    async def timestamps(execution_id):
        async with async_session_factory() as db:
            row = await db.get(Execution, UUID(execution_id))
            assert row is not None and row.started_at and row.completed_at
            return {
                "server_total_seconds": (row.completed_at - row.created_at).total_seconds(),
                "created_to_started_seconds": (row.started_at - row.created_at).total_seconds(),
                "started_to_completed_seconds": (row.completed_at - row.started_at).total_seconds(),
                "recorded_duration_ms": row.duration_ms,
            }

    try:
        for kind, name in names.items():
            registered = write_and_register(e2e_client, platform_admin.headers, path, content, name)
            for cycle in range(11):
                started = time.perf_counter()
                result = execute_workflow_sync(e2e_client, platform_admin.headers,
                    registered["id"], input_data={"value": cycle}, max_wait=120,
                    request_sync=True, request_timeout=120)
                elapsed = time.perf_counter() - started
                assert result["status"] == "Success", result
                assert result["result"]["value"] == cycle
                sample = {"kind": kind, "cycle": cycle, "caller_seconds": elapsed,
                          **result["result"], **asyncio.run(timestamps(result["execution_id"]))}
                print("WORKFLOW_DISPATCH_BENCHMARK " + json.dumps(sample, sort_keys=True))
    finally:
        response = e2e_client.delete(f"/api/files/editor?path={path}", headers=platform_admin.headers)
        assert response.status_code in (200, 204), response.text
