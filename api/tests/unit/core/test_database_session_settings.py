from sqlalchemy.ext.asyncio import create_async_engine

from src.config import Settings
from src.core import database
from src.core.database import _prepare_asyncpg_url, install_session_timeouts


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def exec_driver_sql(self, statement: str) -> None:
        self.statements.append(statement)


def _begin(engine) -> list[str]:
    connection = FakeConnection()
    engine.sync_engine.dispatch.begin(connection)
    return connection.statements


def test_connect_args_carry_only_allowlisted_startup_parameters():
    # PgBouncer in transaction mode rejects startup parameters it does not
    # allowlist, so a timeout in server_settings breaks every connection.
    _, connect_args = _prepare_asyncpg_url("postgresql://user:pass@db.example.com/app")

    assert connect_args["server_settings"] == {"application_name": "bifrost"}


def test_application_name_is_configurable():
    _, connect_args = _prepare_asyncpg_url(
        "postgresql://user:pass@db.example.com/app",
        application_name="bifrost-worker",
    )

    assert connect_args["server_settings"] == {"application_name": "bifrost-worker"}


def test_dsn_application_name_wins_over_the_default():
    url, connect_args = _prepare_asyncpg_url(
        "postgresql://user:pass@db.example.com/app?application_name=custom",
        application_name="bifrost",
    )

    assert "application_name=custom" in url
    assert "server_settings" not in connect_args


def test_timeouts_are_set_locally_in_every_transaction():
    engine = create_async_engine("postgresql+asyncpg://user:pass@db.example.com/app")
    install_session_timeouts(
        engine,
        idle_in_transaction_session_timeout_ms=1234,
        statement_timeout_ms=5678,
    )

    assert _begin(engine) == [
        "SET LOCAL idle_in_transaction_session_timeout = 1234",
        "SET LOCAL statement_timeout = 5678",
    ]


def test_zero_disables_the_timeouts_explicitly():
    engine = create_async_engine("postgresql+asyncpg://user:pass@db.example.com/app")
    install_session_timeouts(
        engine,
        idle_in_transaction_session_timeout_ms=0,
        statement_timeout_ms=0,
    )

    assert _begin(engine) == [
        "SET LOCAL idle_in_transaction_session_timeout = 0",
        "SET LOCAL statement_timeout = 0",
    ]


def test_engine_wires_the_configured_timeouts(monkeypatch):
    settings = Settings(
        database_application_name="bifrost-worker",
        database_idle_in_transaction_timeout_ms=5000,
        database_statement_timeout_ms=7000,
    )
    calls: list[dict] = []
    installs: list[tuple[int, int]] = []
    engine = object()

    def create(url, **kwargs):
        calls.append(kwargs)
        return engine

    def install(target, *, idle_in_transaction_session_timeout_ms, statement_timeout_ms):
        installs.append((idle_in_transaction_session_timeout_ms, statement_timeout_ms))

    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "create_async_engine", create)
    monkeypatch.setattr(database, "install_session_timeouts", install)

    assert database.get_engine(settings) is engine
    assert calls[0]["connect_args"] == {"server_settings": {"application_name": "bifrost-worker"}}
    assert installs == [(5000, 7000)]
