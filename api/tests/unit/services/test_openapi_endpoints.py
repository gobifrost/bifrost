"""
Unit tests for OpenAPI endpoint generation service.

Tests the dynamic OpenAPI schema generation for workflow endpoints,
including parameter mapping, method handling, and schema structure.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.services.openapi_endpoints import (
    generate_workflow_openapi_schema,
    _param_to_openapi_schema,
    get_endpoint_enabled_workflows,
    TYPE_TO_OPENAPI,
)


class TestParamToOpenAPISchema:
    """Tests for parameter type conversion to OpenAPI schema."""

    def test_string_type(self):
        """String parameters map to OpenAPI string type."""
        param = {"name": "message", "type": "string", "required": True}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "string"}

    def test_int_type(self):
        """Int parameters map to OpenAPI integer type."""
        param = {"name": "count", "type": "int", "required": True}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "integer"}

    def test_float_type(self):
        """Float parameters map to OpenAPI number type."""
        param = {"name": "price", "type": "float", "required": False}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "number"}

    def test_bool_type(self):
        """Bool parameters map to OpenAPI boolean type."""
        param = {"name": "enabled", "type": "bool", "required": True}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "boolean"}

    def test_list_type(self):
        """List parameters map to OpenAPI array type."""
        param = {"name": "items", "type": "list", "required": False}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "array", "items": {"type": "string"}}

    def test_json_type(self):
        """JSON/dict parameters map to OpenAPI object type."""
        param = {"name": "data", "type": "json", "required": True}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "object"}

    def test_default_value_included(self):
        """Default values are included in schema."""
        param = {
            "name": "count",
            "type": "int",
            "required": False,
            "default_value": 5,
        }
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "integer", "default": 5}

    def test_unknown_type_defaults_to_string(self):
        """Unknown types default to string."""
        param = {"name": "custom", "type": "custom_type", "required": True}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "string"}

    def test_nested_json_schema_is_preserved(self):
        """Rich json_schema payloads are preserved for OpenAPI consumers."""
        param = {
            "name": "payload_items",
            "type": "list",
            "required": True,
            "json_schema": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                },
            },
        }
        schema = _param_to_openapi_schema(param)
        assert schema == {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": {"type": "integer"},
            },
        }


class TestGenerateWorkflowOpenAPISchema:
    """Tests for generating complete OpenAPI path schemas for workflows."""

    @pytest.fixture
    def mock_workflow(self):
        """Create a mock workflow with endpoint configuration."""
        workflow = MagicMock()
        workflow.name = "test_workflow"
        workflow.description = "A test workflow for unit testing"
        workflow.endpoint_enabled = True
        workflow.allowed_methods = ["GET", "POST"]
        workflow.parameters_schema = [
            {"name": "message", "type": "string", "required": True, "label": "Message"},
            {"name": "count", "type": "int", "required": False, "label": "Count", "default_value": 1},
        ]
        return workflow

    def test_generates_operations_for_allowed_methods(self, mock_workflow):
        """Schema includes operations for each allowed HTTP method."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert "get" in schema
        assert "post" in schema
        assert "put" not in schema
        assert "delete" not in schema

    def test_operation_has_correct_summary(self, mock_workflow):
        """Operations have workflow name as summary."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert schema["get"]["summary"] == "test_workflow"
        assert schema["post"]["summary"] == "test_workflow"

    def test_operation_has_description(self, mock_workflow):
        """Operations include workflow description."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert schema["get"]["description"] == "A test workflow for unit testing"

    def test_operation_has_operation_id(self, mock_workflow):
        """Operations have unique operation IDs."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert schema["get"]["operationId"] == "execute_test_workflow_get"
        assert schema["post"]["operationId"] == "execute_test_workflow_post"

    def test_operations_tagged_as_workflow_endpoints(self, mock_workflow):
        """All operations are tagged as Workflow Endpoints."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert schema["get"]["tags"] == ["Workflow Endpoints"]
        assert schema["post"]["tags"] == ["Workflow Endpoints"]

    def test_operations_require_api_key_security(self, mock_workflow):
        """Operations require BifrostApiKey security."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert schema["get"]["security"] == [{"BifrostApiKey": []}]
        assert schema["post"]["security"] == [{"BifrostApiKey": []}]

    def test_get_has_query_parameters(self, mock_workflow):
        """GET operations have query parameters."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        params = schema["get"]["parameters"]
        assert len(params) == 2

        message_param = next(p for p in params if p["name"] == "message")
        assert message_param["in"] == "query"
        assert message_param["required"] is True
        assert message_param["schema"] == {"type": "string"}
        assert message_param["description"] == "Message"

        count_param = next(p for p in params if p["name"] == "count")
        assert count_param["in"] == "query"
        assert count_param["required"] is False
        assert count_param["schema"] == {"type": "integer", "default": 1}

    def test_post_has_request_body(self, mock_workflow):
        """POST operations have request body schema."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert "requestBody" in schema["post"]
        request_body = schema["post"]["requestBody"]
        assert request_body["required"] is False

        body_schema = request_body["content"]["application/json"]["schema"]
        assert body_schema["type"] == "object"
        assert "message" in body_schema["properties"]
        assert "count" in body_schema["properties"]
        assert "message" in body_schema["required"]
        assert "count" not in body_schema["required"]

    def test_operations_have_responses(self, mock_workflow):
        """Operations define response schemas."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        responses = schema["get"]["responses"]
        assert "200" in responses
        assert "401" in responses
        assert "404" in responses
        assert "405" in responses

        # 200 response references EndpointExecuteResponse
        assert responses["200"]["content"]["application/json"]["schema"]["$ref"] == \
            "#/components/schemas/EndpointExecuteResponse"

    def test_delete_method_no_request_body(self, mock_workflow):
        """DELETE operations don't have request body."""
        mock_workflow.allowed_methods = ["DELETE"]
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert "requestBody" not in schema["delete"]

    def test_workflow_with_no_parameters(self):
        """Workflows without parameters still generate valid schema."""
        workflow = MagicMock()
        workflow.name = "simple_workflow"
        workflow.description = "A simple workflow"
        workflow.endpoint_enabled = True
        workflow.allowed_methods = ["POST"]
        workflow.parameters_schema = []

        schema = generate_workflow_openapi_schema(workflow)

        # Should still have post operation
        assert "post" in schema

        # Parameters should be empty or not present
        params = schema["post"].get("parameters", [])
        assert len(params) == 0

        # Request body should have empty properties
        body_schema = schema["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert body_schema["properties"] == {}

    def test_workflow_with_default_methods(self):
        """Workflows with None allowed_methods default to POST."""
        workflow = MagicMock()
        workflow.name = "default_workflow"
        workflow.description = None
        workflow.endpoint_enabled = True
        workflow.allowed_methods = None  # Should default to POST
        workflow.parameters_schema = []

        schema = generate_workflow_openapi_schema(workflow)

        assert "post" in schema
        assert len(schema) == 1

    def test_missing_description_uses_fallback(self):
        """Workflows without description use fallback text."""
        workflow = MagicMock()
        workflow.name = "no_desc_workflow"
        workflow.description = None
        workflow.endpoint_enabled = True
        workflow.allowed_methods = ["GET"]
        workflow.parameters_schema = []

        schema = generate_workflow_openapi_schema(workflow)

        assert schema["get"]["description"] == "Execute no_desc_workflow workflow"


class TestGetEndpointEnabledWorkflows:
    """Tests for querying endpoint-enabled workflows from database."""

    @pytest.mark.asyncio
    async def test_queries_active_endpoint_enabled_workflows(self):
        """Query filters for endpoint_enabled=True and is_active=True."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        with patch("src.services.openapi_endpoints.select") as mock_select:
            mock_query = MagicMock()
            mock_select.return_value.where.return_value = mock_query

            result = await get_endpoint_enabled_workflows(mock_db)

            assert result == []
            mock_db.execute.assert_called_once()


class TestTypeMapping:
    """Tests for the TYPE_TO_OPENAPI mapping."""

    def test_all_expected_types_mapped(self):
        """All common types have OpenAPI mappings."""
        expected_types = [
            "string", "str",
            "int", "integer",
            "float", "number",
            "bool", "boolean",
            "list", "array",
            "json", "dict", "object",
        ]

        for type_name in expected_types:
            assert type_name in TYPE_TO_OPENAPI, f"Missing mapping for {type_name}"

    def test_string_variants_consistent(self):
        """String type variants produce same schema."""
        assert TYPE_TO_OPENAPI["string"] == TYPE_TO_OPENAPI["str"]

    def test_integer_variants_consistent(self):
        """Integer type variants produce same schema."""
        assert TYPE_TO_OPENAPI["int"] == TYPE_TO_OPENAPI["integer"]

    def test_boolean_variants_consistent(self):
        """Boolean type variants produce same schema."""
        assert TYPE_TO_OPENAPI["bool"] == TYPE_TO_OPENAPI["boolean"]

    def test_array_variants_consistent(self):
        """Array type variants produce same schema."""
        assert TYPE_TO_OPENAPI["list"] == TYPE_TO_OPENAPI["array"]

    def test_object_variants_consistent(self):
        """Object type variants produce same schema."""
        assert TYPE_TO_OPENAPI["json"] == TYPE_TO_OPENAPI["dict"] == TYPE_TO_OPENAPI["object"]
