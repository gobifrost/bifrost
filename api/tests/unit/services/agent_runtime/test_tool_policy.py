"""Automatic CodeMode policy and durable nested-audit metadata."""

from pydantic_ai.capabilities import ToolSearch
from pydantic_ai_harness.code_mode import CodeMode
from src.services.agent_runtime.tool_policy import (
    CODE_MODE_AUDIT_METADATA_KEY,
    CODE_MODE_NESTED_TOOL_CALL_IDS_KEY,
    build_automatic_code_mode_capabilities,
    code_mode_nested_tool_call_ids_from_audit,
)


def test_automatic_code_mode_uses_native_search_and_code_mode() -> None:
    capabilities = build_automatic_code_mode_capabilities()

    [search] = [capability for capability in capabilities if isinstance(capability, ToolSearch)]
    [code_mode] = [capability for capability in capabilities if isinstance(capability, CodeMode)]

    assert callable(search.strategy)
    assert code_mode.dynamic_catalog is True
    assert code_mode.max_tool_calls == 16
    assert code_mode.max_retries == 3
    assert code_mode.eager is False
    assert code_mode.speculate is None


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
