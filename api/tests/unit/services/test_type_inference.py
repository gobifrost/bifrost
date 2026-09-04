import inspect
from enum import Enum
from typing import Any, Literal, Optional, Union

import pytest

from src.services.execution.type_inference import (
    build_json_schema,
    _is_execution_context,
    extract_parameters_from_signature,
    generate_label,
    get_literal_options,
    get_python_type_name,
    get_ui_type,
    is_optional_type,
)


class TestIsExecutionContext:

    def test_none_returns_false(self):
        assert _is_execution_context(None) is False

    def test_empty_annotation_returns_false(self):
        assert _is_execution_context(inspect.Parameter.empty) is False

    def test_string_execution_context_returns_true(self):
        assert _is_execution_context("ExecutionContext") is True

    def test_string_containing_execution_context_returns_true(self):
        assert _is_execution_context("Optional[ExecutionContext]") is True

    def test_class_named_execution_context_returns_true(self):
        class ExecutionContext:
            pass

        assert _is_execution_context(ExecutionContext) is True

    def test_regular_str_type_returns_false(self):
        assert _is_execution_context(str) is False

    def test_regular_int_type_returns_false(self):
        assert _is_execution_context(int) is False

    def test_unrelated_string_returns_false(self):
        assert _is_execution_context("SomeOtherType") is False

    def test_class_with_qualname_containing_execution_context(self):
        class Outer:
            class ExecutionContext:
                pass

        assert _is_execution_context(Outer.ExecutionContext) is True


class TestGetUiType:

    @pytest.mark.parametrize(
        "python_type, expected",
        [
            (str, "string"),
            (int, "int"),
            (float, "float"),
            (bool, "bool"),
            (list, "list"),
            (dict, "json"),
        ],
    )
    def test_direct_type_mapping(self, python_type, expected):
        assert get_ui_type(python_type) == expected

    def test_generic_list(self):
        assert get_ui_type(list[str]) == "list"

    def test_generic_dict(self):
        assert get_ui_type(dict[str, Any]) == "json"

    def test_none_type(self):
        assert get_ui_type(type(None)) == "string"

    def test_optional_str(self):
        assert get_ui_type(Optional[str]) == "string"

    def test_optional_int(self):
        assert get_ui_type(Optional[int]) == "int"

    def test_union_str_none(self):
        assert get_ui_type(Union[str, None]) == "string"

    def test_pipe_union_str_none(self):
        assert get_ui_type(str | None) == "string"

    def test_literal_strings(self):
        assert get_ui_type(Literal["a", "b"]) == "string"

    def test_literal_ints(self):
        assert get_ui_type(Literal[1, 2]) == "int"

    def test_literal_bools(self):
        assert get_ui_type(Literal[True, False]) == "bool"

    def test_unknown_type_returns_json(self):
        class Custom:
            pass

        assert get_ui_type(Custom) == "json"


class TestIsOptionalType:

    def test_str_not_optional(self):
        assert is_optional_type(str) is False

    def test_int_not_optional(self):
        assert is_optional_type(int) is False

    def test_optional_str(self):
        assert is_optional_type(Optional[str]) is True

    def test_union_str_none(self):
        assert is_optional_type(Union[str, None]) is True

    def test_pipe_union_str_none(self):
        assert is_optional_type(str | None) is True

    def test_union_without_none(self):
        assert is_optional_type(Union[str, int]) is False


class TestGetLiteralOptions:

    def test_literal_strings(self):
        result = get_literal_options(Literal["Open", "Closed"])
        assert result == [
            {"label": "Open", "value": "Open"},
            {"label": "Closed", "value": "Closed"},
        ]

    def test_non_literal_returns_none(self):
        assert get_literal_options(str) is None

    def test_optional_literal(self):
        result = get_literal_options(Optional[Literal["a", "b"]])
        assert result == [
            {"label": "a", "value": "a"},
            {"label": "b", "value": "b"},
        ]

    def test_pipe_union_literal(self):
        result = get_literal_options(Literal["x", "y"] | None)
        assert result == [
            {"label": "x", "value": "x"},
            {"label": "y", "value": "y"},
        ]

    def test_literal_ints_as_string_values(self):
        result = get_literal_options(Literal[1, 2, 3])
        assert result == [
            {"label": "1", "value": "1"},
            {"label": "2", "value": "2"},
            {"label": "3", "value": "3"},
        ]

    def test_plain_int_returns_none(self):
        assert get_literal_options(int) is None

    def test_enum_members_return_options(self):
        class TicketState(str, Enum):
            OPEN = "open"
            CLOSED = "closed"

        assert get_literal_options(TicketState) == [
            {"label": "open", "value": "open"},
            {"label": "closed", "value": "closed"},
        ]


class TestGenerateLabel:

    @pytest.mark.parametrize(
        "param_name, expected",
        [
            ("user_email", "User Email"),
            ("firstName", "First Name"),
            ("api_key", "Api Key"),
            ("name", "Name"),
        ],
    )
    def test_label_generation(self, param_name, expected):
        assert generate_label(param_name) == expected

    def test_multiple_underscores(self):
        assert generate_label("my_long_param_name") == "My Long Param Name"

    def test_all_caps_stays_title(self):
        assert generate_label("URL") == "Url"


