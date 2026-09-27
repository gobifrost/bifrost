"""CLI parity for Solution access gates.

`solution create`/`init` accept ``--allow-inbound-access`` (symmetric with
``--allow-outbound-access``) and feed both the descriptor and the create body.
`solution update` PATCHes install-local fields — name, scope, and both access
gates — leaving fields not passed untouched.

These tests mock ``BifrostClient.get_instance`` so no network/DB is touched.
"""
from __future__ import annotations

import pathlib
from contextlib import ExitStack
from unittest import mock

from click.testing import CliRunner

from bifrost.commands.solution import solution_group

SOL = "11111111-1111-1111-1111-111111111111"
ORG = "44444444-4444-4444-4444-444444444444"
SLUG = "mna"


def _fake_response(payload: dict, status: int = 200):
    resp = mock.MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


def _make_client(captured: dict) -> mock.AsyncMock:
    install = {"id": SOL, "slug": SLUG, "organization_id": ORG}

    async def get(path):  # type: ignore[no-untyped-def]
        captured.setdefault("gets", []).append(path)
        if path == "/api/solutions":
            return _fake_response({"solutions": [install]})
        return _fake_response({})

    async def post(path, json=None):  # type: ignore[no-untyped-def]
        captured.setdefault("posts", []).append((path, json))
        return _fake_response({"id": SOL, "slug": json.get("slug"), "organization_id": ORG})

    async def patch(path, json=None):  # type: ignore[no-untyped-def]
        captured.setdefault("patches", []).append((path, json))
        return _fake_response({**install, **(json or {})})

    client = mock.AsyncMock()
    client.organization = {"id": ORG}
    client.get = get
    client.post = post
    client.patch = patch
    return client


def _invoke(args: list[str], captured: dict, *, org_resolved: str | None = None):
    """Invoke a `solution` subcommand with a mocked client + org resolver."""
    client = _make_client(captured)
    with ExitStack() as stack:
        stack.enter_context(
            mock.patch("bifrost.client.BifrostClient.get_instance", return_value=client)
        )
        stack.enter_context(
            mock.patch(
                "bifrost.commands.solution._resolve_install_org",
                new=mock.AsyncMock(return_value=org_resolved),
            )
        )
        return CliRunner().invoke(solution_group, args)


def _workspace(tmp_path: pathlib.Path) -> pathlib.Path:
    (tmp_path / "bifrost.solution.yaml").write_text(
        f"slug: {SLUG}\nname: MNA\nversion: 0.1.0\n"
        "allow_outbound_access: false\nallow_inbound_access: true\n"
    )
    return tmp_path


def test_update_inbound_false(tmp_path: pathlib.Path) -> None:
    captured: dict = {}
    result = _invoke(
        ["update", str(_workspace(tmp_path)), "--no-allow-inbound-access"], captured
    )
    assert result.exit_code == 0, result.output
    assert captured["patches"] == [
        (f"/api/solutions/{SOL}", {"allow_inbound_access": False})
    ]


def test_update_outbound_legacy_alias(tmp_path: pathlib.Path) -> None:
    captured: dict = {}
    result = _invoke(
        ["update", str(_workspace(tmp_path)), "--global-repo-access"], captured
    )
    assert result.exit_code == 0, result.output
    assert captured["patches"][0][1] == {"allow_outbound_access": True}


def test_update_name_and_both_flags(tmp_path: pathlib.Path) -> None:
    captured: dict = {}
    result = _invoke(
        [
            "update",
            str(_workspace(tmp_path)),
            "--name",
            "Renamed",
            "--allow-outbound-access",
            "--no-allow-inbound-access",
        ],
        captured,
    )
    assert result.exit_code == 0, result.output
    assert captured["patches"][0][1] == {
        "name": "Renamed",
        "allow_outbound_access": True,
        "allow_inbound_access": False,
    }


def test_update_global_scope_moves_install(tmp_path: pathlib.Path) -> None:
    captured: dict = {}
    result = _invoke(
        ["update", str(_workspace(tmp_path)), "--global"], captured, org_resolved=None
    )
    assert result.exit_code == 0, result.output
    assert captured["patches"][0][1] == {"organization_id": None}


def test_update_requires_a_field(tmp_path: pathlib.Path) -> None:
    captured: dict = {}
    result = _invoke(["update", str(_workspace(tmp_path))], captured)
    assert result.exit_code == 2
    assert "Nothing to update" in result.output
    assert "patches" not in captured


def test_update_selects_by_solution_ref(tmp_path: pathlib.Path) -> None:
    captured: dict = {}
    result = _invoke(
        ["update", str(_workspace(tmp_path)), "--solution", SOL,
         "--no-allow-inbound-access"],
        captured,
    )
    assert result.exit_code == 0, result.output
    assert captured["patches"][0][0] == f"/api/solutions/{SOL}"


def test_create_writes_inbound_false_to_descriptor_and_body(tmp_path: pathlib.Path) -> None:
    captured: dict = {}
    result = _invoke(
        ["create", str(tmp_path), "--slug", SLUG, "--url", "http://test",
         "--no-allow-inbound-access"],
        captured,
    )
    assert result.exit_code == 0, result.output
    assert "allow_inbound_access: false" in (tmp_path / "bifrost.solution.yaml").read_text()
    _, body = captured["posts"][0]
    assert body["allow_inbound_access"] is False


def test_create_defaults_inbound_true(tmp_path: pathlib.Path) -> None:
    captured: dict = {}
    result = _invoke(
        ["create", str(tmp_path), "--slug", SLUG, "--url", "http://test"], captured
    )
    assert result.exit_code == 0, result.output
    assert "allow_inbound_access: true" in (tmp_path / "bifrost.solution.yaml").read_text()
    _, body = captured["posts"][0]
    assert body["allow_inbound_access"] is True


def test_init_accepts_inbound_flag(tmp_path: pathlib.Path) -> None:
    captured: dict = {}
    result = _invoke(
        ["init", str(tmp_path), "--slug", SLUG, "--url", "http://test",
         "--allow-inbound-access"],
        captured,
    )
    assert result.exit_code == 0, result.output
    _, body = captured["posts"][0]
    assert body["allow_inbound_access"] is True
