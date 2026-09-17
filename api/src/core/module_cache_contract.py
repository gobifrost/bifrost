"""Shared stdlib-only module-cache contracts.

Both the async cache implementation and the synchronous virtual-import cache
use these values. Keep this module free of storage, Redis, and network imports.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


MODULE_KEY_PREFIX = "bifrost:module:"
MODULE_INDEX_KEY = "bifrost:module:index"
MODULE_RESOLUTION_KEY_PREFIX = "bifrost:module:resolution:"
MODULE_RESOLUTION_TTL = 86400
MODULE_RESOLUTION_NEGATIVE_TTL = 30


def module_resolution_cache_key(
    name: str,
    *,
    solution_id: str | None,
    global_repo_access: bool,
) -> str:
    """Build the shared Redis key suffix for one scoped import name."""
    dotted_name = name.strip().replace("/", ".").strip(".")
    return f"{solution_id or '-'}:{int(global_repo_access)}:{dotted_name}"


class CachedModule(TypedDict):
    """Schema for cached module data."""

    content: str
    path: str
    hash: str
    storage_path: NotRequired[str]
