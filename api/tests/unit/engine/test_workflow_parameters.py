"""
Unit tests for workflow parameter parsing and passing.

Tests that workflow parameters are correctly:
1. Extracted from function signatures (no @param decorator)
2. Passed to workflow functions during execution
3. Handled with/without ExecutionContext parameter
"""

import pytest
from enum import Enum
from typing import Literal

from src.sdk.context import ExecutionContext, Organization
from src.sdk.decorators import workflow
from src.services.execution.engine import _execute_workflow_with_trace


@pytest.fixture
def mock_context():
    """Mock execution context for testing"""
    return ExecutionContext(
        user_id="test-user-123",
        email="test@example.com",
        name="Test User",
        scope="test-org-456",
        organization=Organization(
            id="test-org-456",
            name="Test Organization",
            is_active=True
        ),
        is_platform_admin=False,
        is_function_key=False,
        execution_id="test-exec-789"
    )


class TestWorkflowParameterParsing:
    """Test parameter parsing from function signatures"""

    def test_parameters_attached_to_metadata(self):
        """Test that function signature creates parameters in workflow metadata"""
        @workflow(name="test_workflow", description="Test")
        async def test_func(name: str, age: int = 25):
            return {"name": name, "age": age}

        # Verify metadata attached
        assert hasattr(test_func, '_executable_metadata')
        metadata = test_func._executable_metadata

        # Verify parameters were extracted from signature
        assert len(metadata.parameters) == 2

        name_param = metadata.parameters[0]
        assert name_param.name == "name"
        assert name_param.type == "string"
        assert name_param.required is True

        age_param = metadata.parameters[1]
        assert age_param.name == "age"
        assert age_param.type == "int"
        assert age_param.required is False
        assert age_param.default_value == 25

    def test_parameters_with_various_types(self):
        """Test parameters with different type annotations"""
        @workflow(name="test_workflow", description="Test")
        async def test_func(
            text: str,
            number: int,
            fraction: float,
            flag: bool = True,
            items: list | None = None,
            config: dict | None = None
        ):
            return {}

        metadata = test_func._executable_metadata

        # Verify parameter types were correctly inferred
        types = {p.name: p.type for p in metadata.parameters}
        assert types["text"] == "string"
        assert types["number"] == "int"
        assert types["fraction"] == "float"
        assert types["flag"] == "bool"
        assert types["items"] == "list"
        assert types["config"] == "json"

    def test_parameters_preserve_nested_list_and_enum_schema(self):
        """Test that richer annotations survive onto workflow metadata."""

        class Status(str, Enum):
            OPEN = "open"
            CLOSED = "closed"

        @workflow(name="test_workflow", description="Test")
        async def test_func(
            payload_items: list[dict[str, int]],
            status: Literal["open", "closed"],
            severity: Status,
        ):
            return {}

        metadata = test_func._executable_metadata
        params = {param.name: param for param in metadata.parameters}

        assert params["payload_items"].type == "list"
        assert params["payload_items"].python_type == "list[dict[str, int]]"
        assert params["payload_items"].json_schema == {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": {"type": "integer"},
            },
        }

        assert params["status"].options == [
            {"label": "open", "value": "open"},
            {"label": "closed", "value": "closed"},
        ]
        assert params["status"].json_schema == {
            "enum": ["open", "closed"],
            "type": "string",
        }

        assert params["severity"].options == [
            {"label": "open", "value": "open"},
            {"label": "closed", "value": "closed"},
        ]
        assert params["severity"].json_schema == {
            "enum": ["open", "closed"],
            "type": "string",
        }


