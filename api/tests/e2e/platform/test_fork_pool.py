"""
E2E tests for fork-based process pool.

These tests verify that workflow execution works correctly when the
pool uses fork-from-template instead of multiprocessing.spawn.
They run against the full stack (API + workers + Redis + PostgreSQL).
"""

import pytest
from tests.e2e.conftest import write_and_register, execute_workflow_sync


# --- Test workflows ---

SIMPLE_WORKFLOW = '''
"""Simple workflow for fork pool testing."""
from bifrost import workflow

@workflow(name="e2e_fork_simple", execution_mode="async")
async def e2e_fork_simple(value: int = 42) -> dict:
    """A simple workflow that returns a result."""
    return {"status": "ok", "value": value}
'''

CONCURRENT_WORKFLOW = '''
"""Workflow with state to test isolation between processes."""
import os
from bifrost import workflow

@workflow(name="e2e_fork_concurrent", execution_mode="async")
async def e2e_fork_concurrent(index: int = 0) -> dict:
    """Return process ID and index to verify isolation."""
    return {
        "index": index,
        "pid": os.getpid(),
        "status": "completed"
    }
'''

TIMEOUT_WORKFLOW = '''
"""Workflow that sleeps for testing timeout handling."""
import time
from bifrost import workflow

@workflow(name="e2e_fork_timeout", execution_mode="async")
async def e2e_fork_timeout(sleep_seconds: int = 60) -> dict:
    """Sleep for specified duration to test timeout."""
    time.sleep(sleep_seconds)
    return {"status": "completed"}
'''

LARGE_FLAT_RESULT_WORKFLOW = '''
"""Production-shaped flat result regression for issue #480."""
from bifrost import workflow

@workflow(name="e2e_large_flat_result", execution_mode="async")
async def e2e_large_flat_result(row_count: int = 5395) -> list[dict[str, str]]:
    """Return the scrubbed production row shape without customer values."""
    return [
        {
            "Attendees": "x",
            "Cost": "x" * 5,
            "Cost Formatted": "x" * 6,
            "Duration": "x" * 3,
            "End Date": "x" * 19,
            "Start Date": "x" * 19,
            "Subject": "x" * 24,
            "Total Hours": "xx",
        }
        for _ in range(row_count)
    ]
'''


@pytest.fixture(scope="module")
def simple_workflow(e2e_client, platform_admin):
    """Create a simple workflow for fork pool tests."""
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_fork_simple.py",
        SIMPLE_WORKFLOW,
        "e2e_fork_simple",
    )
    yield result
    # Cleanup
    e2e_client.delete(
        "/api/files/editor?path=e2e_fork_simple.py",
        headers=platform_admin.headers,
    )


@pytest.fixture(scope="module")
def concurrent_workflow(e2e_client, platform_admin):
    """Create a workflow for concurrent execution tests."""
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_fork_concurrent.py",
        CONCURRENT_WORKFLOW,
        "e2e_fork_concurrent",
    )
    yield result
    # Cleanup
    e2e_client.delete(
        "/api/files/editor?path=e2e_fork_concurrent.py",
        headers=platform_admin.headers,
    )


@pytest.fixture(scope="module")
def timeout_workflow(e2e_client, platform_admin):
    """Create a workflow for timeout tests."""
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_fork_timeout.py",
        TIMEOUT_WORKFLOW,
        "e2e_fork_timeout",
    )
    yield result
    # Cleanup
    e2e_client.delete(
        "/api/files/editor?path=e2e_fork_timeout.py",
        headers=platform_admin.headers,
    )


@pytest.fixture(scope="module")
def large_flat_result_workflow(e2e_client, platform_admin):
    """Create the sanitized 5,395-row production-shape reproduction."""
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_large_flat_result.py",
        LARGE_FLAT_RESULT_WORKFLOW,
        "e2e_large_flat_result",
    )
    yield result
    e2e_client.delete(
        "/api/files/editor?path=e2e_large_flat_result.py",
        headers=platform_admin.headers,
    )


