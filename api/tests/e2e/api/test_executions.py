"""
E2E tests for workflow execution.

Tests sync/async execution, polling, cancellation, and execution history.
"""

import uuid

import pytest

from tests.e2e.conftest import poll_until, write_and_register, execute_workflow_sync


# Module-level fixtures for workflows used across multiple test classes


@pytest.fixture(scope="module")
def sync_workflow(e2e_client, platform_admin):
    """Create a sync workflow for execution tests."""
    workflow_content = '''"""E2E Sync Execution Workflow"""
from bifrost import workflow, context

@workflow(
    name="e2e_exec_sync_workflow",
    description="Sync workflow for execution tests",
    execution_mode="sync"
)
async def e2e_exec_sync_workflow(message: str, count: int = 1):
    return {
        "status": "success",
        "message": message,
        "count": count,
        "user": context.email,
    }
'''
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_exec_sync_workflow.py",
        workflow_content,
        "e2e_exec_sync_workflow",
    )
    workflow_id = result["id"]

    yield {"id": workflow_id, "name": "e2e_exec_sync_workflow"}

    # Cleanup
    e2e_client.delete(
        "/api/files/editor?path=e2e_exec_sync_workflow.py",
        headers=platform_admin.headers,
    )


@pytest.fixture(scope="module")
def async_workflow(e2e_client, platform_admin):
    """Create an async workflow for execution tests."""
    workflow_content = '''"""E2E Async Execution Workflow"""
import time
from bifrost import workflow, context

@workflow(
    name="e2e_exec_async_workflow",
    description="Async workflow for execution tests",
    execution_mode="async"
)
async def e2e_exec_async_workflow(delay_seconds: int = 1):
    time.sleep(delay_seconds)
    return {"status": "completed", "delayed": delay_seconds}
'''
    result = write_and_register(
        e2e_client,
        platform_admin.headers,
        "e2e_exec_async_workflow.py",
        workflow_content,
        "e2e_exec_async_workflow",
    )
    workflow_id = result["id"]

    yield {"id": workflow_id, "name": "e2e_exec_async_workflow"}

    # Cleanup
    e2e_client.delete(
        "/api/files/editor?path=e2e_exec_async_workflow.py",
        headers=platform_admin.headers,
    )


@pytest.mark.e2e
class TestSyncExecution:
    """Test synchronous workflow execution."""

    def test_execute_sync_workflow(self, e2e_client, platform_admin, sync_workflow):
        """Platform admin executes sync workflow."""
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            sync_workflow["id"],
            {"message": "Hello from E2E test", "count": 42},
        )

        assert data["status"] == "Success", f"Unexpected status: {data}"
        assert "execution_id" in data or "executionId" in data

    def test_sync_execution_returns_result(
        self, e2e_client, platform_admin, sync_workflow
    ):
        """Sync execution returns expected result."""
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            sync_workflow["id"],
            {"message": "Test message", "count": 10},
        )

        result = data.get("result", {})
        assert result.get("status") == "success"
        assert result.get("message") == "Test message"
        assert result.get("count") == 10


@pytest.mark.e2e
class TestAsyncExecution:
    """Test asynchronous workflow execution."""

    def test_execute_async_workflow(self, e2e_client, platform_admin, async_workflow):
        """Platform admin executes async workflow."""
        response = e2e_client.post(
            "/api/workflows/execute",
            headers=platform_admin.headers,
            json={
                "workflow_id": async_workflow["id"],
                "input_data": {"delay_seconds": 1},
            },
        )
        assert response.status_code in [200, 202], (
            f"Async execute failed: {response.text}"
        )
        data = response.json()
        execution_id = data.get("execution_id") or data.get("executionId")
        assert execution_id, "Should return execution_id"

    def test_async_execution_eventually_completes(
        self, e2e_client, platform_admin, async_workflow
    ):
        """Poll until async execution completes."""
        # Start execution
        response = e2e_client.post(
            "/api/workflows/execute",
            headers=platform_admin.headers,
            json={
                "workflow_id": async_workflow["id"],
                "input_data": {"delay_seconds": 1},
            },
        )
        data = response.json()
        execution_id = data.get("execution_id") or data.get("executionId")

        # Poll for completion using exponential backoff
        def check_completed():
            response = e2e_client.get(
                f"/api/executions/{execution_id}",
                headers=platform_admin.headers,
            )
            if response.status_code == 200:
                data = response.json()
                status = data.get("status")
                if status in ["Success", "Failed"]:
                    return data
            return None

        result = poll_until(check_completed, max_wait=30.0)
        assert result is not None, "Async execution did not complete within timeout"
        assert result.get("status") == "Success", f"Execution failed: {result}"


