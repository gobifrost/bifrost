"""The model-facing reference covers the Python execution SDK surface."""

import pytest

from src.services.mcp_server.tools.docs import get_docs


@pytest.mark.asyncio
async def test_platform_docs_teach_inline_execution_and_include_all_python_sdk_facades():
    result = await get_docs(object())
    assert not result.is_error
    docs = result.structured_content["schema"]
    for module in (
        "agents", "ai", "artifacts", "config", "events", "executions", "files",
        "forms", "integrations", "knowledge", "organizations", "roles", "tables",
        "users", "workflows",
    ):
        assert f"### {module}\n" in docs
    for method in ("files.read(", "files.list(", "artifacts.create_document(", "events.emit("):
        assert method in docs
    assert "bifrost_execute_code" in docs
    assert "bifrost_execution_get" in docs
    assert "modules.customer_helpers" in docs


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [
    "agents", "ai", "artifacts", "config", "events", "executions", "files",
    "forms", "integrations", "knowledge", "organizations", "roles", "tables",
    "users", "workflows",
])
async def test_focused_sdk_reference_survives_native_agent_result_limits(module):
    from src.services.agent_runtime.toolset import bound_tool_result_for_model

    result = await get_docs(object(), sdk_module=module)
    assert not result.is_error
    assert f"### {module}\n" in result.structured_content["schema"]
    payload = result.to_call_tool_result().model_dump(mode="json")
    assert bound_tool_result_for_model(payload) is payload


@pytest.mark.asyncio
async def test_unknown_sdk_module_returns_supported_names_and_no_guessed_reference():
    result = await get_docs(object(), sdk_module="not_a_module")
    assert result.is_error
    assert "files" in result.structured_content["sdk_modules"]
    assert "schema" not in result.structured_content


def test_assigned_docs_schema_accepts_the_sdk_module_filter():
    from src.services.capabilities.catalog import require_valid_capability_arguments
    from src.services.mcp_server.server import get_system_tools

    tool = next(tool for tool in get_system_tools() if tool["id"] == "get_docs")
    require_valid_capability_arguments(tool["parameters"], {"sdk_module": "files"})
