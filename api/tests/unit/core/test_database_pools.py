import pytest

from src.core import database
from src.config import Settings


@pytest.fixture
def engines(monkeypatch):
    calls: list[dict] = []
    installed: list[int] = []

    def fake_create_async_engine(url, **kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(database, "create_async_engine", fake_create_async_engine)
    monkeypatch.setattr(database, "async_sessionmaker", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        database,
        "install_session_timeouts",
        lambda engine, **timeouts: installed.append(timeouts),
    )
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
    assert installed == [
        {
            "idle_in_transaction_session_timeout_ms": database.get_settings().database_idle_in_transaction_timeout_ms,
            "statement_timeout_ms": database.get_settings().database_statement_timeout_ms,
        }
    ] * 3
    return created


def test_every_pool_reconnects_by_invalidation_not_pre_ping(engines):
    for kwargs in engines.values():
        assert kwargs["pool_pre_ping"] is False
        assert kwargs["pool_recycle"] == 1800


def test_main_reuses_its_existing_budget_and_other_pools_are_unchanged(engines):
    settings = database.get_settings()
    assert (engines["main"]["pool_size"], engines["main"]["max_overflow"]) == (
        settings.database_pool_size + settings.database_max_overflow, 0,
    )
    assert (engines["dispatch"]["pool_size"], engines["dispatch"]["max_overflow"]) == (
        settings.database_dispatch_pool_size, settings.database_dispatch_max_overflow,
    )
    assert (engines["liveness"]["pool_size"], engines["liveness"]["max_overflow"]) == (1, 0)
    assert engines["liveness"]["pool_timeout"] == 10


@pytest.mark.parametrize(
    ("base", "overflow", "expected"),
    [
        (5, 10, (15, 0)),  # Defaults.
        (2, 10, (12, 0)),  # Only the existing base setting is overridden.
        (5, 2, (7, 0)),  # Only the existing overflow setting is overridden.
        (1, 2, (3, 0)),  # Existing worker budget.
        (3, 0, (3, 0)),
        (0, 10, (0, 10)),  # Unlimited pool size.
        (5, -1, (5, -1)),  # Unlimited overflow.
        (0, -1, (0, -1)),
    ],
)
def test_main_pool_preserves_configured_limits_without_mutating_settings(monkeypatch, base, overflow, expected):
    settings = Settings(database_pool_size=base, database_max_overflow=overflow)
    calls = []
    engine = object()

    def create(url, **kwargs):
        calls.append(kwargs)
        return engine

    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "create_async_engine", create)
    monkeypatch.setattr(database, "install_session_timeouts", lambda *args, **kwargs: None)
    assert database.get_engine(settings) is engine
    assert database.get_engine(settings) is engine
    assert len(calls) == 1, "the process reuses one engine"
    assert (calls[0]["pool_size"], calls[0]["max_overflow"]) == expected
    assert (settings.database_pool_size, settings.database_max_overflow) == (base, overflow)


def test_connections_share_one_application_name(engines):
    # Behind PgBouncer transaction pooling, differing application names make
    # server connections keep switching names as they pass between pools.
    # One name for every pool keeps that from happening.
    names = {kwargs["connect_args"]["server_settings"]["application_name"] for kwargs in engines.values()}
    assert names == {database.get_settings().database_application_name}