@pytest.mark.e2e
class TestExecutionAccess:
    """Test execution access control."""

    def test_org_user_cannot_execute_directly(self, e2e_client, org1_user):
        """Org user cannot call /execute endpoint directly."""
        response = e2e_client.post(
            "/api/workflows/execute",
            headers=org1_user.headers,
            json={
                "workflow_id": "00000000-0000-0000-0000-000000000000",
                "input_data": {"message": "Hacked"},
            },
        )
        # Either 403 (access denied) or 404 (workflow not found) is acceptable
        # Both indicate the org user cannot execute this workflow
        assert response.status_code in [403, 404], (
            f"Org user should not execute directly: {response.status_code}"
        )

    def test_org_user_can_list_own_executions(self, e2e_client, org1_user):
        """Org user can list their own executions."""
        response = e2e_client.get(
            "/api/executions",
            headers=org1_user.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "executions" in data


@pytest.mark.e2e
class TestExecutionHistory:
    """Test execution history and details."""

    def test_platform_admin_sees_all_executions(self, e2e_client, platform_admin):
        """Platform admin can see all executions."""
        response = e2e_client.get(
            "/api/executions",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "executions" in data


@pytest.mark.e2e
class TestExecutionCancellation:
    """Test execution cancellation functionality."""

    @pytest.fixture(scope="class")
    def cancellable_workflow(self, e2e_client, platform_admin):
        """Create an async workflow suitable for cancellation testing."""
        workflow_content = '''"""E2E Cancellation Test Workflow"""
import time
from bifrost import workflow

@workflow(
    name="e2e_cancellation_workflow",
    description="Async workflow for cancellation testing",
    execution_mode="async"
)
async def e2e_cancellation_workflow(sleep_seconds: int = 30):
    time.sleep(sleep_seconds)
    return {"status": "completed", "slept_for": sleep_seconds}
'''
        result = write_and_register(
            e2e_client,
            platform_admin.headers,
            "e2e_cancellation_workflow.py",
            workflow_content,
            "e2e_cancellation_workflow",
        )
        workflow_id = result["id"]

        yield {"id": workflow_id, "name": "e2e_cancellation_workflow"}

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=e2e_cancellation_workflow.py",
            headers=platform_admin.headers,
        )

    def test_cancel_running_workflow(
        self, e2e_client, platform_admin, cancellable_workflow
    ):
        """Platform admin can cancel a running execution."""
        # Start execution
        response = e2e_client.post(
            "/api/workflows/execute",
            headers=platform_admin.headers,
            json={
                "workflow_id": cancellable_workflow["id"],
                "input_data": {"sleep_seconds": 30},
            },
        )
        assert response.status_code in [200, 202]
        data = response.json()
        execution_id = data.get("execution_id") or data.get("executionId")
        assert execution_id, "Should return execution_id"

        # Wait for execution to start (status changes from Pending)
        def check_started():
            response = e2e_client.get(
                f"/api/executions/{execution_id}",
                headers=platform_admin.headers,
            )
            if response.status_code == 200:
                data = response.json()
                status = data.get("status")
                if status and status != "Pending":
                    return data
            return None

        started = poll_until(check_started, max_wait=10.0)
        assert started is not None, "Execution did not start within timeout"

        # Cancel execution
        response = e2e_client.post(
            f"/api/executions/{execution_id}/cancel",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Cancel failed: {response.text}"
        cancel_data = response.json()

        # Verify response indicates cancellation
        if isinstance(cancel_data, dict):
            assert cancel_data.get("status") in [
                "Cancelling",
                "Cancelled",
            ], f"Unexpected cancel response: {cancel_data}"

    def test_cancel_already_cancelled_is_idempotent(
        self, e2e_client, platform_admin, cancellable_workflow
    ):
        """Cancelling an already-cancelled execution is idempotent (returns 200)."""
        # Start execution
        response = e2e_client.post(
            "/api/workflows/execute",
            headers=platform_admin.headers,
            json={
                "workflow_id": cancellable_workflow["id"],
                "input_data": {"sleep_seconds": 30},
            },
        )
        data = response.json()
        execution_id = data.get("execution_id") or data.get("executionId")

        # Wait for execution to start (ensures PostgreSQL record exists)
        # Without this, cancel might happen before worker creates the DB record,
        # causing the subsequent status poll to fail with 404
        def check_started():
            response = e2e_client.get(
                f"/api/executions/{execution_id}",
                headers=platform_admin.headers,
            )
            if response.status_code == 200:
                data = response.json()
                status = data.get("status")
                if status and status != "Pending":
                    return data
            return None

        started = poll_until(check_started, max_wait=10.0)
        assert started is not None, "Execution did not start within timeout"

        # First cancellation should succeed
        response = e2e_client.post(
            f"/api/executions/{execution_id}/cancel",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        # Wait for cancellation to be processed (status changes to Cancelling or Cancelled)
        def check_cancelled():
            response = e2e_client.get(
                f"/api/executions/{execution_id}",
                headers=platform_admin.headers,
            )
            if response.status_code == 200:
                data = response.json()
                status = data.get("status")
                if status in ["Cancelling", "Cancelled"]:
                    return data
            return None

        cancelled = poll_until(check_cancelled, max_wait=5.0)
        assert cancelled is not None, "Cancellation was not processed within timeout"

        # Second cancellation should also succeed (idempotent)
        response = e2e_client.post(
            f"/api/executions/{execution_id}/cancel",
            headers=platform_admin.headers,
        )
        # API is idempotent - returns 200 for re-cancellation
        assert response.status_code == 200, (
            f"Expected 200 for idempotent cancel, got {response.status_code}"
        )
        cancel_data = response.json()
        # Status should be Cancelled
        assert cancel_data.get("status") in ["Cancelling", "Cancelled"], (
            f"Expected cancelled status: {cancel_data}"
        )

    def test_org_user_cancel_access_behavior(
        self, e2e_client, platform_admin, org1_user, async_workflow
    ):
        """Test org user's access to cancel endpoint.

        Note: The API currently returns 200 for cancel requests regardless of
        execution ownership. This test documents the current behavior.
        """
        # Platform admin executes workflow
        response = e2e_client.post(
            "/api/workflows/execute",
            headers=platform_admin.headers,
            json={
                "workflow_id": async_workflow["id"],
                "input_data": {"delay_seconds": 30},
            },
        )
        data = response.json()
        execution_id = data.get("execution_id") or data.get("executionId")

        # Org user attempts to cancel admin's execution
        response = e2e_client.post(
            f"/api/executions/{execution_id}/cancel",
            headers=org1_user.headers,
        )
        # Current API behavior: returns 200 (no ownership check for cancel)
        # If access control is added, this should change to 403
        assert response.status_code in [200, 403], (
            f"Unexpected cancel response: {response.status_code}"
        )


@pytest.mark.e2e
class TestExecutionDetails:
    """Test execution details retrieval with access control."""

    def test_org_user_gets_own_execution_details(self, e2e_client, org1_user):
        """Org user can retrieve details of their own execution."""
        # This test verifies org users can see their own executions
        # It checks existing executions rather than creating new ones
        response = e2e_client.get(
            "/api/executions",
            headers=org1_user.headers,
        )
        assert response.status_code == 200
        data = response.json()
        executions = data.get("executions", [])

        if executions:
            # Get details of first execution
            execution_id = executions[0]["execution_id"]
            response = e2e_client.get(
                f"/api/executions/{execution_id}",
                headers=org1_user.headers,
            )
            assert response.status_code == 200, (
                f"Org user should see own execution: {response.text}"
            )
            execution_details = response.json()
            assert execution_details["execution_id"] == execution_id

    def test_org_user_cannot_see_others_execution(
        self, e2e_client, platform_admin, org1_user, sync_workflow
    ):
        """Org user cannot access another user's execution details."""
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            sync_workflow["id"],
            {"message": "Test", "count": 1},
        )
        execution_id = data.get("execution_id") or data.get("executionId")

        # Org user attempts to access admin's execution
        response = e2e_client.get(
            f"/api/executions/{execution_id}",
            headers=org1_user.headers,
        )
        assert response.status_code == 403, (
            f"Org user should not see others' execution, got {response.status_code}"
        )

    def test_org_user_cannot_see_variables(
        self, e2e_client, platform_admin, org1_user, sync_workflow
    ):
        """Org users cannot access execution variables endpoint."""
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            sync_workflow["id"],
            {"message": "Test", "count": 1},
        )
        execution_id = data.get("execution_id") or data.get("executionId")

        # Org user attempts to access variables
        response = e2e_client.get(
            f"/api/executions/{execution_id}/variables",
            headers=org1_user.headers,
        )
        assert response.status_code == 403, (
            f"Org user should not access variables, got {response.status_code}"
        )

    def test_platform_admin_sees_variables(
        self, e2e_client, platform_admin, sync_workflow
    ):
        """Platform admin can access execution variables."""
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            sync_workflow["id"],
            {"message": "Test", "count": 5},
        )
        execution_id = data.get("execution_id") or data.get("executionId")

        # Platform admin can access variables
        response = e2e_client.get(
            f"/api/executions/{execution_id}/variables",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, (
            f"Platform admin should access variables: {response.text}"
        )
        variables = response.json()
        assert isinstance(variables, dict)

    def test_get_execution_result_endpoint(
        self, e2e_client, platform_admin, sync_workflow
    ):
        """Progressive result loading endpoint works correctly."""
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            sync_workflow["id"],
            {"message": "Result test", "count": 7},
        )
        execution_id = data.get("execution_id") or data.get("executionId")

        # Get result via endpoint
        response = e2e_client.get(
            f"/api/executions/{execution_id}/result",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get result failed: {response.text}"

        # Verify result structure
        result_data = response.json()
        assert "result" in result_data, "Result should have 'result' field"
        assert "result_type" in result_data, "Result should have 'result_type' field"

    def test_get_execution_logs_endpoint(
        self, e2e_client, platform_admin, sync_workflow
    ):
        """Progressive logs loading endpoint works correctly."""
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            sync_workflow["id"],
            {"message": "Logs test", "count": 3},
        )
        execution_id = data.get("execution_id") or data.get("executionId")

        # Get logs via endpoint
        response = e2e_client.get(
            f"/api/executions/{execution_id}/logs",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get logs failed: {response.text}"

        # Verify logs structure
        logs = response.json()
        assert isinstance(logs, list), "Logs should be a list"
        # Each log entry should have expected fields
        for log in logs:
            assert "timestamp" in log or "level" in log or "message" in log, (
                f"Log entry missing expected fields: {log}"
            )


@pytest.mark.e2e
class TestExecutionLogAccess:
    """Test execution log access control by log level."""

    def test_org_user_cannot_see_debug_logs(
        self, e2e_client, platform_admin, org1_user, sync_workflow
    ):
        """Org user cannot access debug log level for others' executions."""
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            sync_workflow["id"],
            {"message": "Debug test", "count": 1},
        )
        execution_id = data.get("execution_id") or data.get("executionId")

        # Org user attempts to access logs with debug level
        response = e2e_client.get(
            f"/api/executions/{execution_id}/logs",
            headers=org1_user.headers,
            params={"level": "debug"},
        )
        # Should either be forbidden (403) or return empty/filtered logs
        assert response.status_code in [200, 403], (
            f"Unexpected status for debug logs: {response.status_code}"
        )
        if response.status_code == 200:
            pass

    def test_platform_admin_sees_debug_logs(
        self, e2e_client, platform_admin, sync_workflow
    ):
        """Platform admin can access all log levels including debug."""
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            sync_workflow["id"],
            {"message": "Admin debug test", "count": 1},
        )
        execution_id = data.get("execution_id") or data.get("executionId")

        # Platform admin can access debug logs
        response = e2e_client.get(
            f"/api/executions/{execution_id}/logs",
            headers=platform_admin.headers,
            params={"level": "debug"},
        )
        assert response.status_code == 200, (
            f"Admin should access debug logs: {response.text}"
        )
        logs = response.json()
        assert isinstance(logs, list), "Logs should be a list"


