"""
Contract tests for Forms API models
Tests Pydantic validation rules for request/response models
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.models import (
    CreateFormRequest,
    FormFieldType,
    FormSchema,
)
from src.models.contracts.base import DataProviderInputMode
from src.models.contracts.forms import (
    DataProviderInputConfig,
    Form,
    FormCreate,
    FormSubmissionRequest,
    FormField,
    FormUpdate,
)
from shared.form_runtime import DEFAULT_FORM_CONFIRMATION_MARKDOWN


# Note: Models use snake_case (e.g., workflow_id, form_schema, is_global)
# This matches the OpenAPI/TypeScript schema


class TestCreateFormRequest:
    """Test validation for CreateFormRequest model"""

    @pytest.mark.parametrize("missing_field", ["name", "workflow_id", "form_schema"])
    def test_missing_required_field(self, missing_field):
        """Test that each required field is enforced"""
        fields = {
            "name": "Test Form",
            "workflow_id": "00000000-0000-0000-0000-000000000001",
            "form_schema": FormSchema(fields=[]),
        }
        del fields[missing_field]
        with pytest.raises(ValidationError) as exc_info:
            CreateFormRequest(**fields)

        errors = exc_info.value.errors()
        assert any(e["loc"] == (missing_field,) and e["type"] == "missing" for e in errors)


class TestFormConfirmationContract:
    def test_create_defaults_to_standard_confirmation(self):
        request = FormCreate(name="Test", form_schema={"fields": []})

        assert request.confirmation_markdown == DEFAULT_FORM_CONFIRMATION_MARKDOWN

    def test_update_accepts_markdown_at_limit(self):
        request = FormUpdate(confirmation_markdown="x" * 20_000)

        assert len(request.confirmation_markdown or "") == 20_000

    def test_update_rejects_markdown_over_limit(self):
        with pytest.raises(ValidationError):
            FormUpdate(confirmation_markdown="x" * 20_001)


class TestFormSchema:
    """Test validation for FormSchema model"""

    def test_missing_fields_array(self):
        """Test that fields array is required"""
        with pytest.raises(ValidationError) as exc_info:
            FormSchema()

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("fields",) and e["type"] == "missing" for e in errors)


class TestFormField:
    """Test validation for FormField model"""

    def test_missing_required_name(self):
        """Test that name is required"""
        with pytest.raises(ValidationError) as exc_info:
            FormField(
                label="Test",
                type=FormFieldType.TEXT
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("name",) and e["type"] == "missing" for e in errors)

    def test_missing_required_label_for_input_fields(self):
        """Test that label is required for input fields (text, select, etc.)"""
        with pytest.raises(ValidationError) as exc_info:
            FormField(
                name="test",
                type=FormFieldType.TEXT
            )

        errors = exc_info.value.errors()
        # Now raises a value_error from model_validator (not a missing field error)
        assert any("label is required" in str(e.get("msg", "")) for e in errors)

    def test_label_not_required_for_markdown(self):
        """Test that label is NOT required for markdown display fields"""
        # Markdown fields use 'content' instead of 'label'
        field = FormField(
            name="info",
            type=FormFieldType.MARKDOWN,
            content="## Welcome\n\nThis is markdown content."
        )
        assert field.label is None
        assert field.content == "## Welcome\n\nThis is markdown content."

    def test_content_required_for_markdown(self):
        """Test that content is required for markdown fields"""
        with pytest.raises(ValidationError) as exc_info:
            FormField(
                name="info",
                type=FormFieldType.MARKDOWN
            )

        errors = exc_info.value.errors()
        assert any("content is required" in str(e.get("msg", "")) for e in errors)

    def test_missing_required_type(self):
        """Test that type is required"""
        with pytest.raises(ValidationError) as exc_info:
            FormField(
                name="test",
                label="Test"
            )

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("type",) and e["type"] == "missing" for e in errors)

    def test_invalid_field_type(self):
        """Test that type must be a valid enum value"""
        with pytest.raises(ValidationError) as exc_info:
            FormField(
                name="test",
                label="Test",
                type="invalid_type"
            )

        errors = exc_info.value.errors()
        assert any("type" in str(e) for e in errors)

    def test_multi_select_with_static_options_is_valid(self):
        """Multi-select validates the same way select does (static options)."""
        field = FormField(
            name="tags",
            label="Tags",
            type=FormFieldType.MULTI_SELECT,
            options=[
                {"label": "Urgent", "value": "urgent"},
                {"label": "Billing", "value": "billing"},
            ],
        )
        assert field.type == FormFieldType.MULTI_SELECT
        assert field.options is not None
        assert len(field.options) == 2

    def test_multi_select_with_data_provider_is_valid(self):
        """Multi-select with a data provider validates (parity with select)."""
        field = FormField(
            name="mailboxes",
            label="Mailboxes",
            type=FormFieldType.MULTI_SELECT,
            data_provider_id=UUID("00000000-0000-0000-0000-000000000123"),
            data_provider_inputs={
                "org_id": DataProviderInputConfig(
                    mode=DataProviderInputMode.EXPRESSION,
                    expression="context.field.org_id",
                ),
            },
        )
        assert field.data_provider_id is not None
        assert field.data_provider_inputs is not None

    def test_multi_select_default_value_comma_list_is_trimmed(self):
        """Multi-select default_value is trimmed and empty entries dropped."""
        field = FormField(
            name="tags",
            label="Tags",
            type=FormFieldType.MULTI_SELECT,
            default_value="a, b ,  ,c",
        )
        assert field.default_value == "a,b,c"

    def test_multi_select_default_value_all_empty_becomes_none(self):
        """Multi-select default_value of only empty/whitespace entries becomes None."""
        field = FormField(
            name="tags",
            label="Tags",
            type=FormFieldType.MULTI_SELECT,
            default_value=" , , ",
        )
        assert field.default_value is None


class TestFormResponse:
    """Test Form response model structure"""

    def test_form_missing_required_fields(self):
        """Test that all required fields must be present"""
        with pytest.raises(ValidationError) as exc_info:
            Form(
                id="form-123",
                org_id="org-456",
                name="Test Form"
                # Missing: form_schema, created_by, created_at, updated_at
            )

        errors = exc_info.value.errors()
        required_fields = {"form_schema", "created_by", "created_at", "updated_at"}
        missing_fields = {e["loc"][0] for e in errors if e["type"] == "missing"}
        assert required_fields.issubset(missing_fields)

    def test_form_json_serialization(self):
        """Test that form can be serialized to JSON mode"""
        form = Form(
            id="form-123",
            org_id="org-456",
            name="Test Form",
            workflow_id="00000000-0000-0000-0000-000000000001",
            form_schema=FormSchema(fields=[]),
            is_active=True,
            is_global=False,
            created_by="user-789",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )

        form_dict = form.model_dump(mode="json")
        assert isinstance(form_dict["created_at"], str)  # datetime -> ISO string
        assert isinstance(form_dict["updated_at"], str)  # datetime -> ISO string


# ==========================================================================
# FormSubmissionRequest scheduling contract
# ==========================================================================


def _future(seconds: int = 60) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


class TestFormSubmissionRequestScheduling:
    """Mirror of the WorkflowExecutionRequest scheduling contract."""

    def test_accepts_scheduled_at_alone(self):
        req = FormSubmissionRequest(scheduled_at=_future(120))
        assert req.scheduled_at is not None
        assert req.delay_seconds is None

    def test_accepts_delay_seconds_alone(self):
        req = FormSubmissionRequest(delay_seconds=60)
        assert req.delay_seconds == 60
        assert req.scheduled_at is None

    def test_rejects_both_scheduling_fields(self):
        with pytest.raises(ValidationError, match="mutually exclusive"):
            FormSubmissionRequest(scheduled_at=_future(60), delay_seconds=60)

    def test_rejects_naive_scheduled_at(self):
        with pytest.raises(ValidationError, match="timezone"):
            FormSubmissionRequest(
                scheduled_at=datetime.now() + timedelta(minutes=5),  # naive
            )

    def test_rejects_past_scheduled_at(self):
        with pytest.raises(ValidationError, match="future"):
            FormSubmissionRequest(
                scheduled_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            )

    def test_rejects_scheduled_at_beyond_one_year(self):
        with pytest.raises(ValidationError, match="1 year"):
            FormSubmissionRequest(
                scheduled_at=datetime.now(timezone.utc) + timedelta(days=366),
            )

    def test_rejects_delay_seconds_zero_or_negative(self):
        with pytest.raises(ValidationError):
            FormSubmissionRequest(delay_seconds=0)

    def test_rejects_delay_seconds_beyond_one_year(self):
        with pytest.raises(ValidationError):
            FormSubmissionRequest(delay_seconds=31_536_001)
