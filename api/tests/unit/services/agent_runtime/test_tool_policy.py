"""Automatic CodeMode selects Bifrost business tools and keeps framework controls native."""

from src.services.agent_runtime.tool_policy import (
    CODE_MODE_AUDIT_METADATA_KEY,
    CODE_MODE_NESTED_TOOL_CALL_IDS_KEY,
    code_mode_nested_tool_call_ids_from_audit,
    is_automatic_code_mode_tool,
)


def test_automatic_code_mode_selects_business_tools_only() -> None:
    assert is_automatic_code_mode_tool("read_ticket")
    assert is_automatic_code_mode_tool("search_knowledge")
    assert is_automatic_code_mode_tool("artifact_create_document")
    assert not is_automatic_code_mode_tool("search_tools")
    assert not is_automatic_code_mode_tool("load_capability")
    assert not is_automatic_code_mode_tool("delegate_task")
    assert not is_automatic_code_mode_tool("run_code")


def test_nested_history_ids_come_only_from_outer_audit_metadata() -> None:
    audit = {
        CODE_MODE_AUDIT_METADATA_KEY: {
            CODE_MODE_NESTED_TOOL_CALL_IDS_KEY: ["outer__1", "outer__2", 3],
        }
    }

    assert code_mode_nested_tool_call_ids_from_audit("run_code", audit) == {
        "outer__1",
        "outer__2",
    }
    assert code_mode_nested_tool_call_ids_from_audit("read__1", audit) == set()
