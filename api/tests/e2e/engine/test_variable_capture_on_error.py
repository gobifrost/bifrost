"""
Test variable capture during workflow exceptions.
"""

import pytest
from src.sdk.decorators import workflow
from src.services.execution.engine import execute, ExecutionRequest, ExecutionStatus
from src.sdk.context import Caller, Organization


class TestVariableCaptureOnError:
    """Test that variables are captured when workflows raise exceptions"""

    @pytest.mark.asyncio
    async def test_variables_captured_on_exception(self):
        """Test that local variables are captured when a workflow raises an exception"""

        @workflow(
            name="failing_workflow_with_vars",
            description="Workflow that fails after creating local variables",
            category="test"
        )
        async def failing_workflow(context):
            """Workflow that creates variables then fails"""
            # Create some local variables (intentionally unused for testing variable capture)
            user_name = "John Doe"  # noqa: F841
            user_age = 30  # noqa: F841
            settings = {"theme": "dark", "notifications": True}  # noqa: F841
            counter = 42  # noqa: F841

            # Now raise an exception
            raise ValueError("Something went wrong!")

        # Execute workflow and expect failure
        request = ExecutionRequest(
            execution_id="test-exec-123",
            caller=Caller(user_id="test-user", email="test@example.com", name="Test User"),
            organization=Organization(id="test-org", name="Test Org"),
            name="failing_workflow_with_vars",
            parameters={},
            func=failing_workflow
        )

        result = await execute(request)

        # Verify execution failed
        assert result.status == ExecutionStatus.FAILED
        assert result.error_type == "ValueError"
        assert "Something went wrong!" in result.error_message

        # Verify variables were captured despite the exception
        assert result.variables is not None
        assert "user_name" in result.variables
        assert result.variables["user_name"] == "John Doe"
        assert "user_age" in result.variables
        assert result.variables["user_age"] == 30
        assert "settings" in result.variables
        assert result.variables["settings"] == {"theme": "dark", "notifications": True}
        assert "counter" in result.variables
        assert result.variables["counter"] == 42

    @pytest.mark.asyncio
    async def test_variables_captured_on_async_exception(self):
        """Test that variables are captured when an async operation fails"""

        @workflow(
            name="async_failing_workflow",
            description="Workflow that fails during async operation",
            category="test"
        )
        async def async_failing_workflow(context):
            """Workflow with async operations that fails"""
            import asyncio

            # Create some local variables (intentionally unused for testing variable capture)
            data = {"id": 123, "name": "Test"}  # noqa: F841
            status = "processing"  # noqa: F841
            attempt = 1  # noqa: F841

            # Simulate async operation
            await asyncio.sleep(0.01)

            # Update variables
            status = "failed"  # noqa: F841
            attempt = 2  # noqa: F841

            # Raise exception after async operation
            raise RuntimeError("Async operation failed")

        # Execute workflow
        request = ExecutionRequest(
            execution_id="test-exec-456",
            caller=Caller(user_id="test-user", email="test@example.com", name="Test User"),
            organization=Organization(id="test-org", name="Test Org"),
            name="async_failing_workflow",
            parameters={},
            func=async_failing_workflow
        )

        result = await execute(request)

        # Verify execution failed
        assert result.status == ExecutionStatus.FAILED
        assert result.error_type == "RuntimeError"
        assert "Async operation failed" in result.error_message

        # Verify variables were captured with their latest values
        assert result.variables is not None
        assert "data" in result.variables
        assert result.variables["data"] == {"id": 123, "name": "Test"}
        assert "status" in result.variables
        assert result.variables["status"] == "failed"  # Updated value
        assert "attempt" in result.variables
        assert result.variables["attempt"] == 2  # Updated value

    @pytest.mark.asyncio
    async def test_attribute_error_captures_dict_variable(self):
        """Test the exact scenario: accessing dict as object attribute"""

        @workflow(
            name="dict_attribute_error_workflow",
            description="Workflow that fails with AttributeError on dict",
            category="test"
        )
        async def dict_attribute_workflow(context):
            """Simulates the oauth.get_token() scenario"""
            # Simulate oauth response as dict
            oauth_response = {
                "access_token": "test_token_123",
                "expires_in": 3600
            }

            url = "https://graph.microsoft.com/v1.0/users"  # noqa: F841
            headers = {"Authorization": f"Bearer {oauth_response.access_token}"}  # noqa: F841

            return {"success": True}

        # Execute workflow
        request = ExecutionRequest(
            execution_id="test-exec-attr",
            caller=Caller(user_id="test-user", email="test@example.com", name="Test User"),
            organization=Organization(id="test-org", name="Test Org"),
            name="dict_attribute_error_workflow",
            parameters={},
            func=dict_attribute_workflow
        )

        result = await execute(request)

        # Verify execution failed with AttributeError
        assert result.status == ExecutionStatus.FAILED
        assert result.error_type == "AttributeError"

        # Verify variables were captured
        assert result.variables is not None
        assert "oauth_response" in result.variables
        assert result.variables["oauth_response"] == {
            "access_token": "test_token_123",
            "expires_in": 3600
        }
        assert "url" in result.variables  # noqa: F821
        assert result.variables["url"] == "https://graph.microsoft.com/v1.0/users"
        # Note: headers might not be captured since the error happens on that line