@pytest.mark.e2e
class TestForkBasedExecution:
    """Test that workflows execute correctly with fork-based pool."""

    def test_basic_workflow_execution(self, e2e_client, platform_admin, simple_workflow):
        """A simple workflow should execute and return results via fork pool."""
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            simple_workflow["id"],
            {"value": 42},
            max_wait=30.0,
        )

        assert data["status"] == "Success", f"Unexpected status: {data}"
        result = data.get("result", {})
        assert result.get("status") == "ok"
        assert result.get("value") == 42

    def test_workflow_with_custom_input(self, e2e_client, platform_admin, simple_workflow):
        """Workflow should correctly handle custom input values."""
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            simple_workflow["id"],
            {"value": 999},
            max_wait=30.0,
        )

        assert data["status"] == "Success"
        result = data.get("result", {})
        assert result.get("value") == 999

    def test_large_flat_result_returns_without_polluting_history_payload(
        self,
        e2e_client,
        platform_admin,
        large_flat_result_workflow,
    ):
        """Sync callers get all rows while history remains summary-only."""
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            large_flat_result_workflow["id"],
            {"row_count": 5395},
            max_wait=30.0,
            request_sync=True,
            request_timeout=30.0,
        )

        assert data["status"] == "Success", f"Unexpected status: {data}"
        rows = data["result"]
        assert len(rows) == 5395
        assert set(rows[0]) == {
            "Attendees",
            "Cost",
            "Cost Formatted",
            "Duration",
            "End Date",
            "Start Date",
            "Subject",
            "Total Hours",
        }

        history_response = e2e_client.get(
            "/api/executions",
            headers=platform_admin.headers,
            params={"workflowId": large_flat_result_workflow["id"], "limit": 10},
        )
        assert history_response.status_code == 200
        executions = history_response.json()["executions"]
        matching = [
            execution
            for execution in executions
            if execution["execution_id"] == data["execution_id"]
        ]
        assert len(matching) == 1
        # List items are summaries: payload fields are omitted entirely, not
        # returned as null/{} (which would read as "this run had no result").
        assert "result" not in matching[0]
        assert "input_data" not in matching[0]
        assert "variables" not in matching[0]
        assert "execution_context" not in matching[0]

    def test_concurrent_executions(self, e2e_client, platform_admin, concurrent_workflow):
        """Multiple concurrent executions should complete without interference."""
        import asyncio

        async def run_one(i: int):
            response = e2e_client.post(
                "/api/workflows/execute",
                headers=platform_admin.headers,
                json={
                    "workflow_id": concurrent_workflow["id"],
                    "input_data": {"index": i},
                },
            )
            assert response.status_code in [200, 202], f"Execute failed: {response.text}"
            data = response.json()
            execution_id = data.get("execution_id") or data.get("executionId")
            assert execution_id, "Should return execution_id"
            return execution_id

        # Run multiple executions concurrently
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            execution_ids = loop.run_until_complete(
                asyncio.gather(*[run_one(i) for i in range(5)])
            )
        finally:
            loop.close()

        # Poll for all to complete
        from tests.e2e.conftest import poll_until

        def check_all_completed():
            results = []
            for exec_id in execution_ids:
                resp = e2e_client.get(
                    f"/api/executions/{exec_id}",
                    headers=platform_admin.headers,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") in ["Success", "Failed"]:
                        results.append(data)
            return results if len(results) == len(execution_ids) else None

        results = poll_until(check_all_completed, max_wait=30.0, interval=0.5)
        assert results is not None, "Not all executions completed within timeout"
        assert len(results) == 5, f"Expected 5 results, got {len(results)}"

        # All should succeed
        for r in results:
            assert r["status"] == "Success", f"Execution failed: {r}"

        # Results should have correct indices
        result_data = [r.get("result", {}) for r in results]
        indices = sorted(d.get("index") for d in result_data)
        assert indices == [0, 1, 2, 3, 4], f"Wrong indices: {indices}"

    def test_execution_timeout(self, e2e_client, platform_admin, timeout_workflow):
        """Timeout should still kill forked processes."""
        # Bake a short timeout into the workflow definition itself
        # (WorkflowExecutionRequest has no per-request timeout override)
        put_resp = e2e_client.patch(
            f"/api/workflows/{timeout_workflow['id']}",
            headers=platform_admin.headers,
            json={"timeout_seconds": 3},
        )
        assert put_resp.status_code == 200, f"Failed to set timeout: {put_resp.text}"

        # Request execution
        response = e2e_client.post(
            "/api/workflows/execute",
            headers=platform_admin.headers,
            json={
                "workflow_id": timeout_workflow["id"],
                "input_data": {"sleep_seconds": 60},
            },
        )
        assert response.status_code in [200, 202], f"Execute failed: {response.text}"
        data = response.json()
        execution_id = data.get("execution_id") or data.get("executionId")
        assert execution_id

        # Poll for completion
        from tests.e2e.conftest import poll_until

        def check_completion():
            resp = e2e_client.get(
                f"/api/executions/{execution_id}",
                headers=platform_admin.headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") in ["Success", "Failed", "Timeout"]:
                    return data
            return None

        result = poll_until(check_completion, max_wait=30.0, interval=0.5)
        assert result is not None, "Execution did not complete within timeout"

        # Should be killed/failed, not success
        # Status might be "Failed", "Timeout", or similar depending on implementation
        assert result["status"] in ["Failed", "Timeout"], (
            f"Expected Failed or Timeout status, got {result['status']}"
        )

    def test_execution_isolation_across_calls(
        self, e2e_client, platform_admin, simple_workflow
    ):
        """Each execution should be isolated with independent global state."""
        # Execute twice with different inputs
        data1 = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            simple_workflow["id"],
            {"value": 100},
            max_wait=30.0,
        )
        assert data1["status"] == "Success"
        assert data1.get("result", {}).get("value") == 100

        data2 = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            simple_workflow["id"],
            {"value": 200},
            max_wait=30.0,
        )
        assert data2["status"] == "Success"
        assert data2.get("result", {}).get("value") == 200

        # Both should have correct values (proving isolation)
        assert data1.get("result", {}).get("value") == 100
        assert data2.get("result", {}).get("value") == 200
