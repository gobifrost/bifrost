"""
Contract tests for Enhanced Forms API (User Story 1 & 2)
Tests Pydantic validation for form context, query parameters, visibility, and rich components
"""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.models import (
    CreateFormRequest,
    DataProviderInputConfig,
    DataProviderInputMode,
    FileUploadRequest,
    FileUploadResponse,
    FormFieldType,
    FormSchema,
    UploadedFileMetadata,
)
# Import Pydantic models from contracts (not ORM models)
from src.models.contracts.forms import Form, FormField




class TestFileUploadAPI:
    """Test validation for FileUpload request/response models"""

    def test_valid_file_upload_request(self):
        """Test valid file upload SAS URL request"""
        request = FileUploadRequest(
            file_name="document.pdf",
            content_type="application/pdf",
            file_size=1048576  # 1 MB in bytes
        )
        assert request.file_name == "document.pdf"
        assert request.content_type == "application/pdf"
        assert request.file_size == 1048576

    def test_file_upload_request_large_file(self):
        """Test file upload request with large file (100MB)"""
        request = FileUploadRequest(
            file_name="large_video.mp4",
            content_type="video/mp4",
            file_size=104857600  # 100 MB in bytes
        )
        assert request.file_size == 104857600

    def test_file_upload_request_missing_fields(self):
        """Test that all FileUploadRequest fields are required"""
        with pytest.raises(ValidationError) as exc_info:
            FileUploadRequest(
                file_name="test.pdf"
                # Missing: content_type, file_size
            )

        errors = exc_info.value.errors()
        required_fields = {"content_type", "file_size"}
        missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}
        assert required_fields.issubset(missing_fields)

    def test_valid_file_upload_response(self):
        """Test valid file upload SAS URL response"""
        response = FileUploadResponse(
            upload_url="https://storage.blob.core.windows.net/uploads/file.pdf?sas_token=xyz",
            upload_headers={"Content-Type": "application/pdf"},
            blob_uri="https://storage.blob.core.windows.net/uploads/file.pdf",
            expires_at="2024-01-01T13:00:00Z",
            file_metadata=UploadedFileMetadata(
                name="file.pdf",
                container="uploads",
                path="file.pdf",
                content_type="application/pdf",
                size=1024
            )
        )
        assert "sas_token" in response.upload_url
        assert response.blob_uri == "https://storage.blob.core.windows.net/uploads/file.pdf"
        assert response.expires_at == "2024-01-01T13:00:00Z"
        assert response.file_metadata.name == "file.pdf"
        assert response.file_metadata.container == "uploads"

    def test_file_upload_response_missing_fields(self):
        """Test that all FileUploadResponse fields are required"""
        with pytest.raises(ValidationError) as exc_info:
            FileUploadResponse(
                upload_url="https://storage.blob.core.windows.net/uploads/file.pdf"
                # Missing: upload_headers, blob_uri, expires_at, file_metadata
            )

        errors = exc_info.value.errors()
        required_fields = {"upload_headers", "blob_uri", "expires_at", "file_metadata"}
        missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}
        assert required_fields.issubset(missing_fields)


# ==================== COMBINED INTEGRATION TESTS ====================

