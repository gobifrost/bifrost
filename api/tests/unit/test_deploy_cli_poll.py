"""Unit: the CLI deploy poll loop prints a heartbeat + terminal status.

The deploy endpoint is async (Task 7) — the CLI POSTs, gets a job id, then
polls ``GET /api/solutions/deploy-jobs/{id}`` until a terminal status. While the
job is still running it prints ``Still deploying... Ns``; on success it prints
``Deploy complete``; on failure it surfaces the server-captured error.
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

    def __init__(self, terminal: dict, running_count: int = 2) -> None:
        self._terminal = terminal
        self._running_left = running_count
        self.calls = 0

    async def get(self, path: str, **kwargs):  # noqa: ANN003
        self.calls += 1
        if self._running_left > 0:
            self._running_left -= 1
            return _FakeResponse({"status": "running", "error": None})
        return _FakeResponse(self._terminal)


def _run(coro):
    return asyncio.run(coro)


def test_poll_prints_heartbeat_and_result(capsys):
    client = _FakeClient(
        {"status": "succeeded", "error": None, "install_id": "abc"}
    )
    rc = _run(_poll_deploy_job(client, "job-1", interval=0.0))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Still deploying..." in out
    assert "Deploy complete" in out


def test_poll_surfaces_failure(capsys):
    client = _FakeClient(
        {"status": "failed", "error": "manifest entry `diverged` mismatch"}
    )
    rc = _run(_poll_deploy_job(client, "job-2", interval=0.0))
    captured = capsys.readouterr()
    assert rc == 1
    assert "diverged" in (captured.out + captured.err)


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
