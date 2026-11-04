"""Shared stdlib-only module-cache contracts.

Both the async cache implementation and the synchronous virtual-import cache
use these values. Keep this module free of storage, Redis, and network imports.
"""

from __future__ import annotations

from typing import NotRequired, TypedDict


MODULE_KEY_PREFIX = "bifrost:module:"
MODULE_INDEX_KEY = "bifrost:module:index"
# Version metadata so pre-bound records drain under their existing TTLs.
# Preserve the generation namespace: old and new writers must invalidate one
# another during a rolling upgrade. Existing tokens enter the index on use.
MODULE_RESOLUTION_KEY_PREFIX = "bifrost:module:resolution:v3:"
MODULE_RESOLUTION_GENERATION_PREFIX = "bifrost:module:resolution:generation:"
MODULE_RESOLUTION_INDEX_KEY = "bifrost:module:resolution:v3:index"
MODULE_RESOLUTION_GENERATION_INDEX_KEY = "bifrost:module:resolution:generation:v2:index"
MODULE_RESOLUTION_TTL = 86400
# A mutation rotates the name generation, so a confirmed miss can safely live
# as long as a hit. Redis indexes below enforce cardinality independently.
MODULE_RESOLUTION_NEGATIVE_TTL = MODULE_RESOLUTION_TTL
MODULE_RESOLUTION_MAX_ENTRIES = 10_000
MODULE_RESOLUTION_MAX_KEY_BYTES = 512
MODULE_RESOLUTION_MAX_RESULT_BYTES = 2_048


def module_resolution_cache_key(
    name: str,
    *,
    solution_id: str | None,
    global_repo_access: bool,
) -> str | None:
    """Build the shared Redis key suffix for one scoped import name."""
    dotted_name = name.strip().replace("/", ".").strip(".")
    key = f"{solution_id or '-'}:{int(global_repo_access)}:{dotted_name}"
    return key if len(key.encode("utf-8")) <= MODULE_RESOLUTION_MAX_KEY_BYTES else None


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
local now = redis.call('TIME')
local score = now[1] * 1000000 + now[2]
redis.call('ZADD', KEYS[2], score, KEYS[1])
redis.call('EXPIRE', KEYS[2], ARGV[2])
local overflow = redis.call('ZCARD', KEYS[2]) - tonumber(ARGV[3])
if overflow > 0 then
    local evicted = redis.call('ZPOPMIN', KEYS[2], overflow)
    for i = 1, #evicted, 2 do redis.call('DEL', evicted[i]) end
end
return token
"""

MODULE_RESOLUTION_GENERATION_ROTATE_SCRIPT = """
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
local now = redis.call('TIME')
local score = now[1] * 1000000 + now[2]
redis.call('ZADD', KEYS[2], score, KEYS[1])
redis.call('EXPIRE', KEYS[2], ARGV[2])
local overflow = redis.call('ZCARD', KEYS[2]) - tonumber(ARGV[3])
if overflow > 0 then
    local evicted = redis.call('ZPOPMIN', KEYS[2], overflow)
    for i = 1, #evicted, 2 do redis.call('DEL', evicted[i]) end
end
return ARGV[1]
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
local now = redis.call('TIME')
local score = now[1] * 1000000 + now[2]
redis.call('ZADD', KEYS[3], score, KEYS[2])
redis.call('EXPIRE', KEYS[3], ARGV[2])
local overflow = redis.call('ZCARD', KEYS[3]) - tonumber(ARGV[4])
if overflow > 0 then
    local evicted = redis.call('ZPOPMIN', KEYS[3], overflow)
    for i = 1, #evicted, 2 do redis.call('DEL', evicted[i]) end
end
return 1
"""

# A cold source read may overlap a workspace mutation. It can return the old
# bytes to its already-started caller, but it must not restore those bytes to
# Redis after the mutation rotates this logical name's generation.
MODULE_SOURCE_RECACHE_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if not current or current ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
redis.call('SADD', KEYS[3], ARGV[4])
return 1
"""
