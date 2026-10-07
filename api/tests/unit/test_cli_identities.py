"""Unit tests for the identity-facing CLI commands.

``users list [--identities]`` and ``users create --identity`` read and create
identities; ``workflows update --run-as`` names the identity a workflow runs as;
``workflows recommendations`` shows what that identity would also need and
``workflows grant`` applies the recommended grants. These tests replace :class:`BifrostClient` with a
fake that has the real ``get``/``post``/``put``/``patch`` call shapes so we can
assert on every URL, param and body the CLI sends and on the text it renders.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import click
import httpx
import pytest
from click.testing import CliRunner, Result

from bifrost import client as bifrost_client_module
from bifrost.commands.users import users_group
from bifrost.commands.workflows import workflows_group

ORG_ID = str(uuid4())
FABRIKAM_ID = str(uuid4())
WORKFLOW_ID = str(uuid4())
DEFAULT_IDENTITY_ID = str(uuid4())
NIGHTLY_IDENTITY_ID = str(uuid4())
USER_ROLE_ID = str(uuid4())
HELPDESK_ROLE_ID = str(uuid4())
HR_ROLE_ID = str(uuid4())


def _identity(identity_id: str, name: str, kind: str, workflows_using: int) -> dict[str, Any]:
    return {
        "id": identity_id,
        "name": name,
        "identity_kind": kind,
        "organization_id": ORG_ID,
        "organization_name": "Contoso",
        "base_role": {"id": USER_ROLE_ID, "name": "User"},
        "additional_roles": [],
        "workflows_using": workflows_using,
    }


DEFAULT_IDENTITY = _identity(DEFAULT_IDENTITY_ID, "Default Identity", "org_default", 4)
NIGHTLY_IDENTITY = _identity(NIGHTLY_IDENTITY_ID, "Contoso Nightly", "custom", 1)

RECOMMENDED: dict[str, Any] = {
    "identity_id": DEFAULT_IDENTITY_ID,
    "observed_runs": 12,
    "window_days": 30,
    "items": [
        {
            "kind": "reach",
            "label": "Fabrikam",
            "detail": "Switched into Fabrikam in 3 runs.",
            "grant": {
                "role_id": HELPDESK_ROLE_ID,
                "boundaries": [{"kind": "organization", "organization_id": FABRIKAM_ID}],
            },
        },
        {
            "kind": "policy_role",
            "label": "HR",
            "detail": "A policy looked for the HR role in 5 runs.",
            "grant": None,
        },
        {
            "kind": "workflow_role",
            "label": "Helpdesk",
            "detail": "Only Helpdesk users may start this workflow.",
            "grant": {"role_id": HELPDESK_ROLE_ID, "boundaries": [{"kind": "managed_organizations"}]},
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
            "permissions": [],
            "boundaries": [{"kind": "organization", "organization_id": ORG_ID, "organization_name": "Contoso"}],
        }
    ],
    "is_protected": False,
    "assignable_roles": [],
}


class _FakeClient:
    """Routes ``(method, path)`` to canned JSON bodies and records each call."""

    def __init__(self) -> None:
        self.api_url = "http://test.local"
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self._responses: dict[tuple[str, str], Any] = {
            ("GET", "/api/users"): [
                {"id": str(uuid4()), "name": "Ada Lovelace", "email": "ada@contoso.test", "identity_kind": None}
            ],
            ("GET", "/api/organizations"): [
                {"id": ORG_ID, "name": "Contoso"},
                {"id": FABRIKAM_ID, "name": "Fabrikam"},
            ],
            ("GET", "/api/roles"): [
                {"id": USER_ROLE_ID, "name": "User"},
                {"id": HELPDESK_ROLE_ID, "name": "Helpdesk"},
                {"id": HR_ROLE_ID, "name": "HR"},
            ],
            ("GET", "/api/identities"): [DEFAULT_IDENTITY, NIGHTLY_IDENTITY],
            ("POST", "/api/identities"): NIGHTLY_IDENTITY,
            ("GET", "/api/workflows"): [
                {"id": WORKFLOW_ID, "name": "Sync Invoices", "function_name": "sync", "source_file_path": "workflows/sync.py"}
            ],
            ("PATCH", f"/api/workflows/{WORKFLOW_ID}"): {"id": WORKFLOW_ID, "run_identity_id": NIGHTLY_IDENTITY_ID},
            ("GET", f"/api/workflows/{WORKFLOW_ID}/recommended-access"): RECOMMENDED,
            ("GET", f"/api/users/{DEFAULT_IDENTITY_ID}/role-assignments"): ASSIGNMENTS,
            ("PUT", f"/api/users/{DEFAULT_IDENTITY_ID}/role-assignments"): ASSIGNMENTS,
        }

    def _response(self, method: str, path: str) -> httpx.Response:
        request = httpx.Request(method, f"{self.api_url}{path}")
        return httpx.Response(200, json=self._responses[(method, path)], request=request)

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        self.calls.append(("GET", path, params))
        return self._response("GET", path)

    async def post(self, path: str, *, json: dict[str, Any] | None = None) -> httpx.Response:
        self.calls.append(("POST", path, json))
        return self._response("POST", path)

    async def put(self, path: str, *, json: dict[str, Any] | None = None) -> httpx.Response:
        self.calls.append(("PUT", path, json))
        return self._response("PUT", path)

    async def patch(self, path: str, *, json: dict[str, Any] | None = None) -> httpx.Response:
        self.calls.append(("PATCH", path, json))
        return self._response("PATCH", path)


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )
    return fake


def _users(args: list[str]) -> Result:
    return CliRunner().invoke(users_group, args, standalone_mode=False, catch_exceptions=False)


def _workflows(args: list[str]) -> Result:
    return CliRunner().invoke(workflows_group, args, standalone_mode=False, catch_exceptions=False)


class TestUsersList:
    def test_people_are_listed_without_the_identities_switch(self, fake_client: _FakeClient) -> None:
        result = _users(["list"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [("GET", "/api/users", None)]
        assert [line.split() for line in result.output.splitlines()] == [["Ada", "Lovelace", "ada@contoso.test"]]

    def test_identities_switch_lists_identities_and_names_a_default_by_its_place(self, fake_client: _FakeClient) -> None:
        global_default = {**DEFAULT_IDENTITY, "identity_kind": "global_default", "organization_id": None, "organization_name": None}
        fake_client._responses[("GET", "/api/identities")] = [global_default, DEFAULT_IDENTITY, NIGHTLY_IDENTITY]
        result = _users(["list", "--identities"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [("GET", "/api/identities", None)]
        assert result.output.splitlines() == [
            "Default Identity (Global)  Global",
            "Default Identity (Contoso)  Default",
            "Contoso Nightly  Custom",
        ]

    def test_identities_json_passes_the_identities_through(self, fake_client: _FakeClient) -> None:
        result = _users(["list", "--identities", "--json"])
        assert json.loads(result.output) == [DEFAULT_IDENTITY, NIGHTLY_IDENTITY]

    def test_json_passes_the_list_through(self, fake_client: _FakeClient) -> None:
        result = _users(["list", "--json"])
        assert json.loads(result.output) == fake_client._responses[("GET", "/api/users")]


class TestUsersCreateIdentity:
    def test_org_name_is_resolved_and_the_identity_posted(self, fake_client: _FakeClient) -> None:
        result = _users(["create", "--identity", "--org", "Contoso", "--name", "Contoso Nightly"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [
            ("GET", "/api/organizations", None),
            ("POST", "/api/identities", {"name": "Contoso Nightly", "organization_id": ORG_ID}),
        ]
        assert result.output.splitlines() == [f"Created Contoso Nightly (Custom, Contoso)  {NIGHTLY_IDENTITY_ID}"]

    def test_global_is_a_null_organization(self, fake_client: _FakeClient) -> None:
        fake_client._responses[("POST", "/api/identities")] = {
            **NIGHTLY_IDENTITY,
            "organization_id": None,
            "organization_name": None,
        }
        result = _users(["create", "--identity", "--org", "global", "--name", "Nightly"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [("POST", "/api/identities", {"name": "Nightly", "organization_id": None})]
        assert "(Custom, Global)" in result.output

    def test_json_passes_the_identity_through(self, fake_client: _FakeClient) -> None:
        result = _users(["create", "--identity", "--org", "global", "--name", "Nightly", "--json"])
        assert json.loads(result.output) == NIGHTLY_IDENTITY

    @pytest.mark.parametrize(
        "args",
        [
            ["--org", "Contoso", "--name", "Contoso Nightly"],
            ["--identity", "--name", "Contoso Nightly"],
            ["--identity", "--org", "Contoso"],
        ],
    )
    def test_identity_org_and_name_are_required(self, fake_client: _FakeClient, args: list[str]) -> None:
        result = CliRunner().invoke(users_group, ["create", *args], standalone_mode=False)
        assert result.exit_code != 0
        assert fake_client.calls == []


class TestWorkflowsUpdateRunAs:
    def test_identity_name_is_resolved_to_run_identity_id(self, fake_client: _FakeClient) -> None:
        result = _workflows(["update", "Sync Invoices", "--run-as", "contoso nightly"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [
            ("GET", "/api/workflows", None),
            ("GET", "/api/identities", None),
            ("PATCH", f"/api/workflows/{WORKFLOW_ID}", {"run_identity_id": NIGHTLY_IDENTITY_ID}),
        ]

    def test_identity_uuid_passes_through_without_a_lookup(self, fake_client: _FakeClient) -> None:
        _workflows(["update", WORKFLOW_ID, "--run-as", NIGHTLY_IDENTITY_ID])
        assert fake_client.calls == [
            ("PATCH", f"/api/workflows/{WORKFLOW_ID}", {"run_identity_id": NIGHTLY_IDENTITY_ID}),
        ]

    def test_run_as_default_sends_a_null_identity(self, fake_client: _FakeClient) -> None:
        _workflows(["update", WORKFLOW_ID, "--run-as-default"])
        assert fake_client.calls == [("PATCH", f"/api/workflows/{WORKFLOW_ID}", {"run_identity_id": None})]

    def test_run_as_combines_with_other_flags(self, fake_client: _FakeClient) -> None:
        _workflows(["update", WORKFLOW_ID, "--run-as", NIGHTLY_IDENTITY_ID, "--description", "Nightly sync"])
        assert fake_client.calls == [
            (
                "PATCH",
                f"/api/workflows/{WORKFLOW_ID}",
                {"description": "Nightly sync", "run_identity_id": NIGHTLY_IDENTITY_ID},
            ),
        ]

    def test_run_as_and_run_as_default_are_exclusive(self, fake_client: _FakeClient) -> None:
        result = CliRunner().invoke(
            workflows_group,
            ["update", WORKFLOW_ID, "--run-as", NIGHTLY_IDENTITY_ID, "--run-as-default"],
            standalone_mode=False,
        )
        assert result.exit_code != 0
        assert fake_client.calls == []

    def test_the_raw_id_flag_is_replaced_by_run_as(self) -> None:
        output = CliRunner().invoke(workflows_group, ["update", "--help"]).output
        assert "--run-as" in output
        assert "--run-identity-id" not in output


class TestWorkflowsRecommendations:
    def test_fetches_the_recommendations_and_names_the_identity_roles_and_places(self, fake_client: _FakeClient) -> None:
        result = _workflows(["recommendations", "Sync Invoices"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [
            ("GET", "/api/workflows", None),
            ("GET", f"/api/workflows/{WORKFLOW_ID}/recommended-access", None),
            ("GET", "/api/identities", None),
            ("GET", "/api/roles", None),
            ("GET", "/api/organizations", {"include_inactive": True}),
        ]
        assert result.output.splitlines()[0] == (
            "Based on 12 runs in the last 30 days, Default Identity (Contoso) would also need:"
        )
        assert [line.split() for line in result.output.splitlines()[1:]] == [
            [],
            ["1.", "Reach:", "Fabrikam"],
            ["Switched", "into", "Fabrikam", "in", "3", "runs."],
            ["Grant:", "Helpdesk", "at", "Fabrikam"],
            [],
            ["2.", "Policy", "Role:", "HR"],
            ["A", "policy", "looked", "for", "the", "HR", "role", "in", "5", "runs."],
            ["Grant:", "none", "(choose", "a", "role", "with", "bifrost", "users", "roles", "set)"],
            [],
            ["3.", "Workflow", "Role:", "Helpdesk"],
            ["Only", "Helpdesk", "users", "may", "start", "this", "workflow."],
            ["Grant:", "Helpdesk", "at", "All", "Customer", "Organizations"],
        ]

    def test_an_organization_missing_from_the_list_is_shown_by_its_id(self, fake_client: _FakeClient) -> None:
        fake_client._responses[("GET", "/api/organizations")] = [{"id": ORG_ID, "name": "Contoso"}]
        result = _workflows(["recommendations", WORKFLOW_ID])
        assert result.exit_code == 0, result.output
        assert f"Grant: Helpdesk at {FABRIKAM_ID}" in result.output

    def test_no_observed_runs_says_so_without_further_lookups(self, fake_client: _FakeClient) -> None:
        fake_client._responses[("GET", f"/api/workflows/{WORKFLOW_ID}/recommended-access")] = {
            **RECOMMENDED,
            "observed_runs": 0,
            "items": [],
        }
        result = _workflows(["recommendations", WORKFLOW_ID])
        assert result.output.splitlines() == ["No runs observed yet."]
        assert fake_client.calls == [("GET", f"/api/workflows/{WORKFLOW_ID}/recommended-access", None)]

    def test_runs_observed_with_nothing_missing_says_so(self, fake_client: _FakeClient) -> None:
        fake_client._responses[("GET", f"/api/workflows/{WORKFLOW_ID}/recommended-access")] = {**RECOMMENDED, "items": []}
        assert _workflows(["recommendations", WORKFLOW_ID]).output.splitlines() == ["Nothing missing."]

    def test_requirements_is_now_recommendations(self) -> None:
        commands = workflows_group.list_commands(click.Context(workflows_group))
        assert "recommendations" in commands and "requirements" not in commands
        grant_help = CliRunner().invoke(workflows_group, ["grant", "--help"]).output
        assert "--recommendation" in grant_help and "--requirement" not in grant_help

    def test_json_passes_the_recommendations_through(self, fake_client: _FakeClient) -> None:
        result = _workflows(["recommendations", WORKFLOW_ID, "--json"])
        assert json.loads(result.output) == RECOMMENDED


class TestWorkflowsGrant:
    def test_a_recommendation_is_merged_into_the_identitys_roles(self, fake_client: _FakeClient) -> None:
        result = _workflows(["grant", WORKFLOW_ID, "--recommendation", "1", "--yes"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls == [
            ("GET", f"/api/workflows/{WORKFLOW_ID}/recommended-access", None),
            ("GET", "/api/identities", None),
            ("GET", f"/api/users/{DEFAULT_IDENTITY_ID}/role-assignments", None),
            (
                "PUT",
                f"/api/users/{DEFAULT_IDENTITY_ID}/role-assignments",
                {
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
                },
            ),
        ]

    def test_a_role_the_identity_lacks_is_added(self, fake_client: _FakeClient) -> None:
        fake_client._responses[("GET", f"/api/users/{DEFAULT_IDENTITY_ID}/role-assignments")] = {
            **ASSIGNMENTS,
            "additional": [],
        }
        _workflows(["grant", WORKFLOW_ID, "--recommendation", "1", "--yes"])
        assert fake_client.calls[-1] == (
            "PUT",
            f"/api/users/{DEFAULT_IDENTITY_ID}/role-assignments",
            {
                "base_role_id": USER_ROLE_ID,
                "additional": [
                    {
                        "role_id": HELPDESK_ROLE_ID,
                        "boundaries": [{"kind": "organization", "organization_id": FABRIKAM_ID}],
                    }
                ],
            },
        )

    def test_all_applies_every_grant_in_one_put_and_names_the_skipped_ones(
        self, fake_client: _FakeClient
    ) -> None:
        result = _workflows(["grant", WORKFLOW_ID, "--all", "--yes"])
        assert result.exit_code == 0, result.output
        assert result.stderr.splitlines()[:1] == ["Recommendation 2 needs a role chosen: use bifrost users roles set."]
        puts = [call for call in fake_client.calls if call[0] == "PUT"]
        assert len(puts) == 1
        assert puts[0][2] == {
            "base_role_id": USER_ROLE_ID,
            "additional": [
                {
                    "role_id": HELPDESK_ROLE_ID,
                    "boundaries": [
                        {"kind": "organization", "organization_id": ORG_ID},
                        {"kind": "organization", "organization_id": FABRIKAM_ID},
                        {"kind": "managed_organizations"},
                    ],
                }
            ],
        }

    def test_a_default_identity_needs_yes_and_the_refusal_names_how_many_workflows_share_it(
        self, fake_client: _FakeClient
    ) -> None:
        result = CliRunner().invoke(workflows_group, ["grant", WORKFLOW_ID, "--recommendation", "1"], standalone_mode=False)
        assert result.exit_code != 0
        assert str(result.exception).startswith(
            "Default Identity (Contoso) is shared: this applies to all 4 workflows in Contoso that run as it."
        )
        assert "--yes" in str(result.exception)
        assert [call[0] for call in fake_client.calls] == ["GET", "GET"]

    def test_the_global_default_identity_is_named_by_place(self, fake_client: _FakeClient) -> None:
        fake_client._responses[("GET", "/api/identities")] = [
            {**DEFAULT_IDENTITY, "identity_kind": "global_default", "organization_id": None, "organization_name": None}
        ]
        result = CliRunner().invoke(workflows_group, ["grant", WORKFLOW_ID, "--recommendation", "1"], standalone_mode=False)
        assert str(result.exception).startswith(
            "Default Identity (Global) is shared: this applies to all 4 workflows in Global that run as it."
        )

    def test_a_custom_identity_needs_no_yes(self, fake_client: _FakeClient) -> None:
        fake_client._responses[("GET", f"/api/workflows/{WORKFLOW_ID}/recommended-access")] = {
            **RECOMMENDED,
            "identity_id": NIGHTLY_IDENTITY_ID,
        }
        fake_client._responses[("GET", f"/api/users/{NIGHTLY_IDENTITY_ID}/role-assignments")] = ASSIGNMENTS
        fake_client._responses[("PUT", f"/api/users/{NIGHTLY_IDENTITY_ID}/role-assignments")] = ASSIGNMENTS
        result = _workflows(["grant", WORKFLOW_ID, "--recommendation", "1"])
        assert result.exit_code == 0, result.output
        assert fake_client.calls[-1][:2] == ("PUT", f"/api/users/{NIGHTLY_IDENTITY_ID}/role-assignments")

    def test_a_recommendation_without_a_grant_is_refused_before_any_write(self, fake_client: _FakeClient) -> None:
        result = CliRunner().invoke(
            workflows_group, ["grant", WORKFLOW_ID, "--recommendation", "2"], standalone_mode=False
        )
        assert result.exit_code != 0
        assert "roles set" in str(result.exception)
        assert [call[0] for call in fake_client.calls] == ["GET"]

    def test_a_recommendation_number_out_of_range_is_refused(self, fake_client: _FakeClient) -> None:
        result = CliRunner().invoke(
            workflows_group, ["grant", WORKFLOW_ID, "--recommendation", "4"], standalone_mode=False
        )
        assert result.exit_code != 0
        assert [call[0] for call in fake_client.calls] == ["GET"]

    def test_recommendation_or_all_is_required_but_not_both(self, fake_client: _FakeClient) -> None:
        for args in ([], ["--recommendation", "1", "--all"]):
            result = CliRunner().invoke(workflows_group, ["grant", WORKFLOW_ID, *args], standalone_mode=False)
            assert result.exit_code != 0
        assert fake_client.calls == []

    def test_nothing_to_grant_makes_no_write(self, fake_client: _FakeClient) -> None:
        fake_client._responses[("GET", f"/api/workflows/{WORKFLOW_ID}/recommended-access")] = {
            **RECOMMENDED,
            "items": [RECOMMENDED["items"][1]],
        }
        result = CliRunner().invoke(workflows_group, ["grant", WORKFLOW_ID, "--all"], standalone_mode=False)
        assert result.exit_code != 0
        assert [call[0] for call in fake_client.calls] == ["GET"]

    def test_json_passes_the_new_role_assignments_through(self, fake_client: _FakeClient) -> None:
        result = _workflows(["grant", WORKFLOW_ID, "--recommendation", "1", "--yes", "--json"])
        assert json.loads(result.stdout) == ASSIGNMENTS
