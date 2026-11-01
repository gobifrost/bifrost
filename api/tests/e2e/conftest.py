"""
E2E test configuration.

E2E tests run against the full API stack (API + Jobs workers) with real
PostgreSQL, Redis and S3 services.

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
import re
import uuid

import httpx
import pytest
from bifrost._refreshing import RefreshingClient, refresh_extensions

# Re-export so existing ``from tests.e2e.conftest import poll_until`` still works.
from tests.helpers.polling import poll_until  # noqa: F401
from tests.e2e.fixtures.users import E2EUser


# E2E test API URL (from docker-compose.test.yml)
# Default to api:8000 since tests run inside Docker network
E2E_API_URL = os.getenv("TEST_API_URL", "http://api:8000")
_SOLUTION_DEPLOY_RE = re.compile(r"^/api/solutions/([^/]+)/deploy$")


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
    """Fail the run when the E2E boundary is unavailable.

    Returning a green session in which every integration test was skipped made
    a broken stack indistinguishable from a successful product run.
    """
    is_available, reason = _check_api_available()

    if not is_available:
        pytest.exit(f"E2E test boundary unavailable: {reason}", returncode=1)


@pytest.fixture(scope="session")
def e2e_api_url():
    """Base URL for E2E API tests."""
    return E2E_API_URL


def _legacy_deploy_body_to_zip(client: "_E2EClient", solution_id: str, headers, body: dict) -> bytes:
    """Package legacy test deploy JSON as the production workspace zip."""
    from src.models.orm.solutions import Solution
    from src.services.solutions.deploy import SolutionBundle
    from src.services.solutions.export import build_workspace_zip

    listed = client.get("/api/solutions", headers=headers)
    assert listed.status_code == 200, (
        f"solution lookup failed: {listed.status_code} {listed.text}"
    )
    solution = None
    for item in listed.json().get("solutions", []):
        if str(item.get("id")) == solution_id:
            solution = item
            break
    assert solution is not None, f"solution {solution_id} not found"

    org_id = solution.get("organization_id")
    sol = Solution(
        id=uuid.UUID(solution_id),
        slug=solution["slug"],
        name=solution["name"],
        version=solution.get("version"),
        organization_id=uuid.UUID(org_id) if org_id else None,
        allow_outbound_access=bool(
            solution.get(
                "allow_outbound_access",
                solution.get("global_repo_access", False),
            )
        ),
    )
    bundle = SolutionBundle(
        solution=sol,
        python_files=body.get("python_files", {}),
        workflows=body.get("workflows", []),
        tables=body.get("tables", []),
        apps=body.get("apps", []),
        forms=body.get("forms", []),
        agents=body.get("agents", []),
        claims=body.get("claims", []),
        config_schemas=body.get("config_schemas", []),
        connection_schemas=body.get("connection_schemas", []),
        file_locations=body.get("file_locations", []),
        events=body.get("events", []),
        version=body.get("version"),
        logo_b64=body.get("logo_b64"),
        logo_content_type=body.get("logo_content_type"),
        readme=body.get("readme"),
    )
    return build_workspace_zip(bundle)


class _E2EClient:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def __getattr__(self, name: str):
        return getattr(self._client, name)

    @staticmethod
    def _fixture_bearer(headers) -> str | None:
        if not headers:
            return None
        authorization = httpx.Headers(headers).get("authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization[7:]
        return None

    def _refresh_fixture_user(self, challenged_access_token: str | None) -> str | None:
        """Rotate only the matching fixture user's real human session."""
        user = E2EUser.user_for_access_token(challenged_access_token)
        if user is None or not user.refresh_token:
            return None

        response = httpx.post(
            f"{str(self._client.base_url).rstrip('/')}/auth/refresh",
            json={"refresh_token": user.refresh_token},
            timeout=60.0,
        )
        if response.status_code != 200:
            return None
        token_data = response.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        if not isinstance(access_token, str) or not isinstance(refresh_token, str):
            return None
        user.set_tokens(access_token, refresh_token)
        return access_token

    def request(self, method: str, url, **kwargs):
        """Use production challenge refresh for registered fixture credentials."""
        bearer = self._fixture_bearer(kwargs.get("headers"))
        if E2EUser.user_for_access_token(bearer) is not None:
            kwargs["extensions"] = refresh_extensions(kwargs.get("extensions"))
        return self._client.request(method, url, **kwargs)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def options(self, url, **kwargs):
        return self.request("OPTIONS", url, **kwargs)

    def head(self, url, **kwargs):
        return self.request("HEAD", url, **kwargs)

    def post(self, path: str, **kwargs):
        match = _SOLUTION_DEPLOY_RE.match(path)
        if match and "json" in kwargs and "files" not in kwargs:
            body = kwargs.pop("json") or {}
            solution_id = match.group(1)
            headers = kwargs.get("headers")
            zip_bytes = _legacy_deploy_body_to_zip(self, solution_id, headers, body)
            params = dict(kwargs.pop("params", {}) or {})
            if body.get("force"):
                params["force"] = "true"
            if params:
                kwargs["params"] = params
            upload_headers = dict(headers or {})
            for key in list(upload_headers):
                if key.lower() == "content-type":
                    upload_headers.pop(key)
            kwargs["headers"] = upload_headers
            kwargs["files"] = {
                "file": ("deploy.zip", zip_bytes, "application/zip"),
            }
        return self.request("POST", path, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)


