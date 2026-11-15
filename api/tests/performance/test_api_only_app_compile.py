"""Spike: run the canonical compiler inside an API-scoped workflow child.

This measures the remote compute seam, not application.deploy admission or
activation. No new job transport or production routing is introduced.
"""

import hashlib
import json
import time
from uuid import uuid4

import pytest

from tests.e2e.conftest import execute_workflow_sync, write_and_register
from tests.performance.test_workflow_dispatch_latency import (
    test_first_and_warm_workflow_dispatch as _measure_workflows,
)

pytestmark = pytest.mark.slow


def test_canonical_app_compiler_without_platform_credentials(
    e2e_client, platform_admin, async_session_factory,
):
    from src.services.solutions.app_build import SolutionAppBuilder

    tag = uuid4().hex[:12]
    name = f"api_only_compile_{tag}"
    path = f"{name}.py"
    # An actual Vite build using the same compiler as App deploys. npm install
    # remains part of the measurement; registry/cache variance is not RPC cost.
    content = f'''from bifrost import workflow

@workflow(name="{name}")
async def {name}():
    import hashlib
    import os
    import time
    from bifrost.client import get_engine_socket_path
    from src.services.solutions.app_build import SolutionAppBuilder

    forbidden = ["BIFROST_DATABASE_URL", "BIFROST_DATABASE_URL_SYNC",
                 "BIFROST_REDIS_URL", "BIFROST_S3_ACCESS_KEY",
                 "BIFROST_S3_SECRET_KEY", "BIFROST_SECRET_KEY"]
    assert not any(os.environ.get(key) for key in forbidden)
    assert get_engine_socket_path() is None
    started = time.perf_counter()
    builder = SolutionAppBuilder()
    dist = builder.compile_dist("00000000-0000-0000-0000-000000001400", {{
        "index.html": b'<html><body><script type="module" src="/main.js"></script></body></html>',
        "main.js": b'document.body.append("API-only worker compile");',
    }}, {{}})
    assert "index.html" in dist
    assert any(path.endswith(".js") for path in dist)
    return {{"compile_seconds": time.perf_counter() - started,
            "artifact_bytes": sum(len(data) for data in dist.values()),
            "artifacts": {{path: hashlib.sha256(data).hexdigest()
                          for path, data in sorted(dist.items())}},
            "build_identity": builder.last_build_identity,
            "platform_credentials_present": False}}
'''
    # The existing compiler called directly is the compute control. The
    # workflow path adds the real dispatch, scoped inputs and result transport.
    # This is not a before/after measurement of the platform-job lifecycle.
    started = time.perf_counter()
    control = SolutionAppBuilder().compile_dist(
        "00000000-0000-0000-0000-000000001400", {
            "index.html": b'<html><body><script type="module" src="/main.js"></script></body></html>',
            "main.js": b'document.body.append("API-only worker compile");',
        }, {},
    )
    expected = {path: hashlib.sha256(data).hexdigest() for path, data in sorted(control.items())}
    print("API_ONLY_APP_COMPILE_BENCHMARK " + json.dumps({
        "variant": "direct_compiler", "compile_seconds": time.perf_counter() - started,
        "artifacts": expected,
    }, sort_keys=True))
    try:
        registered = write_and_register(
            e2e_client, platform_admin.headers, path, content, name,
        )
        identities = []
        for cycle in range(4):
            started = time.perf_counter()
            result = execute_workflow_sync(
                e2e_client, platform_admin.headers, registered["id"],
                request_sync=True, request_timeout=180, max_wait=180,
            )
            assert result["status"] == "Success", result
            sample = result["result"]
            assert sample["platform_credentials_present"] is False
            assert sample["artifact_bytes"] > 0
            assert sample["build_identity"]
            assert sample["artifacts"] == expected
            identities.append(sample["artifacts"])
            print("API_ONLY_APP_COMPILE_BENCHMARK " + json.dumps({
                "variant": "api_scoped_workflow", "cycle": cycle,
                "caller_seconds": time.perf_counter() - started,
                **sample,
            }, sort_keys=True))
        assert all(identity == identities[0] for identity in identities)

        # A fifth compile creates real mixed load while the exact baseline
        # workflow harness runs. Raw timestamps determine overlap; do not infer
        # contention merely because both requests were submitted together.
        response = e2e_client.post(
            "/api/workflows/execute", headers=platform_admin.headers,
            json={"workflow_id": registered["id"], "input_data": {}},
        )
        assert response.status_code == 200, response.text
        build_execution_id = response.json()["execution_id"]
        print("API_ONLY_MIXED_LOAD_START " + json.dumps({
            "build_execution_id": build_execution_id,
        }))
        _measure_workflows(e2e_client, platform_admin, async_session_factory)
        from tests.e2e.conftest import poll_until

        def finished_build():
            response = e2e_client.get(
                f"/api/executions/{build_execution_id}", headers=platform_admin.headers,
            )
            assert response.status_code == 200, response.text
            execution = response.json()
            return execution if execution["status"] in ("Success", "Failed", "Cancelled") else None

        build = poll_until(finished_build, max_wait=120, interval=0.2)
        assert build is not None, "Mixed-load compiler did not complete"
        assert build["status"] == "Success", build
        print("API_ONLY_MIXED_LOAD_BUILD " + json.dumps({
            key: build.get(key) for key in ("execution_id", "created_at", "started_at", "completed_at")
        }, sort_keys=True))
    finally:
        response = e2e_client.delete(
            f"/api/files/editor?path={path}", headers=platform_admin.headers,
        )
        assert response.status_code in (200, 204), response.text
