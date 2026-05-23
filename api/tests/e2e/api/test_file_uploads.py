"""
E2E tests for file uploads (presigned URLs).

Tests the complete file upload workflow:
1. Generate presigned URL for file upload
2. Upload file to S3 via presigned URL
3. Create workflow that reads the uploaded file
4. Verify workflow can access uploaded content
"""

import httpx
import pytest

from tests.e2e.conftest import write_and_register, execute_workflow_sync


@pytest.mark.e2e
class TestFileUploads:
    """Test file upload via presigned URLs."""

    @pytest.fixture
    def test_form(self, e2e_client, platform_admin):
        """Create a test form for file uploads."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "E2E Upload Test Form",
                "description": "Form for testing file uploads",
                "workflow_id": None,
                "form_schema": {
                    "fields": [
                        {
                            "name": "document",
                            "type": "file",
                            "label": "Document",
                            "required": True,
                        },
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201, f"Create form failed: {response.text}"
        form = response.json()

        yield form

        # Cleanup
        e2e_client.delete(
            f"/api/forms/{form['id']}",
            headers=platform_admin.headers,
        )

    def test_generate_presigned_upload_url(self, e2e_client, platform_admin, test_form):
        """Generate presigned S3 URL for file upload."""
        response = e2e_client.post(
            f"/api/forms/{test_form['id']}/upload",
            headers=platform_admin.headers,
            json={
                "file_name": "test_upload.txt",
                "content_type": "text/plain",
                "file_size": 1024,
            },
        )
        assert response.status_code == 200, (
            f"Generate upload URL failed: {response.text}"
        )
        data = response.json()

        # Verify response structure
        assert "upload_url" in data, "Missing upload_url"
        assert "upload_headers" in data, "Missing upload_headers"
        assert "blob_uri" in data, "Missing blob_uri"
        assert "expires_at" in data, "Missing expires_at"
        assert "file_metadata" in data, "Missing file_metadata"
        assert data["upload_headers"]["Content-Type"] == "text/plain"

        # blob_uri is now the path relative to the uploads/ location:
        # `{form_id}/{uuid}/{filename}`. The full S3 key is
        # `uploads/{scope}/{blob_uri}` and the SDK handles that translation.
        assert "/" in data["blob_uri"], (
            f"Invalid blob_uri: {data['blob_uri']}"
        )
        assert not data["blob_uri"].startswith("uploads/"), (
            f"blob_uri should be location-relative, got: {data['blob_uri']}"
        )

        # Verify file metadata
        metadata = data["file_metadata"]
        assert metadata["name"] == "test_upload.txt"
        assert metadata["container"] == "uploads"
        assert metadata["content_type"] == "text/plain"
        assert metadata["size"] == 1024

        # Verify presigned URL looks valid
        assert "http" in data["upload_url"], "Presigned URL should be HTTP URL"

    def test_upload_file_via_presigned_url(self, e2e_client, platform_admin, test_form):
        """Upload file directly to S3 via presigned URL."""
        # Generate presigned URL
        response = e2e_client.post(
            f"/api/forms/{test_form['id']}/upload",
            headers=platform_admin.headers,
            json={
                "file_name": "test_upload.txt",
                "content_type": "text/plain",
                "file_size": 47,  # Actual size of content below
            },
        )
        assert response.status_code == 200, (
            f"Generate upload URL failed: {response.text}"
        )
        data = response.json()
        upload_url = data["upload_url"]
        upload_headers = data["upload_headers"]
        # blob_uri available in data["blob_uri"] if needed

        # Upload file content using presigned URL
        file_content = b"Test file content for E2E upload test.\nLine 2."

        # Use a separate httpx client for direct S3 upload
        with httpx.Client(timeout=30.0) as s3_client:
            response = s3_client.put(
                upload_url,
                content=file_content,
                headers=upload_headers,
            )
        # S3/Minio accepts 200, 201, or 204 for successful upload
        assert response.status_code in [200, 201, 204], (
            f"S3 upload failed with status {response.status_code}: {response.text}"
        )

    def test_workflow_can_read_uploaded_file(
        self, e2e_client, platform_admin, test_form
    ):
        """Create workflow that reads uploaded file and verify it can access the content."""
        # First, upload a file
        response = e2e_client.post(
            f"/api/forms/{test_form['id']}/upload",
            headers=platform_admin.headers,
            json={
                "file_name": "workflow_test.txt",
                "content_type": "text/plain",
                "file_size": 47,
            },
        )
        assert response.status_code == 200, (
            f"Generate upload URL failed: {response.text}"
        )
        upload_data = response.json()
        upload_url = upload_data["upload_url"]
        upload_headers = upload_data["upload_headers"]
        # blob_uri is the path relative to the `uploads` location — pass it
        # straight to `files.read(..., location="uploads")` and the SDK adds
        # the `uploads/{scope}/` prefix.
        file_path_for_workflow = upload_data["blob_uri"]

        # Upload file content
        file_content = b"Test file content for E2E upload test.\nLine 2."
        with httpx.Client(timeout=30.0) as s3_client:
            s3_response = s3_client.put(
                upload_url,
                content=file_content,
                headers=upload_headers,
            )
        assert s3_response.status_code in [200, 201, 204], (
            f"S3 upload failed: {s3_response.status_code}"
        )

        # Create workflow that reads the file
        workflow_content = '''"""E2E File Read Test Workflow"""
from bifrost import workflow, files

@workflow(
    name="e2e_file_read_workflow",
    description="Reads uploaded file from S3",
    execution_mode="sync"
)
async def e2e_file_read_workflow(file_path: str):
    """Read a file from S3 and return contents."""
    # Use location="uploads" to read from S3 bucket
    # file_path should NOT include the "uploads/" prefix as the location handles that
    content = await files.read(file_path, location="uploads")
    return {
        "file_path": file_path,
        "content": content,
        "length": len(content) if content else 0,
    }
'''
        result = write_and_register(
            e2e_client,
            platform_admin.headers,
            "e2e_file_read_workflow.py",
            workflow_content,
            "e2e_file_read_workflow",
        )
        workflow_id = result["id"]

        result = execute_workflow_sync(
            e2e_client,
            platform_admin.headers,
            workflow_id,
            {"file_path": file_path_for_workflow},
            max_wait=60.0,
        )

        # Verify the workflow could read the file content
        assert "result" in result, "Missing result in execution response"
        workflow_result = result["result"]
        assert isinstance(workflow_result, dict), (
            f"Result should be a dict, got {type(workflow_result)}: {workflow_result}"
        )
        assert "content" in workflow_result, "Missing content in workflow result"
        assert workflow_result["content"] == file_content.decode("utf-8"), (
            f"File content mismatch: expected {file_content}, got {workflow_result['content']}"
        )
        assert workflow_result["length"] == len(file_content), (
            f"File length mismatch: expected {len(file_content)}, got {workflow_result['length']}"
        )
        assert workflow_result["file_path"] == file_path_for_workflow, (
            f"File path mismatch: expected {file_path_for_workflow}, got {workflow_result['file_path']}"
        )

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=e2e_file_read_workflow.py",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestFileUploadAccessControl:
    """Test file upload access control."""

    @pytest.fixture
    def upload_form(self, e2e_client, platform_admin):
        """Create a form with file upload field."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Access Control Form",
                "description": "Form for testing upload access",
                "workflow_id": None,
                "form_schema": {
                    "fields": [
                        {
                            "name": "file",
                            "type": "file",
                            "label": "File",
                            "required": True,
                        },
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201
        form = response.json()
        yield form
        e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)

    def test_org_user_can_upload_to_authenticated_form(
        self, e2e_client, platform_admin, org1_user, upload_form
    ):
        """Org user can generate upload URL for authenticated form."""
        response = e2e_client.post(
            f"/api/forms/{upload_form['id']}/upload",
            headers=org1_user.headers,
            json={
                "file_name": "user_file.txt",
                "content_type": "text/plain",
                "file_size": 100,
            },
        )
        assert response.status_code == 200, (
            f"Upload URL generation failed: {response.text}"
        )
        data = response.json()
        assert "upload_url" in data
        assert data["upload_headers"]["Content-Type"] == "text/plain"
        assert "blob_uri" in data

    def test_anonymous_cannot_generate_upload_url(self, e2e_client, upload_form):
        """Anonymous user cannot generate upload URL."""
        # Use a fresh client with no cookies to simulate anonymous access
        with httpx.Client(
            base_url=str(e2e_client.base_url), timeout=60.0
        ) as anon_client:
            response = anon_client.post(
                f"/api/forms/{upload_form['id']}/upload",
                json={
                    "file_name": "user_file.txt",
                    "content_type": "text/plain",
                    "file_size": 100,
                },
            )
        # Should get 401 or 403 depending on form access level
        assert response.status_code in [401, 403], (
            f"Should deny access to anonymous user: {response.status_code}"
        )


@pytest.mark.e2e
class TestFileUploadEdgeCases:
    """Test file upload edge cases and error handling."""

    @pytest.fixture
    def upload_form(self, e2e_client, platform_admin):
        """Create a form with file upload field."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Edge Case Form",
                "description": "Form for testing edge cases",
                "workflow_id": None,
                "form_schema": {
                    "fields": [
                        {
                            "name": "file",
                            "type": "file",
                            "label": "File",
                            "required": True,
                        },
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201
        form = response.json()
        yield form
        e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)

    def test_upload_nonexistent_form(self, e2e_client, platform_admin):
        """Cannot generate upload URL for non-existent form."""
        response = e2e_client.post(
            "/api/forms/00000000-0000-0000-0000-000000000000/upload",
            headers=platform_admin.headers,
            json={
                "file_name": "test.txt",
                "content_type": "text/plain",
                "file_size": 100,
            },
        )
        assert response.status_code == 404, "Should return 404 for non-existent form"

    def test_filename_sanitization(self, e2e_client, platform_admin, upload_form):
        """Filename is sanitized for safe storage."""
        response = e2e_client.post(
            f"/api/forms/{upload_form['id']}/upload",
            headers=platform_admin.headers,
            json={
                "file_name": "../../../etc/passwd",  # Path traversal attempt
                "content_type": "text/plain",
                "file_size": 100,
            },
        )
        assert response.status_code == 200, (
            f"Upload URL generation failed: {response.text}"
        )
        data = response.json()

        # Verify filename doesn't allow path traversal
        blob_uri = data["blob_uri"]
        assert "../" not in blob_uri, "Path traversal should be sanitized"
        # The API should sanitize the filename - path components get flattened
        # The result should not allow escaping the uploads prefix

    def test_large_file_size(self, e2e_client, platform_admin, upload_form):
        """Can generate upload URL for large file."""
        response = e2e_client.post(
            f"/api/forms/{upload_form['id']}/upload",
            headers=platform_admin.headers,
            json={
                "file_name": "large_file.bin",
                "content_type": "application/octet-stream",
                "file_size": 5 * 1024 * 1024 * 1024,  # 5 GB
            },
        )
        assert response.status_code == 200, (
            f"Upload URL generation failed: {response.text}"
        )
        data = response.json()
        assert "upload_url" in data
