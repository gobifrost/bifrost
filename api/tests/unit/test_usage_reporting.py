"""Unit contracts for the daily pseudonymous usage report."""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from shared.models import UsageReport
import shared.usage_reporting as usage_reporting


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2026, 10, 31, 12, tzinfo=UTC)
        return value if tz is None else value.astimezone(tz)


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class _RowsResult:
    def __init__(self, row) -> None:
        self.row = row

    def scalars(self) -> "_RowsResult":
        return self

    def first(self):
        return self.row


class _Client:
    def __init__(self, result: httpx.Response | Exception) -> None:
        self.result = result
        self.posts: list[tuple[str, dict]] = []

    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def post(self, url: str, *, json: dict) -> httpx.Response:
        self.posts.append((url, json))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _report() -> UsageReport:
    return UsageReport(
        installation_id=UUID("123e4567-e89b-42d3-a456-426614174000"),
        version="1.4.2",
        period=date(2026, 10, 30),
        workflow_runs=2,
        agent_runs=3,
    )


@pytest.mark.asyncio
async def test_collect_usage_report_emits_the_fixed_pseudonymous_payload_shape() -> (
    None
):
    """The receiver accepts only its fixed seven-field JSON contract."""
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_ScalarResult(2), _ScalarResult(3)])

    report = await usage_reporting.collect_usage_report(
        db,
        now=datetime(2026, 10, 31, 12, tzinfo=UTC),
        installation_id=UUID("123e4567-e89b-42d3-a456-426614174000"),
        version="v1.4.2",
    )

    assert report.model_dump(mode="json") == {
        "schema_version": 1,
        "installation_id": "123e4567-e89b-42d3-a456-426614174000",
        "version": "1.4.2",
        "period": "2026-10-30",
        "workflow_runs": 2,
        "agent_runs": 3,
        "environment": "production",
    }


def test_usage_report_rejects_unrecognized_fields_invalid_semver_and_out_of_range_counts() -> (
    None
):
    payload = _report().model_dump()

    assert (
        UsageReport.model_validate({**payload, "version": "1.2.3-rc.1+build.7"}).version
        == "1.2.3-rc.1+build.7"
    )

    with pytest.raises(ValidationError):
        UsageReport.model_validate({**payload, "extra": "not allowed"})
    with pytest.raises(ValidationError):
        UsageReport.model_validate({**payload, "version": "v1.4.2"})
    with pytest.raises(ValidationError):
        UsageReport.model_validate(
            {**payload, "installation_id": "6fa459ea-ee8a-11d0-9908-00aa006e6f8a"}
        )
    with pytest.raises(ValidationError):
        UsageReport.model_validate({**payload, "workflow_runs": 1_000_000_001})
    with pytest.raises(ValidationError):
        UsageReport.model_validate({**payload, "agent_runs": -1})


def test_usage_reporting_enabled_reads_false_from_the_environment(monkeypatch) -> None:
    from src.config import Settings

    monkeypatch.setenv("BIFROST_USAGE_REPORTING_ENABLED", "false")

    assert Settings().usage_reporting_enabled is False


@pytest.mark.asyncio
async def test_existing_non_v4_installation_id_fails_closed() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=_RowsResult(
            SimpleNamespace(value_json={"id": "6fa459ea-ee8a-11d0-9908-00aa006e6f8a"})
        )
    )

    with pytest.raises(ValueError, match="installation ID is invalid"):
        await usage_reporting.get_or_create_installation_id(db)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        SimpleNamespace(
            environment="development", debug=False, usage_reporting_enabled=True
        ),
        SimpleNamespace(
            environment="testing", debug=False, usage_reporting_enabled=True
        ),
        SimpleNamespace(
            environment="production", debug=True, usage_reporting_enabled=True
        ),
        SimpleNamespace(
            environment="production", debug=False, usage_reporting_enabled=False
        ),
    ],
)
async def test_report_usage_gate_skips_before_opening_a_database_or_network_connection(
    monkeypatch, settings
) -> None:
    forbidden = MagicMock(
        side_effect=AssertionError("disabled reporting must not use dependencies")
    )
    monkeypatch.setattr(usage_reporting, "get_settings", lambda: settings)
    monkeypatch.setattr(usage_reporting, "get_session_factory", forbidden)
    monkeypatch.setattr(usage_reporting.httpx, "AsyncClient", forbidden)

    outcome = await usage_reporting.report_usage()

    assert outcome.summary == "Usage reporting skipped"
    forbidden.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["unknown", "1.2.3-01", "1.2.3+" + "x" * 125])
