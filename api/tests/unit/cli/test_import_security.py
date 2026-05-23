"""Security regression tests for ``bifrost import`` bundle reads."""

from __future__ import annotations

import base64
import pathlib

import click
import pytest

from bifrost.commands.import_cmd import (
    _collect_code_files,
    _read_manifest_files,
    _validate_bundle_dir,
)


def _mark_symlink(monkeypatch: pytest.MonkeyPatch, symlink_path: pathlib.Path) -> None:
    original = pathlib.Path.is_symlink

    def fake_is_symlink(path: pathlib.Path) -> bool:
        return path == symlink_path or original(path)

    monkeypatch.setattr(pathlib.Path, "is_symlink", fake_is_symlink)


def test_manifest_symlink_is_rejected_before_read(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    bifrost_dir = bundle / ".bifrost"
    bifrost_dir.mkdir(parents=True)
    manifest = bifrost_dir / "workflows.yaml"
    manifest.write_text("token: secret\n", encoding="utf-8")
    _mark_symlink(monkeypatch, manifest)

    validated = _validate_bundle_dir(bundle)

    with pytest.raises(click.ClickException, match="must not be a symlink"):
        _read_manifest_files(validated, drop_cross_env_seeds=False)


def test_code_symlink_is_rejected_before_upload(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "bundle"
    (bundle / ".bifrost").mkdir(parents=True)
    (bundle / ".bifrost/workflows.yaml").write_text("[]\n", encoding="utf-8")
    (bundle / "workflows").mkdir()
    leaked = bundle / "workflows/leak.py"
    leaked.write_text("API_KEY = 'secret'\n", encoding="utf-8")
    _mark_symlink(monkeypatch, leaked)

    with pytest.raises(click.ClickException, match="must not be a symlink"):
        _collect_code_files(bundle)


def test_bundle_directory_symlink_is_rejected(tmp_path: pathlib.Path) -> None:
    real_bundle = tmp_path / "real-bundle"
    (real_bundle / ".bifrost").mkdir(parents=True)
    (real_bundle / ".bifrost/workflows.yaml").write_text("[]\n", encoding="utf-8")
    bundle_link = tmp_path / "bundle-link"
    try:
        bundle_link.symlink_to(real_bundle, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    with pytest.raises(click.ClickException, match="must not be a symlink"):
        _validate_bundle_dir(bundle_link)


def test_regular_bundle_files_are_still_collected(tmp_path: pathlib.Path) -> None:
    bundle = tmp_path / "bundle"
    (bundle / ".bifrost").mkdir(parents=True)
    (bundle / ".bifrost/workflows.yaml").write_text("[]\n", encoding="utf-8")
    (bundle / "workflows").mkdir()
    (bundle / "workflows/ok.py").write_text("def run(): pass\n", encoding="utf-8")

    files = _collect_code_files(bundle)

    assert base64.b64decode(files["workflows/ok.py"]).decode("utf-8").replace(
        "\r\n", "\n"
    ) == "def run(): pass\n"
