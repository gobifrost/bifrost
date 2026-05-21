"""Tests for SDK generator template rendering."""

from jinja2.sandbox import SandboxedEnvironment

from src.services import sdk_generator


def test_generate_sdk_renders_python_source_without_html_escaping(monkeypatch) -> None:
    sandbox_calls = []

    def tracking_sandboxed_environment(*args, **kwargs):
        sandbox_calls.append((args, kwargs))
        return SandboxedEnvironment(*args, **kwargs)

    monkeypatch.setattr(
        sdk_generator,
        "SandboxedEnvironment",
        tracking_sandboxed_environment,
    )
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Quotes API"},
        "paths": {
            "/items": {
                "get": {
                    "summary": "Return \"quoted\" values",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }

    code, module_name = sdk_generator.generate_sdk(spec, "quotes", "bearer")

    assert sandbox_calls
    assert module_name == "quotes_api"
    assert '"""Return "quoted" values"""' in code
    assert "&quot;" not in code
