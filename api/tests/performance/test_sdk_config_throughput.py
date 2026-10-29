"""Diagnostic shared-API SDK config throughput benchmark.

Run explicitly with:

    ./test.sh tests/performance/test_sdk_config_throughput.py -s -v

The benchmark exercises one organization-scoped workflow through the shared API
config service. It reports latency and throughput for comparison between
runtime builds; assertions verify behavior only and impose no timing threshold.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from tests.e2e.conftest import E2E_API_URL, execute_workflow_sync, write_and_register


pytestmark = pytest.mark.slow


@pytest.fixture(scope="session")
def e2e_client():
    with httpx.Client(base_url=E2E_API_URL, timeout=120.0) as client:
        yield client


def test_sdk_config_roundtrips_use_shared_api_transport(
    e2e_client,
    platform_admin,
    org1,
    org1_user,
) -> None:
    tag = uuid.uuid4().hex[:12]
    name = f"sdk_config_throughput_{tag}"
    path = f"{name}.py"
    key = f"sdk_config_throughput_key_{tag}"
    registered = None
    content = f'''from bifrost import config, workflow
from bifrost.client import get_engine_socket_path


@workflow(name="{name}", description="Shared API SDK config throughput benchmark")
async def {name}():
    import asyncio
    import statistics
    import time

    assert get_engine_socket_path() is None

    await config.set("{key}", "bench-value")
    value = await config.get("{key}")
    assert value == "bench-value"
    before_reads = await config.list()
    assert before_reads["{key}"] == "bench-value"

    async def read_batch():
        samples = []
        for _ in range(20):
            started = time.perf_counter()
            assert await config.get("{key}") == "bench-value"
            samples.append((time.perf_counter() - started) * 1_000)
        return samples

    started = time.perf_counter()
    batches = await asyncio.gather(*(read_batch() for _ in range(8)))
    elapsed_seconds = time.perf_counter() - started
    samples = [sample for batch in batches for sample in batch]
    assert len(samples) == 160

    deleted = await config.delete("{key}")
    assert deleted is True
    after_delete = await config.list()
    assert "{key}" not in after_delete

    ordered = sorted(samples)
    return {{
        "socket_path": get_engine_socket_path(),
        "roundtrips": {{
            "get": value,
            "listed": before_reads["{key}"],
            "deleted": deleted,
            "absent_after_delete": "{key}" not in after_delete,
        }},
        "reads": {{
            "count": len(ordered),
            "p50_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[int((len(ordered) - 1) * 0.95)], 3),
            "elapsed_seconds": round(elapsed_seconds, 3),
            "throughput_per_second": round(len(ordered) / elapsed_seconds, 3),
        }},
    }}
'''

    try:
        registered = write_and_register(
            e2e_client,
            platform_admin.headers,
            path,
            content,
            name,
            organization_id=org1["id"],
        )
        response = e2e_client.patch(
            f"/api/workflows/{registered['id']}",
            headers=platform_admin.headers,
            json={"organization_id": org1["id"], "access_level": "authenticated"},
        )
        assert response.status_code == 200, response.text

        result = execute_workflow_sync(
            e2e_client,
            org1_user.headers,
            registered["id"],
            max_wait=120.0,
        )
        assert result["status"] == "Success", result
        measurements = result["result"]
        assert measurements["socket_path"] is None
        assert measurements["roundtrips"] == {
            "get": "bench-value",
            "listed": "bench-value",
            "deleted": True,
            "absent_after_delete": True,
        }
        assert measurements["reads"]["count"] == 160
        assert measurements["reads"]["elapsed_seconds"] >= 0
        assert measurements["reads"]["throughput_per_second"] >= 0
        print("SDK_CONFIG_THROUGHPUT_BENCHMARK " + json.dumps(measurements, sort_keys=True))
    finally:
        cleanup = e2e_client.post(
            "/api/sdk/config/delete",
            headers=platform_admin.headers,
            json={"key": key, "scope": org1["id"]},
        )
        assert cleanup.status_code == 200, cleanup.text
        if registered is not None:
            cleanup = e2e_client.request(
                "DELETE",
                f"/api/workflows/{registered['id']}",
                headers=platform_admin.headers,
                json={"force_deactivation": True},
            )
            assert cleanup.status_code == 200, cleanup.text
        else:
            cleanup = e2e_client.delete(
                f"/api/files/editor?path={path}", headers=platform_admin.headers
            )
            assert cleanup.status_code in (204, 404), cleanup.text
