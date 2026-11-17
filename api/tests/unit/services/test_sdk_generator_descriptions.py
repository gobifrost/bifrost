"""Tests for vendor text rendered into generated SDK source.

Vendor descriptions and operation summaries are free text. Descriptions become
model docstrings and property comments; summaries become method docstrings. A
trailing quote, a trailing backslash, an escape sequence or a newline must not
be able to end the emitted literal early.
"""

from __future__ import annotations

import ast

import pytest

from src.services import sdk_generator

HOSTILE_TEXT = [
    pytest.param("First line\nSecond line\n- BULLET: value", id="multiline"),
    pytest.param('ends "', id="trailing-quote"),
    pytest.param("ends \\", id="trailing-backslash"),
    pytest.param("bad \\uZZ", id="invalid-escape"),
    pytest.param('contains """triple quotes"""', id="triple-quotes"),
    pytest.param("it's quoted", id="apostrophe"),
]


def _spec(description: str = "plain", summary: str = "Read items") -> dict:
    """Inline response schema: its own description becomes a model docstring."""
    return {
        "info": {"title": "Describe API"},
        "paths": {
            "/items": {
                "get": {
                    "summary": summary,
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "description": description,
                                        "properties": {
                                            "bill_date": {
                                                "type": "string",
                                                "description": description,
                                            }
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }


@pytest.mark.parametrize("description", HOSTILE_TEXT)
def test_property_description_cannot_break_generated_source(description: str) -> None:
    code, _ = sdk_generator.generate_sdk(_spec(description), "describe", "bearer")

    compile(code, "<generated-sdk>", "exec")


@pytest.mark.parametrize("summary", HOSTILE_TEXT)
def test_operation_summary_cannot_break_generated_source(summary: str) -> None:
    code, _ = sdk_generator.generate_sdk(_spec(summary=summary), "describe", "bearer")

    compile(code, "<generated-sdk>", "exec")


@pytest.mark.parametrize("description", HOSTILE_TEXT)
def test_model_description_survives_as_a_docstring(description: str) -> None:
    code, _ = sdk_generator.generate_sdk(_spec(description), "describe", "bearer")

    tree = ast.parse(code)
    docstrings = [
        ast.get_docstring(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    ]

    assert description in docstrings


def test_property_comment_is_a_single_line() -> None:
    code, _ = sdk_generator.generate_sdk(
        _spec("First line\nSecond line\n- BULLET: value"), "describe", "bearer"
    )

    assert "# First line Second line - BULLET: value" in code