class TestWorkflowParameterExecution:
    """Test parameter passing during workflow execution"""

    @pytest.mark.asyncio
    async def test_workflow_receives_parameters_with_context(self, mock_context):
        """Test that workflow receives both context and parameters"""
        @workflow(name="simple_greeting", description="Simple greeting")
        async def simple_greeting(
            context: ExecutionContext,
            name: str,
            greeting_type: str = "Hello"
        ):
            return {
                "greeting": f"{greeting_type}, {name}!",
                "org_id": context.org_id
            }

        # Execute with parameters
        result, captured_vars, logs = await _execute_workflow_with_trace(
            simple_greeting,
            mock_context,
            {"name": "Alice", "greeting_type": "Hi"}
        )

        # Verify result
        assert result["greeting"] == "Hi, Alice!"
        assert result["org_id"] == "test-org-456"

    @pytest.mark.asyncio
    async def test_workflow_with_default_parameters(self, mock_context):
        """Test that default parameters work correctly"""
        @workflow(name="greeting_with_defaults", description="Greeting with defaults")
        async def greeting_workflow(
            context: ExecutionContext,
            name: str,
            greeting_type: str = "Hello",
            include_time: bool = False
        ):
            greeting = f"{greeting_type}, {name}!"
            if include_time:
                greeting += " (with time)"
            return {"greeting": greeting}

        # Execute with only required parameter
        result, _, _ = await _execute_workflow_with_trace(
            greeting_workflow,
            mock_context,
            {"name": "Bob"}
        )

        assert result["greeting"] == "Hello, Bob!"

        # Execute with all parameters
        result, _, _ = await _execute_workflow_with_trace(
            greeting_workflow,
            mock_context,
            {"name": "Bob", "greeting_type": "Hey", "include_time": True}
        )

        assert result["greeting"] == "Hey, Bob! (with time)"

    @pytest.mark.asyncio
    async def test_workflow_without_context_parameter(self, mock_context):
        """Test workflow that doesn't take context parameter"""
        @workflow(name="no_context_workflow", description="No context")
        async def no_context_workflow(value: int):
            return {"doubled": value * 2}

        # Execute - should work without passing context to function
        result, _, _ = await _execute_workflow_with_trace(
            no_context_workflow,
            mock_context,
            {"value": 21}
        )

        assert result["doubled"] == 42

    @pytest.mark.asyncio
    async def test_workflow_with_multiple_types(self, mock_context):
        """Test workflow with various parameter types"""
        @workflow(name="multi_type_workflow", description="Multiple types")
        async def multi_type_workflow(
            context: ExecutionContext,
            text: str,
            number: int,
            enabled: bool = True
        ):
            return {
                "text": text,
                "number": number,
                "enabled": enabled,
                "org": context.org_id
            }

        result, _, _ = await _execute_workflow_with_trace(
            multi_type_workflow,
            mock_context,
            {"text": "hello", "number": 42, "enabled": False}
        )

        assert result["text"] == "hello"
        assert result["number"] == 42
        assert result["enabled"] is False
        assert result["org"] == "test-org-456"

    @pytest.mark.asyncio
    async def test_missing_required_parameter_raises_error(self, mock_context):
        """Test that missing required parameter raises TypeError"""
        @workflow(name="required_param_workflow", description="Required param")
        async def required_param_workflow(
            context: ExecutionContext,
            required_field: str
        ):
            return {"value": required_field}

        # Execute without required parameter - should raise TypeError
        with pytest.raises(TypeError, match="missing 1 required positional argument: 'required_field'"):
            await _execute_workflow_with_trace(
                required_param_workflow,
                mock_context,
                {}  # Empty parameters
            )

    @pytest.mark.asyncio
    async def test_extra_parameters_stored_in_context(self, mock_context):
        """Test that extra parameters (not in function signature) are stored in context.parameters"""
        @workflow(name="extra_params_workflow", description="Extra params")
        async def extra_params_workflow(
            context: ExecutionContext,
            expected_param: str
        ):
            # extra params are now available via context.parameters (not globals)
            # This avoids race conditions when concurrent workflows share the same module
            return {
                "expected": expected_param,
                "extra_from_context": context.parameters.get("extra_param"),
                "all_extra_params": dict(context.parameters)
            }

        result, captured_vars, _ = await _execute_workflow_with_trace(
            extra_params_workflow,
            mock_context,
            {
                "expected_param": "value1",
                "extra_param": "value2"
            }
        )

        assert result["expected"] == "value1"
        # Extra params should be accessible via context.parameters
        assert result["extra_from_context"] == "value2"
        assert result["all_extra_params"] == {"expected_param": "value1", "extra_param": "value2"}
        # Extra params should also be captured in variables for execution details
        assert "extra_param" in captured_vars
        assert captured_vars["extra_param"] == "value2"

    @pytest.mark.asyncio
    async def test_workflow_with_var_keyword_accepts_all_params(self, mock_context):
        """Test that workflow with **kwargs accepts all parameters"""
        @workflow(name="kwargs_workflow", description="Kwargs workflow")
        async def kwargs_workflow(context: ExecutionContext, **kwargs):
            return {
                "name": kwargs.get("name"),
                "optional": kwargs.get("optional"),
                "org": context.org_id
            }

        result, _, _ = await _execute_workflow_with_trace(
            kwargs_workflow,
            mock_context,
            {"name": "Alice", "optional": "extra", "random_field": "ignored"}
        )

        assert result["name"] == "Alice"
        assert result["optional"] == "extra"
        assert result["org"] == "test-org-456"


class TestContextParameterDetection:
    """Test detection of context parameter in function signatures"""

    @pytest.mark.asyncio
    async def test_context_by_type_annotation(self, mock_context):
        """Test context detection by ExecutionContext type annotation"""
        @workflow(name="test", description="Test")
        async def workflow_func(ctx: ExecutionContext, value: int):
            return {"org": ctx.org_id, "value": value}

        result, _, _ = await _execute_workflow_with_trace(
            workflow_func,
            mock_context,
            {"value": 42}
        )

        assert result["org"] == "test-org-456"
        assert result["value"] == 42

    @pytest.mark.asyncio
    async def test_context_by_parameter_name(self, mock_context):
        """Test context detection by 'context' parameter name (legacy)"""
        @workflow(name="test", description="Test")
        async def workflow_func(context, value: int):
            return {"org": context.org_id, "value": value}

        result, _, _ = await _execute_workflow_with_trace(
            workflow_func,
            mock_context,
            {"value": 42}
        )

        assert result["org"] == "test-org-456"
        assert result["value"] == 42

    @pytest.mark.asyncio
    async def test_no_context_parameter(self, mock_context):
        """Test workflow without context parameter"""
        @workflow(name="test", description="Test")
        async def workflow_func(value: int):
            return {"value": value * 2}

        result, _, _ = await _execute_workflow_with_trace(
            workflow_func,
            mock_context,
            {"value": 21}
        )

        assert result["value"] == 42
