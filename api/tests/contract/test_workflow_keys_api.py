"""
Contract tests for Workflow Keys API (User Story 3)
Tests Pydantic validation for workflow API key generation, validation, and management
"""


import pytest
from pydantic import ValidationError



# ==================== FILE UPLOAD VALIDATION TESTS ====================

class TestFileUploadRequestValidation:
    """Test FileUploadRequest validation - only test required field checks"""

    def test_file_upload_request_missing_fields(self):
        """Test that all FileUploadRequest fields are required"""
        from src.models import FileUploadRequest

        with pytest.raises(ValidationError) as exc_info:
            FileUploadRequest(
                file_name="test.pdf"
                # Missing: content_type, file_size
            )

        errors = exc_info.value.errors()
        required_fields = {"content_type", "file_size"}
        missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}
        assert required_fields.issubset(missing_fields)


class TestFileUploadResponseValidation:
    """Test FileUploadResponse validation - only test required field checks"""

    def test_file_upload_response_missing_fields(self):
        """Test that all FileUploadResponse fields are required"""
        from src.models import FileUploadResponse

        with pytest.raises(ValidationError) as exc_info:
            FileUploadResponse(
                upload_url="https://storage.blob.core.windows.net/uploads/file.pdf"
                # Missing: upload_headers, blob_uri, expires_at
            )

        errors = exc_info.value.errors()
        required_fields = {"upload_headers", "blob_uri", "expires_at"}
        missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}
        assert required_fields.issubset(missing_fields)
