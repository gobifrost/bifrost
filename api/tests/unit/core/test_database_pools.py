import pytest

from src.core import database


@pytest.fixture
def engines(monkeypatch):
    calls: list[dict] = []

    def fake_create_async_engine(url, **kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(database, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(database, "async_sessionmaker", lambda *args, **kwargs: object())
    for name in ("_engine", "_dispatch_engine", "_dispatch_session_factory", "_liveness_engine", "_liveness_session_factory"):
        monkeypatch.setattr(database, name, None)
    created: dict[str, dict] = {}
    for pool, build in (
        ("main", database.get_engine),
        ("dispatch", database._get_dispatch_session_factory),
        ("liveness", database._get_liveness_session_factory),
    ):
        build()
        assert len(calls) == len(created) + 1, f"{pool} should build exactly one engine"
        created[pool] = calls[-1]
    return created


def test_every_pool_reconnects_by_invalidation_not_pre_ping(engines):
    for kwargs in engines.values():
        assert kwargs["pool_pre_ping"] is False
        assert kwargs["pool_recycle"] == 1800


def test_pool_sizes_are_unchanged(engines):
    settings = database.get_settings()
    assert (engines["main"]["pool_size"], engines["main"]["max_overflow"]) == (
        settings.database_pool_size, settings.database_max_overflow,
    )
    assert (engines["dispatch"]["pool_size"], engines["dispatch"]["max_overflow"]) == (
        settings.database_dispatch_pool_size, settings.database_dispatch_max_overflow,
    )
    assert (engines["liveness"]["pool_size"], engines["liveness"]["max_overflow"]) == (1, 0)
    assert engines["liveness"]["pool_timeout"] == 10


def test_connections_carry_no_per_pool_application_name(engines):
    # Behind PgBouncer transaction pooling, differing application names make
    # PgBouncer re-send SET application_name whenever a server connection
    # passes between pools.
    for kwargs in engines.values():
        assert "server_settings" not in kwargs["connect_args"]
