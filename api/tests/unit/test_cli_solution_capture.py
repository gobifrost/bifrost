"""`bifrost solution capture` — thin CLI wrapper over the capture REST surface.

A migration skill can't drive the React capture dialog, so the CLI mirrors it:
- ``--dry-run`` POSTs ``/capture/preview`` and prints the forward closure
  (``pulled_in``) plus reverse-reference warnings (``outside_references``).
- apply POSTs ``/capture`` and prints the captured counts.

Selectors accept entity NAMES or ids — names resolve against
``GET /capture/candidates`` (the same loose universe the dialog lists). These
tests mock ``BifrostClient.get_instance`` so no network/DB is touched.
"""
from __future__ import annotations

from unittest import mock

from click.testing import CliRunner

from bifrost.commands.solution import solution_group

SOL = "11111111-1111-1111-1111-111111111111"
WF_ID = "22222222-2222-2222-2222-222222222222"
TBL_ID = "33333333-3333-3333-3333-333333333333"


def _fake_response(payload: dict, status: int = 200):
    resp = mock.MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


_CANDIDATES = {
    "workflows": [{"id": WF_ID, "name": "sync_orders", "path": "workflows/sync.py",
                   "function_name": "run"}],
    "tables": [{"id": TBL_ID, "name": "orders"}],
    "apps": [], "forms": [], "agents": [], "claims": [], "configs": [],
}

_PREVIEW = {
    "pulled_in": [
        {"kind": "table", "ref": TBL_ID, "name": "orders", "in_selection": False},
    ],
    "outside_references": [
        {"referencer_kind": "workflow", "referencer_ref": "x", "referencer_name": "nightly_sync",
         "target_kind": "table", "target_ref": TBL_ID, "target_name": "orders"},
    ],
    "scan_is_static": True,
}


def _make_client(captured: dict) -> mock.AsyncMock:
    async def get(path):  # type: ignore[no-untyped-def]
        captured.setdefault("gets", []).append(path)
        if path.endswith("/capture/candidates"):
            return _fake_response(_CANDIDATES)
        return _fake_response({})

    async def post(path, json=None):  # type: ignore[no-untyped-def]
        captured.setdefault("posts", []).append((path, json))
        if path.endswith("/capture/preview"):
            return _fake_response(_PREVIEW)
        if path.endswith("/capture"):
            return _fake_response({
                "solution_id": SOL, "workflows_captured": 1, "tables_captured": 1,
                "apps_captured": 0, "forms_captured": 0, "agents_captured": 0,
                "claims_captured": 0, "config_declarations_captured": 0,
            })
        return _fake_response({})

    client = mock.AsyncMock()
    client.get = get
    client.post = post
    return client


def _invoke(args: list[str], captured: dict):
    client = _make_client(captured)
    with mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client):
        return CliRunner().invoke(solution_group, ["capture", *args])


def test_dry_run_previews_without_applying() -> None:
    captured: dict = {}
    result = _invoke([SOL, "--workflow", "sync_orders", "--dry-run"], captured)
    assert result.exit_code == 0, result.output
    # Only the preview endpoint is POSTed — never the apply endpoint.
    posted_paths = [p for p, _ in captured["posts"]]
    assert f"/api/solutions/{SOL}/capture/preview" in posted_paths
    assert f"/api/solutions/{SOL}/capture" not in posted_paths
    # The workflow NAME resolved to its id in the preview body.
    body = next(b for p, b in captured["posts"] if p.endswith("/preview"))
    assert body["workflows"] == [WF_ID]
    # Output surfaces both the pulled-in dependency and the outside warning.
    assert "orders" in result.output
    assert "nightly_sync" in result.output


def test_apply_captures_and_reports_counts() -> None:
    captured: dict = {}
    result = _invoke([SOL, "--workflow", WF_ID, "--table", "orders"], captured)
    assert result.exit_code == 0, result.output
    posted_paths = [p for p, _ in captured["posts"]]
    assert f"/api/solutions/{SOL}/capture" in posted_paths
    body = next(b for p, b in captured["posts"] if p.endswith(f"{SOL}/capture"))
    assert body["workflows"] == [WF_ID]
    assert body["tables"] == [TBL_ID]
    assert "1 workflow" in result.output


def test_include_imports_flag_forwarded() -> None:
    captured: dict = {}
    result = _invoke([SOL, "--workflow", WF_ID, "--include-imports"], captured)
    assert result.exit_code == 0, result.output
    body = next(b for p, b in captured["posts"] if p.endswith(f"{SOL}/capture"))
    assert body["include_imports"] is True


def test_unknown_name_errors_cleanly() -> None:
    captured: dict = {}
    result = _invoke([SOL, "--workflow", "does_not_exist", "--dry-run"], captured)
    assert result.exit_code != 0
    assert "does_not_exist" in result.output


def test_no_selectors_errors() -> None:
    captured: dict = {}
    result = _invoke([SOL], captured)
    assert result.exit_code != 0
    assert "no entities" in result.output.lower() or "at least one" in result.output.lower()
