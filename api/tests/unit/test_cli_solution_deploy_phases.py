"""`bifrost solution deploy` — the progress phases the CLI prints.

Deploy used to go quiet between collecting files and the bundle summary, with
the network-bound vendoring step hidden. These tests pin the visible phases so
that gap stays instrumented:

  Scanning solution files...  ->  found N ... file(s)
  Vendoring shared dependencies...  ->  (vendored M | no shared dependencies)
  Bundle: ...
  Uploading workspace zip...  ->  Deploying install ...

BifrostClient is mocked so no network/DB is touched. Deploying with --global and
the default (vendoring-on) descriptor drives the no-shared-deps branch: the
mocked /api/files/read returns nothing, so vendoring resolves to zero files.
"""
from __future__ import annotations

import pathlib
from unittest import mock

import yaml
from click.testing import CliRunner

from bifrost.commands.solution import solution_group
from bifrost.solution_descriptor import DESCRIPTOR_FILENAME

INSTALL_ID = "33333333-3333-3333-3333-333333333333"


def _resp(payload, status=200):
    r = mock.MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.text = str(payload)
    return r


def _client(captured: dict | None = None):
    async def get(path, **_kwargs):  # type: ignore[no-untyped-def]
        if path == "/api/solutions":
            return _resp({"solutions": []})
        if "/deploy-jobs/" in path:
            return _resp({"status": "succeeded", "error": None, "install_id": INSTALL_ID})
        return _resp({}, status=404)

    async def post(path, json=None, **kwargs):  # type: ignore[no-untyped-def]  # noqa: ARG001
        if captured is not None:
            captured.setdefault("posts", []).append((path, {"json": json, **kwargs}))
        if path == "/api/solutions":
            return _resp({"id": INSTALL_ID}, status=201)
        if path == "/api/files/read":
            # Nothing resolvable in _repo/ -> nothing to vendor.
            return _resp({"content": None}, status=404)
        if path.endswith("/deploy"):
            return _resp({"deploy_job_id": "job-1"}, status=202)
        return _resp({}, status=404)

    c = mock.AsyncMock()
    c.get = get
    c.post = post
    c.organization = {"id": "00000000-0000-0000-0000-000000000000"}
    return c


def _scaffold(tmp_path: pathlib.Path) -> pathlib.Path:
    ws = tmp_path / "sol"
    ws.mkdir()
    (ws / DESCRIPTOR_FILENAME).write_text(
        yaml.safe_dump(
            {
                "slug": "demo",
                "name": "Demo",
                "version": "0.1.0",
                "global_repo_access": False,
            },
            sort_keys=False,
        )
    )
    (ws / "workflows").mkdir()
    (ws / "workflows" / "hello.py").write_text("def run():\n    return 1\n")
    return ws


def _invoke(ws: pathlib.Path, captured: dict | None = None):
    with mock.patch(
        "bifrost.client.BifrostClient.get_instance", return_value=_client(captured)
    ):
        return CliRunner().invoke(
            solution_group, ["deploy", str(ws), "--global"], catch_exceptions=False
        )


def test_deploy_prints_each_phase(tmp_path) -> None:
    result = _invoke(_scaffold(tmp_path))
    assert result.exit_code == 0, result.output
    out = result.output
    # Each phase is announced before its (possibly slow) work runs.
    assert "Scanning solution files..." in out
    assert "found" in out and "python file(s)" in out
    assert "Vendoring shared dependencies..." in out
    assert "Bundle:" in out
    assert "Uploading workspace zip..." in out
    assert "Deploying install" in out


def test_deploy_reports_when_nothing_to_vendor(tmp_path) -> None:
    result = _invoke(_scaffold(tmp_path))
    assert result.exit_code == 0, result.output
    # The vendoring announcement always resolves to a result line, even at zero.
    assert "no shared dependencies to vendor." in result.output


def test_deploy_uploads_workspace_zip_not_json_bundle(tmp_path) -> None:
    captured: dict = {}
    result = _invoke(_scaffold(tmp_path), captured)
    assert result.exit_code == 0, result.output

    deploy_calls = [
        kwargs for path, kwargs in captured["posts"] if path.endswith("/deploy")
    ]
    assert len(deploy_calls) == 1
    call = deploy_calls[0]
    assert call["json"] is None
    assert "files" in call
    upload = call["files"]["file"]
    assert upload[0].endswith(".zip")
    assert upload[2] == "application/zip"

    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(upload[1])) as zf:
        names = set(zf.namelist())
    assert "bifrost.solution.yaml" in names
    assert "workflows/hello.py" in names
