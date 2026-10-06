"""Unit tests for the ``bifrost users`` CLI commands.

``access check`` resolves its user, organization and workflow refs, then posts
the what-if to ``POST /api/users/{id}/access/check``; ``access`` alone shows the
user's access map; ``roles get|set`` read and replace role assignments. These
tests replace :class:`BifrostClient` with a fake that has the real
``get``/``post``/``put`` call shapes so we can assert on every URL, param and
body the CLI sends and on the text it renders.
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
FABRIKAM_ID = str(uuid4())
PROVIDER_ID = str(uuid4())
USER_ROLE_ID = str(uuid4())
HELPDESK_ROLE_ID = str(uuid4())
AUDITOR_ROLE_ID = str(uuid4())

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


ACCESS_MAP: dict[str, Any] = {
    "user_id": USER_ID,
    "name": "Ada Lovelace",
    "email": "ada@contoso.test",
    "home_organization": {"id": ORG_ID, "name": "Contoso"},
    "is_platform_admin": False,
    "is_protected": False,
    "privileged_permissions": [],
    "reach": [
        {"kind": "home", "organization_id": ORG_ID, "organization_name": "Contoso", "label": "Contoso (home)"},
        {"kind": "managed_organizations", "organization_id": None, "organization_name": None, "label": "All customer organizations"},
        {"kind": "platform", "organization_id": None, "organization_name": None, "label": "Global"},
    ],
    "rows": [
        {
            "place": {"kind": "home", "organization_id": ORG_ID, "organization_name": "Contoso", "label": "Contoso (home)"},
            "grants": [
                {
                    "permission": "tables.read",
                    "domain": "tables",
                    "action": "read",
                    "scope": "per_organization",
                    "sources": [{"role_id": USER_ROLE_ID, "role_name": "User", "via": "base"}],
                },
                {
                    "permission": "forms.execute",
                    "domain": "forms",
                    "action": "execute",
                    "scope": "per_organization",
                    "sources": [
                        {"role_id": USER_ROLE_ID, "role_name": "User", "via": "base"},
                        {"role_id": HELPDESK_ROLE_ID, "role_name": "Helpdesk", "via": "additional"},
                    ],
                },
            ],
        },
        {
            "place": {"kind": "platform", "organization_id": None, "organization_name": None, "label": "Global"},
            "grants": [
                {
                    "permission": "audit.read",
                    "domain": "audit",
                    "action": "read",
                    "scope": "platform_wide",
                    "sources": [{"role_id": AUDITOR_ROLE_ID, "role_name": "Auditor", "via": "additional"}],
                }
            ],
        },
    ],
}

ASSIGNMENTS: dict[str, Any] = {
    "base_role": {"id": USER_ROLE_ID, "name": "User", "description": None, "is_builtin": True},
    "additional": [
        {
            "role_id": HELPDESK_ROLE_ID,
            "name": "Helpdesk",
            "description": None,
            "is_builtin": False,
            "permissions": ["tables.read"],
            "boundaries": [
                {"kind": "organization", "organization_id": ORG_ID, "organization_name": "Contoso"},
                {"kind": "organization", "organization_id": FABRIKAM_ID, "organization_name": "Fabrikam"},
            ],
        },
        {
            "role_id": AUDITOR_ROLE_ID,
            "name": "Auditor",
            "description": None,
            "is_builtin": False,
            "permissions": ["audit.read"],
            "boundaries": [
                {"kind": "managed_organizations", "organization_id": None, "organization_name": None},
                {"kind": "platform", "organization_id": None, "organization_name": None},
            ],
        },
    ],
    "is_protected": False,
    "assignable_roles": [],
}


class _FakeClient:
    """Routes request paths to canned JSON bodies and records each call."""

    def __init__(self) -> None:
        self.api_url = "http://test.local"
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._responses: dict[str, Any] = {
            "/api/users": [{"id": USER_ID, "email": "ada@contoso.test", "organization_id": None}],
            "/api/organizations": [{"id": ORG_ID, "name": "Contoso"}, {"id": FABRIKAM_ID, "name": "Fabrikam"}],
            "/api/roles": [
                {"id": USER_ROLE_ID, "name": "User"},
                {"id": HELPDESK_ROLE_ID, "name": "Helpdesk"},
                {"id": AUDITOR_ROLE_ID, "name": "Auditor"},
            ],
            "/auth/authorization": {"provider_organization_id": PROVIDER_ID},
            f"/api/users/{USER_ID}/access": ACCESS_MAP,
            f"/api/users/{USER_ID}/role-assignments": ASSIGNMENTS,
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

    async def put(self, path: str, *, json: dict[str, Any] | None = None) -> httpx.Response:
        self.calls.append(("PUT", path, json))
        return self._response("PUT", path)


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


class TestUsersAccessShow:
    @pytest.mark.parametrize("args", [["access", USER_ID], ["access", "show", USER_ID]])
    def test_a_user_with_no_subcommand_fetches_the_access_map(self, fake_client: _FakeClient, args: list[str]) -> None:
        result = _invoke(args)
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [("GET", f"/api/users/{USER_ID}/access", None)]

    def test_email_is_resolved_to_the_user_id(self, fake_client: _FakeClient) -> None:
        _invoke(["access", "ada@contoso.test"])
        assert fake_client.calls == [
            ("GET", "/api/users", {"search": "ada@contoso.test"}),
            ("GET", f"/api/users/{USER_ID}/access", None),
        ]

    def test_renders_the_reach_line_then_a_block_per_place(self, fake_client: _FakeClient) -> None:
        output = _invoke(["access", USER_ID]).output
        assert [line.split() for line in output.splitlines()] == [
            ["Reach:", "Contoso", "(home),", "All", "customer", "organizations,", "Global"],
            [],
            ["Contoso", "(home)"],
            ["tables.read", "per-org", "(User,", "base)"],
            ["forms.execute", "per-org", "(User,", "base;", "Helpdesk,", "additional)"],
            [],
            ["Global"],
            ["audit.read", "platform-wide", "(Auditor,", "additional)"],
        ]

    def test_grants_are_indented_under_their_place(self, fake_client: _FakeClient) -> None:
        lines = _invoke(["access", USER_ID]).output.splitlines()
        assert lines[2] == "Contoso (home)"
        assert lines[3].startswith("  tables.read")

    def test_json_passes_the_access_map_through(self, fake_client: _FakeClient) -> None:
        assert json.loads(_invoke(["access", USER_ID, "--json"]).output) == ACCESS_MAP

    def test_json_before_the_user_also_dispatches_to_show(self, fake_client: _FakeClient) -> None:
        assert json.loads(_invoke(["access", "--json", USER_ID]).output) == ACCESS_MAP

    def test_check_still_dispatches_to_check(self, fake_client: _FakeClient) -> None:
        result = _invoke(["access", "check", USER_ID, "--org", "global", "--operation", "tables.documents.create"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls[-1][0:2] == ("POST", f"/api/users/{USER_ID}/access/check")

    def test_help_lists_both_commands(self) -> None:
        output = CliRunner().invoke(users_group, ["access", "--help"]).output
        assert "check" in output
        assert "show" in output


class TestUsersRolesGet:
    def test_fetches_the_role_assignments(self, fake_client: _FakeClient) -> None:
        result = _invoke(["roles", "get", USER_ID])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [("GET", f"/api/users/{USER_ID}/role-assignments", None)]

    def test_renders_the_base_role_then_each_additional_role_with_its_places(self, fake_client: _FakeClient) -> None:
        output = _invoke(["roles", "get", USER_ID]).output
        assert [line.split() for line in output.splitlines()] == [
            ["Base", "role:", "User"],
            ["Helpdesk", "Contoso,", "Fabrikam"],
            ["Auditor", "All", "customer", "organizations,", "Global"],
        ]

    def test_no_additional_roles_renders_only_the_base_role(self, fake_client: _FakeClient) -> None:
        fake_client._responses[f"/api/users/{USER_ID}/role-assignments"] = {**ASSIGNMENTS, "additional": []}
        assert _invoke(["roles", "get", USER_ID]).output.splitlines() == ["Base role: User"]

    def test_json_passes_the_assignments_through(self, fake_client: _FakeClient) -> None:
        assert json.loads(_invoke(["roles", "get", USER_ID, "--json"]).output) == ASSIGNMENTS


class TestUsersRolesSet:
    URL = f"/api/users/{USER_ID}/role-assignments"

    def test_role_without_places_sends_no_boundaries_and_keeps_the_current_base(self, fake_client: _FakeClient) -> None:
        result = _invoke(["roles", "set", USER_ID, "--role", "Helpdesk"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [
            ("GET", "/api/roles", None),
            ("GET", self.URL, None),
            ("PUT", self.URL, {"base_role_id": USER_ROLE_ID, "additional": [{"role_id": HELPDESK_ROLE_ID}]}),
        ]

    def test_base_given_skips_reading_the_current_assignments(self, fake_client: _FakeClient) -> None:
        _invoke(["roles", "set", USER_ID, "--base", "Helpdesk", "--role", "Auditor"])
        assert fake_client.calls[-1] == (
            "PUT",
            self.URL,
            {"base_role_id": HELPDESK_ROLE_ID, "additional": [{"role_id": AUDITOR_ROLE_ID}]},
        )
        assert ("GET", self.URL, None) not in fake_client.calls

    def test_org_places_resolve_to_organization_boundaries(self, fake_client: _FakeClient) -> None:
        _invoke(["roles", "set", USER_ID, "--base", "User", "--role", "Helpdesk=org:Contoso,org:Fabrikam"])
        assert fake_client.calls[-1][2] == {
            "base_role_id": USER_ROLE_ID,
            "additional": [
                {
                    "role_id": HELPDESK_ROLE_ID,
                    "boundaries": [
                        {"kind": "organization", "organization_id": ORG_ID},
                        {"kind": "organization", "organization_id": FABRIKAM_ID},
                    ],
                }
            ],
        }

    def test_customers_and_global_map_to_their_boundary_kinds(self, fake_client: _FakeClient) -> None:
        _invoke(["roles", "set", USER_ID, "--base", "User", "--role", "Auditor=customers,global"])
        assert fake_client.calls[-1][2]["additional"] == [
            {
                "role_id": AUDITOR_ROLE_ID,
                "boundaries": [{"kind": "managed_organizations"}, {"kind": "platform"}],
            }
        ]

    def test_all_expands_to_customers_the_provider_organization_and_global(self, fake_client: _FakeClient) -> None:
        _invoke(["roles", "set", USER_ID, "--base", "User", "--role", "Auditor=all"])
        assert ("GET", "/auth/authorization", None) in fake_client.calls
        assert fake_client.calls[-1][2]["additional"] == [
            {
                "role_id": AUDITOR_ROLE_ID,
                "boundaries": [
                    {"kind": "managed_organizations"},
                    {"kind": "organization", "organization_id": PROVIDER_ID},
                    {"kind": "platform"},
                ],
            }
        ]

    def test_a_place_named_twice_is_sent_once(self, fake_client: _FakeClient) -> None:
        _invoke(["roles", "set", USER_ID, "--base", "User", "--role", "Auditor=global,all"])
        kinds = [b["kind"] for b in fake_client.calls[-1][2]["additional"][0]["boundaries"]]
        assert kinds == ["platform", "managed_organizations", "organization"]

    def test_several_roles_are_sent_in_order(self, fake_client: _FakeClient) -> None:
        _invoke(["roles", "set", USER_ID, "--base", "User", "--role", "Helpdesk", "--role", "Auditor=global"])
        assert fake_client.calls[-1][2]["additional"] == [
            {"role_id": HELPDESK_ROLE_ID},
            {"role_id": AUDITOR_ROLE_ID, "boundaries": [{"kind": "platform"}]},
        ]

    def test_no_roles_clears_the_additional_roles(self, fake_client: _FakeClient) -> None:
        result = _invoke(["roles", "set", USER_ID, "--no-roles"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls[-1] == ("PUT", self.URL, {"base_role_id": USER_ROLE_ID, "additional": []})

    def test_prints_the_updated_assignments(self, fake_client: _FakeClient) -> None:
        output = _invoke(["roles", "set", USER_ID, "--no-roles"]).output
        assert output.splitlines()[0] == "Base role: User"

    def test_nothing_to_change_is_a_usage_error(self, fake_client: _FakeClient) -> None:
        result = CliRunner().invoke(users_group, ["roles", "set", USER_ID], standalone_mode=False)
        assert result.exit_code != 0
        assert fake_client.calls == []

    def test_no_roles_conflicts_with_role(self, fake_client: _FakeClient) -> None:
        result = CliRunner().invoke(
            users_group, ["roles", "set", USER_ID, "--no-roles", "--role", "Helpdesk"], standalone_mode=False
        )
        assert result.exit_code != 0
        assert fake_client.calls == []

    @pytest.mark.parametrize("role", ["Helpdesk=nowhere", "Helpdesk=", "Helpdesk=org:"])
    def test_a_bad_place_is_a_usage_error_and_nothing_is_sent(self, fake_client: _FakeClient, role: str) -> None:
        result = CliRunner().invoke(
            users_group, ["roles", "set", USER_ID, "--base", "User", "--role", role], standalone_mode=False
        )
        assert result.exit_code != 0
        assert not any(call[0] == "PUT" for call in fake_client.calls)
