"""`bifrost solution export` — thin CLI wrapper over POST /api/solutions/{id}/export.

The export is a POST so the full-backup password rides in the request body, not
the URL query string. The full-without-password check still fails at Click
validation time (UsageError) before any HTTP call.

Other tests mock ``BifrostClient.get_instance`` so no network/DB is touched.
"""
from __future__ import annotations

import pathlib
from unittest import mock

from click.testing import CliRunner

from bifrost.commands.solution import solution_group

SOL_ID = "11111111-1111-1111-1111-111111111111"
SOL_SLUG = "my-solution"


def _resp(payload, status=200):
    r = mock.MagicMock()
    r.status_code = status
    r.json.return_value = payload
    r.text = str(payload)
    r.content = b""
    r.headers = {}
    return r


def _zip_resp(data: bytes, filename: str):
    r = mock.MagicMock()
    r.status_code = 200
    r.content = data
    r.headers = {"content-disposition": f'attachment; filename="{filename}"'}
    return r


def _client(captured, *, solutions=None, zip_data=b"PK\x05\x06" + b"\x00" * 18):
    sols = solutions or [{"id": SOL_ID, "slug": SOL_SLUG}]

    async def get(path, **kwargs):  # type: ignore[no-untyped-def]
        captured.setdefault("gets", []).append((path, kwargs))
        if path == "/api/solutions":
            return _resp({"solutions": sols})
        return _resp({})

    async def post(path, **kwargs):  # type: ignore[no-untyped-def]
        captured.setdefault("posts", []).append((path, kwargs))
        if path.endswith("/export"):
            return _zip_resp(zip_data, f"{SOL_SLUG}-0.1.0.zip")
        return _resp({})

    c = mock.AsyncMock()
    c.get = get
    c.post = post
    return c


def test_export_full_requires_password() -> None:
    """--mode full without --password must fail at Click validation (no HTTP)."""
    runner = CliRunner()
    res = runner.invoke(solution_group, ["export", "some-slug", "--mode", "full"])
    assert res.exit_code != 0
    assert "password" in res.output.lower()


def test_export_shareable_by_id(tmp_path: pathlib.Path) -> None:
    """--mode shareable (default) with a UUID ref: GET /api/solutions/<id>/export."""
    captured: dict = {}
    out_file = tmp_path / "out.zip"
    zip_data = b"PK\x05\x06" + b"\x00" * 18

    with mock.patch("bifrost.client.BifrostClient.get_instance",
                    return_value=_client(captured, zip_data=zip_data)):
        res = CliRunner().invoke(
            solution_group,
            ["export", SOL_ID, "--out", str(out_file)],
        )
    assert res.exit_code == 0, res.output
    # Should have hit the export endpoint via POST (id passed directly — no list call)
    posts = captured.get("posts", [])
    export_paths = [p for p, _ in posts if "/export" in p]
    assert export_paths, f"No export POST in {posts}"
    assert out_file.read_bytes() == zip_data


def test_export_shareable_by_slug(tmp_path: pathlib.Path) -> None:
    """When solution_ref is a slug (not a UUID), resolve via GET /api/solutions."""
    captured: dict = {}
    out_file = tmp_path / "out.zip"
    zip_data = b"PK\x05\x06" + b"\x00" * 18

    with mock.patch("bifrost.client.BifrostClient.get_instance",
                    return_value=_client(captured, zip_data=zip_data)):
        res = CliRunner().invoke(
            solution_group,
            ["export", SOL_SLUG, "--out", str(out_file)],
        )
    assert res.exit_code == 0, res.output
    gets = captured.get("gets", [])
    # Must have called list to resolve slug
    list_calls = [p for p, _ in gets if p == "/api/solutions"]
    assert list_calls, f"Expected /api/solutions list call in {gets}"
    posts = captured.get("posts", [])
    export_calls = [p for p, _ in posts if "/export" in p]
    assert export_calls
    assert SOL_ID in export_calls[0]


def test_export_full_with_password(tmp_path: pathlib.Path) -> None:
    """--mode full + --password is accepted; password is forwarded in the POST
    BODY (never the URL query), mode stays in the query."""
    captured: dict = {}
    out_file = tmp_path / "out.zip"

    with mock.patch("bifrost.client.BifrostClient.get_instance",
                    return_value=_client(captured)):
        res = CliRunner().invoke(
            solution_group,
            ["export", SOL_ID, "--mode", "full", "--password", "s3cr3t",
             "--out", str(out_file)],
        )
    assert res.exit_code == 0, res.output
    posts = captured.get("posts", [])
    export_calls = [(p, kw) for p, kw in posts if "/export" in p]
    assert export_calls
    _path, kwargs = export_calls[0]
    params = kwargs.get("params", {})
    body = kwargs.get("json", {})
    assert params.get("mode") == "full"
    assert "password" not in params  # must NOT be in the query string
    assert body.get("password") == "s3cr3t"


def test_install_help_shows_new_flags() -> None:
    """--help on install must list the new flags."""
    res = CliRunner().invoke(solution_group, ["install", "--help"])
    assert res.exit_code == 0, res.output
    assert "--password" in res.output
    assert "--replace-secrets" in res.output
    assert "--replace-data" in res.output
