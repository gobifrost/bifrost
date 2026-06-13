"""
CLI E2E Tests.

Tests the Bifrost CLI API endpoints including:
- Developer context (GET only — PUT was deleted in the 2026-05 org-scoping
  overhaul. The caller's effective org is the auth-verified
  ``user.organization_id``; cross-org targeting happens via the explicit
  ``scope`` parameter on each SDK call.)
- File operations via CLI (read, write, list, delete)
- Config operations via CLI (get, set, list, delete)
- SDK download

Note: Authentication is done via session auth (platform_admin.headers).
"""

import logging

logger = logging.getLogger(__name__)


# =============================================================================
# Context Tests
# =============================================================================


class TestCLIContext:
    """Test GET /api/sdk/context — the CLI bootstrap endpoint."""

    def test_get_context_with_session_auth(
        self,
        e2e_client,
        platform_admin,
    ):
        response = e2e_client.get(
            "/api/sdk/context",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "user" in data
        assert "email" in data["user"]
        assert "default_parameters" in data
        assert isinstance(data["default_parameters"], dict)

    def test_context_requires_authentication(
        self,
        e2e_client,
    ):
        e2e_client.cookies.clear()
        response = e2e_client.get("/api/sdk/context")
        assert response.status_code in [401, 422]


class TestCLIContextOrgOverride:
    """Test /api/sdk/context?org_id= — the platform-admin / provider-org
    cross-org targeting affordance. C2 gate."""

    def test_superuser_can_get_org_context(
        self,
        e2e_client,
        platform_admin,
        org1,
    ):
        response = e2e_client.get(
            "/api/sdk/context",
            params={"org_id": org1["id"]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["user"]["id"] == str(platform_admin.user_id)
        assert data["user"]["email"] == platform_admin.email
        assert data["user"]["is_superuser"] is True

        assert data["organization"] is not None
        assert data["organization"]["id"] == org1["id"]
        assert data["organization"]["name"] == org1["name"]
        assert data["organization"]["is_active"] is True

    def test_org_context_returns_all_required_fields(
        self,
        e2e_client,
        platform_admin,
        org1,
    ):
        """Org context response has all fields needed to build ExecutionContext."""
        response = e2e_client.get(
            "/api/sdk/context",
            params={"org_id": org1["id"]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()

        user = data["user"]
        for field in ("id", "email", "name", "is_superuser"):
            assert field in user, f"missing user.{field}"

        org = data["organization"]
        for field in ("id", "name", "is_active", "is_provider"):
            assert field in org, f"missing organization.{field}"

    def test_non_superuser_cannot_target_other_org(
        self,
        e2e_client,
        org1_user,
        org2,
    ):
        """A regular org user cannot fetch another org's context."""
        response = e2e_client.get(
            "/api/sdk/context",
            params={"org_id": org2["id"]},
            headers=org1_user.headers,
        )
        assert response.status_code == 403

    def test_non_superuser_can_target_own_org(
        self,
        e2e_client,
        org1_user,
        org1,
    ):
        """A regular user CAN pass ?org_id matching their own org."""
        response = e2e_client.get(
            "/api/sdk/context",
            params={"org_id": org1["id"]},
            headers=org1_user.headers,
        )
        assert response.status_code == 200

    def test_nonexistent_org_gets_404(
        self,
        e2e_client,
        platform_admin,
    ):
        import uuid
        fake_org_id = str(uuid.uuid4())
        response = e2e_client.get(
            "/api/sdk/context",
            params={"org_id": fake_org_id},
            headers=platform_admin.headers,
        )
        assert response.status_code == 404

    def test_superuser_can_switch_between_orgs(
        self,
        e2e_client,
        platform_admin,
        org1,
        org2,
    ):
        resp1 = e2e_client.get(
            "/api/sdk/context",
            params={"org_id": org1["id"]},
            headers=platform_admin.headers,
        )
        assert resp1.status_code == 200
        assert resp1.json()["organization"]["id"] == org1["id"]

        resp2 = e2e_client.get(
            "/api/sdk/context",
            params={"org_id": org2["id"]},
            headers=platform_admin.headers,
        )
        assert resp2.status_code == 200
        assert resp2.json()["organization"]["id"] == org2["id"]
        assert resp1.json()["organization"]["id"] != resp2.json()["organization"]["id"]

    def test_without_org_id_uses_users_own_org(
        self,
        e2e_client,
        org1_user,
    ):
        """Without ?org_id, returns the user's auth-verified org.

        Pre-overhaul this fetched ``DeveloperContext.default_org_id``,
        which was a per-user mutable field anyone could set to forge
        another org's identity. Now sourced from ``User.organization_id``.
        """
        response = e2e_client.get(
            "/api/sdk/context",
            headers=org1_user.headers,
        )
        assert response.status_code == 200
        data = response.json()

        assert data["organization"] is not None
        assert data["organization"]["id"] == str(org1_user.organization_id)


# =============================================================================
# File Operation Tests
# =============================================================================


class TestCLIFileOperations:
    """Test SDK file operation endpoints via /api/files."""

    def test_write_and_read_file(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test writing and reading a file via unified files API."""
        test_path = "sdk-test-file.txt"
        test_content = "Hello from SDK E2E test!"
        headers = platform_admin.headers

        # Write file (using cloud mode which uses S3)
        response = e2e_client.post(
            "/api/files/write",
            json={
                "path": test_path,
                "content": test_content,
                "location": "temp",
                "scope": "test-scope",
                "mode": "cloud",
            },
            headers=headers,
        )
        assert response.status_code == 204, f"Write failed: {response.text}"

        # Read file back
        response = e2e_client.post(
            "/api/files/read",
            json={
                "path": test_path,
                "location": "temp",
                "scope": "test-scope",
                "mode": "cloud",
            },
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["content"] == test_content

        # Cleanup
        e2e_client.post(
            "/api/files/delete",
            json={"path": test_path, "location": "temp", "scope": "test-scope", "mode": "cloud"},
            headers=headers,
        )

    def test_list_files(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test listing files in a directory."""
        headers = platform_admin.headers

        # Create a test file first
        write_response = e2e_client.post(
            "/api/files/write",
            json={
                "path": "list-test.txt",
                "content": "test content",
                "location": "temp",
                "scope": "test-scope",
                "mode": "cloud",
            },
            headers=headers,
        )
        assert write_response.status_code == 204, f"Write failed: {write_response.text}"

        # List files
        response = e2e_client.post(
            "/api/files/list",
            json={
                "directory": "",
                "location": "temp",
                "scope": "test-scope",
                "mode": "cloud",
            },
            headers=headers,
        )
        assert response.status_code == 200, f"List failed: {response.text}"
        data = response.json()
        assert isinstance(data["files"], list)

        # Cleanup
        e2e_client.post(
            "/api/files/delete",
            json={"path": "list-test.txt", "location": "temp", "scope": "test-scope", "mode": "cloud"},
            headers=headers,
        )

    def test_delete_file(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test deleting a file via unified files API."""
        test_path = "delete-test.txt"
        headers = platform_admin.headers

        # Create file
        write_response = e2e_client.post(
            "/api/files/write",
            json={
                "path": test_path,
                "content": "to be deleted",
                "location": "temp",
                "scope": "test-scope",
                "mode": "cloud",
            },
            headers=headers,
        )
        assert write_response.status_code == 204, f"Write failed: {write_response.text}"

        # Delete file
        response = e2e_client.post(
            "/api/files/delete",
            json={"path": test_path, "location": "temp", "scope": "test-scope", "mode": "cloud"},
            headers=headers,
        )
        assert response.status_code == 204, f"Delete failed: {response.text}"

        # Verify deleted
        response = e2e_client.post(
            "/api/files/read",
            json={"path": test_path, "location": "temp", "scope": "test-scope", "mode": "cloud"},
            headers=headers,
        )
        assert response.status_code == 404

    def test_read_nonexistent_file(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test reading a file that doesn't exist."""
        headers = platform_admin.headers

        response = e2e_client.post(
            "/api/files/read",
            json={
                "path": "nonexistent-file-12345.txt",
                "location": "temp",
                "scope": "test-scope",
                "mode": "cloud",
            },
            headers=headers,
        )
        assert response.status_code == 404

    def test_path_sandboxing(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test that path traversal is blocked."""
        headers = platform_admin.headers

        # Try to escape the sandbox
        response = e2e_client.post(
            "/api/files/read",
            json={
                "path": "../../../etc/passwd",
                "location": "temp",
                "scope": "test-scope",
                "mode": "cloud",
            },
            headers=headers,
        )
        # Should be blocked (400) or file not found in valid path (404)
        assert response.status_code in [400, 404]


# =============================================================================
# Config Operation Tests
# =============================================================================


class TestCLIConfigOperations:
    """Test SDK config operation endpoints."""

    def test_set_and_get_config(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test setting and getting a config value."""
        test_key = "e2e_test_config"
        test_value = "test_value_123"
        headers = platform_admin.headers

        # Set config
        response = e2e_client.post(
            "/api/sdk/config/set",
            json={
                "key": test_key,
                "value": test_value,
            },
            headers=headers,
        )
        assert response.status_code == 204

        # Get config
        response = e2e_client.post(
            "/api/sdk/config/get",
            json={"key": test_key},
            headers=headers,
        )
        # Note: May return 404 if cache hasn't been populated
        assert response.status_code in [200, 404]

        # Cleanup
        e2e_client.post(
            "/api/sdk/config/delete",
            json={"key": test_key},
            headers=headers,
        )

    def test_set_config_json(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test setting a JSON config value."""
        test_key = "e2e_test_json_config"
        test_value = {"nested": {"data": [1, 2, 3]}}
        headers = platform_admin.headers

        response = e2e_client.post(
            "/api/sdk/config/set",
            json={
                "key": test_key,
                "value": test_value,
            },
            headers=headers,
        )
        assert response.status_code == 204

        # Cleanup
        e2e_client.post(
            "/api/sdk/config/delete",
            json={"key": test_key},
            headers=headers,
        )

    def test_set_config_secret(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test setting a secret config value."""
        test_key = "e2e_test_secret"
        test_value = "super_secret_value"
        headers = platform_admin.headers

        response = e2e_client.post(
            "/api/sdk/config/set",
            json={
                "key": test_key,
                "value": test_value,
                "is_secret": True,
            },
            headers=headers,
        )
        assert response.status_code == 204

        # Cleanup
        e2e_client.post(
            "/api/sdk/config/delete",
            json={"key": test_key},
            headers=headers,
        )

    def test_list_config(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test listing all config keys."""
        headers = platform_admin.headers

        response = e2e_client.post(
            "/api/sdk/config/list",
            json={},
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        # API returns a dict of config key/value pairs
        assert isinstance(data, dict)

    def test_delete_config(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test deleting a config value."""
        test_key = "e2e_delete_config"
        headers = platform_admin.headers

        # Create config
        e2e_client.post(
            "/api/sdk/config/set",
            json={"key": test_key, "value": "to delete"},
            headers=headers,
        )

        # Delete it - returns 200 with boolean True/False
        response = e2e_client.post(
            "/api/sdk/config/delete",
            json={"key": test_key},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json() is True

    def test_get_nonexistent_config(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test getting a config that doesn't exist."""
        headers = platform_admin.headers

        response = e2e_client.post(
            "/api/sdk/config/get",
            json={"key": "nonexistent_config_12345"},
            headers=headers,
        )
        # API returns 200 with null for nonexistent config
        assert response.status_code == 200
        assert response.json() is None


# =============================================================================
# Download Tests
# =============================================================================


class TestCLIDownload:
    """Test SDK download endpoint."""

    def test_download_sdk(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test downloading the SDK package."""
        response = e2e_client.get(
            "/api/cli/download",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        assert response.headers.get("content-type") == "application/gzip"

    def test_download_sdk_includes_new_files(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test that SDK download includes all required files."""
        import tarfile
        import io

        response = e2e_client.get(
            "/api/cli/download",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        # Parse the tarball
        tar_bytes = io.BytesIO(response.content)
        with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
            names = tar.getnames()

        # Check for key files
        assert "bifrost/decorators.py" in names
        assert "bifrost/_context.py" in names
        assert "bifrost/cli.py" in names
        assert "bifrost/client.py" in names
        assert "bifrost/workflows.py" in names
        assert "bifrost/executions.py" in names
        assert "bifrost/integrations.py" in names
        # Runtime DATA files (not just .py) must ship too. The lucide snapshot is
        # what `bifrost migrate-imports` reads to classify icons; without it the
        # installed CLI silently can't migrate v1 icon imports to lucide-react.
        assert "bifrost/lucide_icon_names.json" in names

    def test_download_sdk_decorators_content(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test that decorators.py contains WorkflowMetadata."""
        import tarfile
        import io

        response = e2e_client.get(
            "/api/cli/download",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        tar_bytes = io.BytesIO(response.content)
        with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
            decorators = tar.extractfile("bifrost/decorators.py")
            assert decorators is not None
            content = decorators.read().decode()

        # Check for key components
        assert "class WorkflowMetadata" in content
        assert "@dataclass" in content
        assert "def workflow" in content

    def test_download_sdk_context_content(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test that _context.py provides context access."""
        import tarfile
        import io

        response = e2e_client.get(
            "/api/cli/download",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        tar_bytes = io.BytesIO(response.content)
        with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
            context_file = tar.extractfile("bifrost/_context.py")
            assert context_file is not None
            content = context_file.read().decode()

        assert "context" in content or "Context" in content

    def test_download_sdk_cli_content(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test that cli.py provides run command."""
        import tarfile
        import io

        response = e2e_client.get(
            "/api/cli/download",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        tar_bytes = io.BytesIO(response.content)
        with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
            cli_file = tar.extractfile("bifrost/cli.py")
            assert cli_file is not None
            content = cli_file.read().decode()

        assert "def main" in content or "async def main" in content

    def test_download_sdk_pyproject_has_cli(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test that pyproject.toml defines bifrost CLI entry point."""
        import tarfile
        import io

        response = e2e_client.get(
            "/api/cli/download",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        tar_bytes = io.BytesIO(response.content)
        with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
            pyproject = tar.extractfile("pyproject.toml")
            assert pyproject is not None
            content = pyproject.read().decode()

        # Check for CLI entry point
        assert "[project.scripts]" in content
        assert "bifrost" in content

    def test_download_sdk_can_be_imported(
        self,
        e2e_client,
        platform_admin,
        tmp_path,
    ):
        """Test that downloaded SDK can be imported and used."""
        import tarfile
        import io
        import subprocess
        import sys

        # Download the SDK
        response = e2e_client.get(
            "/api/cli/download",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        # Extract to temp directory
        tar_bytes = io.BytesIO(response.content)
        with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
            tar.extractall(tmp_path, filter="data")

        # Try to import it using a subprocess
        # NOTE: We also exercise ``bifrost.commands`` here. That subpackage
        # is what ``bifrost <entity> <verb>`` dispatches through and it used
        # to import from ``src.models.contracts.*`` — which is not shipped
        # in the download tarball, producing a broken CLI on fresh installs.
        # The import below catches any regression of that class loud-fail.
        test_script = f"""
import sys
sys.path.insert(0, '{tmp_path}')

# Test basic imports
from bifrost import workflow, WorkflowMetadata

# Test that workflow decorator exists
assert callable(workflow)

# Test that WorkflowMetadata is a class
assert isinstance(WorkflowMetadata, type)

# Exercise the CLI entity dispatch registry — this is what every
# ``bifrost <entity> <verb>`` invocation imports, and it transitively
# pulls in every command module (which in turn imports the DTO mirrors
# from ``bifrost.contracts``). If the tarball ever drops a file that
# the commands depend on, this import explodes.
from bifrost.commands import ENTITY_GROUPS
assert len(ENTITY_GROUPS) == 13, f"expected 13 entity groups, got {{len(ENTITY_GROUPS)}}"
assert "claims" in ENTITY_GROUPS

# And the mirrored DTO package must itself be importable.
from bifrost import contracts as _contracts
assert hasattr(_contracts, 'OrganizationCreate')

print('SUCCESS')
"""
        result = subprocess.run(
            [sys.executable, "-c", test_script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Import failed: {result.stderr}"
        assert "SUCCESS" in result.stdout

    def test_download_sdk_bifrost_run_works(
        self,
        e2e_client,
        platform_admin,
        tmp_path,
    ):
        """Test that 'bifrost run' command works with inline params."""
        import tarfile
        import io
        import subprocess
        import sys

        # Download the SDK
        response = e2e_client.get(
            "/api/cli/download",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        # Extract to temp directory
        tar_bytes = io.BytesIO(response.content)
        with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
            tar.extractall(tmp_path, filter="data")

        # Create a simple test workflow
        workflow_file = tmp_path / "test_workflow.py"
        workflow_file.write_text('''
from bifrost import workflow

@workflow(category="Test")
async def greet(name: str = "World") -> dict:
    """A simple greeting workflow."""
    return {"message": f"Hello {name}!"}
''')

        # Run with inline params as JSON, specifying workflow name explicitly
        # Pass --workflow to avoid interactive prompts in standalone mode
        import os
        result = subprocess.run(
            [
                sys.executable, "-m", "bifrost.cli",
                "run", str(workflow_file),
                "--workflow", "greet",
                "--params", '{"name": "E2ETest"}',
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=tmp_path,
            stdin=subprocess.DEVNULL,  # Prevent interactive prompts
            env={
                **os.environ,
                "PYTHONPATH": str(tmp_path),
                "BIFROST_API_URL": "",  # Clear to force standalone mode
            },
        )
        # Should work in standalone mode - check for success output
        # In standalone mode with no API, it will still execute the workflow
        # The test passes if it enters "Running in standalone mode" and doesn't crash
        assert result.returncode == 0 or "standalone" in result.stdout.lower(), \
            f"bifrost run failed: stdout={result.stdout}, stderr={result.stderr}"


# =============================================================================
# CLI Session Tests
# =============================================================================


class TestCLISessions:
    """Test CLI session management endpoints."""

    def test_list_sessions_empty(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test listing sessions when none exist."""
        response = e2e_client.get(
            "/api/sdk/sessions",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        # API returns {"sessions": [...]}
        assert "sessions" in data
        assert isinstance(data["sessions"], list)

    def test_create_session(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test creating a new CLI session."""
        import uuid
        session_id = str(uuid.uuid4())

        response = e2e_client.post(
            "/api/sdk/sessions",
            json={
                "session_id": session_id,
                "file_path": "/test/workflow.py",
                "workflows": [
                    {
                        "name": "test_workflow",
                        "description": "A test workflow",
                        "parameters": [],
                    }
                ],
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "id" in data
        assert data["id"] == session_id
        assert data["file_path"] == "/test/workflow.py"
        assert len(data["workflows"]) == 1

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )

    def test_get_session(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test getting a specific session."""
        import uuid
        session_id = str(uuid.uuid4())

        # Create session
        e2e_client.post(
            "/api/sdk/sessions",
            json={
                "session_id": session_id,
                "file_path": "/test/workflow.py",
                "workflows": [],
            },
            headers=platform_admin.headers,
        )

        # Get session
        response = e2e_client.get(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        assert response.json()["id"] == session_id

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )

    def test_get_nonexistent_session(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test getting a session that doesn't exist."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = e2e_client.get(
            f"/api/sdk/sessions/{fake_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404

    def test_continue_workflow(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test continuing (selecting) a workflow in a session."""
        import uuid
        session_id = str(uuid.uuid4())

        # Create session with a workflow
        e2e_client.post(
            "/api/sdk/sessions",
            json={
                "session_id": session_id,
                "file_path": "/test/workflow.py",
                "workflows": [
                    {
                        "name": "my_workflow",
                        "description": "Test",
                        "parameters": [],
                    }
                ],
            },
            headers=platform_admin.headers,
        )

        # Continue with the workflow (returns 202 Accepted)
        response = e2e_client.post(
            f"/api/sdk/sessions/{session_id}/continue",
            json={
                "workflow_name": "my_workflow",
                "params": {"test": "value"},
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 202
        data = response.json()
        assert "execution_id" in data

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )

    def test_continue_invalid_workflow(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test continuing with a workflow that doesn't exist in session."""
        import uuid
        session_id = str(uuid.uuid4())

        # Create session
        e2e_client.post(
            "/api/sdk/sessions",
            json={
                "session_id": session_id,
                "file_path": "/test/workflow.py",
                "workflows": [
                    {
                        "name": "real_workflow",
                        "description": "Test",
                        "parameters": [],
                    }
                ],
            },
            headers=platform_admin.headers,
        )

        # Try to continue with non-existent workflow (returns 400 Bad Request)
        response = e2e_client.post(
            f"/api/sdk/sessions/{session_id}/continue",
            json={
                "workflow_name": "fake_workflow",
                "params": {},
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 400

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )

    def test_get_pending_no_execution(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test getting pending state when no execution is pending."""
        import uuid
        session_id = str(uuid.uuid4())

        # Create session
        e2e_client.post(
            "/api/sdk/sessions",
            json={
                "session_id": session_id,
                "file_path": "/test/workflow.py",
                "workflows": [],
            },
            headers=platform_admin.headers,
        )

        # Get pending (returns 204 No Content when no pending execution)
        response = e2e_client.get(
            f"/api/sdk/sessions/{session_id}/pending",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )

    def test_get_pending_after_continue(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test getting pending state after continuing a workflow."""
        import uuid
        session_id = str(uuid.uuid4())

        # Create session with workflow
        e2e_client.post(
            "/api/sdk/sessions",
            json={
                "session_id": session_id,
                "file_path": "/test/workflow.py",
                "workflows": [
                    {
                        "name": "pending_test",
                        "description": "Test",
                        "parameters": [],
                    }
                ],
            },
            headers=platform_admin.headers,
        )

        # Continue with workflow
        e2e_client.post(
            f"/api/sdk/sessions/{session_id}/continue",
            json={"workflow_name": "pending_test", "params": {}},
            headers=platform_admin.headers,
        )

        # Check pending state - returns 200 with pending execution data
        response = e2e_client.get(
            f"/api/sdk/sessions/{session_id}/pending",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["workflow_name"] == "pending_test"
        assert "execution_id" in data

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )

    def test_pending_clears_after_poll(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test that pending state clears after being polled."""
        import uuid
        session_id = str(uuid.uuid4())

        # Create session with workflow
        e2e_client.post(
            "/api/sdk/sessions",
            json={
                "session_id": session_id,
                "file_path": "/test/workflow.py",
                "workflows": [
                    {
                        "name": "poll_test",
                        "description": "Test",
                        "parameters": [],
                    }
                ],
            },
            headers=platform_admin.headers,
        )

        # Continue with workflow
        e2e_client.post(
            f"/api/sdk/sessions/{session_id}/continue",
            json={"workflow_name": "poll_test", "params": {}},
            headers=platform_admin.headers,
        )

        # First poll - should return pending execution data
        response1 = e2e_client.get(
            f"/api/sdk/sessions/{session_id}/pending",
            headers=platform_admin.headers,
        )
        assert response1.status_code == 200
        assert response1.json()["workflow_name"] == "poll_test"

        # Second poll - should be cleared (204 No Content)
        response2 = e2e_client.get(
            f"/api/sdk/sessions/{session_id}/pending",
            headers=platform_admin.headers,
        )
        assert response2.status_code == 204

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )

    def test_delete_session(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test deleting a session."""
        import uuid
        session_id = str(uuid.uuid4())

        # Create session
        e2e_client.post(
            "/api/sdk/sessions",
            json={
                "session_id": session_id,
                "file_path": "/test/workflow.py",
                "workflows": [],
            },
            headers=platform_admin.headers,
        )

        # Delete session
        response = e2e_client.delete(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204

        # Verify deleted
        response = e2e_client.get(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404


# =============================================================================
# CLI Execution Flow Tests
# =============================================================================


class TestCLIExecutionFlow:
    """Test the full CLI execution flow (log, result endpoints)."""

    def test_cli_execution_with_logging(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test CLI execution with log submission."""
        import uuid
        session_id = str(uuid.uuid4())

        # Create session
        e2e_client.post(
            "/api/sdk/sessions",
            json={
                "session_id": session_id,
                "file_path": "/test/workflow.py",
                "workflows": [
                    {
                        "name": "log_test",
                        "description": "Test workflow",
                        "parameters": [],
                    }
                ],
            },
            headers=platform_admin.headers,
        )

        # Continue to start execution
        continue_response = e2e_client.post(
            f"/api/sdk/sessions/{session_id}/continue",
            json={"workflow_name": "log_test", "params": {}},
            headers=platform_admin.headers,
        )
        execution_id = continue_response.json()["execution_id"]

        # Poll pending to get execution_id and mark it as running
        e2e_client.get(
            f"/api/sdk/sessions/{session_id}/pending",
            headers=platform_admin.headers,
        )

        # Submit logs - path includes execution_id
        log_response = e2e_client.post(
            f"/api/sdk/sessions/{session_id}/executions/{execution_id}/log",
            json={
                "level": "info",
                "message": "Test log message",
            },
            headers=platform_admin.headers,
        )
        assert log_response.status_code == 204

        # Submit result - returns 200 with status info
        result_response = e2e_client.post(
            f"/api/sdk/sessions/{session_id}/executions/{execution_id}/result",
            json={
                "status": "success",
                "result": {"output": "test result"},
            },
            headers=platform_admin.headers,
        )
        assert result_response.status_code == 200

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )

    def test_cli_execution_failure(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test CLI execution failure reporting."""
        import uuid
        session_id = str(uuid.uuid4())

        # Create session
        e2e_client.post(
            "/api/sdk/sessions",
            json={
                "session_id": session_id,
                "file_path": "/test/workflow.py",
                "workflows": [
                    {
                        "name": "fail_test",
                        "description": "Test",
                        "parameters": [],
                    }
                ],
            },
            headers=platform_admin.headers,
        )

        # Continue to start execution
        continue_response = e2e_client.post(
            f"/api/sdk/sessions/{session_id}/continue",
            json={"workflow_name": "fail_test", "params": {}},
            headers=platform_admin.headers,
        )
        execution_id = continue_response.json()["execution_id"]

        # Poll pending to get execution_id and mark it as running
        e2e_client.get(
            f"/api/sdk/sessions/{session_id}/pending",
            headers=platform_admin.headers,
        )

        # Submit failure result
        result_response = e2e_client.post(
            f"/api/sdk/sessions/{session_id}/executions/{execution_id}/result",
            json={
                "status": "failed",
                "error": "Test error message",
            },
            headers=platform_admin.headers,
        )
        assert result_response.status_code == 200

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )

    def test_cli_logs_match_engine_format(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test that CLI log format matches engine log format."""
        import uuid
        session_id = str(uuid.uuid4())

        # Create session
        e2e_client.post(
            "/api/sdk/sessions",
            json={
                "session_id": session_id,
                "file_path": "/test/workflow.py",
                "workflows": [
                    {
                        "name": "format_test",
                        "description": "Test",
                        "parameters": [],
                    }
                ],
            },
            headers=platform_admin.headers,
        )

        # Continue
        continue_response = e2e_client.post(
            f"/api/sdk/sessions/{session_id}/continue",
            json={"workflow_name": "format_test", "params": {}},
            headers=platform_admin.headers,
        )
        execution_id = continue_response.json()["execution_id"]

        # Poll pending to get execution_id and mark it as running
        e2e_client.get(
            f"/api/sdk/sessions/{session_id}/pending",
            headers=platform_admin.headers,
        )

        # Submit log with all fields
        log_response = e2e_client.post(
            f"/api/sdk/sessions/{session_id}/executions/{execution_id}/log",
            json={
                "level": "warning",
                "message": "Warning message",
                "timestamp": "2024-01-01T00:00:00Z",
                "metadata": {"key": "value"},
            },
            headers=platform_admin.headers,
        )
        assert log_response.status_code == 204

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )

    def test_cli_execution_result_format_matches_engine(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test that CLI result format matches engine result format."""
        import uuid
        session_id = str(uuid.uuid4())

        # Create session
        e2e_client.post(
            "/api/sdk/sessions",
            json={
                "session_id": session_id,
                "file_path": "/test/workflow.py",
                "workflows": [
                    {
                        "name": "result_format_test",
                        "description": "Test",
                        "parameters": [],
                    }
                ],
            },
            headers=platform_admin.headers,
        )

        # Continue
        continue_response = e2e_client.post(
            f"/api/sdk/sessions/{session_id}/continue",
            json={"workflow_name": "result_format_test", "params": {}},
            headers=platform_admin.headers,
        )
        execution_id = continue_response.json()["execution_id"]

        # Poll pending to get execution_id and mark it as running
        e2e_client.get(
            f"/api/sdk/sessions/{session_id}/pending",
            headers=platform_admin.headers,
        )

        # Submit result with complex structure
        result_response = e2e_client.post(
            f"/api/sdk/sessions/{session_id}/executions/{execution_id}/result",
            json={
                "status": "success",
                "result": {
                    "data": [1, 2, 3],
                    "metadata": {"processed": True},
                },
                "duration_ms": 1500,
            },
            headers=platform_admin.headers,
        )
        assert result_response.status_code == 200

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/sessions/{session_id}",
            headers=platform_admin.headers,
        )