class TestEnhancedFormIntegration:
    """Test complete enhanced form configuration with all new features"""

    def test_form_with_all_new_field_types(self):
        """Test form schema with all new component types"""
        schema = FormSchema(
            fields=[
                FormField(
                    name="markdown_intro",
                    label="Introduction",
                    type=FormFieldType.MARKDOWN,
                    required=False,
                    content="# Welcome\nPlease fill out the form below."
                ),
                FormField(
                    name="html_banner",
                    label="Banner",
                    type=FormFieldType.HTML,
                    required=False,
                    content="<div class='banner'>Hello ${context.field.name}!</div>"
                ),
                FormField(
                    name="appointment_date",
                    label="Appointment Date",
                    type=FormFieldType.DATETIME,
                    required=True
                ),
                FormField(
                    name="priority",
                    label="Priority",
                    type=FormFieldType.RADIO,
                    required=True,
                    options=[
                        {"label": "Low", "value": "low"},
                        {"label": "High", "value": "high"}
                    ]
                ),
                FormField(
                    name="attachments",
                    label="Attachments",
                    type=FormFieldType.FILE,
                    required=False,
                    multiple=True,
                    allowed_types=["application/pdf", "image/png"],
                    max_size_mb=10
                )
            ]
        )
        assert len(schema.fields) == 5
        assert schema.fields[0].type == FormFieldType.MARKDOWN
        assert schema.fields[1].type == FormFieldType.HTML
        assert schema.fields[2].type == FormFieldType.DATETIME
        assert schema.fields[3].type == FormFieldType.RADIO
        assert schema.fields[4].type == FormFieldType.FILE

    def test_form_with_context_and_visibility(self):
        """Test form with workflow, query params, and field visibility"""
        licenses_provider_id = UUID("550e8400-e29b-41d4-a716-446655440030")
        request = CreateFormRequest(
            name="Advanced Dynamic Form",
            workflow_id="550e8400-e29b-41d4-a716-446655440000",
            form_schema=FormSchema(
                fields=[
                    FormField(
                        name="customer_type",
                        label="Customer Type",
                        type=FormFieldType.SELECT,
                        required=True
                    ),
                    FormField(
                        name="enterprise_options",
                        label="Enterprise Options",
                        type=FormFieldType.TEXT,
                        required=False,
                        visibility_expression="context.field.customer_type === 'enterprise'"
                    ),
                    FormField(
                        name="available_licenses",
                        label="Available Licenses",
                        type=FormFieldType.SELECT,
                        required=False,
                        visibility_expression="context.workflow.has_licenses === true",
                        data_provider_id=licenses_provider_id
                    )
                ]
            ),
            allowed_query_params=["customer_id", "source"]
        )
        assert request.workflow_id == "550e8400-e29b-41d4-a716-446655440000"
        assert request.allowed_query_params == ["customer_id", "source"]
        assert len(request.form_schema.fields) == 3
        assert request.form_schema.fields[1].visibility_expression == "context.field.customer_type === 'enterprise'"
        assert request.form_schema.fields[2].visibility_expression == "context.workflow.has_licenses === true"

    def test_complete_form_response_with_enhancements(self):
        """Test Form response model with all enhanced features"""
        form = Form(
            id="form-enhanced-123",
            org_id="org-456",
            name="Enhanced Form",
            description="Form with all new features",
            workflow_id="550e8400-e29b-41d4-a716-446655440000",
            form_schema=FormSchema(
                fields=[
                    FormField(
                        name="markdown_section",
                        label="Instructions",
                        type=FormFieldType.MARKDOWN,
                        required=False,
                        content="## Instructions\nPlease complete all required fields."
                    ),
                    FormField(
                        name="date_field",
                        label="Select Date",
                        type=FormFieldType.DATETIME,
                        required=True,
                        visibility_expression="context.query.requires_date === 'true'"
                    ),
                    FormField(
                        name="file_upload",
                        label="Upload Files",
                        type=FormFieldType.FILE,
                        required=False,
                        multiple=True,
                        allowed_types=["application/pdf"],
                        max_size_mb=50
                    )
                ]
            ),
            is_active=True,
            is_global=False,
            created_by="user-789",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            allowed_query_params=["customer_id", "requires_date"]
        )

        # Verify all enhanced features are preserved
        assert form.workflow_id == "550e8400-e29b-41d4-a716-446655440000"
        assert form.allowed_query_params == ["customer_id", "requires_date"]
        assert len(form.form_schema.fields) == 3

        # Verify new field types
        assert form.form_schema.fields[0].type == FormFieldType.MARKDOWN
        assert form.form_schema.fields[1].type == FormFieldType.DATETIME
        assert form.form_schema.fields[2].type == FormFieldType.FILE

        # Verify field-specific features
        assert form.form_schema.fields[0].content is not None
        assert form.form_schema.fields[1].visibility_expression is not None
        assert form.form_schema.fields[2].multiple is True
        assert form.form_schema.fields[2].allowed_types == ["application/pdf"]
        assert form.form_schema.fields[2].max_size_mb == 50


