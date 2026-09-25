"""Opt-in, live-worker comparison of config SDK HTTP and local transports.

Run with ``./test.sh tests/performance/test_sdk_config_transport.py -s -v``.
Both paths execute in the same forked workflow child against the same shared
config service. The HTTP path is selected explicitly to represent the old SDK.
Latency numbers are diagnostic, never a CI threshold.
"""

import json
import uuid

import httpx
import pytest

from tests.e2e.conftest import E2E_API_URL, execute_workflow_sync, write_and_register


@pytest.fixture(scope="session")
def e2e_client():
    with httpx.Client(base_url=E2E_API_URL, timeout=60.0) as client:
        yield client


def test_config_http_vs_local_in_live_worker(e2e_client, platform_admin, org1, org1_user):
    tag = uuid.uuid4().hex[:8]
    name = f"sdk_config_transport_bench_{tag}"
    path = f"{name}.py"
    key = f"sdk_config_transport_bench_key_{tag}"
    content = f'''from bifrost import workflow, config

@workflow(name="{name}", description="Config transport benchmark")
async def {name}():
    import importlib
    import statistics
    import time

    cfg = importlib.import_module("bifrost.config")
    original_transport = cfg._get_local_transport
    original_client = cfg.get_client
    assert original_transport() is not None, "engine local transport is missing"
    http_calls = 0

    def counted_client():
        nonlocal http_calls
        http_calls += 1
        return original_client()

    cfg.get_client = counted_client
    results = {{"http": {{op: [] for op in ("set", "get", "list", "delete")}},
               "local": {{op: [] for op in ("set", "get", "list", "delete")}}}}
    measured_calls = {{"http": 0, "local": 0}}

    async def one_cycle(mode, record):
        async def timed(op, action):
            started = time.perf_counter_ns()
            value = await action
            if record:
                results[mode][op].append((time.perf_counter_ns() - started) / 1_000_000)
            return value

        await timed("set", config.set("{key}", "bench-value"))
        assert await timed("get", config.get("{key}")) == "bench-value"
        listed = await timed("list", config.list())
        assert listed["{key}"] == "bench-value"
        assert await timed("delete", config.delete("{key}")) is True

    try:
        # Counterbalanced order limits drift from cache warming and host load.
        for mode in ("http", "local", "local", "http"):
            cfg._get_local_transport = (lambda: None) if mode == "http" else original_transport
            for _ in range(5):
                await one_cycle(mode, False)
            before = http_calls
            for _ in range(30):
                await one_cycle(mode, True)
            measured_calls[mode] += http_calls - before
    finally:
        cfg._get_local_transport = original_transport
        cfg.get_client = original_client

    def summarize(samples):
        ordered = sorted(samples)
        return {{
            "count": len(ordered),
            "p50_ms": round(statistics.median(ordered), 3),
            "p95_ms": round(ordered[int((len(ordered) - 1) * 0.95)], 3),
            "mean_ms": round(statistics.mean(ordered), 3),
        }}

    return {{
        "latency": {{mode: {{op: summarize(samples) for op, samples in ops.items()}}
                    for mode, ops in results.items()}},
        "http_requests": measured_calls,
    }}
'''

    with httpx.Client(base_url=E2E_API_URL, timeout=60.0) as client:
        registered = write_and_register(
            client, platform_admin.headers, path, content, name,
            organization_id=org1["id"],
        )
        try:
            response = client.patch(
                f"/api/workflows/{registered['id']}",
                headers=platform_admin.headers,
                json={"organization_id": org1["id"], "access_level": "authenticated"},
            )
            assert response.status_code == 200, response.text
            result = execute_workflow_sync(
                client, org1_user.headers, registered["id"], max_wait=120.0,
            )
            assert result["status"] == "Success", result
            measurements = result["result"]
            assert measurements["http_requests"] == {"http": 240, "local": 0}
            for mode in ("http", "local"):
                for operation in ("set", "get", "list", "delete"):
                    assert measurements["latency"][mode][operation]["count"] == 60
            print("SDK_CONFIG_TRANSPORT_BENCHMARK " + json.dumps(measurements, sort_keys=True))
        finally:
            client.post(
                "/api/sdk/config/delete", headers=org1_user.headers,
                json={"key": key},
            )
            client.delete(f"/api/files/editor?path={path}", headers=platform_admin.headers)
