"""Unit tests for the ``bifrost users access check`` CLI command.

The command resolves its user, organization and workflow refs, then posts the
what-if to ``POST /api/users/{id}/access/check``. These tests replace
:class:`BifrostClient` with a fake that has the real ``get``/``post`` call
shapes so we can assert on every URL, param and body the CLI sends and on the
text it renders.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from click.testing import CliRunner, Result

from bifrost import client as bifrost_client_module
from bifrost.commands.users import users_group

USER_ID = str(uuid4())
ORG_ID = str(uuid4())
WORKFLOW_ID = str(uuid4())

TRACE: dict[str, Any] = {
    "outcome": "failure",
    "enforced": False,
    "steps": [
        {"key": "run_user", "label": "Run user", "status": "passed", "reason": "", "facts": {}},
        {"key": "permission", "label": "Permission", "status": "stopped", "reason": "no_permission", "facts": {}},
        {"key": "powers", "label": "Workflow powers", "status": "not_applicable", "reason": "no_workflow", "facts": {}},
        {"key": "reach", "label": "Reach", "status": "not_reached", "reason": "", "facts": {}},
    ],
}


class _FakeClient:
    """Routes request paths to canned JSON bodies and records each call."""

    def __init__(self) -> None:
        self.api_url = "http://test.local"
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._responses: dict[str, Any] = {
            "/api/users": [{"id": USER_ID, "email": "ada@contoso.test", "organization_id": None}],
            "/api/organizations": [{"id": ORG_ID, "name": "Contoso"}],
            "/api/workflows": [
                {"id": WORKFLOW_ID, "name": "Sync Invoices", "function_name": "sync", "source_file_path": "workflows/sync.py"}
            ],
            f"/api/users/{USER_ID}/access/check": TRACE,
        }

    def _response(self, method: str, path: str) -> httpx.Response:
        request = httpx.Request(method, f"{self.api_url}{path}")
        return httpx.Response(200, json=self._responses[path], request=request)

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        self.calls.append(("GET", path, params))
        return self._response("GET", path)

    async def post(self, path: str, *, json: dict[str, Any] | None = None) -> httpx.Response:
        self.calls.append(("POST", path, json))
        return self._response("POST", path)


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )
    return fake


def _invoke(args: list[str]) -> Result:
    return CliRunner().invoke(users_group, args, standalone_mode=False, catch_exceptions=False)


class TestUsersAccessCheck:
    def test_global_target_resolves_the_user_and_posts_the_what_if(self, fake_client: _FakeClient) -> None:
        result = _invoke(
            ["access", "check", "ada@contoso.test", "--org", "global", "--operation", "tables.documents.create"]
        )
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [
            ("GET", "/api/users", {"search": "ada@contoso.test"}),
            (
                "POST",
                f"/api/users/{USER_ID}/access/check",
                {"organization_id": "global", "operation": "tables.documents.create"},
            ),
        ]

    def test_org_and_workflow_refs_are_resolved_to_ids(self, fake_client: _FakeClient) -> None:
        result = _invoke(
            [
                "access", "check", USER_ID,
                "--org", "Contoso",
                "--operation", "tables.documents.create",
                "--workflow", "Sync Invoices",
            ]
        )
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [
            ("GET", "/api/organizations", None),
            ("GET", "/api/workflows", None),
            (
                "POST",
                f"/api/users/{USER_ID}/access/check",
                {
                    "organization_id": ORG_ID,
                    "operation": "tables.documents.create",
                    "workflow_id": WORKFLOW_ID,
                },
            ),
        ]

    def test_renders_one_line_per_step_then_the_outcome(self, fake_client: _FakeClient) -> None:
        output = _invoke(
            ["access", "check", USER_ID, "--org", "global", "--operation", "tables.documents.create"]
        ).output
        assert [line.split() for line in output.splitlines()] == [
            ["✓", "Run", "user"],
            ["✗", "Permission", "no_permission"],
            ["–", "Workflow", "powers", "no_workflow"],
            ["·", "Reach"],
            ["Outcome:", "failure", "(not", "enforced)"],
        ]

    def test_outcome_drops_the_not_enforced_note_once_enforced(self, fake_client: _FakeClient) -> None:
        fake_client._responses[f"/api/users/{USER_ID}/access/check"] = {**TRACE, "outcome": "success", "enforced": True}
        output = _invoke(
            ["access", "check", USER_ID, "--org", "global", "--operation", "tables.documents.create"]
        ).output
        assert output.rstrip().splitlines()[-1] == "Outcome: success"

    def test_json_passes_the_trace_through(self, fake_client: _FakeClient) -> None:
        result = _invoke(
            ["access", "check", USER_ID, "--org", "global", "--operation", "tables.documents.create", "--json"]
        )
        assert json.loads(result.output) == TRACE

    def test_org_and_operation_are_required(self, fake_client: _FakeClient) -> None:
        result = CliRunner().invoke(users_group, ["access", "check", USER_ID], standalone_mode=False)
        assert result.exit_code != 0
        assert fake_client.calls == []