class TestExtractParametersFromSignature:

    def test_simple_typed_args(self):
        def func(name: str, count: int):
            pass

        params = extract_parameters_from_signature(func)
        assert len(params) == 2
        assert params[0]["name"] == "name"
        assert params[0]["type"] == "string"
        assert params[0]["required"] is True
        assert params[0]["label"] == "Name"
        assert params[0]["python_type"] == "str"
        assert params[0]["json_schema"] == {"type": "string"}
        assert params[1]["name"] == "count"
        assert params[1]["type"] == "int"
        assert params[1]["required"] is True
        assert params[1]["label"] == "Count"
        assert params[1]["python_type"] == "int"
        assert params[1]["json_schema"] == {"type": "integer"}

    def test_nested_generics_and_enum_metadata(self):
        class Severity(str, Enum):
            LOW = "low"
            HIGH = "high"

        def func(
            payload_items: list[dict[str, int]],
            status: Literal["open", "closed"],
            severity: Severity,
        ):
            pass

        params = extract_parameters_from_signature(func)
        payload = next(param for param in params if param["name"] == "payload_items")
        assert payload["type"] == "list"
        assert payload["python_type"] == "list[dict[str, int]]"
        assert payload["json_schema"] == {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": {"type": "integer"},
            },
        }

        status = next(param for param in params if param["name"] == "status")
        assert status["options"] == [
            {"label": "open", "value": "open"},
            {"label": "closed", "value": "closed"},
        ]
        assert status["json_schema"] == {
            "enum": ["open", "closed"],
            "type": "string",
        }

        severity = next(param for param in params if param["name"] == "severity")
        assert severity["options"] == [
            {"label": "low", "value": "low"},
            {"label": "high", "value": "high"},
        ]
        assert severity["json_schema"] == {
            "enum": ["low", "high"],
            "type": "string",
        }

    def test_python_type_name_helper(self):
        assert get_python_type_name(list[dict[str, Any]]) == "list[dict[str, Any]]"
        assert get_python_type_name(Literal["a", "b"]) == "Literal['a', 'b']"
        assert get_python_type_name(int | None) == "int | None"

    def test_json_schema_helper(self):
        assert build_json_schema(list[dict[str, Any]]) == {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
        }
        assert build_json_schema(Literal["a", "b"]) == {
            "enum": ["a", "b"],
            "type": "string",
        }

    def test_default_values(self):
        def func(name: str = "world", active: bool = True):
            pass

        params = extract_parameters_from_signature(func)
        assert len(params) == 2
        assert params[0]["required"] is False
        assert params[0]["default_value"] == "world"
        assert params[1]["required"] is False
        assert params[1]["default_value"] is True

    def test_none_default_not_included(self):
        def func(value: Optional[str] = None):
            pass

        params = extract_parameters_from_signature(func)
        assert len(params) == 1
        assert "default_value" not in params[0]
        assert params[0]["required"] is False

    def test_args_kwargs_skipped(self):
        def func(name: str, *args, **kwargs):
            pass

        params = extract_parameters_from_signature(func)
        assert len(params) == 1
        assert params[0]["name"] == "name"

    def test_no_type_hints_defaults_to_string(self):
        def func(name, count):
            pass

        params = extract_parameters_from_signature(func)
        assert len(params) == 2
        assert params[0]["type"] == "string"
        assert params[1]["type"] == "string"

    def test_literal_type_includes_options(self):
        def func(status: Literal["Open", "Closed"]):
            pass

        params = extract_parameters_from_signature(func)
        assert len(params) == 1
        assert params[0]["options"] == [
            {"label": "Open", "value": "Open"},
            {"label": "Closed", "value": "Closed"},
        ]
        assert params[0]["type"] == "string"

    def test_context_param_without_type_skipped(self):
        def func(context, name: str):
            pass

        params = extract_parameters_from_signature(func)
        assert len(params) == 1
        assert params[0]["name"] == "name"

    def test_execution_context_param_skipped(self):
        class ExecutionContext:
            pass

        def func(ctx: ExecutionContext, name: str):
            pass

        params = extract_parameters_from_signature(func)
        assert len(params) == 1
        assert params[0]["name"] == "name"

    def test_optional_type_not_required(self):
        def func(name: Optional[str]):
            pass

        params = extract_parameters_from_signature(func)
        assert len(params) == 1
        assert params[0]["required"] is False
        assert params[0]["type"] == "string"

    def test_empty_function(self):
        def func():
            pass

        params = extract_parameters_from_signature(func)
        assert params == []

    def test_non_serializable_default_excluded(self):
        sentinel = object()

        def func(value: str = sentinel):
            pass

        params = extract_parameters_from_signature(func)
        assert len(params) == 1
        assert "default_value" not in params[0]
        assert params[0]["required"] is False

    def test_invalid_object_returns_empty(self):
        params = extract_parameters_from_signature("not_a_function")
        assert params == []
