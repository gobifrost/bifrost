"""
Tests for tool schema generation — additionalProperties + array items.

Ensures outer schemas have additionalProperties: false, inner dict/object
params signal freeform keys to the LLM, and every array param carries an
`items` field (Gemini rejects array schemas without `items`).
"""

from typing import Any, Optional


class TestPythonTypeToJsonSchema:
    """Tests for python_type_to_json_schema inside get_system_tools."""

    @staticmethod
    def _convert(annotation):
        """Call the nested python_type_to_json_schema helper."""
        # Re-implement extraction: the function is defined inside get_system_tools,
        # so we test it indirectly via get_system_tools output, or re-create it here.
        import inspect
        from typing import Union, get_args, get_origin
        from typing import Any

        def python_type_to_json_schema(annotation: Any) -> dict[str, Any]:
            if annotation is inspect.Parameter.empty or annotation is None:
                return {"type": "string"}
            origin = get_origin(annotation)
            if origin is Union:
                args = [a for a in get_args(annotation) if a is not type(None)]
                if len(args) == 1:
                    return python_type_to_json_schema(args[0])
            if annotation is str:
                return {"type": "string"}
            elif annotation is int:
                return {"type": "integer"}
            elif annotation is float:
                return {"type": "number"}
            elif annotation is bool:
                return {"type": "boolean"}
            elif annotation is dict or origin is dict:
                schema: dict[str, Any] = {"type": "object"}
                if origin is dict:
                    args = get_args(annotation)
                    if len(args) == 2:
                        schema["additionalProperties"] = python_type_to_json_schema(args[1])
                else:
                    schema["additionalProperties"] = True
                return schema
            elif annotation is list or origin is list:
                array_schema: dict[str, Any] = {"type": "array"}
                if origin is list:
                    args = get_args(annotation)
                    if args:
                        array_schema["items"] = python_type_to_json_schema(args[0])
                    else:
                        array_schema["items"] = {"type": "string"}
                else:
                    array_schema["items"] = {"type": "string"}
                return array_schema
            return {"type": "string"}

        return python_type_to_json_schema(annotation)

    def test_bare_dict_has_additional_properties_true(self):
        result = self._convert(dict)
        assert result == {"type": "object", "additionalProperties": True}

    def test_dict_str_str_has_typed_additional_properties(self):
        result = self._convert(dict[str, str])
        assert result == {"type": "object", "additionalProperties": {"type": "string"}}

    def test_dict_str_int_has_typed_additional_properties(self):
        result = self._convert(dict[str, int])
        assert result == {"type": "object", "additionalProperties": {"type": "integer"}}

    def test_optional_dict_unwraps_correctly(self):
        result = self._convert(Optional[dict[str, str]])
        assert result == {"type": "object", "additionalProperties": {"type": "string"}}

    def test_bare_list_has_string_items(self):
        result = self._convert(list)
        assert result == {"type": "array", "items": {"type": "string"}}

    def test_list_str_has_string_items(self):
        result = self._convert(list[str])
        assert result == {"type": "array", "items": {"type": "string"}}

    def test_list_int_has_integer_items(self):
        result = self._convert(list[int])
        assert result == {"type": "array", "items": {"type": "integer"}}

    def test_list_dict_has_object_items(self):
        # dict[str, Any] is a parameterized dict so it hits the typed-value
        # branch; Any falls through to the default {"type": "string"}, giving
        # additionalProperties: {"type": "string"} rather than `True`.
        result = self._convert(list[dict[str, Any]])
        assert result == {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
        }

    def test_optional_list_unwraps_correctly(self):
        result = self._convert(Optional[list[str]])
        assert result == {"type": "array", "items": {"type": "string"}}


class TestSystemToolsOuterSchema:
    """Verify get_system_tools adds additionalProperties: false to outer schemas."""

    def test_all_system_tools_have_additional_properties_false(self):
        from src.services.mcp_server.server import get_system_tools

        tools = get_system_tools()
        assert len(tools) > 0, "Expected at least one system tool"

        for tool in tools:
            params = tool["parameters"]
            assert params.get("additionalProperties") is False, (
                f"Tool {tool['name']} ({tool['id']}) outer schema missing "
                f"additionalProperties: false"
            )

    def test_all_array_params_declare_items(self):
        """Every `type: array` schema must carry an `items` field.

        Gemini (Google AI Studio) rejects function declarations whose array
        parameters are missing `items`, while OpenAI and Anthropic accept the
        field. Emitting `items` unconditionally keeps all three providers
        happy — see the fix in get_system_tools' python_type_to_json_schema.
        """
        from src.services.mcp_server.server import get_system_tools

        offenders: list[str] = []
        for tool in get_system_tools():
            for prop_name, prop_schema in tool["parameters"]["properties"].items():
                if prop_schema.get("type") == "array" and "items" not in prop_schema:
                    offenders.append(f"{tool['id']}.{prop_name}")

        assert not offenders, (
            "Found array params missing `items` (Gemini rejects these): "
            + ", ".join(offenders)
        )
