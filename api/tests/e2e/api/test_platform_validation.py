"""
E2E tests for platform entity validation.

Tests validation of references between platform entities:
- Form references to workflows and data providers
- Agent references to tools, delegated agents

These tests verify that the API rejects requests with invalid references
rather than silently ignoring them.
"""

import pytest
from uuid import uuid4

from tests.e2e.conftest import write_and_register


@pytest.mark.e2e
class TestFormReferenceValidation:
    """Test form reference validation."""

    def test_form_create_with_invalid_workflow_id(self, e2e_client, platform_admin):
        """Creating a form with non-existent workflow_id returns 422."""
        fake_workflow_id = str(uuid4())
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Test Form",
                "workflow_id": fake_workflow_id,
                "form_schema": {"fields": []},
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"
        data = response.json()
        assert "errors" in data.get("detail", {}), f"Expected errors in response: {data}"
        assert any(fake_workflow_id in err for err in data["detail"]["errors"])

    def test_form_create_with_invalid_launch_workflow_id(self, e2e_client, platform_admin):
        """Creating a form with non-existent launch_workflow_id returns 422."""
        fake_workflow_id = str(uuid4())
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Test Form",
                "launch_workflow_id": fake_workflow_id,
                "form_schema": {"fields": []},
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"
        data = response.json()
        assert "errors" in data.get("detail", {}), f"Expected errors in response: {data}"
        assert any(fake_workflow_id in err for err in data["detail"]["errors"])

    def test_form_create_with_invalid_data_provider_id_in_field(
        self, e2e_client, platform_admin
    ):
        """Creating a form with non-existent data_provider_id in field returns 422."""
        fake_dp_id = str(uuid4())
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Test Form",
                "form_schema": {
                    "fields": [
                        {
                            "name": "category",
                            "type": "select",
                            "label": "Category",
                            "required": True,
                            "data_provider_id": fake_dp_id,
                        }
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"
        data = response.json()
        assert "errors" in data.get("detail", {}), f"Expected errors in response: {data}"
        assert any(fake_dp_id in err for err in data["detail"]["errors"])

    def test_form_update_with_invalid_workflow_id(
        self, e2e_client, platform_admin
    ):
        """Updating a form with non-existent workflow_id returns 422."""
        # Create a valid form first
        create_response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Valid Form",
                "form_schema": {"fields": []},
                "access_level": "authenticated",
            },
        )
        assert create_response.status_code == 201
        form_id = create_response.json()["id"]

        # Try to update with invalid workflow_id
        fake_workflow_id = str(uuid4())
        update_response = e2e_client.patch(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
            json={"workflow_id": fake_workflow_id},
        )
        assert update_response.status_code == 422, f"Expected 422, got {update_response.status_code}"

        # Cleanup
        e2e_client.delete(f"/api/forms/{form_id}", headers=platform_admin.headers)

    def test_form_update_with_invalid_launch_workflow_id(
        self, e2e_client, platform_admin
    ):
        """Updating a form with non-existent launch_workflow_id returns 422."""
        # Create a valid form first
        create_response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Valid Form",
                "form_schema": {"fields": []},
                "access_level": "authenticated",
            },
        )
        assert create_response.status_code == 201
        form_id = create_response.json()["id"]

        # Try to update with invalid launch_workflow_id
        fake_workflow_id = str(uuid4())
        update_response = e2e_client.patch(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
            json={"launch_workflow_id": fake_workflow_id},
        )
        assert update_response.status_code == 422, f"Expected 422, got {update_response.status_code}"

        # Cleanup
        e2e_client.delete(f"/api/forms/{form_id}", headers=platform_admin.headers)