async def test_report_usage_invalid_version_skips_before_opening_a_database_or_network_connection(
    monkeypatch, version
) -> None:
    forbidden = MagicMock(
        side_effect=AssertionError("invalid version must not use dependencies")
    )
    monkeypatch.setattr(
        usage_reporting,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production", debug=False, usage_reporting_enabled=True
        ),
    )
    monkeypatch.setattr(usage_reporting, "get_version", lambda: version)
    monkeypatch.setattr(usage_reporting, "get_session_factory", forbidden)
    monkeypatch.setattr(usage_reporting.httpx, "AsyncClient", forbidden)

    outcome = await usage_reporting.report_usage()

    assert outcome.summary == "Usage reporting skipped"
    forbidden.assert_not_called()


@pytest.mark.asyncio
async def test_report_usage_debugpy_gate_skips_before_opening_a_database_or_network_connection(
    monkeypatch,
) -> None:
    forbidden = MagicMock(
        side_effect=AssertionError("debug reporting must not use dependencies")
    )
    monkeypatch.setenv("ENABLE_DEBUG", "true")
    monkeypatch.setattr(
        usage_reporting,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production", debug=False, usage_reporting_enabled=True
        ),
    )
    monkeypatch.setattr(usage_reporting, "get_session_factory", forbidden)
    monkeypatch.setattr(usage_reporting.httpx, "AsyncClient", forbidden)

    outcome = await usage_reporting.report_usage()

    assert outcome.summary == "Usage reporting skipped"
    forbidden.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delivery", "expected_summary"),
    [
        (httpx.ConnectError("offline"), "Usage report delivery failed"),
        (httpx.Response(200), "Usage report delivery failed"),
        (httpx.Response(302), "Usage report delivery failed"),
        (httpx.Response(204), "Usage report delivered"),
    ],
)
async def test_report_usage_only_acknowledges_a_successful_no_content_delivery(
    monkeypatch, delivery, expected_summary
) -> None:
    db = MagicMock()
    client = _Client(delivery)
    acknowledgement = AsyncMock()

    @asynccontextmanager
    async def session():
        yield db

    client_kwargs: dict = {}
    monkeypatch.setattr(
        usage_reporting,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production", debug=False, usage_reporting_enabled=True
        ),
    )
    monkeypatch.setattr(usage_reporting, "get_version", lambda: "v1.4.2")
    monkeypatch.setattr(usage_reporting, "datetime", _FrozenDateTime)
    monkeypatch.setattr(usage_reporting, "get_session_factory", lambda: session)
    monkeypatch.setattr(
        usage_reporting, "_last_successful_period", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        usage_reporting,
        "get_or_create_installation_id",
        AsyncMock(return_value=UUID("123e4567-e89b-42d3-a456-426614174000")),
    )
    monkeypatch.setattr(
        usage_reporting, "collect_usage_report", AsyncMock(return_value=_report())
    )
    monkeypatch.setattr(usage_reporting, "_record_successful_period", acknowledgement)

    def make_client(**kwargs):
        client_kwargs.update(kwargs)
        return client

    monkeypatch.setattr(usage_reporting.httpx, "AsyncClient", make_client)

    outcome = await usage_reporting.report_usage()

    assert outcome.summary == expected_summary
    assert client.posts == [
        ("https://reports.gobifrost.com/usage", _report().model_dump(mode="json"))
    ]
    assert client_kwargs["trust_env"] is False
    assert client_kwargs["follow_redirects"] is False
    assert client_kwargs["timeout"].connect == 10
    if isinstance(delivery, httpx.Response) and delivery.status_code == 204:
        acknowledgement.assert_awaited_once_with(db, _report().period)
    else:
        acknowledgement.assert_not_awaited()
