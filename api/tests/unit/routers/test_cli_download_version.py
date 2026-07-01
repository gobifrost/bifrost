"""CLI download package version normalization."""

from src.routers.cli import _to_pep440


def test_to_pep440_preserves_dev_release_versions() -> None:
    assert _to_pep440("1.0.8-dev.11") == "1.0.8.dev11"
    assert _to_pep440("v1.0.8-dev.11") == "1.0.8.dev11"


def test_to_pep440_preserves_dirty_dev_release_versions() -> None:
    assert _to_pep440("1.0.8-dev.11-dirty") == "1.0.8.dev11+dirty"
