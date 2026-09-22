"""Live service memory map: pool-hash parsing (pure) + cross-worker merge."""

from datetime import datetime, timedelta, timezone

from src.services import service_memory
from src.services.service_memory import parse_service_memory_entries


def _entry(memory_mb, age_seconds):
    updated = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    return {"memory_mb": memory_mb, "updated_at": updated}


def test_parse_accepts_fresh_entries():
    import json

    raw = json.dumps(
        {
            "attempt-1": _entry(128.5, 10),
            "attempt-2": _entry(64, 89),
        }
    )
    assert parse_service_memory_entries(raw) == {
        "attempt-1": 128.5,
        "attempt-2": 64.0,
    }


def test_parse_drops_stale_unparseable_and_nonnumeric():
    import json

    raw = json.dumps(
        {
            "stale": _entry(999.0, 91),
            "from-the-future": _entry(999.0, -3600),
            "no-memory": {"updated_at": datetime.now(timezone.utc).isoformat()},
            "bool-memory": {
                "memory_mb": True,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            "bad-timestamp": {"memory_mb": 12.0, "updated_at": "not-a-date"},
            "not-a-dict": [1, 2, 3],
        }
    )
    assert parse_service_memory_entries(raw) == {}


def test_parse_rejects_garbage_payloads():
    assert parse_service_memory_entries(None) == {}
    assert parse_service_memory_entries("") == {}
    assert parse_service_memory_entries("{not json") == {}
    assert parse_service_memory_entries("[1,2]") == {}


class _FakeRedis:
    """Minimal scan/hget surface for read_service_memory."""

    def __init__(self, hashes):
        self._hashes = hashes

    async def scan(self, cursor, match=None, count=None):
        assert match == "bifrost:pool:*"
        return 0, list(self._hashes)

    async def hget(self, key, field):
        assert field == "services"
        return self._hashes.get(key)


class _FakeCM:
    def __init__(self, redis):
        self._redis = redis

    async def __aenter__(self):
        return self._redis

    async def __aexit__(self, *args):
        return False


async def test_read_merges_workers_and_skips_non_registration_keys(monkeypatch):
    """Two-colon registration keys merge; heartbeat keys are skipped."""
    import json

    hashes = {
        "bifrost:pool:worker-1": json.dumps({"attempt-1": _entry(100.0, 5)}),
        "bifrost:pool:worker-1:heartbeat": json.dumps(
            {"attempt-1": _entry(777.0, 5)}
        ),
        "bifrost:pool:worker-2": json.dumps({"attempt-2": _entry(50.0, 5)}),
    }
    monkeypatch.setattr(
        "src.core.cache.get_redis", lambda: _FakeCM(_FakeRedis(hashes))
    )
    merged = await service_memory.read_service_memory()
    assert merged == {"attempt-1": 100.0, "attempt-2": 50.0}


async def test_read_returns_empty_map_on_redis_failure(monkeypatch):
    """Memory is informational — scan failures never break the endpoint."""

    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("redis down")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("src.core.cache.get_redis", lambda: _Boom())
    assert await service_memory.read_service_memory() == {}
