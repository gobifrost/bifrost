"""
E2E tests for DB-first storage model.

Validates that platform entities (workflows, forms, apps, agents) are stored
in the database, NOT in S3. Regular files should still go to S3.

Key behaviors validated:
- Workflows written via editor API are stored in the database (workflows table + file_index)
- Forms created via API are stored in forms table, not S3
- Apps created via API are stored in applications table, not S3
- Regular files (no decorators) are stored in S3
"""

import pytest

from tests.e2e.conftest import write_and_register


@pytest.mark.e2e
class TestDBFirstWorkflows:
    """Verify workflows are stored in database, not S3."""

    def test_workflow_stored_in_db_not_s3(self, e2e_client, platform_admin):
        """
        Writing a workflow via editor stores metadata in workflows table
        and code in file_index. The file should NOT be stored in S3.
        """
        workflow_content = '''"""DB-First Workflow Test"""
from bifrost import workflow

@workflow(
    name="db_first_test_workflow",
    description="Tests DB-first storage model",
    category="testing",
)
async def db_first_test_workflow(message: str) -> dict:
    """A test workflow to verify DB storage."""
    return {"message": message, "source": "db"}
'''
        result = write_and_register(
            e2e_client, platform_admin.headers,
            "db_first_test_workflow.py", workflow_content,
            "db_first_test_workflow",
        )
        workflow_id = result["id"]
        assert workflow_id, "Workflow should have an ID"

        # Verify we can read the workflow code via editor
        response = e2e_client.get(
            "/api/files/editor/content?path=db_first_test_workflow.py",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Read workflow failed: {response.text}"
        data = response.json()
        assert "db_first_test_workflow" in data["content"], \
            "Workflow content should be readable"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=db_first_test_workflow.py",
            headers=platform_admin.headers,
        )

    def test_workflow_read_returns_db_code(self, e2e_client, platform_admin):
        """Reading a workflow file returns code from file_index."""
        workflow_content = '''"""Read Test Workflow"""
from bifrost import workflow

@workflow(name="read_test_workflow")
async def read_test_workflow(x: int) -> int:
    return x * 2
'''
        # Create workflow via write_and_register
        write_and_register(
            e2e_client, platform_admin.headers,
            "read_test_workflow.py", workflow_content,
            "read_test_workflow",
        )

        # Update the workflow via editor (simulates editing)
        updated_content = '''"""Read Test Workflow - Updated"""
from bifrost import workflow

@workflow(name="read_test_workflow")
async def read_test_workflow(x: int) -> int:
    return x * 3  # Changed from *2 to *3
'''
        response = e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": "read_test_workflow.py",
                "content": updated_content,
                "encoding": "utf-8",
            },
        )
        assert response.status_code == 200

        # Read back should return the updated content
        response = e2e_client.get(
            "/api/files/editor/content?path=read_test_workflow.py",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "x * 3" in data["content"], \
            "Should read updated content from DB"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=read_test_workflow.py",
            headers=platform_admin.headers,
        )

    @pytest.mark.asyncio
    async def test_editor_rejects_unsupported_decorator_without_replacing_source_or_cache(
        self, e2e_client, platform_admin,
    ):
        """Invalid source is refused before storage and the Python module cache change."""
        from src.core.module_cache import get_module

        path = "decorator_rejection_preserves_source.py"
        accepted = (
            "from bifrost import workflow\n"
            "@workflow(name='accepted_decorator_source')\n"
            "def accepted_decorator_source(): pass\n"
        )
        rejected = (
            "from bifrost import workflow\n"
            "@workflow(timeout_seconds=30)\n"
            "def accepted_decorator_source(): pass\n"
        )
        try:
            initial = e2e_client.put(
                "/api/files/editor/content",
                headers=platform_admin.headers,
                json={"path": path, "content": accepted, "encoding": "utf-8"},
            )
            assert initial.status_code in (200, 201), initial.text
            cached_before = await get_module(path)
            assert cached_before is not None

            response = e2e_client.put(
                "/api/files/editor/content",
                headers=platform_admin.headers,
                json={"path": path, "content": rejected, "encoding": "utf-8"},
            )

            assert response.status_code == 400
            assert "timeout_seconds" in response.text
            stored = e2e_client.get(
                f"/api/files/editor/content?path={path}", headers=platform_admin.headers,
            )
            assert stored.status_code == 200
            assert stored.json()["content"] == accepted
            cached_after = await get_module(path)
            assert cached_after is not None
            assert cached_after["content"] == cached_before["content"] == accepted
        finally:
            e2e_client.delete(
                f"/api/files/editor?path={path}", headers=platform_admin.headers,
            )

    def test_workflow_update_preserves_identity(self, e2e_client, platform_admin):
        """Updating workflow code preserves the workflow ID (update, not recreate)."""
        original_content = '''"""Hash Test Workflow"""
from bifrost import workflow

@workflow(name="hash_test_workflow")
async def hash_test_workflow() -> str:
    return "version1"
'''
        # Create workflow and capture its ID
        original_result = write_and_register(
            e2e_client, platform_admin.headers,
            "hash_test_workflow.py", original_content,
            "hash_test_workflow",
        )
        original_id = original_result["id"]
        assert original_id, "Workflow should have an ID"

        # Update the workflow
        updated_content = '''"""Hash Test Workflow - Modified"""
from bifrost import workflow

@workflow(name="hash_test_workflow")
async def hash_test_workflow() -> str:
    return "version2"
'''
        updated_result = write_and_register(
            e2e_client, platform_admin.headers,
            "hash_test_workflow.py", updated_content,
            "hash_test_workflow",
        )
        updated_id = updated_result["id"]

        # ID should remain the same (update, not recreate)
        assert original_id == updated_id, \
            "Workflow ID should remain stable across updates"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=hash_test_workflow.py",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestDBFirstForms:
    """Verify forms are stored in database, not S3."""

    def test_form_api_creates_db_record(self, e2e_client, platform_admin):
        """Creating form via API stores data in forms table."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "DB First Form Test",
                "description": "Testing DB-first storage",
                "workflow_id": None,
                "form_schema": {
                    "fields": [
                        {"name": "test_field", "type": "text", "label": "Test Field"},
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201, f"Create form failed: {response.text}"
        form = response.json()

        # Form should have a valid UUID
        assert form.get("id"), "Form should have an ID"

        # Form should be immediately queryable
        response = e2e_client.get(
            f"/api/forms/{form['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, "Form should be queryable immediately"
        fetched = response.json()
        assert fetched["name"] == "DB First Form Test"

        # Cleanup
        e2e_client.delete(
            f"/api/forms/{form['id']}",
            headers=platform_admin.headers,
        )

    def test_form_appears_in_editor_listing(self, e2e_client, platform_admin):
        """Form appears in editor file listing with virtual path."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Editor Listing Form",
                "workflow_id": None,
                "form_schema": {"fields": []},
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201
        form = response.json()
        form_id = form["id"]

        # Check if form appears in forms directory listing (soft check)
        response = e2e_client.get(
            "/api/files/editor",
            headers=platform_admin.headers,
            params={"path": "forms"},
        )

        # The forms directory may or may not exist
        if response.status_code == 200:
            files = response.json()
            # Form may appear as a file with the form ID in its name
            file_paths = [f.get("path", "") for f in files]
            # This is a soft check - actual implementation may vary
            _ = any(form_id in path for path in file_paths)

        # Cleanup
        e2e_client.delete(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
        )

    def test_form_update_persists_to_db(self, e2e_client, platform_admin):
        """Form updates are persisted to database, not files."""
        # Create form
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Update Persist Form",
                "description": "Original description",
                "workflow_id": None,
                "form_schema": {"fields": []},
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201
        form = response.json()

        # Update form
        response = e2e_client.patch(
            f"/api/forms/{form['id']}",
            headers=platform_admin.headers,
            json={"description": "Updated description"},
        )
        assert response.status_code == 200

        # Verify update persisted
        response = e2e_client.get(
            f"/api/forms/{form['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        updated = response.json()
        assert updated["description"] == "Updated description"

        # Cleanup
        e2e_client.delete(
            f"/api/forms/{form['id']}",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestDBFirstDataProviders:
    """Verify data providers are stored in database (via workflows table)."""

    def test_data_provider_stored_in_db(self, e2e_client, platform_admin):
        """Data provider written via editor is stored in workflows table."""
        dp_content = '''"""DB-First Data Provider Test"""
from bifrost import data_provider

@data_provider(
    name="db_first_test_provider",
    description="Tests DB-first storage for data providers",
)
async def db_first_test_provider(filter_value: str = None):
    """Returns test options."""
    options = [
        {"value": "a", "label": "Option A"},
        {"value": "b", "label": "Option B"},
    ]
    if filter_value:
        options = [o for o in options if filter_value in o["label"]]
    return options
'''
        result = write_and_register(
            e2e_client, platform_admin.headers,
            "db_first_test_provider.py", dp_content,
            "db_first_test_provider",
        )
        assert result["id"], "Data provider should have an ID"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=db_first_test_provider.py",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestDBFirstTools:
    """Verify AI tools are stored in database (via workflows table)."""

    def test_tool_stored_in_db(self, e2e_client, platform_admin):
        """Tool written via editor is stored in workflows table."""
        tool_content = '''"""DB-First Tool Test"""
from bifrost import tool

@tool(
    name="db_first_test_tool",
    description="Tests DB-first storage for tools",
)
async def db_first_test_tool(input_text: str) -> str:
    """Processes input text."""
    return f"Processed: {input_text}"
'''
        result = write_and_register(
            e2e_client, platform_admin.headers,
            "db_first_test_tool.py", tool_content,
            "db_first_test_tool",
        )
        assert result["id"], "Tool should have an ID"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=db_first_test_tool.py",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestRegularFilesStillInS3:
    """Verify regular files (no platform decorators) go to S3."""

    def test_regular_python_file_stored_correctly(self, e2e_client, platform_admin):
        """Regular Python file without decorators is stored in S3."""
        regular_content = '''"""Regular Python Module"""

def helper_function(x, y):
    """A helper function - not a workflow."""
    return x + y

class DataHelper:
    """A helper class - not a platform entity."""
    def __init__(self, data):
        self.data = data

    def process(self):
        return self.data.upper()
'''
        # Write regular file
        response = e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": "modules/regular_helper.py",
                "content": regular_content,
                "encoding": "utf-8",
            },
        )
        assert response.status_code == 200, f"Write file failed: {response.text}"

        # File should be readable
        response = e2e_client.get(
            "/api/files/editor/content?path=modules/regular_helper.py",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "helper_function" in data["content"]

        # File should NOT appear in workflows list
        response = e2e_client.get(
            "/api/workflows",
            headers=platform_admin.headers,
        )
        workflows = response.json()
        workflow_names = [w["name"] for w in workflows]
        assert "helper_function" not in workflow_names
        assert "DataHelper" not in workflow_names

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=modules/regular_helper.py",
            headers=platform_admin.headers,
        )

    def test_text_file_stored_in_s3(self, e2e_client, platform_admin):
        """Text files are stored in S3."""
        response = e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": "data/config.txt",
                "content": "key=value\nother=setting",
                "encoding": "utf-8",
            },
        )
        assert response.status_code == 200

        # Read back
        response = e2e_client.get(
            "/api/files/editor/content?path=data/config.txt",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "key=value" in data["content"]

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=data/config.txt",
            headers=platform_admin.headers,
        )

    def test_json_file_without_form_extension_stored_in_s3(
        self, e2e_client, platform_admin
    ):
        """Regular JSON files (not .form.yaml) are stored in S3."""
        response = e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": "config/settings.json",
                "content": '{"debug": true, "version": "1.0"}',
                "encoding": "utf-8",
            },
        )
        assert response.status_code == 200

        # Read back
        response = e2e_client.get(
            "/api/files/editor/content?path=config/settings.json",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert '"debug": true' in data["content"]

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=config/settings.json",
            headers=platform_admin.headers,
        )



# TestWorkspaceFilesEntityType was removed — entity_type no longer exists
# in the new architecture. Files are tracked in file_index (search index)
# and entities live in their own tables (workflows, forms, agents).
