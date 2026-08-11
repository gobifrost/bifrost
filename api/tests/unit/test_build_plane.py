"""Shared app-build contract tests."""

from src.services.builder.build_plane import TOOLCHAIN_VERSION, BuildPlaneUnavailable


def test_toolchain_version_is_explicit_and_cache_safe() -> None:
    assert TOOLCHAIN_VERSION == "node20-vite5-v1"


def test_build_plane_unavailable_is_a_specific_error() -> None:
    assert str(BuildPlaneUnavailable("runner not ready")) == "runner not ready"
