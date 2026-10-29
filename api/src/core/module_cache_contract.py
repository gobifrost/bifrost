"""Shared stdlib-only module-cache contracts.

Both the async cache implementation and the synchronous virtual-import cache
use these values. Keep this module free of storage, Redis, and network imports.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


MODULE_KEY_PREFIX = "bifrost:module:"
MODULE_INDEX_KEY = "bifrost:module:index"
MODULE_RESOLUTION_KEY_PREFIX = "bifrost:module:resolution:v2:"
MODULE_RESOLUTION_GENERATION_PREFIX = "bifrost:module:resolution:generation:"
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


def module_resolution_generation_key(cache_key: str) -> str:
    """One expiring generation per logical name, shared by every scope."""
    return f"{MODULE_RESOLUTION_GENERATION_PREFIX}{cache_key.rsplit(':', 1)[-1]}"


# Shared scripts keep sync worker and async API reads atomic and identical.
MODULE_RESOLUTION_GENERATION_SCRIPT = """
local token = redis.call('GET', KEYS[1])
if not token then
    token = ARGV[1]
    redis.call('SET', KEYS[1], token, 'EX', ARGV[2])
end
return token
"""

MODULE_RESOLUTION_READ_SCRIPT = """
local token = redis.call('GET', KEYS[1])
local raw = redis.call('GET', KEYS[2])
if not token or not raw then return false end
local entry = cjson.decode(raw)
if entry.generation ~= token then return false end
return cjson.encode(entry.result)
"""

MODULE_RESOLUTION_WRITE_SCRIPT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[2], ARGV[3], 'EX', ARGV[2])
return 1
"""
