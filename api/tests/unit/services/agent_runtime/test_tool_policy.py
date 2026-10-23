"""Automatic CodeMode policy and durable nested-audit metadata."""

from pydantic_ai.capabilities import ToolSearch

from src.services.agent_runtime.sandbox_sdk import BifrostSandboxSDK
from src.services.agent_runtime.tool_policy import (
    CODE_MODE_AUDIT_METADATA_KEY,
    CODE_MODE_NESTED_TOOL_CALL_IDS_KEY,
    build_automatic_code_mode_capabilities,
    code_mode_nested_tool_call_ids_from_audit,
)


def test_automatic_code_mode_uses_catalog_private_sdk_without_tool_search() -> None:
    capabilities = build_automatic_code_mode_capabilities()

    assert any(isinstance(capability, BifrostSandboxSDK) for capability in capabilities)
    assert not any(isinstance(capability, ToolSearch) for capability in capabilities)


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
