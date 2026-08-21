from __future__ import annotations

from types import SimpleNamespace

import pytest

from bifrost.commands.solution import (
    _poll_deploy_job,
    _post_create_install_for_descriptor,
    _solution_authorization_headers,
)


class _Response:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self) -> dict:
        return self._body


def test_solution_authorization_headers_are_exact() -> None:
    assert _solution_authorization_headers(None) == {"X-Bifrost-Boundary": "platform"}
    assert _solution_authorization_headers("org-1") == {
        "X-Bifrost-Boundary": "organization:org-1"
    }


@pytest.mark.asyncio
async def test_create_install_forwards_the_target_boundary() -> None:
    calls: list[dict] = []

    class _Client:
        async def post(self, path, **kwargs):  # noqa: ANN001, ANN201
            calls.append({"path": path, **kwargs})
            return _Response(201, {"id": "solution-1"})

    descriptor = SimpleNamespace(
        slug="expense-tracker",
        name="Expense Tracker",
        global_repo_access=False,
        git_connected=False,
        git_repo_url=None,
        repo_subpath=None,
        git_ref=None,
    )

    await _post_create_install_for_descriptor(_Client(), descriptor, "org-1")

    assert calls == [
        {
            "path": "/api/solutions",
            "json": {
                "slug": "expense-tracker",
                "name": "Expense Tracker",
                "organization_id": "org-1",
                "global_repo_access": False,
                "git_connected": False,
                "git_repo_url": None,
                "repo_subpath": None,
                "git_ref": None,
            },
            "headers": {"X-Bifrost-Boundary": "organization:org-1"},
        }
    ]


@pytest.mark.asyncio
async def test_deploy_job_poll_keeps_the_original_boundary() -> None:
    calls: list[dict] = []

    class _Client:
        async def get(self, path, **kwargs):  # noqa: ANN001, ANN201
            calls.append({"path": path, **kwargs})
            return _Response(200, {"status": "succeeded", "result": {}})

    headers = {"X-Bifrost-Boundary": "organization:org-1"}
    result = await _poll_deploy_job(
        _Client(),
        "job-1",
        interval=0,
        authorization_headers=headers,
    )

    assert result == 0
    assert calls == [
        {
            "path": "/api/solutions/deploy-jobs/job-1",
            "headers": headers,
        }
    ]
