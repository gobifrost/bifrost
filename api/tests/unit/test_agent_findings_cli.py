"""Agent findings CLI local contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from bifrost import client as bifrost_client_module
from bifrost import refs as refs_module
from bifrost.commands.agent_findings import agent_findings_group
from shared.models import FindingCreate, FindingUpdate

_AGENT_ID = "11111111-1111-1111-1111-111111111111"
_FINDING_ID = "22222222-2222-2222-2222-222222222222"
_REVIEW_ID = "33333333-3333-3333-3333-333333333333"
_REVIEW_RUN_ID = "44444444-4444-4444-4444-444444444444"


class _FakeResponse:
    def __init__(self, status: int, body: Any):
        self.status_code = status
        self._body = body
        self.request = httpx.Request("GET", "http://test.local")
        self.text = str(body)

    def json(self) -> Any:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error", request=self.request, response=httpx.Response(self.status_code)
            )


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.post_responses: list[_FakeResponse] = []
        self.get_responses: list[_FakeResponse] = []
        self.patch_responses: list[_FakeResponse] = []

    async def post(self, path: str, *, json: dict[str, Any] | None = None, **_kwargs):
        self.calls.append(("POST", path, json))
        if not self.post_responses:
            raise AssertionError(f"Unexpected POST {path}")
        return self.post_responses.pop(0)

    async def get(self, path: str, **kwargs):
        self.calls.append(("GET", path, kwargs.get("params")))
        if not self.get_responses:
            raise AssertionError(f"Unexpected GET {path}")
        return self.get_responses.pop(0)

    async def patch(self, path: str, *, json: dict[str, Any] | None = None, **_kwargs):
        self.calls.append(("PATCH", path, json))
        if not self.patch_responses:
            raise AssertionError(f"Unexpected PATCH {path}")
        return self.patch_responses.pop(0)


@pytest.fixture
def findings_cli(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    fake = _FakeClient()
    monkeypatch.setattr(
        bifrost_client_module.BifrostClient,
        "get_instance",
        classmethod(lambda cls, require_auth=False: fake),
    )

    async def _resolve(self, kind: str, value: str) -> str:
        if kind == "agent":
            return _AGENT_ID
        return value

    monkeypatch.setattr(refs_module.RefResolver, "resolve", _resolve)
    return fake


def _invoke(args: list[str]):
    return CliRunner().invoke(agent_findings_group, args, catch_exceptions=True)


def test_findings_cli_declares_request_dto_fields():
    assert set(FindingCreate.model_fields) == {
        "agent_id",
        "description",
        "expected_behavior",
        "source_kind",
        "source_run_id",
        "source_sequence",
        "external_ref",
        "finding_kind",
        "evidence_markdown",
    }
    assert set(FindingUpdate.model_fields) == {
        "description",
        "expected_behavior",
        "status",
        "finding_kind",
        "evidence_markdown",
    }
    # Provenance is read-only: never a request field or CLI flag.
    for name in ("source_review_id", "source_review_run_id", "source_ordinal"):
        assert name not in FindingCreate.model_fields
        assert name not in FindingUpdate.model_fields


def test_findings_create_payload_from_flags_and_file(
    findings_cli: _FakeClient, tmp_path: Path
):
    body_file = tmp_path / "finding.yaml"
    body_file.write_text("description: from-file\nevidence_markdown: old\n", encoding="utf-8")
    findings_cli.post_responses.append(_FakeResponse(201, {"id": _FINDING_ID}))
    result = _invoke(
        [
            "--json",
            "create",
            "--file",
            str(body_file),
            "--agent",
            "my-agent",
            "--description",
            "from-flags",
            "--kind",
            "opportunity",
            "--evidence-markdown",
            "proof",
        ]
    )
    assert result.exit_code == 0, result.output
    assert findings_cli.calls[0] == (
        "POST",
        "/api/agent-findings",
        {
            "description": "from-flags",
            "evidence_markdown": "proof",
            "agent_id": _AGENT_ID,
            "finding_kind": "opportunity",
        },
    )


def test_findings_cli_malformed_file_is_usage_error(findings_cli: _FakeClient, tmp_path: Path):
    missing = tmp_path / "missing.yaml"
    result = _invoke(["create", "--file", str(missing)])
    assert result.exit_code == 2

    bad = tmp_path / "bad.yaml"
    bad.write_text("- not\n- object\n", encoding="utf-8")
    result = _invoke(["create", "--file", str(bad)])
    assert result.exit_code == 2


def test_findings_cli_local_usage_errors_are_json_stdout_and_exit_two(
    findings_cli: _FakeClient, tmp_path: Path
):
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("description: ok\nbogus: nope\n", encoding="utf-8")
    result = _invoke(["--json", "create", "--file", str(unknown)])
    assert result.exit_code == 2
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"] == "usage_error"
    assert "unknown field" in payload["message"]

    result = _invoke(["--json", "update", _FINDING_ID])
    assert result.exit_code == 2
    assert result.stderr == ""
    assert json.loads(result.stdout)["error"] == "usage_error"


def test_findings_cli_maps_server_validation_to_invocation_error(
    findings_cli: _FakeClient, tmp_path: Path
):
    body_file = tmp_path / "invalid.yaml"
    body_file.write_text("description: x\nfinding_kind: bogus\n", encoding="utf-8")
    findings_cli.post_responses.append(
        _FakeResponse(422, {"detail": "finding_kind invalid"})
    )
    result = _invoke(["--json", "create", "--agent", "my-agent", "--file", str(body_file)])
    assert result.exit_code == 2
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["error"] == "usage_error"
    assert "finding_kind invalid" in payload["message"]


def test_findings_search_list_get_paths(findings_cli: _FakeClient):
    findings_cli.get_responses.append(
        _FakeResponse(200, {"items": [], "total": 0, "limit": 50, "offset": 0})
    )
    result = _invoke(
        [
            "--json",
            "search",
            "--agent",
            "my-agent",
            "--status",
            "open",
            "--kind",
            "opportunity",
            "--review",
            _REVIEW_ID,
            "--review-run",
            _REVIEW_RUN_ID,
            "--q",
            "approval",
            "--limit",
            "10",
        ]
    )
    assert result.exit_code == 0, result.output
    assert findings_cli.calls[-1] == (
        "GET",
        "/api/agent-findings/search",
        {
            "limit": 10,
            "offset": 0,
            "agent_id": _AGENT_ID,
            "status": "open",
            "finding_kind": "opportunity",
            "review_id": _REVIEW_ID,
            "review_run_id": _REVIEW_RUN_ID,
            "q": "approval",
        },
    )

    findings_cli.get_responses.append(_FakeResponse(200, []))
    result = _invoke(["--json", "list", "--agent", "my-agent", "--status", "open"])
    assert result.exit_code == 0, result.output
    assert findings_cli.calls[-1] == (
        "GET",
        "/api/agent-findings",
        {"agent_id": _AGENT_ID, "status": "open"},
    )

    findings_cli.get_responses.append(_FakeResponse(200, {"id": _FINDING_ID}))
    result = _invoke(["--json", "get", _FINDING_ID])
    assert result.exit_code == 0, result.output


def test_findings_update_dismiss_and_clear_evidence(findings_cli: _FakeClient):
    findings_cli.patch_responses.append(
        _FakeResponse(200, {"id": _FINDING_ID, "status": "dismissed"})
    )
    result = _invoke(["--json", "update", _FINDING_ID, "--status", "dismissed"])
    assert result.exit_code == 0, result.output
    assert findings_cli.calls[-1] == (
        "PATCH",
        f"/api/agent-findings/{_FINDING_ID}",
        {"status": "dismissed"},
    )

    findings_cli.patch_responses.append(_FakeResponse(200, {"id": _FINDING_ID}))
    result = _invoke(["--json", "update", _FINDING_ID, "--clear-evidence-markdown"])
    assert result.exit_code == 0, result.output
    assert findings_cli.calls[-1] == (
        "PATCH",
        f"/api/agent-findings/{_FINDING_ID}",
        {"evidence_markdown": None},
    )


def test_findings_cli_has_no_provenance_mutation_flags():
    names: set[str] = set()
    for command in agent_findings_group.commands.values():
        for param in command.params:
            names.add(param.name)
            names.update(param.opts)
    for forbidden in (
        "source_review_id",
        "source-review-id",
        "source_review_run_id",
        "source-review-run-id",
        "source_ordinal",
        "source-ordinal",
    ):
        assert forbidden not in names
