"""Independent App migration and slug-cutover CLI coverage."""

from __future__ import annotations

import pathlib
from unittest import mock

from click.testing import CliRunner

from bifrost.commands.app import _scaffold, app_group

A_ID = "11111111-1111-1111-1111-111111111111"
B_ID = "22222222-2222-2222-2222-222222222222"


def _response(payload: dict, status: int = 200) -> mock.MagicMock:
    response = mock.MagicMock()
    response.status_code = status
    response.json.return_value = payload
    response.text = str(payload)
    response.raise_for_status.return_value = None
    return response


def _v1_app(root: pathlib.Path) -> pathlib.Path:
    source = root / "legacy"
    (source / "pages").mkdir(parents=True)
    (source / "components").mkdir()
    (source / "pages" / "index.tsx").write_text(
        'import { Button, useState, useWorkflowQuery } from "bifrost";\n'
        "export default function Home() { return null; }\n"
    )
    (source / "_layout.tsx").write_text(
        'import { Outlet } from "react-router-dom";\n'
        "export default function Layout() { return <Outlet/>; }\n"
    )
    return source


def test_migrate_uses_independent_app_lifecycle(tmp_path: pathlib.Path) -> None:
    source = _v1_app(tmp_path)
    destination = tmp_path / "modern"

    def create_project(root: pathlib.Path, **_kwargs):  # type: ignore[no-untyped-def]
        _scaffold(root, "modern")
        return {"id": A_ID}, "modern"

    with (
        mock.patch("bifrost.commands.app._create_project", side_effect=create_project),
        mock.patch("subprocess.run") as run,
    ):
        run.return_value = mock.Mock(returncode=0, stdout=b"", stderr=b"")
        result = CliRunner().invoke(
            app_group,
            ["migrate", str(source), str(destination), "--name", "Modern"],
        )

    assert result.exit_code == 0, result.output
    migrated = (destination / "src" / "pages" / "index.tsx").read_text()
    assert 'from "react"' in migrated
    assert 'from "@/components/ui/button"' in migrated
    assert 'useWorkflowQuery } from "bifrost"' in migrated
    assert (destination / "src" / "_layout.tsx").is_file()
    assert "bifrost app start" in result.output
    assert "bifrost app deploy" in result.output
    assert "bifrost app swap-slugs" in result.output
    assert "none are captured" in result.output
    assert "bifrost solution" not in result.output


def test_app_swap_slugs_resolves_refs_and_posts_atomic_cutover() -> None:
    captured: dict[str, object] = {"gets": []}

    async def get(path: str):
        cast_gets = captured["gets"]
        assert isinstance(cast_gets, list)
        cast_gets.append(path)
        slug = path.rsplit("/", 1)[-1]
        return _response({"id": A_ID if slug == "legacy" else B_ID})

    async def post(path: str, json: dict | None = None):
        captured["post"] = (path, json)
        return _response(
            {
                "applications": [
                    {"name": "Modern", "slug": "legacy"},
                    {"name": "Legacy", "slug": "modern"},
                ]
            }
        )

    client = mock.MagicMock(api_url="https://example.test")
    client.get = get
    client.post = post
    with mock.patch("bifrost.commands.app._client", return_value=client):
        result = CliRunner().invoke(
            app_group, ["swap-slugs", "legacy", "modern"]
        )

    assert result.exit_code == 0, result.output
    assert captured["gets"] == [
        "/api/applications/legacy",
        "/api/applications/modern",
    ]
    assert captured["post"] == (
        "/api/applications/swap-slugs",
        {"app_a": A_ID, "app_b": B_ID},
    )
    assert "/apps/legacy" in result.output
