"""CLI build-time artifact generation."""

import tarfile
from pathlib import Path

from shared.cli_artifact import build_cli_artifact, cli_artifact_filename, to_pep440


def test_to_pep440_preserves_dev_release_versions() -> None:
    assert to_pep440("1.0.8-dev.11") == "1.0.8.dev11"
    assert to_pep440("v1.0.8-dev.11") == "1.0.8.dev11"


def test_to_pep440_preserves_dirty_dev_release_versions() -> None:
    assert to_pep440("1.0.8-dev.11-dirty") == "1.0.8.dev11+dirty"


def test_build_cli_artifact_stamps_and_filters_package(tmp_path: Path) -> None:
    source_dir = Path(__file__).resolve().parents[3] / "bifrost"

    artifact = build_cli_artifact(source_dir, tmp_path, "v1.2.3")

    assert artifact.name == cli_artifact_filename("v1.2.3")
    with tarfile.open(artifact, mode="r:gz") as archive:
        names = archive.getnames()
        pyproject = archive.extractfile("pyproject.toml")
        init = archive.extractfile("bifrost/__init__.py")
        assert pyproject is not None
        assert init is not None
        assert 'version = "1.2.3"' in pyproject.read().decode()
        assert '__version__ = "v1.2.3"' in init.read().decode()

    assert "bifrost/lucide_icon_names.json" in names
    assert "bifrost/_write_buffer.py" not in names
    assert "bifrost/_logging.py" not in names


def test_build_cli_artifact_is_deterministic(tmp_path: Path) -> None:
    source_dir = Path(__file__).resolve().parents[3] / "bifrost"
    first = build_cli_artifact(source_dir, tmp_path / "first", "debug")
    second = build_cli_artifact(source_dir, tmp_path / "second", "debug")

    assert first.read_bytes() == second.read_bytes()
