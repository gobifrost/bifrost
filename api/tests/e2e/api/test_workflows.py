"""
E2E tests for workflow management.

Tests workflow listing, discovery, and data providers.
"""

import pytest

from tests.e2e.conftest import write_and_register


@pytest.mark.e2e
class TestWorkflowListing:
    """Test workflow listing operations."""

    def test_list_workflows(self, e2e_client, platform_admin):
        """Platform admin can list workflows."""
        response = e2e_client.get(
            "/api/workflows",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List workflows failed: {response.text}"
        workflows = response.json()
        assert isinstance(workflows, list)

    def test_list_data_providers(self, e2e_client, platform_admin):
        """Platform admin can list data providers."""
        response = e2e_client.get(
            "/api/workflows?type=data_provider",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List data providers failed: {response.text}"
        data = response.json()
        assert isinstance(data, list) or "data_providers" in data


@pytest.mark.e2e
class TestWorkflowDiscovery:
    """Test workflow discovery operations."""

    def test_discovery_info(self, e2e_client, platform_admin):
        """Platform admin can get discovery info."""
        response = e2e_client.get(
            "/api/discovery/info",
            headers=platform_admin.headers,
        )
        # May return 200 or 404 depending on whether discovery is configured
        assert response.status_code in [200, 404], f"Discovery info failed: {response.text}"

    def test_workflow_list_pagination(self, e2e_client, platform_admin):
        """Workflow list supports pagination."""
        response = e2e_client.get(
            "/api/workflows",
            headers=platform_admin.headers,
            params={"limit": 10, "offset": 0},
        )
        assert response.status_code == 200, f"List workflows failed: {response.text}"


@pytest.mark.e2e
class TestWorkflowValidation:
    """Test workflow validation endpoint."""

    def test_validate_valid_workflow(self, e2e_client, platform_admin):
        """Validate a valid workflow file content."""
        valid_workflow = '''
"""Test workflow for validation"""

from bifrost import workflow

@workflow(
    category="testing",
    tags=["test", "validation"],
)
async def e2e_test_validation(name: str, count: int = 1) -> dict:
    """A simple test workflow for E2E validation testing."""
    return {"greeting": f"Hello, {name}!", "count": count}
'''
        response = e2e_client.post(
            "/api/workflows/validate",
            headers=platform_admin.headers,
            json={
                "path": "test_validation.py",
                "content": valid_workflow,
            },
        )
        assert response.status_code == 200, f"Validation failed: {response.text}"
        data = response.json()
        assert data["valid"] is True
        assert data["metadata"] is not None
        assert data["metadata"]["name"] == "e2e_test_validation"
        # Should have parameters extracted from function signature
        assert len(data["metadata"]["parameters"]) == 2

    def test_validate_workflow_requires_platform_admin(self, e2e_client, org1_user):
        """Validation imports the submitted module, so only platform admins may call it."""
        response = e2e_client.post(
            "/api/workflows/validate",
            headers=org1_user.headers,
            json={"path": "test_validation.py", "content": "x = 1\n"},
        )
        assert response.status_code == 403, response.text

    def test_validate_workflow_with_roi(self, e2e_client, platform_admin):
        """Validate workflow with time_saved and value fields (set via API, not decorator)."""
        workflow_with_roi = '''
"""Test workflow with ROI"""

from bifrost import workflow

@workflow(
    category="automation",
    tags=["test", "roi"],
)
async def workflow_with_roi(task: str) -> dict:
    """A workflow that saves time and provides value."""
    return {"task": task, "completed": True}
'''
        response = e2e_client.post(
            "/api/workflows/validate",
            headers=platform_admin.headers,
            json={
                "path": "test_roi.py",
                "content": workflow_with_roi,
            },
        )
        assert response.status_code == 200, f"Validation failed: {response.text}"
        data = response.json()
        assert data["valid"] is True, f"Validation should be valid. Issues: {data.get('issues', [])}"
        assert data["metadata"] is not None
        assert data["metadata"]["name"] == "workflow_with_roi"
        # time_saved and value are managed via API/UI, not decorator kwargs
        # They default to 0 during validation
        assert data["metadata"]["time_saved"] == 0
        assert data["metadata"]["value"] == 0

    def test_validate_workflow_with_syntax_error(self, e2e_client, platform_admin):
        """Validation catches syntax errors."""
        invalid_workflow = '''
"""Invalid workflow"""

def broken_workflow(
    # Missing closing paren
'''
        response = e2e_client.post(
            "/api/workflows/validate",
            headers=platform_admin.headers,
            json={
                "path": "invalid.py",
                "content": invalid_workflow,
            },
        )
        assert response.status_code == 200, f"Request failed: {response.text}"
        data = response.json()
        assert data["valid"] is False
        assert any("syntax" in issue["message"].lower() for issue in data["issues"])

    def test_validate_workflow_without_decorator(self, e2e_client, platform_admin):
        """Validation catches missing @workflow decorator."""
        no_decorator = '''
"""Workflow without decorator"""

async def not_a_workflow(name: str) -> dict:
    """No decorator means not discoverable."""
    return {"name": name}
'''
        response = e2e_client.post(
            "/api/workflows/validate",
            headers=platform_admin.headers,
            json={
                "path": "no_decorator.py",
                "content": no_decorator,
            },
        )
        assert response.status_code == 200, f"Request failed: {response.text}"
        data = response.json()
        assert data["valid"] is False
        assert any("@workflow decorator" in issue["message"] for issue in data["issues"])




@pytest.mark.e2e
class TestWorkflowDBStorage:
    """Tests verifying workflows are stored in database (DB-first model)."""

    def test_workflow_code_stored_in_db(self, e2e_client, platform_admin):
        """Workflow code is stored in workflows.code column."""
        workflow_content = '''"""DB Storage Test Workflow"""
from bifrost import workflow

@workflow(
    name="db_storage_test_workflow",
    description="Tests that code is stored in DB",
)
async def db_storage_test_workflow(x: int) -> int:
    return x * 2
'''
        result = write_and_register(
            e2e_client, platform_admin.headers,
            "db_storage_test.py", workflow_content,
            "db_storage_test_workflow",
        )
        assert result.get("id"), "Workflow should have DB-generated ID"

        # Read file back - should return code from DB
        response = e2e_client.get(
            "/api/files/editor/content?path=db_storage_test.py",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "db_storage_test_workflow" in data["content"]

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=db_storage_test.py",
            headers=platform_admin.headers,
        )

    def test_workflow_update_persists_to_db(self, e2e_client, platform_admin):
        """Workflow updates are persisted to database."""
        original_content = '''"""Original Version"""
from bifrost import workflow

@workflow(name="update_persist_workflow", description="Original")
async def update_persist_workflow() -> str:
    return "original"
'''
        # Create workflow
        original = write_and_register(
            e2e_client, platform_admin.headers,
            "update_persist_workflow.py", original_content,
            "update_persist_workflow",
        )
        original_id = original["id"]

        # Update workflow description via API (not re-registration, which is set-once)
        response = e2e_client.patch(
            f"/api/workflows/{original_id}",
            headers=platform_admin.headers,
            json={"description": "Updated description"},
        )
        assert response.status_code == 200, f"Patch failed: {response.text}"

        # Verify update persisted via workflow list
        response = e2e_client.get(
            "/api/workflows",
            headers=platform_admin.headers,
        )
        workflows = response.json()
        wf = next(
            (w for w in workflows if w["name"] == "update_persist_workflow"),
            None
        )
        assert wf is not None
        assert wf.get("description") == "Updated description", \
            "Description should be updated in DB"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=update_persist_workflow.py",
            headers=platform_admin.headers,
        )

    def test_workflow_update_accepts_documented_timeout_bounds(self, e2e_client, platform_admin):
        """Workflow updates accept the documented 0-86400 timeout range."""
        content = '''"""Timeout update test"""
from bifrost import workflow

@workflow(name="timeout_bounds_workflow", description="Timeout bounds")
async def timeout_bounds_workflow() -> str:
    return "ok"
'''
        workflow = write_and_register(
            e2e_client, platform_admin.headers,
            "timeout_bounds_workflow.py", content,
            "timeout_bounds_workflow",
        )
        workflow_id = workflow["id"]

        try:
            response = e2e_client.patch(
                f"/api/workflows/{workflow_id}",
                headers=platform_admin.headers,
                json={"timeout_seconds": 86400},
            )
            assert response.status_code == 200, f"86400 patch failed: {response.text}"
            assert response.json()["timeout_seconds"] == 86400

            response = e2e_client.patch(
                f"/api/workflows/{workflow_id}",
                headers=platform_admin.headers,
                json={"timeout_seconds": 0},
            )
            assert response.status_code == 200, f"0 patch failed: {response.text}"
            assert response.json()["timeout_seconds"] == 0
        finally:
            e2e_client.delete(
                "/api/files/editor?path=timeout_bounds_workflow.py",
                headers=platform_admin.headers,
            )

    def test_workflow_id_stable_across_updates(self, e2e_client, platform_admin):
        """Workflow ID remains stable across code updates."""
        workflow_content = '''"""Stable ID Test"""
from bifrost import workflow

@workflow(name="stable_id_workflow")
async def stable_id_workflow() -> str:
    return "v1"
'''
        # Create workflow
        result = write_and_register(
            e2e_client, platform_admin.headers,
            "stable_id_workflow.py", workflow_content,
            "stable_id_workflow",
        )
        original_id = result["id"]

        # Update multiple times
        for version in ["v2", "v3", "v4"]:
            updated = f'''"""Stable ID Test - {version}"""
from bifrost import workflow

@workflow(name="stable_id_workflow")
async def stable_id_workflow() -> str:
    return "{version}"
'''
            result = write_and_register(
                e2e_client, platform_admin.headers,
                "stable_id_workflow.py", updated,
                "stable_id_workflow",
            )

        assert result["id"] == original_id, \
            "Workflow ID should remain stable across all updates"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=stable_id_workflow.py",
            headers=platform_admin.headers,
        )

    def test_workflow_delete_removes_from_db(self, e2e_client, platform_admin):
        """Deleting workflow file removes it from database."""
        workflow_content = '''"""Delete Test Workflow"""
from bifrost import workflow

@workflow(name="delete_from_db_workflow")
async def delete_from_db_workflow() -> str:
    return "to be deleted"
'''
        # Create workflow
        write_and_register(
            e2e_client, platform_admin.headers,
            "delete_from_db_workflow.py", workflow_content,
            "delete_from_db_workflow",
        )

        # Delete file
        response = e2e_client.delete(
            "/api/files/editor?path=delete_from_db_workflow.py",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204

        # Verify removed from DB
        response = e2e_client.get(
            "/api/workflows",
            headers=platform_admin.headers,
        )
        workflows = response.json()
        assert not any(w["name"] == "delete_from_db_workflow" for w in workflows), \
            "Workflow should be removed from DB when file is deleted"
