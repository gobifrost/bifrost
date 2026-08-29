import zipfile
from pathlib import Path

import pytest

from src.jobs.platform.application_deploy import _read_source_zip
from src.jobs.platform.base import PlatformJobFailure


def _zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def test_deploy_source_requires_vite_root(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "source.zip", {"src/main.tsx": b"export {}"})
    with pytest.raises(PlatformJobFailure, match="package.json and index.html"):
        _read_source_zip(archive)


def test_deploy_source_is_read_without_persisting_a_tree(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path / "source.zip",
        {"package.json": b"{}", "index.html": b"<div id='root'>", "src/main.tsx": b"x"},
    )
    assert _read_source_zip(archive)["src/main.tsx"] == b"x"
    assert not (tmp_path / "src").exists()
