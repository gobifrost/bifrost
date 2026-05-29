"""Regression tests for running file-sync CLI commands without `.bifrost/`.

`.bifrost/` is import/export-only. Push, sync, watch, and pull should treat
the invocation directory as the workspace root without prompting for or
creating a marker directory.
"""
from __future__ import annotations

import pathlib
from unittest import mock

from bifrost import cli


async def _ok_sync(*_args: object, **_kwargs: object) -> int:
    return 0


def _run_from(path: pathlib.Path, fn, args: list[str]) -> int:  # type: ignore[no-untyped-def]
    with (
        mock.patch("bifrost.client.BifrostClient.get_instance", return_value=mock.Mock()),
        mock.patch("bifrost.cli.BifrostClient.get_instance", return_value=mock.Mock()),
        mock.patch("bifrost.cli._sync_files", side_effect=_ok_sync),
        mock.patch("bifrost.cli._push_with_precheck", side_effect=_ok_sync),
        mock.patch("bifrost.cli.input", side_effect=AssertionError("must not prompt")),
        mock.patch("bifrost.cli.sys.stdin.isatty", return_value=True),
        mock.patch("pathlib.Path.cwd", return_value=path),
    ):
        return fn(args)


def test_push_sync_watch_pull_do_not_require_or_create_dot_bifrost(tmp_path: pathlib.Path) -> None:
    commands = [
        (cli.handle_push, "push"),
        (cli.handle_sync, "sync"),
        (cli.handle_watch, "watch"),
        (cli.handle_pull, "pull"),
    ]

    for handler, name in commands:
        workspace = tmp_path / name
        workspace.mkdir()
        (workspace / "workflow.py").write_text("print('ok')\n")

        assert _run_from(workspace, handler, [str(workspace)]) == 0
        assert not (workspace / ".bifrost").exists()


def test_file_filter_excludes_dot_bifrost(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / "workflows.yaml").write_text("workflows: {}\n")
    (tmp_path / "workflow.py").write_text("print('ok')\n")

    files, skipped = cli._collect_push_files(tmp_path, "")

    assert skipped == 0
    assert "workflow.py" in files
    assert ".bifrost/workflows.yaml" not in files


def test_file_filter_excludes_common_tool_caches(tmp_path: pathlib.Path) -> None:
    for directory in (".pyright", ".mypy_cache", ".pytest_cache", "__pycache__"):
        cache_file = tmp_path / directory / "cache.txt"
        cache_file.parent.mkdir()
        cache_file.write_text("cache\n")
    (tmp_path / "workflow.py").write_text("print('ok')\n")

    files, skipped = cli._collect_push_files(tmp_path, "")

    assert skipped == 0
    assert files.keys() == {"workflow.py"}


def test_subdirectory_push_respects_root_gitignore(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".gitignore").write_text("/apps/my-app/generated/\n*.local\n")
    app_dir = tmp_path / "apps" / "my-app"
    (app_dir / "generated").mkdir(parents=True)
    (app_dir / "generated" / "out.py").write_text("print('generated')\n")
    (app_dir / "settings.local").write_text("secret\n")
    (app_dir / "workflow.py").write_text("print('ok')\n")

    with mock.patch("pathlib.Path.cwd", return_value=tmp_path):
        files, skipped = cli._collect_push_files(app_dir, "apps/my-app")

    assert skipped == 0
    assert files.keys() == {"apps/my-app/workflow.py"}
