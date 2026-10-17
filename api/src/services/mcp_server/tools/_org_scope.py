"""
Shared MCP scope helpers.
"""

from typing import Any

from shared.scope_resolver import has_scope_bypass


def mcp_write_scope_bypass(context: Any) -> bool:
    """Whether ``context`` may write regardless of organization ownership."""
    return has_scope_bypass(
        is_platform_admin=getattr(context, "is_platform_admin", False),
        is_provider_org=getattr(context, "is_provider_org", False),
    )
