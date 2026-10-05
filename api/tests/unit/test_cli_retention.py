"""Unit tests for the ``bifrost retention`` CLI group.

``retention`` wraps the maintenance endpoints for run/event retention and audit
archive retention. These tests replace :class:`BifrostClient` with a fake that has
the real ``get``/``post``/``put`` call shapes so we can assert on the requests the
CLI sends and on the text it renders.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from click.testing import CliRunner, Result

from bifrost import client as bifrost_client_module
from bifrost import platform_jobs
from bifrost.commands.retention import retention_group

RUNS = "/api/maintenance/run-retention/settings"
AUDIT = "/api/maintenance/audit-retention/settings"
RUNS_RUN = "/api/maintenance/run-retention/run"
AUDIT_RUN = "/api/maintenance/audit-retention/run"


class _FakeClient:
    """Routes ``(method, path)`` to canned JSON bodies and records each call.

    A path queued with several bodies answers them in order, repeating the last.
    """

    def __init__(self) -> None:
        self.api_url = "http://test.local"
        self.calls: list[tuple[str, str, Any]] = []
        self._responses: dict[tuple[str, str], list[tuple[int, Any]]] = {}

    def respond(self, method: str, path: str, *bodies: Any, status: int = 200) -> None:
        self._responses[(method, path)] = [(status, body) for body in bodies]

    def _answer(self, method: str, path: str, payload: Any) -> httpx.Response:
        self.calls.append((method, path, payload))
        queue = self._responses[(method, path)]
        status, body = queue.pop(0) if len(queue) > 1 else queue[0]
        request = httpx.Request(method, f"{self.api_url}{path}")
        return httpx.Response(status, json=body, request=request)

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        return self._answer("GET", path, params)

    async def post(self, path: str, *, json: Any = None) -> httpx.Response:
        return self._answer("POST", path, json)

    async def put(self, path: str, *, json: Any = None) -> httpx.Response:
        return self._answer("PUT", path, json)


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )

    async def no_sleep(delay: float, result: Any = None) -> Any:
        return result

    monkeypatch.setattr(platform_jobs.asyncio, "sleep", no_sleep)
    return fake


def _invoke(args: list[str]) -> Result:
    return CliRunner().invoke(retention_group, args, standalone_mode=False, catch_exceptions=False)


def _job(status: str = "succeeded", **overrides: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "id": str(uuid4()),
        "status": status,
        "progress": {"phase": None, "current": 0, "total": None, "percent": None},
        "result": None,
        "error": None,
        "created_at": "2026-10-05T11:00:00+00:00",
        "completed_at": "2026-10-05T11:38:00+00:00",
    }
    job.update(overrides)
    return job


def _runs_status(days: int | None = 30, last_run: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "settings": {"days": days},
        "info": {
            "days": days,
            "oldest_finished_run": "2026-09-04T08:15:00+00:00",
            "rolled_up_runs": 120,
            "rolled_up_through": "2026-09-03",
        },
        "last_run": last_run,
    }


def _audit_status(
    hot_days: int = 90, archive_days: int | None = None, last_run: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "settings": {"hot_days": hot_days, "archive_days": archive_days},
        "info": {
            "hot_days": hot_days,
            "archive_days": archive_days,
            "oldest_in_database": "2026-07-10T01:00:00+00:00",
            "archived_through": "2026-07-09T23:59:59+00:00",
            "archived_segments": 129,
            "archived_rows": 4478,
        },
        "last_run": last_run,
    }


def _accepted(job_id: str) -> dict[str, Any]:
    return {"job_id": job_id, "notification_id": None, "status": "queued", "reused": False}


class TestShow:
    def test_reads_both_settings(self, fake_client: _FakeClient) -> None:
        fake_client.respond("GET", RUNS, _runs_status())
        fake_client.respond("GET", AUDIT, _audit_status())
        assert _invoke(["show"]).exit_code == 0
        assert fake_client.calls == [("GET", RUNS, None), ("GET", AUDIT, None)]

    def test_renders_a_line_per_area(self, fake_client: _FakeClient) -> None:
        fake_client.respond("GET", RUNS, _runs_status(days=30))
        fake_client.respond("GET", AUDIT, _audit_status(hot_days=90, archive_days=None))
        assert _invoke(["show"]).output.splitlines() == [
            "Runs and events: kept 30 days (oldest finished run 2026-09-04)",
            "Audit: 90 days in the database, archive kept forever "
            "(oldest in database 2026-07-10, archived through 2026-07-09)",
        ]

    def test_forever_and_a_finite_archive_window(self, fake_client: _FakeClient) -> None:
        fake_client.respond("GET", RUNS, _runs_status(days=None))
        fake_client.respond("GET", AUDIT, _audit_status(hot_days=30, archive_days=365))
        lines = _invoke(["show"]).output.splitlines()
        assert lines[0] == "Runs and events: kept forever (oldest finished run 2026-09-04)"
        assert lines[1].startswith("Audit: 30 days in the database, archive kept 365 days (")

    def test_empty_tables_leave_out_the_parenthetical(self, fake_client: _FakeClient) -> None:
        runs = _runs_status()
        runs["info"]["oldest_finished_run"] = None
        audit = _audit_status()
        audit["info"]["oldest_in_database"] = None
        audit["info"]["archived_through"] = None
        fake_client.respond("GET", RUNS, runs)
        fake_client.respond("GET", AUDIT, audit)
        assert _invoke(["show"]).output.splitlines() == [
            "Runs and events: kept 30 days",
            "Audit: 90 days in the database, archive kept forever",
        ]

    def test_last_runs_follow_their_area(self, fake_client: _FakeClient) -> None:
        failed = _job(
            "failed",
            error={"code": "archive_storage_unavailable", "message": "Object storage is not configured.", "retryable": False},
        )
        fake_client.respond("GET", RUNS, _runs_status(last_run=_job()))
        fake_client.respond("GET", AUDIT, _audit_status(last_run=failed))
        lines = _invoke(["show"]).output.splitlines()
        assert lines[1] == "  Last run: succeeded 2026-10-05T11:38:00+00:00"
        assert lines[3] == "  Last run: failed 2026-10-05T11:38:00+00:00 — Object storage is not configured."

    def test_a_run_still_going_shows_when_it_was_created(self, fake_client: _FakeClient) -> None:
        running = _job("running", completed_at=None)
        fake_client.respond("GET", RUNS, _runs_status(last_run=running))
        fake_client.respond("GET", AUDIT, _audit_status())
        assert _invoke(["show"]).output.splitlines()[1] == "  Last run: running 2026-10-05T11:00:00+00:00"

    def test_json_nests_both_responses(self, fake_client: _FakeClient) -> None:
        runs, audit = _runs_status(), _audit_status()
        fake_client.respond("GET", RUNS, runs)
        fake_client.respond("GET", AUDIT, audit)
        assert json.loads(_invoke(["show", "--json"]).output) == {"runs": runs, "audit": audit}


class TestSetRuns:
    def test_days_sends_the_window(self, fake_client: _FakeClient) -> None:
        fake_client.respond("PUT", RUNS, _runs_status(days=60))
        result = _invoke(["set", "runs", "--days", "60"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [("PUT", RUNS, {"days": 60})]
        assert result.output.splitlines() == ["Runs and events: kept 60 days (oldest finished run 2026-09-04)"]

    def test_forever_sends_null(self, fake_client: _FakeClient) -> None:
        fake_client.respond("PUT", RUNS, _runs_status(days=None))
        result = _invoke(["set", "runs", "--forever"])
        assert fake_client.calls == [("PUT", RUNS, {"days": None})]
        assert result.output.startswith("Runs and events: kept forever")

    @pytest.mark.parametrize("args", [[], ["--days", "60", "--forever"]])
    def test_exactly_one_of_days_and_forever(self, fake_client: _FakeClient, args: list[str]) -> None:
        result = CliRunner().invoke(retention_group, ["set", "runs", *args], standalone_mode=False)
        assert "exactly one of --days and --forever" in str(result.exception)
        assert fake_client.calls == []

    def test_json_passes_the_response_through(self, fake_client: _FakeClient) -> None:
        body = _runs_status(days=60)
        fake_client.respond("PUT", RUNS, body)
        assert json.loads(_invoke(["set", "runs", "--days", "60", "--json"]).output) == body


class TestSetAudit:
    def test_database_alone_keeps_the_current_archive_window(self, fake_client: _FakeClient) -> None:
        fake_client.respond("GET", AUDIT, _audit_status(hot_days=90, archive_days=365))
        fake_client.respond("PUT", AUDIT, _audit_status(hot_days=60, archive_days=365))
        result = _invoke(["set", "audit", "--database", "60"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [
            ("GET", AUDIT, None),
            ("PUT", AUDIT, {"hot_days": 60, "archive_days": 365}),
        ]
        assert result.output.startswith("Audit: 60 days in the database, archive kept 365 days")

    def test_archive_alone_keeps_the_current_database_window(self, fake_client: _FakeClient) -> None:
        fake_client.respond("GET", AUDIT, _audit_status(hot_days=90, archive_days=None))
        fake_client.respond("PUT", AUDIT, _audit_status(hot_days=90, archive_days=400))
        _invoke(["set", "audit", "--archive", "400"])
        assert fake_client.calls[1] == ("PUT", AUDIT, {"hot_days": 90, "archive_days": 400})

    def test_archive_forever_sends_null(self, fake_client: _FakeClient) -> None:
        fake_client.respond("GET", AUDIT, _audit_status(hot_days=90, archive_days=365))
        fake_client.respond("PUT", AUDIT, _audit_status(hot_days=90, archive_days=None))
        _invoke(["set", "audit", "--archive", "forever"])
        assert fake_client.calls[1] == ("PUT", AUDIT, {"hot_days": 90, "archive_days": None})

    def test_both_flags_set_both_windows(self, fake_client: _FakeClient) -> None:
        fake_client.respond("GET", AUDIT, _audit_status(hot_days=90, archive_days=365))
        fake_client.respond("PUT", AUDIT, _audit_status(hot_days=30, archive_days=None))
        _invoke(["set", "audit", "--database", "30", "--archive", "forever"])
        assert fake_client.calls[1] == ("PUT", AUDIT, {"hot_days": 30, "archive_days": None})

    def test_needs_at_least_one_flag(self, fake_client: _FakeClient) -> None:
        result = CliRunner().invoke(retention_group, ["set", "audit"], standalone_mode=False)
        assert "Give --database, --archive, or both" in str(result.exception)
        assert fake_client.calls == []

    def test_archive_rejects_words_other_than_forever(self, fake_client: _FakeClient) -> None:
        result = CliRunner().invoke(
            retention_group, ["set", "audit", "--archive", "never"], standalone_mode=False
        )
        assert "integer or 'forever'" in str(result.exception)
        assert fake_client.calls == []


@pytest.mark.parametrize(
    ("area", "run_path", "preview_label", "run_label"),
    [
        ("runs", RUNS_RUN, "Run retention preview", "Run retention"),
        ("audit", AUDIT_RUN, "Audit archive preview", "Audit archive"),
    ],
)
class TestPreviewAndRun:
    def test_preview_requests_a_dry_run_and_prints_the_result(
        self, fake_client: _FakeClient, area: str, run_path: str, preview_label: str, run_label: str
    ) -> None:
        job_id = str(uuid4())
        fake_client.respond("POST", run_path, _accepted(job_id), status=202)
        fake_client.respond(
            "GET",
            f"/api/platform-jobs/{job_id}",
            _job(result={"dry_run": True, "workflow_runs": 12, "events": 340}),
        )
        result = _invoke(["preview", area])
        assert result.exit_code == 0, result.output
        assert fake_client.calls[0] == ("POST", run_path, {"dry_run": True})
        assert result.output.splitlines() == ["dry_run: True", "events: 340", "workflow_runs: 12"]

    def test_run_requests_a_real_run(
        self, fake_client: _FakeClient, area: str, run_path: str, preview_label: str, run_label: str
    ) -> None:
        job_id = str(uuid4())
        fake_client.respond("POST", run_path, _accepted(job_id), status=202)
        fake_client.respond("GET", f"/api/platform-jobs/{job_id}", _job(result={"dry_run": False, "deleted": 5}))
        result = _invoke(["run", area])
        assert fake_client.calls[0] == ("POST", run_path, {"dry_run": False})
        assert result.output.splitlines() == ["deleted: 5", "dry_run: False"]

    def test_polls_until_the_job_succeeds(
        self, fake_client: _FakeClient, area: str, run_path: str, preview_label: str, run_label: str
    ) -> None:
        job_id = str(uuid4())
        fake_client.respond("POST", run_path, _accepted(job_id), status=202)
        fake_client.respond(
            "GET",
            f"/api/platform-jobs/{job_id}",
            _job("running", progress={"phase": "Counting", "current": 1, "total": 2, "percent": 50.0}),
            _job("succeeded", result={"dry_run": True}),
        )
        assert _invoke(["preview", area]).exit_code == 0
        assert [call[:2] for call in fake_client.calls] == [
            ("POST", run_path),
            ("GET", f"/api/platform-jobs/{job_id}"),
            ("GET", f"/api/platform-jobs/{job_id}"),
        ]

    def test_failed_job_raises_with_the_label(
        self, fake_client: _FakeClient, area: str, run_path: str, preview_label: str, run_label: str
    ) -> None:
        job_id = str(uuid4())
        fake_client.respond("POST", run_path, _accepted(job_id), status=202)
        fake_client.respond(
            "GET",
            f"/api/platform-jobs/{job_id}",
            _job("failed", error={"code": "x", "message": "boom", "retryable": False}),
        )
        for command, label in (("preview", preview_label), ("run", run_label)):
            result = CliRunner().invoke(retention_group, [command, area], standalone_mode=False)
            assert f"{label} failed (job {job_id}): boom" in str(result.exception)

    def test_json_prints_the_raw_result(
        self, fake_client: _FakeClient, area: str, run_path: str, preview_label: str, run_label: str
    ) -> None:
        job_id = str(uuid4())
        outcome = {"dry_run": True, "workflow_runs": 12}
        fake_client.respond("POST", run_path, _accepted(job_id), status=202)
        fake_client.respond("GET", f"/api/platform-jobs/{job_id}", _job(result=outcome))
        assert json.loads(_invoke(["preview", area, "--json"]).output) == outcome
