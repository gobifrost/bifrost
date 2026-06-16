"""
E2E test configuration.

E2E tests run against the full API stack (API + Jobs workers) with real
PostgreSQL, RabbitMQ, and Redis services.

These tests require:
- docker-compose.test.yml services running (via ./test.sh)
- API service accessible at TEST_API_URL

Session-scoped fixtures provide shared state:
- platform_admin: First registered user (superuser)
- org1, org2: Test organizations
- org1_user, org2_user: Org users with tokens

Note: pytest_plugins moved to tests/conftest.py (root) as required by pytest.
"""

import os

import pytest
import httpx

# Re-export so existing ``from tests.e2e.conftest import poll_until`` still works.
from tests.helpers.polling import poll_until  # noqa: F401


# E2E test API URL (from docker-compose.test.yml)
# Default to api:8000 since tests run inside Docker network
E2E_API_URL = os.getenv("TEST_API_URL", "http://api:8000")


def pytest_configure(config):
    """Register e2e marker."""
    config.addinivalue_line(
        "markers",
        "e2e: End-to-end tests requiring full API stack (auto-skipped if API not available)",
    )


def _check_api_available() -> tuple[bool, str | None]:
    """
    Check if the API is properly running and accessible.

    Returns:
        tuple: (is_available: bool, reason: str)
    """
    try:
        response = httpx.get(f"{E2E_API_URL}/health", timeout=5.0)
        if response.status_code == 200:
            return True, None
        return False, f"API returned status {response.status_code}"
    except httpx.ConnectError:
        return False, f"Cannot connect to API at {E2E_API_URL}"
    except httpx.TimeoutException:
        return False, f"API request timed out at {E2E_API_URL}"
    except Exception as e:
        return False, f"Error checking API: {str(e)}"


def pytest_collection_modifyitems(config, items):
    """Skip e2e tests if API is not available."""
    is_available, reason = _check_api_available()

    if not is_available:
        skip_e2e = pytest.mark.skip(reason=f"E2E tests skipped: {reason}")
        for item in items:
            if "e2e" in item.nodeid:
                item.add_marker(skip_e2e)


@pytest.fixture(scope="session")
def e2e_api_url():
    """Base URL for E2E API tests."""
    return E2E_API_URL


@pytest.fixture(scope="session")
def e2e_client():
    """
    HTTP client for E2E tests.

    Provides a configured httpx client for making requests to the API.
    """
    with httpx.Client(base_url=E2E_API_URL, timeout=60.0) as client:
        yield client


_UNSET = object()


def write_and_register(
    e2e_client, headers, path: str, content: str, function_name: str,
    *, organization_id=_UNSET,
) -> dict:
    """Write a Python file and register its decorated function.

    By default ``organization_id`` is OMITTED from the register request, so the
    workflow HOME-defaults to the caller's own org (unified --org standard). Pass
    ``organization_id=None`` to register a GLOBAL workflow, or a UUID string to
    target a specific org.

    Returns the RegisterWorkflowResponse dict with keys: id, name, function_name, path, type, description.
    """
    # Write file
    resp = e2e_client.put(
        "/api/files/editor/content",
        headers=headers,
        json={"path": path, "content": content, "encoding": "utf-8"},
    )
    assert resp.status_code in (200, 201), (
        f"File write failed: {resp.status_code} {resp.text}"
    )

    # Register the decorated function
    register_body = {"path": path, "function_name": function_name}
    if organization_id is not _UNSET:
        register_body["organization_id"] = organization_id
    resp = e2e_client.post(
        "/api/workflows/register",
        headers=headers,
        json=register_body,
    )
    if resp.status_code == 409:
        # Already registered from a previous test run — look up and return existing
        list_resp = e2e_client.get("/api/workflows", headers=headers)
        assert list_resp.status_code == 200, (
            f"Workflow list failed: {list_resp.status_code}"
        )
        for w in list_resp.json():
            if w.get("function_name") == function_name and w.get("path") == path:
                return w
        # Fallback: match by function_name only
        for w in list_resp.json():
            if w.get("function_name") == function_name:
                return w
        raise AssertionError(
            f"409 but could not find existing workflow {function_name} at {path}"
        )
    assert resp.status_code in (200, 201), (
        f"Register failed for {function_name} at {path}: {resp.status_code} {resp.text}"
    )
    return resp.json()


def execute_workflow_sync(
    e2e_client,
    headers,
    workflow_id: str,
    input_data: dict | None = None,
    max_wait: float = 30.0,
    request_sync: bool = False,
    request_timeout: float | None = None,
    org_id: str | None = None,
) -> dict:
    """Execute a workflow and poll until completion.

    The /api/workflows/execute endpoint is async by default - it queues the
    execution and returns immediately with status=Pending. This helper polls
    the execution status until it reaches a terminal state (Success/Failed).

    Args:
        e2e_client: HTTP client for API requests
        headers: Auth headers
        workflow_id: UUID of workflow to execute
        input_data: Input parameters for the workflow
        max_wait: Maximum time to wait for completion (seconds)
        request_sync: If True, ask the API to block until worker completion
        request_timeout: Optional timeout for the initial execute request

    Returns:
        The execution result dict with status, result, error, etc.

    Raises:
        AssertionError: If execution fails or times out
    """
    payload: dict = {
        "workflow_id": workflow_id,
        "input_data": input_data or {},
        "sync": request_sync,
    }
    if org_id is not None:
        payload["org_id"] = org_id
    response = e2e_client.post(
        "/api/workflows/execute",
        headers=headers,
        json=payload,
        timeout=request_timeout,
    )
    assert response.status_code == 200, f"Execute failed: {response.text}"
    data = response.json()
    execution_id = data.get("execution_id")

    if data["status"] in ("Success", "Failed"):
        return data

    def check_completion():
        resp = e2e_client.get(
            f"/api/executions/{execution_id}",
            headers=headers,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("status") in ("Success", "Failed", "Completed"):
                return result
        return None

    result = poll_until(check_completion, max_wait=max_wait, interval=0.2)
    assert result is not None, f"Execution {execution_id} timed out after {max_wait}s"
    return result


def execute_form_sync(
    e2e_client,
    headers,
    form_id: str,
    form_data: dict,
    max_wait: float = 30.0,
) -> dict:
    """Execute a form and poll until completion.

    The /api/forms/{form_id}/execute endpoint is async by default - it queues
    the execution and returns immediately with status=Pending. This helper polls
    the execution status until it reaches a terminal state (Success/Failed).

    Args:
        e2e_client: HTTP client for API requests
        headers: Auth headers
        form_id: UUID of form to execute
        form_data: Form field values
        max_wait: Maximum time to wait for completion (seconds)

    Returns:
        The execution result dict with status, result, error, etc.

    Raises:
        AssertionError: If execution fails or times out
    """
    response = e2e_client.post(
        f"/api/forms/{form_id}/execute",
        headers=headers,
        json={"form_data": form_data},
    )
    assert response.status_code == 200, f"Form execute failed: {response.text}"
    data = response.json()
    execution_id = data.get("execution_id")

    if data["status"] in ("Success", "Failed"):
        return data

    def check_completion():
        resp = e2e_client.get(
            f"/api/executions/{execution_id}",
            headers=headers,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("status") in ("Success", "Failed", "Completed"):
                return result
        return None

    result = poll_until(check_completion, max_wait=max_wait, interval=0.2)
    assert result is not None, (
        f"Form execution {execution_id} timed out after {max_wait}s"
    )
    return result
