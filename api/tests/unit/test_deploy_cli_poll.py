"""Unit: the CLI deploy poll loop renders progress + terminal status.

The deploy endpoint is async (Task 7) — the CLI POSTs, gets a job id, then
polls ``GET /api/solutions/deploy-jobs/{id}`` until a terminal status. Interactive
terminals animate one spinner line; redirected output stays stable. Failures
surface the shared platform-job error and durable details URL.
"""
from __future__ import annotations

import asyncio

from bifrost.commands.solution import _poll_deploy_job


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.status_code = 200
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Returns ``running`` for the first N status polls, then a terminal state."""

    def __init__(
        self,
        terminal: dict,
        running_count: int = 2,
        platform_error: dict | None = None,
    ) -> None:
        self._terminal = terminal
        self._running_left = running_count
        self._platform_error = platform_error
        self.calls = 0
        self.api_url = "https://bifrost.example"

    async def get(self, path: str, **kwargs):  # noqa: ANN003
        self.calls += 1
        if path.startswith("/api/platform-jobs/"):
            return _FakeResponse({"error": self._platform_error})
        if self._running_left > 0:
            self._running_left -= 1
            return _FakeResponse({"status": "running", "error": None})
        return _FakeResponse(self._terminal)


def _run(coro):
    return asyncio.run(coro)


def test_poll_non_interactive_prints_only_result(capsys):
    client = _FakeClient(
        {"status": "succeeded", "error": None, "install_id": "abc"}
    )
    rc = _run(_poll_deploy_job(client, "job-1", interval=0.0, interactive=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "deploying..." not in out.lower()
    assert "Deploy complete" in out


def test_poll_interactive_animates_one_spinner_line(capsys):
    client = _FakeClient(
        {"status": "succeeded", "error": None}, running_count=1
    )

    rc = _run(_poll_deploy_job(client, "job-spin", interval=0.001, interactive=True))

    out = capsys.readouterr().out
    assert rc == 0
    assert "\r| Deploying..." in out
    assert "Still deploying" not in out
    assert "Deploy complete" in out


def test_poll_surfaces_failure(capsys):
    client = _FakeClient(
        {"status": "failed", "error": "manifest entry `diverged` mismatch"},
        platform_error={
            "code": "solution_deploy_failed",
            "message": "manifest entry `diverged` mismatch",
            "retryable": False,
        },
    )
    rc = _run(_poll_deploy_job(client, "job-2", interval=0.0))
    captured = capsys.readouterr()
    assert rc == 1
    combined = captured.out + captured.err
    assert "diverged" in combined
    assert "solution_deploy_failed" in combined
    assert "https://bifrost.example/api/platform-jobs/job-2" in combined


def test_poll_preserves_failure_when_job_details_lookup_fails(capsys):
    class DetailsFailureClient(_FakeClient):
        async def get(self, path: str, **kwargs):  # noqa: ANN003
            if path.startswith("/api/platform-jobs/"):
                raise RuntimeError("job details unavailable")
            return await super().get(path, **kwargs)

    client = DetailsFailureClient(
        {"status": "failed", "error": "bundle downgrade blocked"}
    )

    rc = _run(_poll_deploy_job(client, "job-details-fail", interval=0.0))

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc == 1
    assert "bundle downgrade blocked" in combined
    assert "https://bifrost.example/api/platform-jobs/job-details-fail" in combined


def test_poll_install_action_reports_solution_id(capsys):
    """The install command reuses the poll loop with ``action="Install"``; on
    success it echoes the solution id the job's ``result`` carries."""
    client = _FakeClient(
        {
            "status": "succeeded",
            "error": None,
            "install_id": None,
            "result": {"solution_id": "sol-123", "slug": "acme"},
        }
    )
    rc = _run(_poll_deploy_job(client, "job-i", interval=0.0, action="Install"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Still installing..." not in out
    assert "Install complete: solution sol-123 (slug=acme)." in out


def test_poll_install_failure_reattaches_inactive_hint(capsys):
    """A build-gate refusal surfaces as a failed job; the poll loop re-attaches
    the reactivate hint when the job result flags an inactive install."""
    client = _FakeClient(
        {
            "status": "failed",
            "error": "An inactive install of 'acme' already exists",
            "result": {"reason": "inactive_install_exists", "slug": "acme"},
        }
    )
    rc = _run(_poll_deploy_job(client, "job-x", interval=0.0, action="Install"))
    captured = capsys.readouterr()
    assert rc == 1
    combined = captured.out + captured.err
    assert "--reactivate" in combined


def test_poll_prints_phase_changes(capsys):
    class PhaseClient:
        def __init__(self) -> None:
            self.payloads = [
                {"status": "running", "result": {"phase": "storing source artifact"}},
                {"status": "running", "result": {"phase": "building app dist"}},
                {"status": "running", "result": {"phase": "building app dist"}},
                {"status": "succeeded", "result": {}},
            ]

        async def get(self, path: str, **kwargs):  # noqa: ANN003
            return _FakeResponse(self.payloads.pop(0))

    rc = _run(_poll_deploy_job(PhaseClient(), "job-3", interval=0.0))
    out = capsys.readouterr().out

    assert rc == 0
    assert "storing source artifact" in out
    assert "building app dist" in out
    assert out.count("building app dist") == 1


def test_poll_stops_at_job_timeout(capsys):
    client = _FakeClient({"status": "succeeded"}, running_count=10)

    rc = _run(
        _poll_deploy_job(client, "job-timeout", interval=0.0, timeout_seconds=0.0)
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert client.calls == 1
    assert "timed out" in (captured.out + captured.err).lower()
    assert "job-timeout" in (captured.out + captured.err)
