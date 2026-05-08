import importlib
import sys
from unittest.mock import patch


def _reload_version():
    """Re-import version module to reset lru_cache between tests."""
    if "shared.version" in sys.modules:
        del sys.modules["shared.version"]
    import shared.version as v  # type: ignore[import-untyped]
    importlib.reload(v)
    return v


def test_get_version_from_env(monkeypatch):
    monkeypatch.setenv("BIFROST_VERSION", "2.1.0-dev.5+abc1234")
    v = _reload_version()
    assert v.get_version() == "2.1.0-dev.5+abc1234"


def test_get_version_unknown_when_no_env_and_no_git(monkeypatch):
    monkeypatch.delenv("BIFROST_VERSION", raising=False)
    with patch("subprocess.check_output", side_effect=FileNotFoundError):
        v = _reload_version()
        assert v.get_version() == "unknown"


def test_get_version_git_fallback(monkeypatch):
    monkeypatch.delenv("BIFROST_VERSION", raising=False)
    with patch("subprocess.check_output", return_value="v2.0.0-12-gabc1234\n"):
        v = _reload_version()
        assert v.get_version() == "v2.0.0-12-gabc1234"


def test_get_version_accepts_semver_dev_format(monkeypatch):
    """Regression: the new CI format `<X>.<Y>.<Z>-dev.<N>` must round-trip
    through BIFROST_VERSION unchanged."""
    monkeypatch.setenv("BIFROST_VERSION", "0.8.1-dev.47")
    v = _reload_version()
    assert v.get_version() == "0.8.1-dev.47"