@pytest.mark.e2e
class TestAgentReferenceValidation:
    """Test agent reference validation."""

    def test_agent_create_with_invalid_tool_id(self, e2e_client, platform_admin):
        """An unknown tool is denied before structural reference validation."""
        fake_tool_id = str(uuid4())
        response = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": "Test Agent",
                "description": "Test agent description",
                "system_prompt": "You are a test agent.",
                "channels": ["chat"],
                "tool_ids": [fake_tool_id],
            },
        )
        assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
        assert response.json()["detail"] == f"You do not have role access to tool '{fake_tool_id}'"

    def test_agent_create_with_invalid_delegated_agent_id(self, e2e_client, platform_admin):
        """Creating an agent with non-existent delegated_agent_id returns 422."""
        fake_agent_id = str(uuid4())
        response = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": "Test Agent",
                "description": "Test agent description",
                "system_prompt": "You are a test agent.",
                "channels": ["chat"],
                "delegated_agent_ids": [fake_agent_id],
            },
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"
        data = response.json()
        assert "errors" in data.get("detail", {}), f"Expected errors in response: {data}"
        assert any(fake_agent_id in err for err in data["detail"]["errors"])

    def test_agent_update_with_invalid_tool_id(self, e2e_client, platform_admin):
        """Updating with an unknown tool is denied before structural validation."""
        # Create a valid agent first
        create_response = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": "Valid Agent",
                "description": "Valid agent description",
                "system_prompt": "You are a valid agent.",
                "channels": ["chat"],
            },
        )
        assert create_response.status_code == 201
        agent_id = create_response.json()["id"]

        # Try to update with invalid tool_id
        fake_tool_id = str(uuid4())
        update_response = e2e_client.put(
            f"/api/agents/{agent_id}",
            headers=platform_admin.headers,
            json={"tool_ids": [fake_tool_id]},
        )
        assert update_response.status_code == 403, f"Expected 403, got {update_response.status_code}"
        assert update_response.json()["detail"] == f"You do not have role access to tool '{fake_tool_id}'"

        # Cleanup
        e2e_client.delete(f"/api/agents/{agent_id}", headers=platform_admin.headers)

    def test_agent_update_with_invalid_delegated_agent_id(self, e2e_client, platform_admin):
        """Updating an agent with non-existent delegated_agent_id returns 422."""
        # Create a valid agent first
        create_response = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": "Valid Agent",
                "description": "Valid agent description",
                "system_prompt": "You are a valid agent.",
                "channels": ["chat"],
            },
        )
        assert create_response.status_code == 201
        agent_id = create_response.json()["id"]

        # Try to update with invalid delegated_agent_id
        fake_agent_id = str(uuid4())
        update_response = e2e_client.put(
            f"/api/agents/{agent_id}",
            headers=platform_admin.headers,
            json={"delegated_agent_ids": [fake_agent_id]},
        )
        assert update_response.status_code == 422, f"Expected 422, got {update_response.status_code}"

        # Cleanup
        e2e_client.delete(f"/api/agents/{agent_id}", headers=platform_admin.headers)

    def test_agent_self_delegation_rejected(self, e2e_client, platform_admin):
        """Updating an agent to delegate to itself returns 422."""
        # Create a valid agent first
        create_response = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": "Self-referencing Agent",
                "description": "Agent that tries to delegate to itself",
                "system_prompt": "You are a test agent.",
                "channels": ["chat"],
            },
        )
        assert create_response.status_code == 201
        agent_id = create_response.json()["id"]

        # Try to add self as delegation
        update_response = e2e_client.put(
            f"/api/agents/{agent_id}",
            headers=platform_admin.headers,
            json={"delegated_agent_ids": [agent_id]},
        )
        assert update_response.status_code == 422, f"Expected 422, got {update_response.status_code}"
        data = update_response.json()
        assert "errors" in data.get("detail", {}), f"Expected errors in response: {data}"
        assert any("itself" in err.lower() for err in data["detail"]["errors"])

        # Cleanup
        e2e_client.delete(f"/api/agents/{agent_id}", headers=platform_admin.headers)

    def test_assign_invalid_tool_to_agent(self, e2e_client, platform_admin):
        """Assigning an unknown tool is denied before structural validation."""
        # Create a valid agent first
        create_response = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": "Tool Assignment Test Agent",
                "description": "Agent for testing tool assignment",
                "system_prompt": "You are a test agent.",
                "channels": ["chat"],
            },
        )
        assert create_response.status_code == 201
        agent_id = create_response.json()["id"]

        # Try to assign non-existent tool via full agent PUT
        fake_tool_id = str(uuid4())
        assign_response = e2e_client.put(
            f"/api/agents/{agent_id}",
            headers=platform_admin.headers,
            json={"tool_ids": [fake_tool_id]},
        )
        assert assign_response.status_code == 403, f"Expected 403, got {assign_response.status_code}"
        assert assign_response.json()["detail"] == f"You do not have role access to tool '{fake_tool_id}'"

        # Cleanup
        e2e_client.delete(f"/api/agents/{agent_id}", headers=platform_admin.headers)

    def test_assign_invalid_delegation_to_agent(self, e2e_client, platform_admin):
        """Assigning non-existent delegation target to agent returns 422 via PUT /api/agents/{id}."""
        # Create a valid agent first
        create_response = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": "Delegation Assignment Test Agent",
                "description": "Agent for testing delegation assignment",
                "system_prompt": "You are a test agent.",
                "channels": ["chat"],
            },
        )
        assert create_response.status_code == 201
        agent_id = create_response.json()["id"]

        # Try to assign non-existent delegation target via full agent PUT
        fake_agent_id = str(uuid4())
        assign_response = e2e_client.put(
            f"/api/agents/{agent_id}",
            headers=platform_admin.headers,
            json={"delegated_agent_ids": [fake_agent_id]},
        )
        assert assign_response.status_code == 422, f"Expected 422, got {assign_response.status_code}"

        # Cleanup
        e2e_client.delete(f"/api/agents/{agent_id}", headers=platform_admin.headers)


