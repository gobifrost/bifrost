"""
E2E tests for worker subprocess memory stability.

Verifies that repeated workflow executions on long-lived worker subprocesses
don't cause unbounded memory growth. Exercises the main leak scenarios:
- Normal executions (baseline)
- Failing executions (traceback cleanup)
"""

import pytest
from tests.e2e.conftest import write_and_register, execute_workflow_sync


# --- Test workflows ---

NORMAL_WORKFLOW = '''
"""Memory test: normal workflow returning moderate data."""
from bifrost import workflow

@workflow(name="e2e_mem_normal")
async def e2e_mem_normal(iteration: int = 0) -> dict:
    # Allocate ~1MB of data per execution
    data = ["x" * 1000 for _ in range(1000)]
    return {"iteration": iteration, "data_len": len(data)}
'''

FAILING_WORKFLOW = '''
"""Memory test: workflow that always raises (exercises traceback cleanup)."""
from bifrost import workflow

@workflow(name="e2e_mem_failing")
async def e2e_mem_failing(iteration: int = 0) -> dict:
    # Allocate data, then fail — traceback holds frame locals
    data = ["x" * 1000 for _ in range(1000)]
    raise RuntimeError(f"Intentional failure at iteration {iteration}, data_len={len(data)}")
'''


@pytest.fixture(scope="module")
def normal_workflow(e2e_client, platform_admin):
    result = write_and_register(
        e2e_client, platform_admin.headers,
        "e2e_mem_normal.py", NORMAL_WORKFLOW, "e2e_mem_normal",
    )
    yield result
    e2e_client.delete(
        "/api/files/editor?path=e2e_mem_normal.py",
        headers=platform_admin.headers,
    )


@pytest.fixture(scope="module")
def failing_workflow(e2e_client, platform_admin):
    result = write_and_register(
        e2e_client, platform_admin.headers,
        "e2e_mem_failing.py", FAILING_WORKFLOW, "e2e_mem_failing",
    )
    yield result
    e2e_client.delete(
        "/api/files/editor?path=e2e_mem_failing.py",
        headers=platform_admin.headers,
    )


def _get_rss_mb(e2e_client, headers, execution_id: str) -> float | None:
    """Get process RSS in MB from execution record."""
    resp = e2e_client.get(f"/api/executions/{execution_id}", headers=headers)
    if resp.status_code != 200:
        return None
    data = resp.json()
    rss_bytes = data.get("process_rss_bytes")
    if rss_bytes:
        return rss_bytes / (1024 * 1024)
    return None


@pytest.mark.e2e
class TestWorkerMemoryStability:
    """Test that worker subprocess memory stays bounded across executions."""

    NUM_EXECUTIONS = 15

    def test_normal_executions_bounded_memory(
        self, e2e_client, platform_admin, normal_workflow
    ):
        """Repeated successful executions should not grow memory linearly."""
        rss_values: list[float] = []

        for i in range(self.NUM_EXECUTIONS):
            data = execute_workflow_sync(
                e2e_client, platform_admin.headers,
                normal_workflow["id"], {"iteration": i}, max_wait=30.0,
            )
            assert data["status"] == "Success"
            rss = _get_rss_mb(e2e_client, platform_admin.headers, data["execution_id"])
            if rss:
                rss_values.append(rss)

        print(f"Normal RSS progression (MB): {[f'{r:.1f}' for r in rss_values]}")

        if len(rss_values) >= 10:
            early = rss_values[4]   # After warmup
            late = rss_values[-1]   # Final
            growth = late - early
            # Allow up to 50MB growth over 10 executions — anything more is a leak
            assert growth < 50, (
                f"Memory grew {growth:.1f}MB over {len(rss_values) - 5} executions "
                f"(early={early:.1f}MB, late={late:.1f}MB). "
                f"Full: {[f'{r:.1f}' for r in rss_values]}"
            )

    def test_failing_executions_bounded_memory(
        self, e2e_client, platform_admin, failing_workflow
    ):
        """Repeated failing executions (tracebacks) should not grow memory linearly."""
        rss_values: list[float] = []

        for i in range(self.NUM_EXECUTIONS):
            data = execute_workflow_sync(
                e2e_client, platform_admin.headers,
                failing_workflow["id"], {"iteration": i}, max_wait=30.0,
            )
            assert data["status"] == "Failed"
            rss = _get_rss_mb(e2e_client, platform_admin.headers, data["execution_id"])
            if rss:
                rss_values.append(rss)

        print(f"Failing RSS progression (MB): {[f'{r:.1f}' for r in rss_values]}")

        if len(rss_values) >= 10:
            early = rss_values[4]
            late = rss_values[-1]
            growth = late - early
            assert growth < 50, (
                f"Memory grew {growth:.1f}MB from failing executions "
                f"(early={early:.1f}MB, late={late:.1f}MB). "
                f"Traceback references may not be cleared. "
                f"Full: {[f'{r:.1f}' for r in rss_values]}"
            )