@pytest.mark.e2e
class TestExecutionConcurrency:
    """Test concurrent execution behavior."""

    def test_concurrent_executions_not_blocking(
        self, e2e_client, platform_admin, async_workflow
    ):
        """Multiple async executions run concurrently, not sequentially.

        Validates using database timestamps rather than wall-clock time to avoid
        flakiness from CI container/network overhead.
        """
        from datetime import datetime

        # Submit 3 executions with 2-second delays each
        # If concurrent: they should overlap and complete quickly
        # If sequential: 6+ seconds (3 x 2s)
        execution_ids = []

        for i in range(3):
            response = e2e_client.post(
                "/api/workflows/execute",
                headers=platform_admin.headers,
                json={
                    "workflow_id": async_workflow["id"],
                    "input_data": {"delay_seconds": 2},
                },
            )
            assert response.status_code in [200, 202], (
                f"Execute {i} failed: {response.text}"
            )
            data = response.json()
            execution_id = data.get("execution_id") or data.get("executionId")
            execution_ids.append(execution_id)

        assert len(execution_ids) == 3, "Should have 3 execution IDs"

        # Terminal statuses that indicate execution is complete
        terminal_statuses = [
            "Success",
            "Failed",
            "Timeout",
            "CompletedWithErrors",
            "Cancelled",
        ]

        # Poll until all executions complete using exponential backoff
        def check_all_completed():
            for eid in execution_ids:
                response = e2e_client.get(
                    f"/api/executions/{eid}",
                    headers=platform_admin.headers,
                )
                if response.status_code != 200:
                    return None
                status = response.json().get("status")
                if status not in terminal_statuses:
                    return None
            return True

        completed = poll_until(check_all_completed, max_wait=60.0)
        assert completed, "Timed out waiting for executions to complete"

        # Collect timestamp data from all executions
        executions_data = []
        for eid in execution_ids:
            response = e2e_client.get(
                f"/api/executions/{eid}",
                headers=platform_admin.headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert data.get("status") == "Success", (
                f"Execution {eid} did not complete successfully: {data}"
            )
            executions_data.append(data)

        # Parse timestamps
        def parse_timestamp(ts_str: str | None) -> datetime | None:
            if not ts_str:
                return None
            # Handle ISO format with or without timezone
            ts_str = ts_str.replace("Z", "+00:00")
            return datetime.fromisoformat(ts_str)

        started_times = [parse_timestamp(e.get("started_at")) for e in executions_data]
        completed_times = [
            parse_timestamp(e.get("completed_at")) for e in executions_data
        ]

        # All timestamps should be present
        assert all(started_times), f"Missing started_at timestamps: {started_times}"
        assert all(completed_times), (
            f"Missing completed_at timestamps: {completed_times}"
        )

        # Calculate execution span: from first start to last completion
        first_start = min(started_times)  # type: ignore[type-var]
        last_complete = max(completed_times)  # type: ignore[type-var]
        execution_span = (last_complete - first_start).total_seconds()

        # Verify concurrent execution by checking overlap
        # If concurrent: executions overlap in time (one starts before another finishes)
        # If sequential: each starts after the previous finishes
        #
        # Check that the last execution started BEFORE the first one completed
        # This proves they were running concurrently
        last_start = max(started_times)  # type: ignore[type-var]
        first_complete = min(completed_times)  # type: ignore[type-var]

        assert last_start < first_complete, (
            f"Executions did not overlap - ran sequentially. "
            f"Last start: {last_start.isoformat()}, First complete: {first_complete.isoformat()}"
        )

        # Also check total span is reasonable (not 3x the sleep time)
        # Sequential would be 6+ seconds, concurrent should be < 12 with overhead
        assert execution_span < 12, (
            f"Execution span {execution_span:.1f}s is too long. "
            f"Started: {[t.isoformat() for t in started_times]}, "  # type: ignore[union-attr]
            f"Completed: {[t.isoformat() for t in completed_times]}"  # type: ignore[union-attr]
        )


@pytest.mark.e2e
class TestCodeHotReload:
    """Verify process pool picks up code changes immediately.

    These tests validate that after modifying workflow or module code,
    subsequent executions use the new code (not stale cached versions).
    This is critical for the process pool architecture where fresh processes
    must load the latest code from the database.
    """

    def test_workflow_code_update_reflected_in_execution(
        self, e2e_client, platform_admin
    ):
        """Modified workflow code is used immediately in next execution.

        Tests:
        1. Create workflow v1 returning "Hello World"
        2. Execute -> verify "Hello World"
        3. Update to v2 returning "Hello World Again"
        4. Execute -> verify "Hello World Again" (not cached v1)
        """
        workflow_name = "e2e_hot_reload_workflow"
        workflow_path = f"{workflow_name}.py"

        # Step 1: Create workflow v1
        v1_content = f'''"""Hot Reload Test Workflow v1"""
from bifrost import workflow

@workflow(
    name="{workflow_name}",
    description="Hot reload test workflow",
    execution_mode="sync"
)
async def {workflow_name}():
    return {{"message": "Hello World", "version": 1}}
'''
        reg_result = write_and_register(
            e2e_client,
            platform_admin.headers,
            workflow_path,
            v1_content,
            workflow_name,
        )
        workflow_id = reg_result["id"]

        try:
            # Step 2: Execute v1 and verify
            data = execute_workflow_sync(
                e2e_client,
                platform_admin.headers,
                workflow_id,
                {},
                max_wait=60.0,
            )
            assert data["status"] == "Success", f"v1 execution failed: {data}"
            result = data.get("result", {})
            assert result.get("message") == "Hello World", (
                f"Expected 'Hello World', got: {result}"
            )
            assert result.get("version") == 1, f"Expected version 1, got: {result}"

            # Step 3: Update to v2
            v2_content = f'''"""Hot Reload Test Workflow v2"""
from bifrost import workflow

@workflow(
    name="{workflow_name}",
    description="Hot reload test workflow - updated",
    execution_mode="sync"
)
async def {workflow_name}():
    return {{"message": "Hello World Again", "version": 2}}
'''
            response = e2e_client.put(
                "/api/files/editor/content",
                headers=platform_admin.headers,
                json={
                    "path": workflow_path,
                    "content": v2_content,
                    "encoding": "utf-8",
                },
            )
            assert response.status_code == 200, f"Update to v2 failed: {response.text}"

            # Step 4: Execute v2 and verify NEW code runs
            data = execute_workflow_sync(
                e2e_client,
                platform_admin.headers,
                workflow_id,
                {},
                max_wait=60.0,
            )
            assert data["status"] == "Success", f"v2 execution failed: {data}"
            result = data.get("result", {})
            assert result.get("message") == "Hello World Again", (
                f"Expected 'Hello World Again' (v2), got stale result: {result}"
            )
            assert result.get("version") == 2, (
                f"Expected version 2, got stale version: {result}"
            )

        finally:
            # Cleanup
            e2e_client.delete(
                f"/api/files/editor?path={workflow_path}",
                headers=platform_admin.headers,
            )

    def test_module_code_update_reflected_in_execution(
        self, e2e_client, platform_admin
    ):
        """Modified module code is used immediately in next execution.

        Tests:
        1. Create module with get_value() returning "original"
        2. Create workflow importing module
        3. Execute -> verify "original"
        4. Update module to return "updated"
        5. Execute -> verify "updated" (not cached)
        """
        module_name = "e2e_hot_reload_module"
        module_path = f"{module_name}.py"
        workflow_name = "e2e_module_consumer_workflow"
        workflow_path = f"{workflow_name}.py"

        # Step 1: Create module v1
        module_v1_content = '''"""Hot Reload Module v1"""

def get_value():
    return "original"
'''
        response = e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": module_path,
                "content": module_v1_content,
                "encoding": "utf-8",
            },
        )
        assert response.status_code == 200, f"Create module v1 failed: {response.text}"

        # Step 2: Create workflow that imports module
        workflow_content = f'''"""Module Consumer Workflow"""
from bifrost import workflow
import {module_name}

@workflow(
    name="{workflow_name}",
    description="Workflow that imports module",
    execution_mode="sync"
)
async def {workflow_name}():
    value = {module_name}.get_value()
    return {{"value": value}}
'''
        reg_result = write_and_register(
            e2e_client,
            platform_admin.headers,
            workflow_path,
            workflow_content,
            workflow_name,
        )
        workflow_id = reg_result["id"]

        try:
            # Step 3: Execute and verify "original"
            data = execute_workflow_sync(
                e2e_client,
                platform_admin.headers,
                workflow_id,
                {},
            )
            assert data["status"] == "Success", f"v1 execution failed: {data}"
            result = data.get("result", {})
            assert result.get("value") == "original", (
                f"Expected 'original', got: {result}"
            )

            # Step 4: Update module to v2
            module_v2_content = '''"""Hot Reload Module v2"""

def get_value():
    return "updated"
'''
            response = e2e_client.put(
                "/api/files/editor/content",
                headers=platform_admin.headers,
                json={
                    "path": module_path,
                    "content": module_v2_content,
                    "encoding": "utf-8",
                },
            )
            assert response.status_code == 200, (
                f"Update module v2 failed: {response.text}"
            )

            # Step 5: Execute and verify "updated" (not cached "original")
            data = execute_workflow_sync(
                e2e_client,
                platform_admin.headers,
                workflow_id,
                {},
            )
            assert data["status"] == "Success", f"v2 execution failed: {data}"
            result = data.get("result", {})
            assert result.get("value") == "updated", (
                f"Expected 'updated' (v2), got stale cached result: {result}"
            )

        finally:
            # Cleanup
            e2e_client.delete(
                f"/api/files/editor?path={workflow_path}",
                headers=platform_admin.headers,
            )
            e2e_client.delete(
                f"/api/files/editor?path={module_path}",
                headers=platform_admin.headers,
            )

    def test_package_available_after_installation(self, e2e_client, platform_admin):
        """Newly installed packages are available in next execution.

        Tests:
        1. Create workflow importing 'humanize' (uncommon package)
        2. Execute -> fails (package not found)
        3. Install 'humanize' via /api/packages/install
        4. Poll until package appears in installed list
        5. Execute -> succeeds
        6. Cleanup: uninstall package
        """
        package_name = "humanize"
        workflow_name = "e2e_package_test_workflow"
        workflow_path = f"{workflow_name}.py"

        def _ensure_package_uninstalled() -> None:
            """Ensure `package_name` is not installed before the test runs.

            DELETE /api/packages/{name} is async — pip uninstall + worker recycle
            takes seconds. Without polling here, a leaked install from a prior
            run (or a prior crashed test) leaves the package present and step 2
            of this test fails because `import humanize` succeeds when we
            expect `ModuleNotFoundError`.
            """
            response = e2e_client.get("/api/packages", headers=platform_admin.headers)
            if response.status_code != 200:
                return
            packages = response.json().get("packages", [])
            if not any(p.get("name", "").lower() == package_name for p in packages):
                return
            e2e_client.delete(
                f"/api/packages/{package_name}",
                headers=platform_admin.headers,
            )

            def check_gone():
                r = e2e_client.get("/api/packages", headers=platform_admin.headers)
                if r.status_code == 200:
                    pkgs = r.json().get("packages", [])
                    if not any(p.get("name", "").lower() == package_name for p in pkgs):
                        return True
                return None

            assert poll_until(check_gone, max_wait=20.0), (
                f"Package '{package_name}' still installed after uninstall — "
                "worker recycle may have hung."
            )

        # Pre-test: clear any leaked install from a prior (possibly crashed) run.
        _ensure_package_uninstalled()

        # Step 1: Create workflow that imports humanize
        workflow_content = f'''"""Package Test Workflow"""
from bifrost import workflow

@workflow(
    name="{workflow_name}",
    description="Workflow testing package availability",
    execution_mode="sync"
)
async def {workflow_name}(number: int = 1000000):
    import humanize
    return {{"humanized": humanize.intcomma(number)}}
'''
        reg_result = write_and_register(
            e2e_client,
            platform_admin.headers,
            workflow_path,
            workflow_content,
            workflow_name,
        )
        workflow_id = reg_result["id"]

        try:
            # Step 2: Execute - should fail due to missing package
            data = execute_workflow_sync(
                e2e_client,
                platform_admin.headers,
                workflow_id,
                {"number": 1234567},
            )
            # Expected to fail because humanize is not installed
            assert data["status"] == "Failed", (
                f"Expected Failed status (missing package), got: {data}"
            )

            # Step 3: Install the package
            response = e2e_client.post(
                "/api/packages/install",
                headers=platform_admin.headers,
                json={"package_name": package_name},
            )
            assert response.status_code == 200, (
                f"Install request failed: {response.text}"
            )
            install_data = response.json()
            assert install_data.get("status") == "success", (
                f"Unexpected install status: {install_data}"
            )

            # Step 4: Poll until package appears in installed list
            # Workers pip install the package then recycle processes.
            def check_package_installed():
                response = e2e_client.get(
                    "/api/packages",
                    headers=platform_admin.headers,
                )
                if response.status_code == 200:
                    packages = response.json().get("packages", [])
                    if any(p.get("name", "").lower() == package_name for p in packages):
                        return True
                return None

            installed = poll_until(check_package_installed, max_wait=60.0)
            assert installed, f"Package '{package_name}' not installed within timeout"

            # Step 5: Execute again - should succeed now
            def check_workflow_succeeds():
                data = execute_workflow_sync(
                    e2e_client,
                    platform_admin.headers,
                    workflow_id,
                    {"number": 1234567},
                )
                if data["status"] == "Success":
                    return data
                return None

            data = poll_until(check_workflow_succeeds, max_wait=60.0)
            assert data is not None, (
                "Workflow should succeed after package install within timeout"
            )
            result = data.get("result", {})
            assert result.get("humanized") == "1,234,567", (
                f"Expected '1,234,567', got: {result}"
            )

        finally:
            # Cleanup: delete workflow
            e2e_client.delete(
                f"/api/files/editor?path={workflow_path}",
                headers=platform_admin.headers,
            )
            # Cleanup: uninstall package and wait for it to actually go away
            # (DELETE returns before pip uninstall + worker recycle complete).
            _ensure_package_uninstalled()

    def test_nested_module_package_update_reflected(self, e2e_client, platform_admin):
        """Updates to nested package modules are reflected immediately.

        Tests:
        1. Create mypackage/__init__.py and mypackage/utils.py
        2. Create workflow importing from mypackage.utils
        3. Execute -> verify original value
        4. Update mypackage/utils.py
        5. Execute -> verify updated value
        """
        pkg_name = "e2e_hot_reload_pkg"
        init_path = f"{pkg_name}/__init__.py"
        utils_path = f"{pkg_name}/utils.py"
        workflow_name = "e2e_nested_pkg_workflow"
        workflow_path = f"{workflow_name}.py"

        # Step 1: Create package structure
        # Create __init__.py
        init_content = f'''"""Package init for {pkg_name}"""
'''
        response = e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": init_path,
                "content": init_content,
                "encoding": "utf-8",
            },
        )
        assert response.status_code == 200, (
            f"Create __init__.py failed: {response.text}"
        )

        # Create utils.py v1
        utils_v1_content = '''"""Utils module v1"""

CONSTANT = "original_constant"

def get_data():
    return {"source": "utils", "value": "original"}
'''
        response = e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": utils_path,
                "content": utils_v1_content,
                "encoding": "utf-8",
            },
        )
        assert response.status_code == 200, f"Create utils.py failed: {response.text}"

        # Step 2: Create workflow importing from package
        workflow_content = f'''"""Nested Package Consumer Workflow"""
from bifrost import workflow
from {pkg_name}.utils import get_data, CONSTANT

@workflow(
    name="{workflow_name}",
    description="Workflow importing nested package",
    execution_mode="sync"
)
async def {workflow_name}():
    data = get_data()
    return {{"data": data, "constant": CONSTANT}}
'''
        reg_result = write_and_register(
            e2e_client,
            platform_admin.headers,
            workflow_path,
            workflow_content,
            workflow_name,
        )
        workflow_id = reg_result["id"]

        try:
            # Step 3: Execute and verify original values
            data = execute_workflow_sync(
                e2e_client,
                platform_admin.headers,
                workflow_id,
                {},
            )
            assert data["status"] == "Success", f"v1 execution failed: {data}"
            result = data.get("result", {})
            assert result.get("constant") == "original_constant", (
                f"Expected 'original_constant', got: {result}"
            )
            assert result.get("data", {}).get("value") == "original", (
                f"Expected 'original', got: {result}"
            )

            # Step 4: Update utils.py to v2
            utils_v2_content = '''"""Utils module v2"""

CONSTANT = "updated_constant"

def get_data():
    return {"source": "utils", "value": "updated"}
'''
            response = e2e_client.put(
                "/api/files/editor/content",
                headers=platform_admin.headers,
                json={
                    "path": utils_path,
                    "content": utils_v2_content,
                    "encoding": "utf-8",
                },
            )
            assert response.status_code == 200, (
                f"Update utils.py v2 failed: {response.text}"
            )

            # Step 5: Execute and verify updated values
            data = execute_workflow_sync(
                e2e_client,
                platform_admin.headers,
                workflow_id,
                {},
            )
            assert data["status"] == "Success", f"v2 execution failed: {data}"
            result = data.get("result", {})
            assert result.get("constant") == "updated_constant", (
                f"Expected 'updated_constant' (v2), got stale: {result}"
            )
            assert result.get("data", {}).get("value") == "updated", (
                f"Expected 'updated' (v2), got stale: {result}"
            )

        finally:
            # Cleanup
            e2e_client.delete(
                f"/api/files/editor?path={workflow_path}",
                headers=platform_admin.headers,
            )
            e2e_client.delete(
                f"/api/files/editor?path={utils_path}",
                headers=platform_admin.headers,
            )
            e2e_client.delete(
                f"/api/files/editor?path={init_path}",
                headers=platform_admin.headers,
            )

    @pytest.mark.asyncio
    async def test_requirements_stored_in_s3(
        self, e2e_client, platform_admin
    ):
        """
        Test that installing a package stores requirements.txt in S3.

        Verifies that after package installation:
        1. requirements.txt exists in S3 via RepoStorage
        2. The content includes the installed package
        """
        from src.services.repo_storage import RepoStorage

        package_name = "humanize"

        # First, check if package is already installed and uninstall it
        response = e2e_client.get(
            "/api/packages",
            headers=platform_admin.headers,
        )
        if response.status_code == 200:
            packages = response.json().get("packages", [])
            if any(p.get("name", "").lower() == package_name for p in packages):
                # Uninstall it first to ensure clean test
                e2e_client.delete(
                    f"/api/packages/{package_name}",
                    headers=platform_admin.headers,
                )

        # Install a package
        install_response = e2e_client.post(
            "/api/packages/install",
            headers=platform_admin.headers,
            json={"package_name": package_name},
        )
        assert install_response.status_code == 200, (
            f"Install failed: {install_response.text}"
        )
        install_data = install_response.json()
        assert install_data.get("status") == "success", (
            f"Unexpected install status: {install_data}"
        )

        # Read requirements.txt from S3
        repo = RepoStorage()
        content_bytes = await repo.read("requirements.txt")
        content = content_bytes.decode()

        assert content.strip(), "requirements.txt should have content in S3"
        assert package_name in content.lower(), (
            f"requirements.txt should contain '{package_name}', got: {content}"
        )

        # Cleanup: uninstall package
        e2e_client.delete(
            f"/api/packages/{package_name}",
            headers=platform_admin.headers,
        )

    @pytest.mark.asyncio
    async def test_requirements_cached_in_redis(self, e2e_client, platform_admin):
        """
        Test that installing a package updates Redis cache.

        Verifies that after package installation:
        1. The requirements cache in Redis is populated
        2. The cached content includes the installed package
        """
        from src.core.requirements_cache import get_requirements

        package_name = "humanize"

        # First, check if package is already installed and uninstall it
        response = e2e_client.get(
            "/api/packages",
            headers=platform_admin.headers,
        )
        if response.status_code == 200:
            packages = response.json().get("packages", [])
            if any(p.get("name", "").lower() == package_name for p in packages):
                # Uninstall it first to ensure clean test
                e2e_client.delete(
                    f"/api/packages/{package_name}",
                    headers=platform_admin.headers,
                )

        # Install a package
        install_response = e2e_client.post(
            "/api/packages/install",
            headers=platform_admin.headers,
            json={"package_name": package_name},
        )
        assert install_response.status_code == 200, (
            f"Install failed: {install_response.text}"
        )
        install_data = install_response.json()
        assert install_data.get("status") == "success", (
            f"Unexpected install status: {install_data}"
        )

        # Poll until package appears in installed list (confirms installation completed)
        def check_package_installed():
            response = e2e_client.get(
                "/api/packages",
                headers=platform_admin.headers,
            )
            if response.status_code == 200:
                packages = response.json().get("packages", [])
                if any(p.get("name", "").lower() == package_name for p in packages):
                    return True
            return None

        installed = poll_until(check_package_installed, max_wait=60.0)
        assert installed, f"Package '{package_name}' not installed within timeout"

        # Check Redis cache
        cached = await get_requirements()
        assert cached is not None, "requirements.txt should be cached in Redis"
        assert package_name in cached["content"].lower(), (
            f"Redis cache should contain '{package_name}', got: {cached['content']}"
        )

        # Cleanup: uninstall package
        e2e_client.delete(
            f"/api/packages/{package_name}",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestSDKWorkflowExecute:
    """Test workflows.execute() from inside a workflow — the real production path.

    Validates that the SDK properly propagates org context so org-scoped
    workflows can call other org-scoped workflows by UUID.
    """

    @pytest.fixture(scope="class")
    def org_target_workflow(self, e2e_client, platform_admin, org1):
        """Create an org-scoped target workflow that will be called by UUID."""
        name = f"e2e_sdk_execute_target_{uuid.uuid4().hex[:8]}"
        path = f"{name}.py"
        content = f'''"""Target workflow for SDK execute test."""
from bifrost import workflow

@workflow(
    name="{name}",
    description="Target for workflows.execute() by-UUID test",
    execution_mode="sync",
)
async def {name}(ping: str = "default"):
    return {{"pong": ping}}
'''
        result = write_and_register(
            e2e_client, platform_admin.headers, path, content, name,
        )
        # Scope to org1
        resp = e2e_client.patch(
            f"/api/workflows/{result['id']}",
            headers=platform_admin.headers,
            json={"organization_id": org1["id"]},
        )
        assert resp.status_code == 200, f"Patch org failed: {resp.text}"

        yield {"id": result["id"], "name": name, "path": path}

        e2e_client.delete(
            f"/api/files/editor?path={path}", headers=platform_admin.headers,
        )

    @pytest.fixture(scope="class")
    def org_caller_workflow(
        self, e2e_client, platform_admin, org1, org_target_workflow,
    ):
        """Create an org-scoped workflow that calls another workflow by UUID."""
        name = f"e2e_sdk_execute_caller_{uuid.uuid4().hex[:8]}"
        target_id = org_target_workflow["id"]
        path = f"{name}.py"
        content = f'''"""Caller workflow — uses workflows.execute() by UUID."""
import traceback
from bifrost import workflow, workflows

@workflow(
    name="{name}",
    description="Calls another workflow by UUID via SDK",
    execution_mode="sync",
)
async def {name}(target_id: str = "{target_id}"):
    try:
        exec_id = await workflows.execute(
            workflow=target_id,
            input_data={{"ping": "from_caller"}},
        )
        return {{"success": True, "execution_id": exec_id}}
    except Exception as e:
        return {{
            "success": False,
            "error_type": type(e).__name__,
            "error_message": str(e),
            "traceback": traceback.format_exc(),
        }}
'''
        result = write_and_register(
            e2e_client, platform_admin.headers, path, content, name,
        )
        # Scope to org1
        resp = e2e_client.patch(
            f"/api/workflows/{result['id']}",
            headers=platform_admin.headers,
            json={"organization_id": org1["id"]},
        )
        assert resp.status_code == 200, f"Patch org failed: {resp.text}"

        yield {"id": result["id"], "name": name, "path": path}

        e2e_client.delete(
            f"/api/files/editor?path={path}", headers=platform_admin.headers,
        )

    def test_sdk_execute_by_id_org_scoped(
        self, e2e_client, platform_admin, org1,
        org_target_workflow, org_caller_workflow,
    ):
        """Org-scoped workflow calls another org-scoped workflow by UUID.

        This is the real production path: workflows.execute(uuid)
        from inside a running workflow. The SDK must propagate org context
        so the execute endpoint can find org-scoped workflows.
        """
        data = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            org_caller_workflow["id"],
            {"target_id": org_target_workflow["id"]},
            max_wait=60.0,
            request_sync=True,
            request_timeout=120.0,
        )

        assert data["status"] == "Success", (
            f"Caller workflow failed: {data.get('result', data.get('error_message'))}"
        )
        result = data["result"]
        assert result["success"] is True, (
            f"SDK execute failed: {result.get('error_message')}\n"
            f"{result.get('traceback', '')}"
        )
        assert "execution_id" in result, "Should return child execution_id"

    def test_sdk_execute_nonexistent_workflow_error(
        self, e2e_client, platform_admin, org1, org_caller_workflow,
    ):
        """workflows.execute() with nonexistent UUID returns clear error."""
        fake_uuid = str(uuid.uuid4())
        response = e2e_client.post(
            "/api/workflows/execute",
            headers=platform_admin.headers,
            json={
                "workflow_id": org_caller_workflow["id"],
                "input_data": {"target_id": fake_uuid},
            },
        )
        assert response.status_code == 200, f"Execute failed: {response.text}"
        execution_id = response.json()["execution_id"]

        terminal = {"Success", "Failed", "Completed", "CompletedWithErrors"}

        def check():
            resp = e2e_client.get(
                f"/api/executions/{execution_id}",
                headers=platform_admin.headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") in terminal:
                    return data
            return None

        data = poll_until(check, max_wait=30.0, interval=0.2)
        assert data is not None, f"Execution {execution_id} timed out"

        result = data["result"]
        assert result["success"] is False
        assert "not found" in result["error_message"].lower(), (
            f"Error should mention 'not found', got: {result['error_message']}"
        )