@pytest.mark.e2e
class TestWorkflowTypeValidation:
    """Test that workflow type validation is enforced."""

    def test_form_accepts_tool_as_workflow_id(self, e2e_client, platform_admin):
        """Form workflow_id accepts both workflow and tool types."""
        tool_content = '''"""Test Tool for Validation"""
from bifrost import tool

@tool(
    name="validation_test_tool",
    description="A tool created for type validation testing",
)
async def validation_test_tool(query: str) -> str:
    return f"Result: {query}"
'''
        result = write_and_register(
            e2e_client, platform_admin.headers,
            "validation_test_tool.py", tool_content, "validation_test_tool",
        )
        assert result["type"] == "tool", f"Should be type='tool', got {result['type']}"

        # Create form with tool ID as workflow_id — tools are valid workflow targets
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Test Form with Tool",
                "workflow_id": result["id"],
                "form_schema": {"fields": []},
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"
        form_id = response.json()["id"]

        # Cleanup
        e2e_client.delete(
            f"/api/forms/{form_id}",
            headers=platform_admin.headers,
        )
        e2e_client.delete(
            "/api/files/editor?path=validation_test_tool.py",
            headers=platform_admin.headers,
        )

    def test_form_rejects_data_provider_as_workflow_id(self, e2e_client, platform_admin):
        """Form workflow_id must reference a workflow type, not a data_provider."""
        dp_content = '''"""Test Data Provider for Validation"""
from bifrost import data_provider

@data_provider(
    name="validation_test_dp",
    description="A data provider created for type validation testing",
)
async def validation_test_dp() -> list:
    return [{"value": "a", "label": "A"}]
'''
        result = write_and_register(
            e2e_client, platform_admin.headers,
            "validation_test_dp.py", dp_content, "validation_test_dp",
        )

        # Try to create form with data_provider ID as workflow_id
        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Test Form with DP",
                "workflow_id": result["id"],
                "form_schema": {"fields": []},
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"
        data = response.json()
        assert "errors" in data.get("detail", {}), f"Expected errors: {data}"
        # Should mention that it's a data_provider, not a workflow
        assert any("data_provider" in err.lower() for err in data["detail"]["errors"])

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=validation_test_dp.py",
            headers=platform_admin.headers,
        )

    def test_agent_rejects_workflow_as_tool(self, e2e_client, platform_admin):
        """Agent tool_ids must reference type='tool', not regular workflows."""
        workflow_content = '''"""Test Workflow for Validation"""
from bifrost import workflow

@workflow(
    name="validation_test_workflow",
    description="A workflow created for type validation testing",
)
async def validation_test_workflow(input: str) -> str:
    return f"Processed: {input}"
'''
        result = write_and_register(
            e2e_client, platform_admin.headers,
            "validation_test_workflow.py", workflow_content, "validation_test_workflow",
        )
        assert result["type"] == "workflow", f"Should be type='workflow', got {result['type']}"

        # Try to create agent with regular workflow as tool
        response = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": "Test Agent with Wrong Tool Type",
                "description": "Testing tool type validation",
                "system_prompt": "You are a test agent.",
                "channels": ["chat"],
                "tool_ids": [result["id"]],
            },
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}: {response.text}"
        assert response.json()["detail"] == "Workflow 'validation_test_workflow' is not a tool"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=validation_test_workflow.py",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestMultipleValidationErrors:
    """Test that multiple validation errors are returned together."""

    def test_form_multiple_invalid_references(self, e2e_client, platform_admin):
        """Form with multiple invalid references returns all errors."""
        fake_workflow_id = str(uuid4())
        fake_launch_id = str(uuid4())
        fake_dp_id = str(uuid4())

        response = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Test Form with Multiple Errors",
                "workflow_id": fake_workflow_id,
                "launch_workflow_id": fake_launch_id,
                "form_schema": {
                    "fields": [
                        {
                            "name": "category",
                            "type": "select",
                            "label": "Category",
                            "required": True,
                            "data_provider_id": fake_dp_id,
                        }
                    ]
                },
                "access_level": "authenticated",
            },
        )
        assert response.status_code == 422, f"Expected 422, got {response.status_code}"
        data = response.json()
        errors = data.get("detail", {}).get("errors", [])

        # Should have at least 3 errors (one for each invalid reference)
        assert len(errors) >= 3, f"Expected at least 3 errors, got {len(errors)}: {errors}"
        assert any(fake_workflow_id in err for err in errors)
        assert any(fake_launch_id in err for err in errors)
        assert any(fake_dp_id in err for err in errors)

    def test_agent_unknown_tool_is_denied_before_other_reference_validation(self, e2e_client, platform_admin):
        """Tool authorization runs before delegated-agent structural validation."""
        fake_tool_id = str(uuid4())
        fake_delegate_id = str(uuid4())

        response = e2e_client.post(
            "/api/agents",
            headers=platform_admin.headers,
            json={
                "name": "Test Agent with Multiple Errors",
                "description": "Testing multiple validation errors",
                "system_prompt": "You are a test agent.",
                "channels": ["chat"],
                "tool_ids": [fake_tool_id],
                "delegated_agent_ids": [fake_delegate_id],
            },
        )
        assert response.status_code == 403, f"Expected 403, got {response.status_code}"
        assert response.json()["detail"] == f"You do not have role access to tool '{fake_tool_id}'"
