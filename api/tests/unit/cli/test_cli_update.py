"""Tests for the self-update CLI command."""

from __future__ import annotations

import pathlib
import subprocess
from unittest.mock import patch

import pytest

from bifrost import cli


def test_update_help_does_not_resolve_or_install(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("bifrost.credentials.resolve_current_connection") as resolve, patch(
        "subprocess.run"
    ) as run:
        rc = cli.handle_update(["--help"])

    assert rc == 0
    assert "Usage: bifrost update" in capsys.readouterr().out
    resolve.assert_not_called()
    run.assert_not_called()


def test_update_requires_url_value(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.handle_update(["--url"]) == 1
    assert "--url requires a value" in capsys.readouterr().err


def test_update_rejects_unknown_option(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.handle_update(["--wat"]) == 1
    assert "Unknown option: --wat" in capsys.readouterr().err


def test_update_resolves_current_connection_and_normalizes_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_update_install_command", lambda url: ["installer", url])
    completed = subprocess.CompletedProcess(["installer"], 0)

    with patch(
        "bifrost.credentials.resolve_current_connection",
        return_value=("https://bifrost.example.com/", "default"),
    ) as resolve, patch("subprocess.run", return_value=completed) as run:
        rc = cli.handle_update([])

    assert rc == 0
    resolve.assert_called_once_with(None, prompt_for_default=True)
    run.assert_called_once_with(
        ["installer", "https://bifrost.example.com/api/cli/download"],
        check=False,
    )


def test_update_url_override_wins() -> None:
    completed = subprocess.CompletedProcess(["installer"], 0)
    with patch(
        "bifrost.credentials.resolve_current_connection",
        return_value=("https://override.example.com", "argument"),
    ) as resolve, patch(
        "bifrost.cli._update_install_command", return_value=["installer"]
    ), patch("subprocess.run", return_value=completed):
        rc = cli.handle_update(["--url", "https://override.example.com/"])

    assert rc == 0
    resolve.assert_called_once_with(
        "https://override.example.com/",
        prompt_for_default=True,
    )


def test_update_errors_without_connection(capsys: pytest.CaptureFixture[str]) -> None:
    with patch(
        "bifrost.credentials.resolve_current_connection",
        return_value=(None, None),
    ), patch("subprocess.run") as run:
        rc = cli.handle_update([])

    assert rc == 1
    assert "no Bifrost connection selected" in capsys.readouterr().err
    run.assert_not_called()


def test_update_uses_pipx_for_pipx_managed_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "pipx_metadata.json").write_text("{}")
    monkeypatch.setattr(cli.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/pipx")

    assert cli._update_install_command("https://example.com/api/cli/download") == [
        "/usr/bin/pipx",
        "install",
        "--force",
        "https://example.com/api/cli/download",
    ]


def test_update_uses_current_python_outside_pipx(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    monkeypatch.setattr(cli.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(cli.sys, "executable", "/venv/bin/python")

    assert cli._update_install_command("https://example.com/api/cli/download") == [
        "/venv/bin/python",
        "-m",
        "pip",
        "install",
        "--upgrade",
        "https://example.com/api/cli/download",
    ]


def test_update_reports_missing_pipx(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "pipx_metadata.json").write_text("{}")
    monkeypatch.setattr(cli.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)

    with patch(
        "bifrost.credentials.resolve_current_connection",
        return_value=("https://example.com", "default"),
    ):
        rc = cli.handle_update([])

    assert rc == 1
    assert "managed by pipx" in capsys.readouterr().err


def test_update_propagates_installer_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    completed = subprocess.CompletedProcess(["installer"], 7)
    with patch(
        "bifrost.credentials.resolve_current_connection",
        return_value=("https://example.com", "default"),
    ), patch(
        "bifrost.cli._update_install_command", return_value=["installer", "package"]
    ), patch("subprocess.run", return_value=completed):
        rc = cli.handle_update([])

    assert rc == 7
    stderr = capsys.readouterr().err
    assert "exit code 7" in stderr
    assert "installer package" in stderr


def test_update_reports_installer_launch_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch(
        "bifrost.credentials.resolve_current_connection",
        return_value=("https://example.com", "default"),
    ), patch(
        "bifrost.cli._update_install_command", return_value=["installer", "package"]
    ), patch("subprocess.run", side_effect=OSError("not found")):
        rc = cli.handle_update([])

    assert rc == 1
    stderr = capsys.readouterr().err
    assert "could not start the installer" in stderr
    assert "installer package" in stderr


def test_main_dispatches_update_without_version_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "_check_cli_version",
        lambda: pytest.fail("update must bypass the compatibility gate"),
    )
    called: list[list[str]] = []
    monkeypatch.setattr(cli, "handle_update", lambda args: called.append(args) or 0)

    assert cli.main(["update", "--url", "https://example.com"]) == 0
    assert called == [["--url", "https://example.com"]]
