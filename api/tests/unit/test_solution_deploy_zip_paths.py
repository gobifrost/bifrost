"""Path-boundary tests for the shared Solution deploy zip builder."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import click
import pytest

from bifrost.commands.solution import _build_deploy_zip


def test_deploy_zip_includes_regular_workspace_file(tmp_path: Path) -> None:
    workspace = tmp_path / "solution"
    source = workspace / "workflows" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n")

    archive = _build_deploy_zip(workspace, extra_text_files={})

    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        assert bundle.namelist() == ["workflows/main.py"]
        assert bundle.read("workflows/main.py") == b"VALUE = 1\n"


def test_deploy_zip_rejects_unavailable_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "missing-solution"

    with pytest.raises(
        click.ClickException,
        match="solution workspace is unavailable",
    ):
        _build_deploy_zip(workspace, extra_text_files={})


@pytest.mark.parametrize("target_kind", ["inside", "outside", "directory", "cycle"])
def test_deploy_zip_rejects_unignored_symlink(
    tmp_path: Path,
    target_kind: str,
) -> None:
    workspace = tmp_path / "solution"
    workspace.mkdir()
    outside = tmp_path / "host-secret.txt"
    outside.write_text("must not be packaged")
    inside = workspace / "inside.txt"
    inside.write_text("inside")
    link = workspace / "linked.txt"

    if target_kind == "inside":
        link.symlink_to(inside)
    elif target_kind == "outside":
        link.symlink_to(outside)
    elif target_kind == "directory":
        link.symlink_to(tmp_path, target_is_directory=True)
    else:
        link.symlink_to(workspace, target_is_directory=True)

    with pytest.raises(click.ClickException, match="refusing symlink"):
        _build_deploy_zip(workspace, extra_text_files={})


def test_deploy_zip_does_not_dereference_ignored_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "solution"
    ignored = workspace / "node_modules" / "package-link.js"
    ignored.parent.mkdir(parents=True)
    outside = tmp_path / "host-secret.txt"
    outside.write_text("must not be packaged")
    ignored.symlink_to(outside)
    source = workspace / "README.md"
    source.write_text("safe")

    archive = _build_deploy_zip(workspace, extra_text_files={})

    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        assert bundle.namelist() == ["README.md"]
        assert bundle.read("README.md") == b"safe"


def test_deploy_zip_does_not_dereference_ignored_directory_symlink(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "solution"
    workspace.mkdir()
    outside = tmp_path / "outside-dependencies"
    outside.mkdir()
    (outside / "host-secret.js").write_text("must not be packaged")
    (workspace / "node_modules").symlink_to(
        outside,
        target_is_directory=True,
    )
    source = workspace / "README.md"
    source.write_text("safe")

    archive = _build_deploy_zip(workspace, extra_text_files={})

    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        assert bundle.namelist() == ["README.md"]
        assert bundle.read("README.md") == b"safe"
