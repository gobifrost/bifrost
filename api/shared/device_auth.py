"""Record the user's decision without extending or reopening a device login."""

from collections.abc import Awaitable
from typing import Literal, cast

from src.core.cache import get_shared_redis
from src.core.cache.keys import device_code_key, device_user_code_index_key


_DECIDE_DEVICE = """
local raw = redis.call('GET', KEYS[1])
if not raw or redis.call('TTL', KEYS[1]) <= 0 then return 0 end
local data = cjson.decode(raw)
if data.user_code ~= ARGV[1] then return 0 end
if data.status ~= 'pending' then return 2 end
data.status = ARGV[2]
data.user_id = ARGV[3]
redis.call('SET', KEYS[1], cjson.encode(data), 'XX', 'KEEPTTL')
return 1
"""


async def decide_device_login(
    user_code: str, user_id: str, approved: bool,
) -> Literal["expired", "decided", "ok"]:
    """Atomically accept the first decision and preserve the original expiry."""
    redis = await get_shared_redis()
    device_code = await redis.get(device_user_code_index_key(user_code))
    if not device_code:
        return "expired"
    if isinstance(device_code, bytes):
        device_code = device_code.decode()
    result = await cast(Awaitable[int], redis.eval(
        _DECIDE_DEVICE,
        1,
        device_code_key(device_code),
        user_code,
        "authorized" if approved else "denied",
        user_id,
    ))
    if result == 0:
        return "expired"
    if result == 2:
        return "decided"
    return "ok"