@pytest.fixture(scope="session")
def e2e_client():
    """
    HTTP client for E2E tests.

    Provides a configured httpx client for making requests to the API.
    """
    e2e: _E2EClient | None = None

    def refresh(challenged_access_token: str | None) -> str | None:
        assert e2e is not None
        return e2e._refresh_fixture_user(challenged_access_token)

    with RefreshingClient(
        base_url=E2E_API_URL,
        timeout=60.0,
        refresh=refresh,
    ) as client:
        e2e = _E2EClient(client)
        yield e2e


@pytest.fixture(scope="session")
def human_minter(e2e_client):
    """Refresh an E2E fixture user's signed human credential pair.

    WebSocket clients and ``requests`` callers do not pass through
    :class:`RefreshingClient`, so they must obtain a current credential before
    opening a connection or constructing their request headers.  This uses the
    same single-use refresh endpoint as the production SDK and records both
    replacement tokens on the fixture user for later challenge-driven callers.
    """

    def _mint(user: E2EUser) -> E2EUser:
        previous_access_token = user.access_token
        assert previous_access_token is not None, f"{user.email} has no access token"
        access_token = e2e_client._refresh_fixture_user(previous_access_token)
        assert access_token is not None, f"Failed to refresh signed human credential for {user.email}"
        assert user.refresh_token is not None, f"Refresh did not return a replacement token for {user.email}"
        return user

    return _mint


@pytest.fixture
def private_client():
    """Client with its own cookie jar, for tests that deliberately log in.

    Use this instead of ``e2e_client`` when the test itself exercises login,
    MFA, refresh or logout, which set auth cookies on the client that made the
    call.
    """
    with httpx.Client(base_url=E2E_API_URL, timeout=60.0) as client:
        yield client


@pytest.fixture(autouse=True)
def shared_client_has_no_ambient_auth(e2e_client):
    """Fail any test or fixture that leaves cookies in the shared client.

    Login, MFA and refresh responses set auth cookies, and the server accepts
    those in place of an ``Authorization`` header. A cookie left in the shared
    jar silently authenticates every later request that meant to be anonymous,
    so results would depend on test order. Credential exchanges must use a
    private client (see ``tests.e2e.fixtures.setup._anonymous_client``).
    """
    yield
    leaked = sorted(cookie.name for cookie in e2e_client.cookies.jar)
    e2e_client.cookies.clear()
    assert not leaked, (
        f"Shared e2e_client kept cookies {leaked}; do credential exchanges on a "
        "private client so tests authenticate only through explicit headers"
    )


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

    The /api/forms/{form_id}/submissions endpoint is async by default - it queues
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
        f"/api/forms/{form_id}/submissions",
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
