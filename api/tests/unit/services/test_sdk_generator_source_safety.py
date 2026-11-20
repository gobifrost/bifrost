"""Remaining source sinks and the syntax-only generation boundary."""

import ast
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services import sdk_generator


PAYLOADS = ['ends "', "ends \\", "bad \\uZZ", 'contains """quotes', "o'clock", "line\nnext"]


def spec(title="Example", key="value", path="/items"):
    return {
        "info": {"title": title},
        "components": {"schemas": {"Item": {
            "type": "object", "properties": {key: {"type": "string"}},
        }}},
        "paths": {path: {"get": {"responses": {}}}},
    }


@pytest.mark.parametrize("text", PAYLOADS)
def test_metadata_and_property_keys_survive_as_literals(text):
    code, _ = sdk_generator.generate_sdk(spec(title=text, key=text), text, "bearer")
    tree = ast.parse(code)
    assert text + " - Python SDK" in ast.get_docstring(tree)
    constants = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)]
    assert text in constants
    integration = next(node for node in ast.walk(tree) if isinstance(node, ast.AnnAssign)
                       and isinstance(node.target, ast.Name) and node.target.id == "_integration_name")
    assert ast.literal_eval(integration.value) == text
    # Verify the original property name reaches the accessor unchanged.
    accessor = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == text)
    assert ast.literal_eval(accessor.args[0]) == text


@pytest.mark.parametrize("path,arguments,expected", [
    ("/items/{itemId}/detail/{itemId}", {"item_id": 42}, "/items/42/detail/42"),
    ("/it's/\"quoted\"/\\uZZ/\n/{item-id}", {"item_id": "abc"}, "/it's/\"quoted\"/\\uZZ/\n/abc"),
    ("/literal}/open{/items", {}, "/literal}/open{/items"),
    ("/items/{str}", {"str": "abc"}, "/items/abc"),
    ("/items/{class}", {"class_": "abc"}, "/items/abc"),
])
def test_path_literals_and_parameter_substitution(path, arguments, expected):
    code, _ = sdk_generator.generate_sdk(spec(path=path), "Example", "bearer")
    tree = ast.parse(code)
    assignment = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                      and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "url")
    expression = ast.Expression(assignment.value)
    url = eval(compile(expression, "<test-url>", "eval"),
               {"self": SimpleNamespace(base_url="https://example.test"), **arguments})
    assert url == "https://example.test" + expected


def test_guard_does_not_execute_generated_code():
    template = MagicMock()
    template.render.return_value = "raise RuntimeError('must not execute')"
    with patch.object(sdk_generator.Environment, "get_template", return_value=template):
        code, _ = sdk_generator.generate_sdk(spec(), "Example", "bearer")
    assert code == template.render.return_value


@pytest.mark.parametrize("source", ["secret-token = (", "secret-token\x00"])
def test_invalid_output_error_is_sanitized(source):
    template = MagicMock()
    template.render.return_value = source
    with patch.object(sdk_generator.Environment, "get_template", return_value=template):
        with pytest.raises(ValueError, match="^Generated SDK contains invalid Python syntax$") as error:
            sdk_generator.generate_sdk(spec(), "Example", "bearer")
    assert error.value.__suppress_context__
