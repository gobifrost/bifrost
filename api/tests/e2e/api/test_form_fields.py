"""
E2E tests for form field types and preservation.

Tests that form field types are correctly preserved during form execution,
ensuring that text, email, number (int/float), select, checkbox, textarea,
radio, and datetime fields maintain their types through the execution pipeline.
"""

import pytest

from tests.e2e.conftest import write_and_register, execute_form_sync


@pytest.mark.e2e
class TestFormFieldTypes:
    """Test that form field types are preserved during execution."""

    @pytest.fixture(scope="class")
    def all_fields_workflow(self, e2e_client, platform_admin):
        """
        Create a workflow that accepts all form field types.

        This workflow returns both the received values and their types,
        allowing tests to verify type preservation.
        """
        workflow_content = '''"""E2E Form Field Types Test Workflow"""
from bifrost import workflow, context

@workflow(
    name="e2e_all_fields_workflow",
    description="Tests all form field types with type preservation",
)
async def e2e_all_fields_workflow(
    text_field: str,
    email_field: str,
    number_field: int,
    select_field: str,
    checkbox_field: bool,
    textarea_field: str,
    radio_field: str,
    datetime_field: str,
    float_field: float = 0.0,
    optional_field: str | None = None,
):
    """Accept all field types and return them with type info."""
    return {
        "received": {
            "text": text_field,
            "email": email_field,
            "number": number_field,
            "select": select_field,
            "checkbox": checkbox_field,
            "textarea": textarea_field,
            "radio": radio_field,
            "datetime": datetime_field,
            "float": float_field,
            "optional": optional_field,
        },
        "types": {
            "text": type(text_field).__name__,
            "email": type(email_field).__name__,
            "number": type(number_field).__name__,
            "select": type(select_field).__name__,
            "checkbox": type(checkbox_field).__name__,
            "textarea": type(textarea_field).__name__,
            "radio": type(radio_field).__name__,
            "datetime": type(datetime_field).__name__,
            "float": type(float_field).__name__,
            "optional": type(optional_field).__name__ if optional_field else "NoneType",
        },
        "user": context.email,
        "scope": context.scope,
    }
'''
        result = write_and_register(
            e2e_client,
            platform_admin.headers,
            "e2e_all_fields_workflow.py",
            workflow_content,
            "e2e_all_fields_workflow",
        )
        workflow_id = result["id"]
        yield {"id": workflow_id, "name": "e2e_all_fields_workflow"}

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=e2e_all_fields_workflow.py",
            headers=platform_admin.headers,
        )

    @pytest.fixture(scope="class")
    def all_fields_form(self, e2e_client, platform_admin, all_fields_workflow):
        """
        Create a form with all field types linked to the all_fields_workflow.

        Form includes:
        - text field
        - email field
        - number field (int)
        - number field (float)
        - select field with options
        - checkbox (boolean)
        - textarea (multiline)
        - radio field with options
        - datetime field (ISO format)
        - optional text field
        """
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "E2E All Field Types Form",
                "description": "Form testing all field types with type preservation",
                "workflow_id": all_fields_workflow["id"],
                "form_schema": {
                    "fields": [
                        {
                            "name": "text_field",
                            "type": "text",
                            "label": "Text Field",
                            "placeholder": "Enter some text",
                            "required": True,
                        },
                        {
                            "name": "email_field",
                            "type": "email",
                            "label": "Email Field",
                            "placeholder": "user@example.com",
                            "required": True,
                        },
                        {
                            "name": "number_field",
                            "type": "number",
                            "label": "Integer Field",
                            "placeholder": "Enter an integer",
                            "required": True,
                        },
                        {
                            "name": "float_field",
                            "type": "number",
                            "label": "Float Field",
                            "placeholder": "Enter a decimal number",
                            "required": False,
                        },
                        {
                            "name": "select_field",
                            "type": "select",
                            "label": "Select Field",
                            "options": [
                                {"value": "opt1", "label": "Option 1"},
                                {"value": "opt2", "label": "Option 2"},
                                {"value": "opt3", "label": "Option 3"},
                            ],
                            "required": True,
                        },
                        {
                            "name": "checkbox_field",
                            "type": "checkbox",
                            "label": "Checkbox Field",
                            "required": False,
                        },
                        {
                            "name": "textarea_field",
                            "type": "textarea",
                            "label": "Textarea Field",
                            "placeholder": "Enter multi-line text",
                            "required": True,
                        },
                        {
                            "name": "radio_field",
                            "type": "radio",
                            "label": "Radio Field",
                            "options": [
                                {"value": "r1", "label": "Radio Option 1"},
                                {"value": "r2", "label": "Radio Option 2"},
                            ],
                            "required": True,
                        },
                        {
                            "name": "datetime_field",
                            "type": "datetime",
                            "label": "DateTime Field",
                            "required": True,
                        },
                        {
                            "name": "optional_field",
                            "type": "text",
                            "label": "Optional Field",
                            "placeholder": "This field is optional",
                            "required": False,
                        },
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201, f"Failed to create form: {response.text}"
        form = response.json()

        yield form

        # Cleanup
        e2e_client.delete(
            f"/api/forms/{form['id']}",
            headers=platform_admin.headers,
        )

    def test_form_created_successfully(self, all_fields_form):
        """Verify the form with all field types was created."""
        assert all_fields_form["id"]
        assert all_fields_form["name"] == "E2E All Field Types Form"
        assert all_fields_form["form_schema"]["fields"]
        assert len(all_fields_form["form_schema"]["fields"]) == 10

    def test_form_has_all_field_types(self, all_fields_form):
        """Verify the form includes all required field types."""
        field_types = {
            f["name"]: f["type"] for f in all_fields_form["form_schema"]["fields"]
        }

        assert field_types["text_field"] == "text"
        assert field_types["email_field"] == "email"
        assert field_types["number_field"] == "number"
        assert field_types["float_field"] == "number"
        assert field_types["select_field"] == "select"
        assert field_types["checkbox_field"] == "checkbox"
        assert field_types["textarea_field"] == "textarea"
        assert field_types["radio_field"] == "radio"
        assert field_types["datetime_field"] == "datetime"
        assert field_types["optional_field"] == "text"

    def test_execute_form_text_field_preserved(
        self, e2e_client, platform_admin, all_fields_form
    ):
        """Execute form and verify text field type is preserved as string."""
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Hello World",
                "email_field": "test@example.com",
                "number_field": 42,
                "select_field": "opt1",
                "checkbox_field": True,
                "textarea_field": "Line 1\nLine 2\nLine 3",
                "radio_field": "r1",
                "datetime_field": "2025-12-09T10:30:00Z",
                "float_field": 3.14,
            },
        )
        result = data.get("result", {})

        assert result["received"]["text"] == "Hello World"
        assert result["types"]["text"] == "str"

    def test_execute_form_email_field_preserved(
        self, e2e_client, platform_admin, all_fields_form
    ):
        """Execute form and verify email field type is preserved as string."""
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Test",
                "email_field": "user@example.com",
                "number_field": 10,
                "select_field": "opt2",
                "checkbox_field": False,
                "textarea_field": "Some text",
                "radio_field": "r2",
                "datetime_field": "2025-12-09T14:00:00Z",
            },
        )
        result = data.get("result", {})

        assert result["received"]["email"] == "user@example.com"
        assert result["types"]["email"] == "str"

    def test_execute_form_int_field_preserved(
        self, e2e_client, platform_admin, all_fields_form
    ):
        """Execute form and verify integer field type is preserved."""
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Test",
                "email_field": "test@example.com",
                "number_field": 100,
                "select_field": "opt1",
                "checkbox_field": True,
                "textarea_field": "Multi\nline\ntext",
                "radio_field": "r1",
                "datetime_field": "2025-12-09T15:45:00Z",
            },
        )
        result = data.get("result", {})

        assert result["received"]["number"] == 100
        assert result["types"]["number"] == "int"

    def test_execute_form_float_field_preserved(
        self, e2e_client, platform_admin, all_fields_form
    ):
        """Execute form and verify float field type is preserved."""
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Test",
                "email_field": "test@example.com",
                "number_field": 42,
                "float_field": 2.71828,
                "select_field": "opt3",
                "checkbox_field": False,
                "textarea_field": "Text",
                "radio_field": "r2",
                "datetime_field": "2025-12-09T16:20:00Z",
            },
        )
        result = data.get("result", {})

        assert result["received"]["float"] == 2.71828
        assert result["types"]["float"] == "float"

    def test_execute_form_select_field_preserved(
        self, e2e_client, platform_admin, all_fields_form
    ):
        """Execute form and verify select field type is preserved as string."""
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Test",
                "email_field": "test@example.com",
                "number_field": 5,
                "select_field": "opt2",
                "checkbox_field": True,
                "textarea_field": "Content",
                "radio_field": "r1",
                "datetime_field": "2025-12-09T17:00:00Z",
            },
        )
        result = data.get("result", {})

        assert result["received"]["select"] == "opt2"
        assert result["types"]["select"] == "str"

    def test_execute_form_checkbox_field_preserved(
        self, e2e_client, platform_admin, all_fields_form
    ):
        """Execute form and verify checkbox field type is preserved as boolean."""
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Test",
                "email_field": "test@example.com",
                "number_field": 20,
                "select_field": "opt1",
                "checkbox_field": True,
                "textarea_field": "Test text",
                "radio_field": "r2",
                "datetime_field": "2025-12-09T18:00:00Z",
            },
        )
        result = data.get("result", {})

        assert result["received"]["checkbox"] is True
        assert result["types"]["checkbox"] == "bool"

        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Test",
                "email_field": "test@example.com",
                "number_field": 15,
                "select_field": "opt2",
                "checkbox_field": False,
                "textarea_field": "More text",
                "radio_field": "r1",
                "datetime_field": "2025-12-09T19:00:00Z",
            },
        )
        result = data.get("result", {})

        assert result["received"]["checkbox"] is False
        assert result["types"]["checkbox"] == "bool"

    def test_execute_form_textarea_field_preserved(
        self, e2e_client, platform_admin, all_fields_form
    ):
        """Execute form and verify textarea field type is preserved as string with newlines."""
        multiline_text = "First line\nSecond line\nThird line\n\nWith blank line"
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Test",
                "email_field": "test@example.com",
                "number_field": 88,
                "select_field": "opt3",
                "checkbox_field": True,
                "textarea_field": multiline_text,
                "radio_field": "r2",
                "datetime_field": "2025-12-09T20:00:00Z",
            },
        )
        result = data.get("result", {})

        assert result["received"]["textarea"] == multiline_text
        assert result["types"]["textarea"] == "str"
        assert "\n" in result["received"]["textarea"]

    def test_execute_form_radio_field_preserved(
        self, e2e_client, platform_admin, all_fields_form
    ):
        """Execute form and verify radio field type is preserved as string."""
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Test",
                "email_field": "test@example.com",
                "number_field": 33,
                "select_field": "opt1",
                "checkbox_field": False,
                "textarea_field": "Radio test",
                "radio_field": "r2",
                "datetime_field": "2025-12-09T21:00:00Z",
            },
        )
        result = data.get("result", {})

        assert result["received"]["radio"] == "r2"
        assert result["types"]["radio"] == "str"

    def test_execute_form_datetime_field_preserved(
        self, e2e_client, platform_admin, all_fields_form
    ):
        """Execute form and verify datetime field type is preserved as ISO string."""
        iso_datetime = "2025-12-09T22:30:45Z"
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Test",
                "email_field": "test@example.com",
                "number_field": 77,
                "select_field": "opt2",
                "checkbox_field": True,
                "textarea_field": "DateTime test",
                "radio_field": "r1",
                "datetime_field": iso_datetime,
            },
        )
        result = data.get("result", {})

        assert result["received"]["datetime"] == iso_datetime
        assert result["types"]["datetime"] == "str"

    def test_execute_form_optional_field_none_preserved(
        self, e2e_client, platform_admin, all_fields_form
    ):
        """Execute form and verify optional field with None value is preserved."""
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Test",
                "email_field": "test@example.com",
                "number_field": 44,
                "select_field": "opt1",
                "checkbox_field": False,
                "textarea_field": "Optional test",
                "radio_field": "r2",
                "datetime_field": "2025-12-09T23:00:00Z",
            },
        )
        result = data.get("result", {})

        assert result["received"]["optional"] is None
        assert result["types"]["optional"] == "NoneType"

    def test_execute_form_optional_field_with_value_preserved(
        self, e2e_client, platform_admin, all_fields_form
    ):
        """Execute form and verify optional field with value is preserved as string."""
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Test",
                "email_field": "test@example.com",
                "number_field": 55,
                "select_field": "opt3",
                "checkbox_field": True,
                "textarea_field": "Optional with value",
                "radio_field": "r1",
                "datetime_field": "2025-12-10T00:00:00Z",
                "optional_field": "Optional Value",
            },
        )
        result = data.get("result", {})

        assert result["received"]["optional"] == "Optional Value"
        assert result["types"]["optional"] == "str"

    def test_execute_form_all_fields_together(
        self, e2e_client, platform_admin, all_fields_form
    ):
        """Execute form with all fields and verify complete type preservation."""
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Sample Text",
                "email_field": "user@domain.com",
                "number_field": 999,
                "float_field": 1.618,
                "select_field": "opt1",
                "checkbox_field": True,
                "textarea_field": "Multi\nLine\nContent",
                "radio_field": "r1",
                "datetime_field": "2025-12-10T12:30:45Z",
                "optional_field": "Complete test",
            },
        )
        result = data.get("result", {})

        # Verify all fields are present and correctly typed
        assert result["received"]["text"] == "Sample Text"
        assert result["types"]["text"] == "str"

        assert result["received"]["email"] == "user@domain.com"
        assert result["types"]["email"] == "str"

        assert result["received"]["number"] == 999
        assert result["types"]["number"] == "int"

        assert result["received"]["float"] == 1.618
        assert result["types"]["float"] == "float"

        assert result["received"]["select"] == "opt1"
        assert result["types"]["select"] == "str"

        assert result["received"]["checkbox"] is True
        assert result["types"]["checkbox"] == "bool"

        assert "Multi\nLine\nContent" in result["received"]["textarea"]
        assert result["types"]["textarea"] == "str"

        assert result["received"]["radio"] == "r1"
        assert result["types"]["radio"] == "str"

        assert result["received"]["datetime"] == "2025-12-10T12:30:45Z"
        assert result["types"]["datetime"] == "str"

        assert result["received"]["optional"] == "Complete test"
        assert result["types"]["optional"] == "str"

    def test_form_execution_captures_user_context(
        self, e2e_client, platform_admin, all_fields_form
    ):
        """Verify form execution captures user context information."""
        result = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            all_fields_form["id"],
            {
                "text_field": "Context test",
                "email_field": "test@example.com",
                "number_field": 123,
                "select_field": "opt1",
                "checkbox_field": True,
                "textarea_field": "Testing context",
                "radio_field": "r1",
                "datetime_field": "2025-12-10T13:00:00Z",
            },
            max_wait=30.0,
        )

        workflow_result = result.get("result", {})

        # Verify user context is captured
        assert workflow_result["user"] == platform_admin.email
        assert workflow_result["scope"]  # Should have a scope (org or GLOBAL)


