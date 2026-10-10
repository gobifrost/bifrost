"""Unit tests for the ``bifrost audit`` CLI group.

``audit list`` wraps ``GET /api/audit`` and ``audit explain`` wraps
``GET /api/audit/{id}/explain``. These tests replace :class:`BifrostClient`
with a fake that has the real ``get`` call shape so we can assert on the URL
and query params the CLI sends and on the text it renders.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from click.testing import CliRunner, Result

from bifrost import client as bifrost_client_module
from bifrost.commands.audit import audit_group


class _FakeClient:
    """Routes ``(method, path)`` to a canned JSON body and records each call."""

    def __init__(self) -> None:
        self.api_url = "http://test.local"
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._responses: dict[str, Any] = {}

    def respond(self, path: str, body: Any) -> None:
        self._responses[path] = body

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        self.calls.append(("GET", path, params))
        request = httpx.Request("GET", f"{self.api_url}{path}")
        return httpx.Response(200, json=self._responses[path], request=request)


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
    return CliRunner().invoke(audit_group, args, standalone_mode=False, catch_exceptions=False)


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": str(uuid4()),
        "timestamp": "2026-10-05T10:00:00+00:00",
        "action": "access.check",
        "resource_type": "policy",
        "resource_id": None,
        "outcome": "failure",
        "source": "http",
        "actor": {
            "user_id": str(uuid4()),
            "user_email": "ada@contoso.test",
            "user_name": "Ada",
            "organization_id": str(uuid4()),
            "organization_name": "Contoso",
        },
    }
    entry.update(overrides)
    return entry


def _rows(output: str) -> list[list[str]]:
    return [line.split() for line in output.splitlines() if line.strip()]


def _table(output: str) -> list[list[str]]:
    return [[cell.strip() for cell in line.split("|")] for line in output.splitlines() if " | " in line]


def _header(output: str) -> dict[str, str]:
    pairs = (line.split(":", 1) for line in output.splitlines() if ":" in line and " | " not in line)
    return {key.strip(): value.strip() for key, value in pairs}


class TestAuditList:
    def test_sends_every_filter_as_a_query_param(self, fake_client: _FakeClient) -> None:
        user_id, execution_id = str(uuid4()), str(uuid4())
        fake_client.respond("/api/audit", {"entries": [], "groups": None, "continuation_token": None})
        result = _invoke(
            [
                "list",
                "--action", "access.check",
                "--outcome", "failure",
                "--resource-type", "policy",
                "--user", user_id,
                "--execution", execution_id,
                "--since", "2026-10-01T00:00:00Z",
                "--until", "2026-10-05T00:00:00Z",
                "--search", "invoices",
                "--limit", "20",
            ]
        )
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [
            (
                "GET",
                "/api/audit",
                {
                    "action": "access.check",
                    "outcome": "failure",
                    "resource_type": "policy",
                    "user_id": user_id,
                    "execution_id": execution_id,
                    "start_date": "2026-10-01T00:00:00Z",
                    "end_date": "2026-10-05T00:00:00Z",
                    "search": "invoices",
                    "limit": 20,
                },
            )
        ]

    def test_sends_nothing_that_was_not_given(self, fake_client: _FakeClient) -> None:
        fake_client.respond("/api/audit", {"entries": [], "groups": None, "continuation_token": None})
        assert _invoke(["list"]).exit_code == 0
        assert fake_client.calls == [("GET", "/api/audit", {})]

    def test_user_email_is_resolved_to_the_acting_user_id(self, fake_client: _FakeClient) -> None:
        user_id = str(uuid4())
        fake_client.respond("/api/users", [{"id": user_id, "email": "ada@contoso.test", "organization_id": None}])
        fake_client.respond("/api/audit", {"entries": [], "groups": None, "continuation_token": None})
        assert _invoke(["list", "--user", "ada@contoso.test"]).exit_code == 0
        assert fake_client.calls == [
            ("GET", "/api/users", {"search": "ada@contoso.test"}),
            ("GET", "/api/audit", {"user_id": user_id}),
        ]

    def test_workflow_and_organization_filters(self, fake_client: _FakeClient) -> None:
        workflow_id, organization_id = str(uuid4()), str(uuid4())
        fake_client.respond("/api/audit", {"entries": [], "groups": None, "continuation_token": None})
        assert _invoke(["list", "--workflow", workflow_id, "--org", organization_id]).exit_code == 0
        assert _invoke(["list", "--org", "global"]).exit_code == 0
        assert fake_client.calls == [
            ("GET", "/api/audit", {"workflow_id": workflow_id, "organization_id": organization_id}),
            ("GET", "/api/audit", {"organization_id": "none"}),
        ]

    def test_renders_one_line_per_entry(self, fake_client: _FakeClient) -> None:
        fake_client.respond(
            "/api/audit",
            {
                "entries": [
                    _entry(),
                    _entry(
                        action="user.login",
                        outcome="success",
                        resource_type=None,
                        actor={"user_id": None, "user_email": None, "user_name": None, "organization_id": None, "organization_name": None},
                    ),
                ],
                "groups": None,
                "continuation_token": None,
            },
        )
        result = _invoke(["list"])
        assert _rows(result.output) == [
            ["2026-10-05T10:00:00+00:00", "access.check", "failure", "ada@contoso.test", "Contoso", "policy"],
            ["2026-10-05T10:00:00+00:00", "user.login", "success", "-", "-", "-"],
        ]

    def test_group_by_sends_the_param_and_renders_groups(self, fake_client: _FakeClient) -> None:
        fake_client.respond(
            "/api/audit",
            {
                "entries": [],
                "groups": [
                    {"key": "access.check", "count": 12, "last_seen": "2026-10-05T10:00:00+00:00", "sample": _entry()},
                    {"key": None, "count": 3, "last_seen": "2026-10-04T09:00:00+00:00", "sample": _entry()},
                ],
                "continuation_token": None,
            },
        )
        result = _invoke(["list", "--group-by", "action"])
        assert fake_client.calls == [("GET", "/api/audit", {"group_by": "action"})]
        assert _rows(result.output) == [
            ["12", "access.check", "2026-10-05T10:00:00+00:00"],
            ["3", "-", "2026-10-04T09:00:00+00:00"],
        ]

    def test_workflow_and_organization_groups_are_named(self, fake_client: _FakeClient) -> None:
        workflow_id, deleted_id, contoso_id = str(uuid4()), str(uuid4()), str(uuid4())
        fake_client.respond(
            "/api/audit",
            {
                "entries": [],
                "groups": [
                    {
                        "key": workflow_id,
                        "count": 6,
                        "last_seen": "2026-10-05T10:00:00+00:00",
                        "sample": _entry(workflow_name="Fleet Invoice Report"),
                    },
                    {"key": None, "count": 2, "last_seen": "2026-10-04T09:00:00+00:00", "sample": _entry(workflow_name=None)},
                    {"key": deleted_id, "count": 1, "last_seen": "2026-10-03T09:00:00+00:00", "sample": _entry(workflow_name=None)},
                ],
                "continuation_token": None,
            },
        )
        lines = _invoke(["list", "--group-by", "workflow"]).output.splitlines()
        assert [line.split("  ")[1].strip() for line in lines] == ["Fleet Invoice Report", "No Workflow", deleted_id]

        fake_client.respond(
            "/api/audit",
            {
                "entries": [],
                "groups": [
                    {"key": contoso_id, "count": 4, "last_seen": "2026-10-05T10:00:00+00:00", "sample": _entry()},
                    {"key": None, "count": 1, "last_seen": "2026-10-04T09:00:00+00:00", "sample": _entry()},
                ],
                "continuation_token": None,
            },
        )
        lines = _invoke(["list", "--group-by", "organization"]).output.splitlines()
        assert [line.split("  ")[1].strip() for line in lines] == ["Contoso", "Global"]

    def test_says_so_when_there_are_no_entries(self, fake_client: _FakeClient) -> None:
        fake_client.respond("/api/audit", {"entries": [], "groups": None, "continuation_token": None})
        assert _invoke(["list"]).output.strip() == "No entries."

    def test_says_so_when_there_are_no_groups(self, fake_client: _FakeClient) -> None:
        fake_client.respond("/api/audit", {"entries": [], "groups": [], "continuation_token": None})
        assert _invoke(["list", "--group-by", "action"]).output.strip() == "No groups."

    def test_rejects_an_unknown_group_by(self, fake_client: _FakeClient) -> None:
        result = CliRunner().invoke(audit_group, ["list", "--group-by", "color"], standalone_mode=False)
        assert isinstance(result.exception, Exception) and "color" in str(result.exception)
        assert fake_client.calls == []

    def test_json_passes_the_response_through(self, fake_client: _FakeClient) -> None:
        body = {"entries": [_entry()], "groups": None, "continuation_token": None}
        fake_client.respond("/api/audit", body)
        result = _invoke(["list", "--json"])
        assert json.loads(result.output) == body


def _trace(outcome: str, steps: list[tuple[str, str, str, str]]) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "enforced": False,
        "steps": [
            {"key": key, "label": label, "status": status, "reason": reason, "facts": {}}
            for key, label, status, reason in steps
        ],
    }


def _explanation(**overrides: Any) -> dict[str, Any]:
    explanation: dict[str, Any] = {
        "event": _entry(),
        "then": _trace(
            "failure",
            [
                ("run_user", "Run user", "passed", ""),
                ("permission", "Permission", "stopped", "no_permission"),
                ("reach", "Reach", "not_reached", ""),
            ],
        ),
        "now": _trace(
            "success",
            [
                ("run_user", "Run user", "passed", ""),
                ("permission", "Permission", "passed", "full"),
                ("reach", "Reach", "passed", "home"),
            ],
        ),
        "now_unavailable": None,
        "changed": True,
    }
    explanation.update(overrides)
    return explanation


class TestAuditExplain:
    def test_fetches_the_explanation_for_the_event(self, fake_client: _FakeClient) -> None:
        event_id = str(uuid4())
        fake_client.respond(f"/api/audit/{event_id}/explain", _explanation())
        assert _invoke(["explain", event_id]).exit_code == 0
        assert fake_client.calls == [("GET", f"/api/audit/{event_id}/explain", None)]

    def test_changed_explanation_shows_then_and_now_per_step(self, fake_client: _FakeClient) -> None:
        event_id = str(uuid4())
        fake_client.respond(f"/api/audit/{event_id}/explain", _explanation())
        output = _invoke(["explain", event_id]).output
        assert _header(output) == {
            "Action": "access.check",
            "Outcome": "failure",
            "When": "2026-10-05T10:00:00+00:00",
            "Run user": "ada@contoso.test",
            "Organization": "Contoso",
            "Changed": "yes",
        }
        assert _table(output) == [
            ["STEP", "THEN", "NOW"],
            ["Run user", "passed", "passed"],
            ["Permission", "stopped (no_permission)", "passed (full)"],
            ["Reach", "not_reached", "passed (home)"],
        ]
        assert output.rstrip().splitlines()[-1] == "Changed: yes"

    def test_a_named_permission_check_names_its_permission(self, fake_client: _FakeClient) -> None:
        event_id = str(uuid4())
        then = _trace(
            "failure",
            [
                ("run_user", "Run user", "passed", "person"),
                ("target", "Target in reach", "stopped", "outside"),
                ("permission", "Permission", "not_reached", ""),
            ],
        )
        then["steps"][2]["facts"] = {"permission": "agents.read", "permission_display_name": "Read Agents"}
        fake_client.respond(
            f"/api/audit/{event_id}/explain",
            _explanation(
                event=_entry(resource_type="permission"),
                then=then,
                now=None,
                now_unavailable="run_user_missing",
                changed=None,
            ),
        )
        output = _invoke(["explain", event_id]).output
        assert _header(output)["Permission"] == "Read Agents (agents.read)"

    def test_unchanged_explanation_says_no(self, fake_client: _FakeClient) -> None:
        event_id = str(uuid4())
        fake_client.respond(f"/api/audit/{event_id}/explain", _explanation(changed=False))
        output = _invoke(["explain", event_id]).output
        assert output.rstrip().splitlines()[-1] == "Changed: no"

    @pytest.mark.parametrize(
        ("reason", "sentence"),
        [
            ("rows_not_stored", "table row decisions can't be re-run: the rows aren't stored"),
            ("run_user_missing", "the run's user no longer exists"),
            ("run_as_user_missing", "the user it acted as no longer exists"),
            ("workflow_missing", "the workflow no longer exists"),
            (
                "solution_not_recorded",
                "this file check was recorded before Solutions were stored with it",
            ),
            ("inputs_not_stored", "this check was recorded before its inputs were stored with it"),
        ],
    )
    def test_unavailable_now_prints_the_reason_instead_of_the_column(
        self, fake_client: _FakeClient, reason: str, sentence: str
    ) -> None:
        event_id = str(uuid4())
        fake_client.respond(
            f"/api/audit/{event_id}/explain",
            _explanation(now=None, now_unavailable=reason, changed=None),
        )
        output = _invoke(["explain", event_id]).output
        assert _table(output) == [
            ["STEP", "THEN"],
            ["Run user", "passed"],
            ["Permission", "stopped (no_permission)"],
            ["Reach", "not_reached"],
        ]
        assert f"NOW: not available — {sentence}" in output.splitlines()
        assert "Changed" not in output

    def test_no_stored_trace_shows_now_only(self, fake_client: _FakeClient) -> None:
        event_id = str(uuid4())
        fake_client.respond(f"/api/audit/{event_id}/explain", _explanation(then=None, changed=None))
        output = _invoke(["explain", event_id]).output
        assert _table(output) == [
            ["STEP", "NOW"],
            ["Run user", "passed"],
            ["Permission", "passed (full)"],
            ["Reach", "passed (home)"],
        ]
        assert "THEN: not stored — this check was recorded before its decision was stored with it" in output.splitlines()
        assert "Changed" not in output

    def test_neither_then_nor_now_prints_both_reasons(self, fake_client: _FakeClient) -> None:
        event_id = str(uuid4())
        fake_client.respond(
            f"/api/audit/{event_id}/explain",
            _explanation(then=None, now=None, now_unavailable="inputs_not_stored", changed=None),
        )
        output = _invoke(["explain", event_id]).output
        assert _table(output) == []
        assert output.rstrip().splitlines()[-2:] == [
            "THEN: not stored — this check was recorded before its decision was stored with it",
            "NOW: not available — this check was recorded before its inputs were stored with it",
        ]

    def test_json_passes_the_explanation_through(self, fake_client: _FakeClient) -> None:
        event_id = str(uuid4())
        body = _explanation()
        fake_client.respond(f"/api/audit/{event_id}/explain", body)
        result = _invoke(["explain", event_id, "--json"])
        assert json.loads(result.output) == body