class TestDataProviderInputsValidation:
    """T020: Contract test for POST /api/forms with dataProviderInputs validation"""

    def test_form_field_with_static_data_provider_inputs(self):
        """
        Test that FormField with data_provider_id and data_provider_inputs validates correctly.

        Expected behavior:
        - data_provider_inputs is optional when data_provider_id is set
        - Each input config must have valid mode (static, fieldRef, expression)
        - Static mode requires 'value' field
        - Input keys should match data provider parameter names
        """
        # Valid form field with data provider and static inputs
        test_provider_id = UUID("550e8400-e29b-41d4-a716-446655440000")
        field = FormField(
            name="repo_selector",
            label="Select Repository",
            type=FormFieldType.SELECT,
            required=True,
            data_provider_id=test_provider_id,
            data_provider_inputs={
                "token": DataProviderInputConfig(
                    mode=DataProviderInputMode.STATIC,
                    value="ghp_test_token_12345"
                ),
                "org": DataProviderInputConfig(
                    mode=DataProviderInputMode.STATIC,
                    value="my-org"
                )
            }
        )

        assert field.data_provider_id == test_provider_id
        assert field.data_provider_inputs is not None
        assert len(field.data_provider_inputs) == 2
        assert field.data_provider_inputs["token"].mode == DataProviderInputMode.STATIC
        assert field.data_provider_inputs["token"].value == "ghp_test_token_12345"
        assert field.data_provider_inputs["org"].mode == DataProviderInputMode.STATIC
        assert field.data_provider_inputs["org"].value == "my-org"

    def test_data_provider_inputs_without_data_provider_cleared(self):
        """
        Test that data_provider_inputs is silently cleared when data_provider_id is not set.

        Expected behavior:
        - Setting data_provider_inputs without data_provider_id silently clears the inputs
          (handles the edge case where a data provider was deleted via FK SET NULL)
        """
        field = FormField(
            name="invalid_field",
            label="Invalid Field",
            type=FormFieldType.SELECT,
            required=False,
            # No data_provider_id set!
            data_provider_inputs={
                "param": DataProviderInputConfig(
                    mode=DataProviderInputMode.STATIC,
                    value="test"
                )
            }
        )

        # Validator silently clears data_provider_inputs when no data_provider_id
        assert field.data_provider_inputs is None

    def test_static_input_mode_validation(self):
        """
        Test that static mode requires 'value' field and rejects other fields.

        Expected behavior:
        - mode="static" requires 'value' to be set
        - mode="static" should reject 'field_name' or 'expression'
        """
        # Valid static mode
        config = DataProviderInputConfig(
            mode=DataProviderInputMode.STATIC,
            value="test_value"
        )
        assert config.mode == DataProviderInputMode.STATIC
        assert config.value == "test_value"
        assert config.field_name is None
        assert config.expression is None

        # Invalid: static mode without value
        with pytest.raises(ValidationError) as exc_info:
            DataProviderInputConfig(
                mode=DataProviderInputMode.STATIC
                # Missing value!
            )
        assert any("value required for static mode" in str(e) for e in exc_info.value.errors())

        # Invalid: static mode with field_name
        with pytest.raises(ValidationError) as exc_info:
            DataProviderInputConfig(
                mode=DataProviderInputMode.STATIC,
                value="test",
                field_name="other_field"  # Should not be set for static mode
            )
        assert any("only value should be set for static mode" in str(e) for e in exc_info.value.errors())

    def test_complete_form_with_data_provider_inputs(self):
        """
        Test complete form creation with data provider inputs in multiple fields.

        Expected behavior:
        - Form can have multiple fields with data_provider_inputs
        - Each field's inputs are independent
        - Form validation passes with valid data_provider_inputs
        """
        # Define test provider IDs
        github_repos_provider_id = UUID("550e8400-e29b-41d4-a716-446655440010")
        github_branches_provider_id = UUID("550e8400-e29b-41d4-a716-446655440020")

        request = CreateFormRequest(
            name="Form with Data Provider Inputs",
            workflow_id="550e8400-e29b-41d4-a716-446655440001",
            form_schema=FormSchema(
                fields=[
                    FormField(
                        name="github_token",
                        label="GitHub Token",
                        type=FormFieldType.TEXT,
                        required=True
                    ),
                    FormField(
                        name="repository",
                        label="Select Repository",
                        type=FormFieldType.SELECT,
                        required=True,
                        data_provider_id=github_repos_provider_id,
                        data_provider_inputs={
                            "token": DataProviderInputConfig(
                                mode=DataProviderInputMode.STATIC,
                                value="ghp_static_token"
                            )
                        }
                    ),
                    FormField(
                        name="branch",
                        label="Select Branch",
                        type=FormFieldType.SELECT,
                        required=True,
                        data_provider_id=github_branches_provider_id,
                        data_provider_inputs={
                            "token": DataProviderInputConfig(
                                mode=DataProviderInputMode.STATIC,
                                value="ghp_static_token"
                            ),
                            "repo": DataProviderInputConfig(
                                mode=DataProviderInputMode.STATIC,
                                value="my-org/my-repo"
                            )
                        }
                    )
                ]
            )
        )

        assert len(request.form_schema.fields) == 3

        # First field has no data provider
        assert request.form_schema.fields[0].data_provider_id is None
        assert request.form_schema.fields[0].data_provider_inputs is None

        # Second field has data provider with 1 input
        assert request.form_schema.fields[1].data_provider_id == github_repos_provider_id
        inputs1 = request.form_schema.fields[1].data_provider_inputs
        assert inputs1 is not None
        assert len(inputs1) == 1
        assert "token" in inputs1

        # Third field has data provider with 2 inputs
        assert request.form_schema.fields[2].data_provider_id == github_branches_provider_id
        inputs2 = request.form_schema.fields[2].data_provider_inputs
        assert inputs2 is not None
        assert len(inputs2) == 2
        assert "token" in inputs2
        assert "repo" in inputs2
