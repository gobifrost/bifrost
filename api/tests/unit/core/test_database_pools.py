import pytest

from src.core import database


@pytest.fixture
def engines(monkeypatch):
    created: dict[str, dict] = {}

    def fake_create_async_engine(url, **kwargs):
        name = kwargs["connect_args"]["server_settings"]["application_name"]
        created[name] = kwargs
        return object()

    monkeypatch.setattr(database, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(database, "async_sessionmaker", lambda *args, **kwargs: object())
    for name in ("_engine", "_dispatch_engine", "_dispatch_session_factory", "_liveness_engine", "_liveness_session_factory"):
        monkeypatch.setattr(database, name, None)
    database.get_engine()
    database._get_dispatch_session_factory()
    database._get_liveness_session_factory()
    return created


def test_every_pool_reconnects_by_invalidation_not_pre_ping(engines):
    assert set(engines) == {"bifrost:main", "bifrost:dispatch", "bifrost:liveness"}
    for kwargs in engines.values():
        assert kwargs["pool_pre_ping"] is False
        assert kwargs["pool_recycle"] == 1800


def test_pool_sizes_are_unchanged(engines):
    settings = database.get_settings()
    assert (engines["bifrost:main"]["pool_size"], engines["bifrost:main"]["max_overflow"]) == (
        settings.database_pool_size, settings.database_max_overflow,
    )
    assert (engines["bifrost:dispatch"]["pool_size"], engines["bifrost:dispatch"]["max_overflow"]) == (
        settings.database_dispatch_pool_size, settings.database_dispatch_max_overflow,
    )
    assert (engines["bifrost:liveness"]["pool_size"], engines["bifrost:liveness"]["max_overflow"]) == (1, 0)
    assert engines["bifrost:liveness"]["pool_timeout"] == 10