@pytest.mark.e2e
class TestMultiSelectField:
    """Multi-select field type preserves list[str] value end-to-end."""

    @pytest.fixture(scope="class")
    def multi_select_workflow(self, e2e_client, platform_admin):
        workflow_content = '''"""E2E Multi-Select Field Test Workflow"""
from bifrost import workflow

@workflow(
    name="e2e_multi_select_workflow",
    description="Echoes a multi-select field back with its runtime type",
)
async def e2e_multi_select_workflow(tags: list[str]):
    return {
        "received": tags,
        "type": type(tags).__name__,
        "element_type": type(tags[0]).__name__ if tags else "NoneType",
        "length": len(tags),
    }
'''
        result = write_and_register(
            e2e_client,
            platform_admin.headers,
            "e2e_multi_select_workflow.py",
            workflow_content,
            "e2e_multi_select_workflow",
        )
        workflow_id = result["id"]
        yield {"id": workflow_id, "name": "e2e_multi_select_workflow"}

        e2e_client.delete(
            "/api/files/editor?path=e2e_multi_select_workflow.py",
            headers=platform_admin.headers,
        )

    @pytest.fixture(scope="class")
    def multi_select_form(self, e2e_client, platform_admin, multi_select_workflow):
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "E2E Multi-Select Form",
                "description": "Verifies multi_select list[str] round-trip",
                "workflow_id": multi_select_workflow["id"],
                "form_schema": {
                    "fields": [
                        {
                            "name": "tags",
                            "type": "multi_select",
                            "label": "Tags",
                            "options": [
                                {"value": "urgent", "label": "Urgent"},
                                {"value": "billing", "label": "Billing"},
                                {"value": "onboarding", "label": "Onboarding"},
                            ],
                            "required": True,
                        },
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201, f"Failed to create form: {response.text}"
        form = response.json()
        yield form

        e2e_client.delete(
            f"/api/forms/{form['id']}",
            headers=platform_admin.headers,
        )

    def test_multi_select_field_persisted(self, multi_select_form):
        fields = multi_select_form["form_schema"]["fields"]
        assert len(fields) == 1
        assert fields[0]["type"] == "multi_select"
        assert len(fields[0]["options"]) == 3

    def test_multi_select_submits_as_list_of_strings(
        self, e2e_client, platform_admin, multi_select_form
    ):
        """Submitting multi-select values flows through as list[str]."""
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            multi_select_form["id"],
            {"tags": ["urgent", "billing"]},
        )
        result = data.get("result", {})
        assert result["received"] == ["urgent", "billing"]
        assert result["type"] == "list"
        assert result["element_type"] == "str"
        assert result["length"] == 2

    def test_multi_select_single_element_list(
        self, e2e_client, platform_admin, multi_select_form
    ):
        """A single selected option still arrives as a list with one element."""
        data = execute_form_sync(
            e2e_client,
            platform_admin.headers,
            multi_select_form["id"],
            {"tags": ["onboarding"]},
        )
        result = data.get("result", {})
        assert result["received"] == ["onboarding"]
        assert result["type"] == "list"
        assert result["length"] == 1
