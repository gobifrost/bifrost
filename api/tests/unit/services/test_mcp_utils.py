from pydantic import BaseModel, Field
from typing import Optional

from src.services.mcp_server.schema_utils import model_to_markdown, _format_type, models_to_markdown
from src.services.mcp_server.tool_result import (
    success_result,
    error_result,
    format_diff,
    format_file_content,
)


class SimpleModel(BaseModel):
    name: str = Field(description="The name")
    count: int = Field(default=0, description="A counter")


class RequiredOptionalModel(BaseModel):
    required_field: str = Field(description="Must provide")
    optional_field: Optional[str] = Field(default=None, description="Can omit")


class AnotherModel(BaseModel):
    value: float = Field(description="A value")


class TestModelToMarkdown:
    def test_simple_model_generates_table(self):
        result = model_to_markdown(SimpleModel)
        assert "## SimpleModel" in result
        assert "| Field | Type | Required | Description |" in result
        assert "| name | string | Yes | The name |" in result
        assert "| count | integer | No | A counter |" in result

    def test_required_and_optional_fields(self):
        result = model_to_markdown(RequiredOptionalModel)
        assert "| required_field | string | Yes | Must provide |" in result
        assert "| optional_field |" in result
        assert "| No |" in result or "No" in result

    def test_custom_title(self):
        result = model_to_markdown(SimpleModel, title="Custom Title")
        assert "## Custom Title" in result
        assert "## SimpleModel" not in result

    def test_default_title_uses_class_name(self):
        result = model_to_markdown(SimpleModel)
        assert "## SimpleModel" in result

    def test_pipe_in_description_escaped(self):
        class PipeModel(BaseModel):
            x: str = Field(description="a | b")

        result = model_to_markdown(PipeModel)
        assert "a \\| b" in result


class TestFormatType:
    def test_string_type(self):
        assert _format_type({"type": "string"}, {}) == "string"

    def test_array_of_strings(self):
        assert _format_type({"type": "array", "items": {"type": "string"}}, {}) == "array[string]"

    def test_ref(self):
        assert _format_type({"$ref": "#/$defs/Foo"}, {}) == "Foo"

    def test_anyof_filters_null(self):
        prop = {"anyOf": [{"type": "string"}, {"type": "null"}]}
        assert _format_type(prop, {}) == "string"

    def test_anyof_all_null_returns_any(self):
        prop = {"anyOf": [{"type": "null"}]}
        assert _format_type(prop, {}) == "any"

    def test_allof_ref(self):
        prop = {"allOf": [{"$ref": "#/$defs/Bar"}]}
        assert _format_type(prop, {}) == "Bar"

    def test_enum_short(self):
        prop = {"enum": ["a", "b"]}
        assert _format_type(prop, {}) == "enum: a, b"

    def test_enum_long(self):
        prop = {"enum": ["a", "b", "c", "d", "e", "f"]}
        assert _format_type(prop, {}) == "enum"

    def test_const(self):
        assert _format_type({"const": "fixed"}, {}) == "const: fixed"

    def test_empty_returns_any(self):
        assert _format_type({}, {}) == "any"


class TestModelsToMarkdown:
    def test_multiple_models(self):
        models = [(SimpleModel, "Simple"), (AnotherModel, "Another")]
        result = models_to_markdown(models, "All Models")
        assert "# All Models" in result
        assert "## Simple" in result
        assert "## Another" in result
        assert "| name |" in result
        assert "| value |" in result


class TestSuccessResult:
    def test_with_data(self):
        result = success_result("OK", {"key": "val"})
        text = result.content[0].text
        assert text.startswith("OK")
        assert '"key": "val"' in text
        assert result.structured_content == {"key": "val"}

    def test_without_data(self):
        result = success_result("Done")
        assert result.content[0].text == "Done"
        assert result.structured_content is None


class TestErrorResult:
    def test_basic_error(self):
        result = error_result("not found")
        text = result.content[0].text
        assert text.startswith("Error: not found")
        assert '"error": "not found"' in text
        assert result.structured_content["error"] == "not found"

    def test_with_extra_data(self):
        result = error_result("fail", {"code": 404})
        assert result.structured_content["error"] == "fail"
        assert result.structured_content["code"] == 404

    def test_without_extra_data(self):
        result = error_result("oops")
        assert "code" not in result.structured_content


class TestFormatDiff:
    def test_old_and_new_lines(self):
        result = format_diff("file.py", ["old line"], ["new line"])
        assert "Updated file.py" in result
        assert "-  old line" in result
        assert "+  new line" in result

    def test_empty_old_lines(self):
        result = format_diff("f.py", [], ["added"])
        assert "+  added" in result
        assert "-" not in result.split("\n", 1)[-1] or result.count("-") == 0


class TestFormatFileContent:
    def test_multiline_content(self):
        result = format_file_content("test.py", "line1\nline2\nline3")
        assert "test.py" in result
        assert "1: line1" in result
        assert "2: line2" in result
        assert "3: line3" in result

    def test_custom_start_line(self):
        result = format_file_content("test.py", "a\nb", start_line=10)
        assert "10: a" in result
        assert "11: b" in result

    def test_line_number_width_padding(self):
        content = "\n".join(f"line{i}" for i in range(11))
        result = format_file_content("x.py", content, start_line=1)
        lines = result.strip().split("\n")
        assert " 1: line0" in lines[1]
        assert "11: line10" in lines[11]
