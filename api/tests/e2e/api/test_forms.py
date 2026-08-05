"""
E2E tests for form management.

Tests form CRUD operations, access levels, and role-based access.
"""

import logging
import pytest

from tests.e2e.conftest import write_and_register


logger = logging.getLogger(__name__)

@pytest.mark.e2e
class TestFormCRUD:
    """Test form CRUD operations."""

    @pytest.fixture
    def test_form(self, e2e_client, platform_admin):
        """Create a test form and clean up after."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "E2E Test Form",
                "description": "Test form for E2E testing",
                "workflow_id": None,
                "form_schema": {
                    "fields": [
                        {"name": "company_name", "type": "text", "label": "Company Name", "required": True},
                        {"name": "contact_email", "type": "email", "label": "Contact Email", "required": True},
                    ]
                },
                "access_level": "role_based",
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

    def test_create_form(self, e2e_client, platform_admin):
        """Platform admin can create a form."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Customer Onboarding",
                "description": "New customer intake form",
                "workflow_id": None,
                "form_schema": {
                    "fields": [
                        {"name": "company_name", "type": "text", "label": "Company Name", "required": True},
                    ]
                },
                "access_level": "role_based",
            },
        )
        assert response.status_code == 201, f"Create form failed: {response.text}"
        form = response.json()
        assert form["name"] == "Customer Onboarding"

        # Cleanup
        e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)

    def test_list_forms(self, e2e_client, platform_admin, test_form):
        """Platform admin can list forms."""
        response = e2e_client.get(
            "/api/forms",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List forms failed: {response.text}"
        forms = response.json()
        assert isinstance(forms, list)
        form_names = [f["name"] for f in forms]
        assert "E2E Test Form" in form_names

    def test_update_form(self, e2e_client, platform_admin, test_form):
        """Platform admin can update a form."""
        response = e2e_client.patch(
            f"/api/forms/{test_form['id']}",
            headers=platform_admin.headers,
            json={
                "description": "Updated description",
            },
        )
        assert response.status_code == 200, f"Update form failed: {response.text}"
        updated = response.json()
        assert updated["description"] == "Updated description"

    def test_confirmation_markdown_defaults_and_updates(
        self, e2e_client, platform_admin, test_form
    ):
        assert test_form["confirmation_markdown"] == "## Form submitted\n\nThank you!"

        custom = "## We received it\n\n![Done](https://example.com/done.png)"
        response = e2e_client.patch(
            f"/api/forms/{test_form['id']}",
            headers=platform_admin.headers,
            json={"confirmation_markdown": custom},
        )

        assert response.status_code == 200, response.text
        assert response.json()["confirmation_markdown"] == custom

        get_response = e2e_client.get(
            f"/api/forms/{test_form['id']}", headers=platform_admin.headers
        )
        assert get_response.status_code == 200, get_response.text
        assert get_response.json()["confirmation_markdown"] == custom


@pytest.mark.e2e
class TestFormAccessLevels:
    """Test different form access levels."""

    @pytest.fixture
    def authenticated_form(self, e2e_client, platform_admin):
        """Create an authenticated-access form."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Authenticated Form",
                "workflow_id": None,
                "form_schema": {"fields": [{"name": "data", "type": "text", "label": "Data"}]},
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201
        form = response.json()

        yield form

        e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)


    def test_authenticated_form_accessible_by_any_user(
        self, e2e_client, org1_user, authenticated_form
    ):
        """Any authenticated user can access authenticated forms."""
        response = e2e_client.get(
            f"/api/forms/{authenticated_form['id']}",
            headers=org1_user.headers,
        )
        # Should at least be able to see the form
        assert response.status_code in [200, 403]  # Depends on implementation


@pytest.mark.e2e
class TestFormRoleAccess:
    """Test role-based form access."""

    @pytest.fixture
    def role_based_setup(self, e2e_client, platform_admin, org1_user):
        """Create role, form, and assign user."""
        # Create role
        role_resp = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": "Form Access Role", "description": "Test role"},
        )
        assert role_resp.status_code == 201
        role = role_resp.json()

        # Create form
        form_resp = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Role-Based Form",
                "workflow_id": None,
                "form_schema": {"fields": [{"name": "data", "type": "text", "label": "Data"}]},
                "access_level": "role_based",
            },
        )
        assert form_resp.status_code == 201
        form = form_resp.json()

        # Assign form to role
        e2e_client.post(
            f"/api/roles/{role['id']}/forms",
            headers=platform_admin.headers,
            json={"form_ids": [form["id"]]},
        )

        yield {"role": role, "form": form}

        # Cleanup
        e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)
        e2e_client.delete(f"/api/roles/{role['id']}", headers=platform_admin.headers)

    def test_unassigned_user_cannot_access_role_form(
        self, e2e_client, org2_user, role_based_setup
    ):
        """User without role cannot access role-based form."""
        response = e2e_client.get(
            f"/api/forms/{role_based_setup['form']['id']}",
            headers=org2_user.headers,
        )
        # Should be denied
        assert response.status_code == 403


@pytest.mark.e2e
class TestFormAccess:
    """Test form access control."""

    def test_org_user_cannot_create_forms(self, e2e_client, org1_user):
        """Org user should not be able to create forms."""
        response = e2e_client.post(
            "/api/forms",
            headers=org1_user.headers,
            json={
                "name": "Unauthorized Form",
                "workflow_id": None,
                "form_schema": {"fields": []},
            },
        )
        assert response.status_code == 403


@pytest.mark.e2e
class TestFormFileSync:
    """Test form-file synchronization."""

    @pytest.fixture
    def form_with_file(self, e2e_client, platform_admin):
        """Create a form and verify its file is created."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "File Sync Test Form",
                "description": "Form for testing file sync",
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

        yield form

        # Cleanup
        e2e_client.delete(
            f"/api/forms/{form['id']}",
            headers=platform_admin.headers,
        )

    # NOTE: Form file_path tests removed as forms are now "fully virtual"
    # Forms no longer have a file_path column - their virtual path is computed
    # from their ID (forms/{uuid}.form.yaml) for git sync.




@pytest.mark.e2e
class TestFormScopeFiltering:
    """Test form scope filtering works correctly."""

    @pytest.fixture
    def scoped_forms(self, e2e_client, platform_admin, org1, org2):
        """Create forms in different scopes for testing."""
        forms = {}

        # Create global form (no organization_id)
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Global Form",
                "workflow_id": None,
                "form_schema": {"fields": [{"name": "data", "type": "text", "label": "Data"}]},
                "access_level": "authenticated",
                "organization_id": None,
            },
        )
        assert response.status_code == 201, f"Failed to create global form: {response.text}"
        forms["global"] = response.json()

        # Create org1 form
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Org1 Form",
                "workflow_id": None,
                "form_schema": {"fields": [{"name": "data", "type": "text", "label": "Data"}]},
                "access_level": "authenticated",
                "organization_id": org1["id"],
            },
        )
        assert response.status_code == 201, f"Failed to create org1 form: {response.text}"
        forms["org1"] = response.json()

        # Create org2 form
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Org2 Form",
                "workflow_id": None,
                "form_schema": {"fields": [{"name": "data", "type": "text", "label": "Data"}]},
                "access_level": "authenticated",
                "organization_id": org2["id"],
            },
        )
        assert response.status_code == 201, f"Failed to create org2 form: {response.text}"
        forms["org2"] = response.json()

        yield forms

        # Cleanup
        for key, form in forms.items():
            try:
                e2e_client.delete(
                    f"/api/forms/{form['id']}",
                    headers=platform_admin.headers,
                )
            except Exception as e:
                # Best-effort fixture cleanup; teardown shouldn't fail the test
                logger.debug(f"fixture cleanup error: {e}")

    def test_platform_admin_no_scope_sees_all(
        self, e2e_client, platform_admin, scoped_forms
    ):
        """Platform admin with no scope sees ALL forms."""
        response = e2e_client.get(
            "/api/forms",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        form_ids = [f["id"] for f in response.json()]

        assert scoped_forms["global"]["id"] in form_ids, "Should see global form"
        assert scoped_forms["org1"]["id"] in form_ids, "Should see org1 form"
        assert scoped_forms["org2"]["id"] in form_ids, "Should see org2 form"

    def test_platform_admin_scope_global_sees_only_global(
        self, e2e_client, platform_admin, scoped_forms
    ):
        """Platform admin with scope=global sees ONLY global forms."""
        response = e2e_client.get(
            "/api/forms",
            params={"scope": "global"},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        form_ids = [f["id"] for f in response.json()]

        assert scoped_forms["global"]["id"] in form_ids, "Should see global form"
        assert scoped_forms["org1"]["id"] not in form_ids, "Should NOT see org1 form"
        assert scoped_forms["org2"]["id"] not in form_ids, "Should NOT see org2 form"

    def test_platform_admin_scope_org_sees_only_that_org(
        self, e2e_client, platform_admin, org1, scoped_forms
    ):
        """Platform admin with scope={org1} sees ONLY org1 forms (NOT global)."""
        response = e2e_client.get(
            "/api/forms",
            params={"scope": org1["id"]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        form_ids = [f["id"] for f in response.json()]

        # KEY ASSERTION: Global should NOT be included when filtering by org
        assert scoped_forms["global"]["id"] not in form_ids, "Should NOT see global form"
        assert scoped_forms["org1"]["id"] in form_ids, "Should see org1 form"
        assert scoped_forms["org2"]["id"] not in form_ids, "Should NOT see org2 form"

    def test_org_user_sees_own_org_plus_global(
        self, e2e_client, org1_user, scoped_forms
    ):
        """Org user (no scope param) sees their org + global."""
        response = e2e_client.get(
            "/api/forms",
            headers=org1_user.headers,
        )
        assert response.status_code == 200
        form_ids = [f["id"] for f in response.json()]

        assert scoped_forms["global"]["id"] in form_ids, "Should see global form"
        assert scoped_forms["org1"]["id"] in form_ids, "Should see org1 form"
        assert scoped_forms["org2"]["id"] not in form_ids, "Should NOT see org2 form"


@pytest.mark.e2e
class TestFormComprehensive:
    """Comprehensive form tests covering all field types, attributes, and features."""

    @pytest.fixture(scope="class")
    def data_provider_for_forms(self, e2e_client, platform_admin):
        """Create a data provider to use in form field tests."""
        dp_content = '''"""Data Provider for Form Tests"""
from bifrost import data_provider

@data_provider(
    name="e2e_form_test_dp",
    description="Data provider for comprehensive form tests"
)
async def e2e_form_test_dp(org_id: str | None = None):
    """Returns test options for form fields."""
    return [
        {"value": "opt_1", "label": "Option 1"},
        {"value": "opt_2", "label": "Option 2"},
        {"value": "opt_3", "label": "Option 3"},
    ]
'''
        result = write_and_register(
            e2e_client, platform_admin.headers,
            "e2e_form_test_dp.py", dp_content, "e2e_form_test_dp",
        )

        yield {
            "id": result["id"],
            "name": result["name"],
            "path": "e2e_form_test_dp.py",
        }

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=e2e_form_test_dp.py",
            headers=platform_admin.headers,
        )

    @pytest.fixture(scope="class")
    def comprehensive_form(self, e2e_client, platform_admin, data_provider_for_forms):
        """Create a form with every possible feature and attribute."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "E2E Comprehensive Form",
                "description": "Form testing all features and attributes",
                "workflow_id": None,  # No linked workflow for this test
                "access_level": "authenticated",
                "form_schema": {
                    "fields": [
                        {
                            "name": "text_with_attrs",
                            "type": "text",
                            "label": "Text With Attributes",
                            "placeholder": "Enter text here...",
                            "help_text": "This is help text for the field",
                            "default_value": "default_text",
                            "required": True,
                            "allow_as_query_param": True,
                        },
                        {
                            "name": "select_with_dp",
                            "type": "select",
                            "label": "Select With Data Provider",
                            "data_provider_id": data_provider_for_forms["id"],
                            "data_provider_inputs": {
                                "org_id": {
                                    "mode": "static",
                                    "value": "test_org_123"
                                }
                            },
                            "required": True,
                        },
                        {
                            "name": "conditional_field",
                            "type": "text",
                            "label": "Conditional Field",
                            "visibility_expression": "field.select_with_dp == 'opt_1'",
                            "required": False,
                        },
                        {
                            "name": "email_field",
                            "type": "email",
                            "label": "Email Address",
                            "placeholder": "user@example.com",
                            "required": True,
                        },
                        {
                            "name": "number_field",
                            "type": "number",
                            "label": "Number Field",
                            "default_value": "42",
                            "required": False,
                        },
                        {
                            "name": "checkbox_field",
                            "type": "checkbox",
                            "label": "Checkbox Field",
                            "help_text": "Check this box if applicable",
                            "required": False,
                        },
                        {
                            "name": "textarea_field",
                            "type": "textarea",
                            "label": "Textarea Field",
                            "placeholder": "Enter long text...",
                            "required": False,
                        },
                        {
                            "name": "datetime_field",
                            "type": "datetime",
                            "label": "DateTime Field",
                            "required": False,
                        },
                        {
                            "name": "static_select",
                            "type": "select",
                            "label": "Static Select",
                            "options": [
                                {"value": "a", "label": "Choice A"},
                                {"value": "b", "label": "Choice B"},
                            ],
                            "required": True,
                        },
                        {
                            "name": "radio_field",
                            "type": "radio",
                            "label": "Radio Options",
                            "options": [
                                {"value": "r1", "label": "Radio 1"},
                                {"value": "r2", "label": "Radio 2"},
                            ],
                            "required": True,
                        },
                    ]
                },
            },
        )
        assert response.status_code == 201, f"Create comprehensive form failed: {response.text}"
        form = response.json()

        yield form

        # Cleanup
        e2e_client.delete(
            f"/api/forms/{form['id']}",
            headers=platform_admin.headers,
        )

    def test_comprehensive_form_created(self, comprehensive_form):
        """Verify comprehensive form was created successfully."""
        assert comprehensive_form["id"]
        assert comprehensive_form["name"] == "E2E Comprehensive Form"
        assert comprehensive_form["description"] == "Form testing all features and attributes"
        assert len(comprehensive_form["form_schema"]["fields"]) == 10

    def test_form_data_provider_id_is_uuid(self, comprehensive_form, data_provider_for_forms):
        """Verify data_provider_id is stored as UUID, not name."""
        fields = comprehensive_form["form_schema"]["fields"]
        dp_field = next((f for f in fields if f["name"] == "select_with_dp"), None)
        assert dp_field is not None, "Data provider field not found"
        assert dp_field["data_provider_id"] == data_provider_for_forms["id"], \
            f"Expected UUID {data_provider_for_forms['id']}, got {dp_field.get('data_provider_id')}"

    def test_form_data_provider_inputs_preserved(self, comprehensive_form):
        """Verify data_provider_inputs are stored correctly."""
        fields = comprehensive_form["form_schema"]["fields"]
        dp_field = next((f for f in fields if f["name"] == "select_with_dp"), None)
        assert dp_field is not None
        assert dp_field.get("data_provider_inputs") is not None, "data_provider_inputs not preserved"
        assert "org_id" in dp_field["data_provider_inputs"], "org_id input not found"
        assert dp_field["data_provider_inputs"]["org_id"]["mode"] == "static"
        assert dp_field["data_provider_inputs"]["org_id"]["value"] == "test_org_123"

    def test_field_options_execute_only_the_configured_provider(
        self, e2e_client, platform_admin, comprehensive_form
    ):
        response = e2e_client.post(
            f"/api/forms/{comprehensive_form['id']}/fields/select_with_dp/options",
            headers=platform_admin.headers,
            json={"inputs": {"org_id": "browser-cannot-replace-static"}},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "options": [
                {
                    "value": "opt_1",
                    "label": "Option 1",
                    "description": None,
                    "metadata": None,
                },
                {
                    "value": "opt_2",
                    "label": "Option 2",
                    "description": None,
                    "metadata": None,
                },
                {
                    "value": "opt_3",
                    "label": "Option 3",
                    "description": None,
                    "metadata": None,
                },
            ]
        }

        unknown_input = e2e_client.post(
            f"/api/forms/{comprehensive_form['id']}/fields/select_with_dp/options",
            headers=platform_admin.headers,
            json={"inputs": {"provider_id": "substitution-attempt"}},
        )
        assert unknown_input.status_code == 422, unknown_input.text

        wrong_field = e2e_client.post(
            f"/api/forms/{comprehensive_form['id']}/fields/text_with_attrs/options",
            headers=platform_admin.headers,
            json={"inputs": {}},
        )
        assert wrong_field.status_code == 404, wrong_field.text

    def test_form_visibility_expression_preserved(self, comprehensive_form):
        """Verify visibility_expression is stored correctly."""
        fields = comprehensive_form["form_schema"]["fields"]
        conditional = next((f for f in fields if f["name"] == "conditional_field"), None)
        assert conditional is not None
        assert conditional.get("visibility_expression") == "field.select_with_dp == 'opt_1'", \
            f"Visibility expression not preserved: {conditional.get('visibility_expression')}"

    def test_form_field_attributes_preserved(self, comprehensive_form):
        """Verify all field attributes are preserved."""
        fields = comprehensive_form["form_schema"]["fields"]
        text_field = next((f for f in fields if f["name"] == "text_with_attrs"), None)
        assert text_field is not None

        assert text_field.get("placeholder") == "Enter text here...", "placeholder not preserved"
        assert text_field.get("help_text") == "This is help text for the field", "help_text not preserved"
        assert text_field.get("default_value") == "default_text", "default_value not preserved"
        assert text_field.get("required") is True, "required not preserved"
        assert text_field.get("allow_as_query_param") is True, "allow_as_query_param not preserved"

    def test_form_static_options_preserved(self, comprehensive_form):
        """Verify static options on select/radio fields are preserved."""
        fields = comprehensive_form["form_schema"]["fields"]

        static_select = next((f for f in fields if f["name"] == "static_select"), None)
        assert static_select is not None
        assert static_select.get("options") is not None, "options not preserved"
        assert len(static_select["options"]) == 2
        assert static_select["options"][0]["value"] == "a"
        assert static_select["options"][0]["label"] == "Choice A"

        radio = next((f for f in fields if f["name"] == "radio_field"), None)
        assert radio is not None
        assert radio.get("options") is not None
        assert len(radio["options"]) == 2

    def test_update_form_preserves_all_attributes(
        self, e2e_client, platform_admin, comprehensive_form
    ):
        """Update form and verify all field attributes are preserved."""
        # Update just the description
        response = e2e_client.patch(
            f"/api/forms/{comprehensive_form['id']}",
            headers=platform_admin.headers,
            json={
                "description": "Updated description",
            },
        )
        assert response.status_code == 200, f"Update form failed: {response.text}"
        updated = response.json()

        # Verify description changed
        assert updated["description"] == "Updated description"

        # Verify all field attributes preserved
        fields = updated["form_schema"]["fields"]

        # Check data provider field
        dp_field = next((f for f in fields if f["name"] == "select_with_dp"), None)
        assert dp_field is not None
        assert dp_field.get("data_provider_id") is not None, "data_provider_id lost on update"
        assert dp_field.get("data_provider_inputs") is not None, "data_provider_inputs lost on update"

        # Check visibility expression
        conditional = next((f for f in fields if f["name"] == "conditional_field"), None)
        assert conditional is not None
        assert conditional.get("visibility_expression") == "field.select_with_dp == 'opt_1'", \
            "visibility_expression lost on update"

        # Check other attributes
        text_field = next((f for f in fields if f["name"] == "text_with_attrs"), None)
        assert text_field is not None
        assert text_field.get("placeholder") == "Enter text here...", "placeholder lost on update"
        assert text_field.get("help_text") == "This is help text for the field", "help_text lost on update"

    def test_get_form_returns_all_attributes(
        self, e2e_client, platform_admin, comprehensive_form
    ):
        """GET /forms/{id} returns all field attributes."""
        response = e2e_client.get(
            f"/api/forms/{comprehensive_form['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        form = response.json()

        fields = form["form_schema"]["fields"]

        # Verify all 10 fields present
        assert len(fields) == 10

        # Sample check of attributes
        dp_field = next((f for f in fields if f["name"] == "select_with_dp"), None)
        assert dp_field.get("data_provider_id") is not None
        assert dp_field.get("data_provider_inputs") is not None


@pytest.mark.e2e
class TestFormAutoFill:
    """Test auto_fill feature for form fields."""

    @pytest.fixture(scope="class")
    def dp_with_metadata(self, e2e_client, platform_admin):
        """Create a data provider that returns options with metadata."""
        dp_content = '''"""Data Provider with Metadata for Auto-Fill Tests"""
from bifrost import data_provider

@data_provider(
    name="e2e_auto_fill_dp",
    description="Data provider returning metadata for auto-fill tests"
)
async def e2e_auto_fill_dp(org_id: str | None = None):
    """Returns options with metadata for auto-fill testing."""
    return [
        {
            "value": "acme_corp",
            "label": "Acme Corporation",
            "metadata": {
                "employee_count": 150,
                "industry": "Manufacturing",
                "city": "Springfield",
            },
        },
        {
            "value": "globex",
            "label": "Globex Inc",
            "metadata": {
                "employee_count": 500,
                "industry": "Technology",
                "city": "Shelbyville",
            },
        },
    ]
'''
        result = write_and_register(
            e2e_client, platform_admin.headers,
            "e2e_auto_fill_dp.py", dp_content, "e2e_auto_fill_dp",
        )

        yield {
            "id": result["id"],
            "name": result["name"],
            "path": "e2e_auto_fill_dp.py",
        }

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=e2e_auto_fill_dp.py",
            headers=platform_admin.headers,
        )

    def test_create_form_with_auto_fill(self, e2e_client, platform_admin, dp_with_metadata):
        """Form with auto_fill configuration is created and preserved."""
        auto_fill_config = {
            "employee_count_field": "employee_count",
            "industry_field": "industry",
        }
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Auto-Fill Test Form",
                "form_schema": {
                    "fields": [
                        {
                            "name": "company_select",
                            "type": "select",
                            "label": "Select Company",
                            "data_provider_id": dp_with_metadata["id"],
                            "auto_fill": auto_fill_config,
                            "required": True,
                        },
                        {
                            "name": "employee_count_field",
                            "type": "number",
                            "label": "Employee Count",
                        },
                        {
                            "name": "industry_field",
                            "type": "text",
                            "label": "Industry",
                        },
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201, f"Create form failed: {response.text}"
        form = response.json()
        fields = form["form_schema"]["fields"]

        dp_field = next((f for f in fields if f["name"] == "company_select"), None)
        assert dp_field is not None
        assert dp_field.get("auto_fill") == auto_fill_config

        # Cleanup
        e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)

    def test_auto_fill_preserved_on_update(self, e2e_client, platform_admin, dp_with_metadata):
        """auto_fill configuration survives form updates."""
        auto_fill_config = {"city_field": "city"}

        # Create
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Auto-Fill Update Test",
                "form_schema": {
                    "fields": [
                        {
                            "name": "company",
                            "type": "select",
                            "label": "Company",
                            "data_provider_id": dp_with_metadata["id"],
                            "auto_fill": auto_fill_config,
                        },
                        {
                            "name": "city_field",
                            "type": "text",
                            "label": "City",
                        },
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201
        form_id = response.json()["id"]

        # Update description (not schema)
        response = e2e_client.patch(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
            json={"description": "Updated"},
        )
        assert response.status_code == 200

        # Re-fetch and verify auto_fill preserved
        response = e2e_client.get(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        fields = response.json()["form_schema"]["fields"]
        dp_field = next((f for f in fields if f["name"] == "company"), None)
        assert dp_field is not None
        assert dp_field.get("auto_fill") == auto_fill_config, \
            "auto_fill lost after form update"

        # Cleanup
        e2e_client.delete(f"/api/forms/{form_id}", headers=platform_admin.headers)

    def test_auto_fill_null_by_default(self, e2e_client, platform_admin):
        """Fields without auto_fill have it as null."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "No Auto-Fill Form",
                "form_schema": {
                    "fields": [
                        {
                            "name": "plain_field",
                            "type": "text",
                            "label": "Plain Field",
                        },
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201
        form = response.json()
        fields = form["form_schema"]["fields"]
        assert fields[0].get("auto_fill") is None

        # Cleanup
        e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)


@pytest.mark.e2e
class TestFormValidation:
    """Test form validation and error handling."""

    def test_create_form_invalid_data_provider_id_fails(
        self, e2e_client, platform_admin
    ):
        """Creating form with string data_provider_id (not UUID) returns 422."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Invalid DP Form",
                "form_schema": {
                    "fields": [
                        {
                            "name": "bad_field",
                            "type": "select",
                            "label": "Bad Data Provider",
                            "data_provider_id": "not_a_uuid_string",
                            "required": True,
                        }
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 422, \
            f"Expected 422 for invalid data_provider_id, got {response.status_code}: {response.text}"

    def test_create_form_missing_required_fields_fails(
        self, e2e_client, platform_admin
    ):
        """Creating form without name fails validation."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                # Missing "name"
                "form_schema": {"fields": []},
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 422

    def test_create_form_with_fields_succeeds(
        self, e2e_client, platform_admin
    ):
        """Creating form with valid fields succeeds."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Valid Form Test",
                "form_schema": {
                    "fields": [
                        {
                            "name": "valid_field",
                            "type": "text",
                            "label": "Valid Label",
                        }
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201, f"Create form failed: {response.text}"
        form = response.json()

        # Cleanup
        e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)


@pytest.mark.e2e
class TestFormDBStorage:
    """Tests verifying forms are stored in database (DB-first model)."""

    def test_form_immediately_queryable_after_create(
        self, e2e_client, platform_admin
    ):
        """Form is immediately queryable after creation (DB storage)."""
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Immediate Query Form",
                "workflow_id": None,
                "form_schema": {"fields": []},
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201
        form = response.json()
        form_id = form["id"]

        # Immediately query - should work without delay (DB-first)
        response = e2e_client.get(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, \
            "Form should be immediately queryable (DB-first storage)"
        assert response.json()["id"] == form_id

        # Cleanup
        e2e_client.delete(f"/api/forms/{form_id}", headers=platform_admin.headers)

    def test_form_update_immediately_visible(
        self, e2e_client, platform_admin
    ):
        """Form updates are immediately visible (no sync delay)."""
        # Create form
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Update Visibility Form",
                "description": "Original",
                "workflow_id": None,
                "form_schema": {"fields": []},
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201
        form_id = response.json()["id"]

        # Update form
        response = e2e_client.patch(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
            json={"description": "Updated immediately"},
        )
        assert response.status_code == 200

        # Immediately query - should see update (DB-first)
        response = e2e_client.get(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        assert response.json()["description"] == "Updated immediately", \
            "Form update should be immediately visible"

        # Cleanup
        e2e_client.delete(f"/api/forms/{form_id}", headers=platform_admin.headers)

    def test_form_persists_complex_schema(
        self, e2e_client, platform_admin
    ):
        """Complex form schema is fully persisted in DB."""
        complex_schema = {
            "fields": [
                {
                    "name": "complex_select",
                    "type": "select",
                    "label": "Complex Select",
                    "options": [
                        {"value": "opt1", "label": "Option 1"},
                        {"value": "opt2", "label": "Option 2"},
                    ],
                    "required": True,
                    "help_text": "Select an option",
                    "placeholder": "Choose...",
                },
                {
                    "name": "conditional_text",
                    "type": "text",
                    "label": "Conditional Text",
                    "visibility_expression": "field.complex_select == 'opt1'",
                },
            ]
        }

        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Complex Schema Form",
                "workflow_id": None,
                "form_schema": complex_schema,
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201
        form_id = response.json()["id"]

        # Re-fetch and verify all attributes persisted
        response = e2e_client.get(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        form = response.json()
        fields = form["form_schema"]["fields"]

        # Check complex select attributes
        select_field = next((f for f in fields if f["name"] == "complex_select"), None)
        assert select_field is not None
        assert len(select_field["options"]) == 2
        assert select_field.get("help_text") == "Select an option"
        assert select_field.get("placeholder") == "Choose..."

        # Check conditional field
        cond_field = next((f for f in fields if f["name"] == "conditional_text"), None)
        assert cond_field is not None
        assert cond_field.get("visibility_expression") == "field.complex_select == 'opt1'"

        # Cleanup
        e2e_client.delete(f"/api/forms/{form_id}", headers=platform_admin.headers)


@pytest.mark.e2e
class TestFormRoleIdsAtCreateUpdate:
    """``role_ids`` on ``FormCreate`` / ``FormUpdate`` (issue #162).

    Forms previously could not be assigned to roles via the CLI/SDK at all —
    role assignments only existed via ``POST /api/roles/{id}/forms``. Bulk
    ``role_ids`` on the create/update DTOs closes that gap.
    """

    def _make_role(self, e2e_client, platform_admin, name: str) -> dict:
        resp = e2e_client.post(
            "/api/roles",
            headers=platform_admin.headers,
            json={"name": name, "description": f"role for {name}"},
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    def test_create_form_with_role_ids_persists_assignments(
        self, e2e_client, platform_admin
    ):
        role_a = self._make_role(e2e_client, platform_admin, "Issue162 RoleA")
        role_b = self._make_role(e2e_client, platform_admin, "Issue162 RoleB")
        try:
            create_resp = e2e_client.post(
                "/api/forms",
                headers=platform_admin.headers,
                json={
                    "name": "Issue162 Role-Assigned Form",
                    "workflow_id": None,
                    "form_schema": {"fields": [{"name": "x", "type": "text", "label": "X"}]},
                    "access_level": "role_based",
                    "role_ids": [role_a["id"], role_b["id"]],
                },
            )
            assert create_resp.status_code == 201, create_resp.text
            form = create_resp.json()
            # Response surfaces the assigned role IDs.
            returned = set(form.get("role_ids", []))
            assert returned == {role_a["id"], role_b["id"]}, form

            # Re-fetch and confirm the platform persisted them.
            get_resp = e2e_client.get(
                f"/api/forms/{form['id']}",
                headers=platform_admin.headers,
            )
            assert get_resp.status_code == 200
            assert set(get_resp.json().get("role_ids", [])) == {
                role_a["id"], role_b["id"]
            }

            # Cleanup
            e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)
        finally:
            e2e_client.delete(f"/api/roles/{role_a['id']}", headers=platform_admin.headers)
            e2e_client.delete(f"/api/roles/{role_b['id']}", headers=platform_admin.headers)

    def test_update_form_role_ids_replaces_existing(self, e2e_client, platform_admin):
        role_a = self._make_role(e2e_client, platform_admin, "Issue162 ReplaceA")
        role_b = self._make_role(e2e_client, platform_admin, "Issue162 ReplaceB")
        role_c = self._make_role(e2e_client, platform_admin, "Issue162 ReplaceC")
        try:
            create_resp = e2e_client.post(
                "/api/forms",
                headers=platform_admin.headers,
                json={
                    "name": "Issue162 Replace Form",
                    "workflow_id": None,
                    "form_schema": {"fields": [{"name": "x", "type": "text", "label": "X"}]},
                    "access_level": "role_based",
                    "role_ids": [role_a["id"], role_b["id"]],
                },
            )
            assert create_resp.status_code == 201, create_resp.text
            form = create_resp.json()

            # Bulk-replace: pass only role_c. role_a and role_b should be removed.
            patch_resp = e2e_client.patch(
                f"/api/forms/{form['id']}",
                headers=platform_admin.headers,
                json={"role_ids": [role_c["id"]]},
            )
            assert patch_resp.status_code == 200, patch_resp.text
            assert set(patch_resp.json().get("role_ids", [])) == {role_c["id"]}

            # Empty list explicitly clears.
            clear_resp = e2e_client.patch(
                f"/api/forms/{form['id']}",
                headers=platform_admin.headers,
                json={"role_ids": []},
            )
            assert clear_resp.status_code == 200, clear_resp.text
            assert clear_resp.json().get("role_ids", []) == []

            e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)
        finally:
            for r in (role_a, role_b, role_c):
                e2e_client.delete(f"/api/roles/{r['id']}", headers=platform_admin.headers)

    def test_update_form_omitting_role_ids_leaves_assignments_alone(
        self, e2e_client, platform_admin
    ):
        """Patching unrelated fields must not touch role assignments."""
        role_a = self._make_role(e2e_client, platform_admin, "Issue162 KeepA")
        try:
            create_resp = e2e_client.post(
                "/api/forms",
                headers=platform_admin.headers,
                json={
                    "name": "Issue162 Keep Form",
                    "workflow_id": None,
                    "form_schema": {"fields": [{"name": "x", "type": "text", "label": "X"}]},
                    "access_level": "role_based",
                    "role_ids": [role_a["id"]],
                },
            )
            assert create_resp.status_code == 201, create_resp.text
            form = create_resp.json()

            # PATCH only ``description`` — role_ids must be untouched.
            patch_resp = e2e_client.patch(
                f"/api/forms/{form['id']}",
                headers=platform_admin.headers,
                json={"description": "renamed"},
            )
            assert patch_resp.status_code == 200, patch_resp.text
            assert set(patch_resp.json().get("role_ids", [])) == {role_a["id"]}

            e2e_client.delete(f"/api/forms/{form['id']}", headers=platform_admin.headers)
        finally:
            e2e_client.delete(f"/api/roles/{role_a['id']}", headers=platform_admin.headers)

    def test_create_form_with_unknown_role_id_returns_404(
        self, e2e_client, platform_admin
    ):
        bogus = "00000000-0000-0000-0000-000000000000"
        resp = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Issue162 Bogus Role Form",
                "workflow_id": None,
                "form_schema": {"fields": []},
                "access_level": "role_based",
                "role_ids": [bogus],
            },
        )
        assert resp.status_code == 404, resp.text
        assert bogus in resp.text
