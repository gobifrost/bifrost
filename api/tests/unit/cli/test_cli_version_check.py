"""Tests for ``bifrost.cli._check_cli_version``.

The check must:

* Skip silently for source/dev installs (``__version__`` of ``"unknown"`` or
  ``"0.0.0+source"``).
* Resolve the API URL via ``credentials._resolve_url`` so a project
  ``.env`` (loaded by python-dotenv before the call) is honored alongside
  the keyring/JSON store. We use ``_resolve_url`` (not
  ``get_credentials``) because the version check only needs the URL — a
  logged-out CLI with ``BIFROST_API_URL`` set in ``.env`` should still get
  checked, even though it has no tokens yet.
* Compare installed vs. server version with string equality and ``sys.exit(1)``
  when they differ — no warning-and-continue, no escape hatch.
* Treat network/parse failures as best-effort: skip silently, do not block.
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_version_check_state():
    """Keep this file's tests from polluting other test files.

    Two state leaks to clean up:

    * The memoized ``_persistent_backend`` global in ``bifrost.credentials``,
      which downstream credentials tests rely on being unset.
    * ``BIFROST_API_URL`` written to ``os.environ`` by ``python-dotenv`` in
      the dotenv-resolution test — monkeypatch doesn't track it because
      load_dotenv writes to os.environ directly. Subsequent SDK credentials
      tests assume the env var is unset and resolve via the JSON store.
    """
    import os

    from bifrost.credentials import _reset_persistent_backend_for_tests

    _reset_persistent_backend_for_tests()
    yield
    _reset_persistent_backend_for_tests()
    os.environ.pop("BIFROST_API_URL", None)


def _patch_version(monkeypatch, value: str) -> None:
    """Patch ``bifrost.__version__`` for the duration of a test.

    ``_check_cli_version`` does ``from bifrost import __version__`` inside
    its body, so we have to monkeypatch the attribute on the package itself.
    """
    monkeypatch.setattr("bifrost.__version__", value, raising=False)


def _make_url_response(payload: dict) -> object:
    """Return a context-manager-shaped fake for ``urllib.request.urlopen``."""

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            return json.dumps(payload).encode()

    return _Resp()


# --------------------------------------------------------------------------- #
# Skip cases
# --------------------------------------------------------------------------- #


class TestSkipCases:
    def test_skips_when_version_is_unknown(self, monkeypatch):
        """Source/dev installs (no baked version) skip the check entirely."""
        _patch_version(monkeypatch, "unknown")
        from bifrost import cli

        with patch("urllib.request.urlopen") as urlopen:
            cli._check_cli_version()
            urlopen.assert_not_called()

    def test_skips_when_version_is_source_marker(self, monkeypatch):
        """The ``0.0.0+source`` marker from pyproject.toml is also a dev install."""
        _patch_version(monkeypatch, "0.0.0+source")
        from bifrost import cli

        with patch("urllib.request.urlopen") as urlopen:
            cli._check_cli_version()
            urlopen.assert_not_called()

    def test_skips_when_no_credentials(self, monkeypatch):
        """No api_url anywhere → nothing to compare against, return silently."""
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        with patch("bifrost.credentials._resolve_url", return_value=None), \
             patch("urllib.request.urlopen") as urlopen:
            cli._check_cli_version()
            urlopen.assert_not_called()

    def test_skips_on_network_error(self, monkeypatch):
        """A connection failure must not block the user's command."""
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url",
            return_value="https://server.example",
        ), patch("urllib.request.urlopen", side_effect=OSError("network down")):
            cli._check_cli_version()  # must not raise SystemExit

    def test_skips_on_malformed_response(self, monkeypatch):
        """Server returns non-JSON or unexpected shape — best-effort, skip."""
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        class _BadResp:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def read(self):
                return b"<html>not json</html>"

        with patch(
            "bifrost.credentials._resolve_url",
            return_value="https://server.example",
        ), patch("urllib.request.urlopen", return_value=_BadResp()):
            cli._check_cli_version()  # must not raise SystemExit

    def test_skips_when_server_version_missing(self, monkeypatch):
        """Server omits ``version`` — skip rather than block on an empty string."""
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url",
            return_value="https://server.example",
        ), patch(
            "urllib.request.urlopen",
            return_value=_make_url_response({}),
        ):
            cli._check_cli_version()  # must not raise SystemExit


# --------------------------------------------------------------------------- #
# Match / mismatch behavior
# --------------------------------------------------------------------------- #


class TestVersionComparison:
    def test_passes_when_versions_match(self, monkeypatch):
        """Installed and server agree → no exit, no stderr noise."""
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        stderr = io.StringIO()
        with patch(
            "bifrost.credentials._resolve_url",
            return_value="https://server.example",
        ), patch(
            "urllib.request.urlopen",
            return_value=_make_url_response({"version": "1.2.3"}),
        ), patch("sys.stderr", stderr):
            cli._check_cli_version()

        assert stderr.getvalue() == ""

    def test_passes_when_server_prefixes_with_v(self, monkeypatch):
        """``v1.2.3`` from the server should match ``1.2.3`` installed."""
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url",
            return_value="https://server.example",
        ), patch(
            "urllib.request.urlopen",
            return_value=_make_url_response({"version": "v1.2.3"}),
        ):
            cli._check_cli_version()  # no SystemExit

    def test_exits_on_stale_cli(self, monkeypatch, capsys):
        """Mismatch → exit 1 with upgrade message on stderr."""
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url",
            return_value="https://server.example",
        ), patch(
            "urllib.request.urlopen",
            return_value=_make_url_response({"version": "1.3.0"}),
        ):
            with pytest.raises(SystemExit) as excinfo:
                cli._check_cli_version()
            assert excinfo.value.code == 1

        err = capsys.readouterr().err
        assert "1.2.3" in err
        assert "1.3.0" in err
        # Upgrade instructions reference the resolved api_url.
        assert "https://server.example/api/cli/download" in err

    def test_exits_when_server_is_older_too(self, monkeypatch):
        """Policy is ``!=``, not ordering — even a 'newer' CLI exits.

        This is intentional: every CLI is expected to track the deployed server
        exactly. If a user is on a fresher dev build than prod, they should
        downgrade (or pin BIFROST_API_URL to the right server) before running.
        """
        _patch_version(monkeypatch, "2.0.0")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url",
            return_value="https://server.example",
        ), patch(
            "urllib.request.urlopen",
            return_value=_make_url_response({"version": "1.9.9"}),
        ):
            with pytest.raises(SystemExit) as excinfo:
                cli._check_cli_version()
            assert excinfo.value.code == 1

    def test_passes_with_semver_dev_format(self, monkeypatch):
        """Regression: the new CI dev-version format `0.8.1-dev.47` must
        match itself through the strict-equality check, just like any other
        string. The check is format-agnostic — this test pins the new
        format so future refactors don't accidentally introduce format
        validation that breaks it."""
        _patch_version(monkeypatch, "0.8.1-dev.47")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url",
            return_value="https://server.example",
        ), patch(
            "urllib.request.urlopen",
            return_value=_make_url_response({"version": "0.8.1-dev.47"}),
        ):
            cli._check_cli_version()  # no SystemExit

    def test_exits_on_dev_count_mismatch(self, monkeypatch):
        """Regression: two dev builds with different commit counts must
        be treated as different versions, even though they share a base."""
        _patch_version(monkeypatch, "0.8.1-dev.47")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url",
            return_value="https://server.example",
        ), patch(
            "urllib.request.urlopen",
            return_value=_make_url_response({"version": "0.8.1-dev.48"}),
        ):
            with pytest.raises(SystemExit) as excinfo:
                cli._check_cli_version()
            assert excinfo.value.code == 1


# --------------------------------------------------------------------------- #
# URL resolution: .env / env-var path
# --------------------------------------------------------------------------- #


class TestUrlResolution:
    def test_delegates_to_credentials_resolve_url(self, monkeypatch):
        """Version check must delegate URL resolution to credentials._resolve_url.

        ``_resolve_url`` already implements the full chain (env → keyring/JSON
        store) and honors python-dotenv-loaded BIFROST_API_URL. The check
        should not reinvent it. Using ``_resolve_url`` (not
        ``get_credentials``) means a logged-out CLI in an env with
        ``BIFROST_API_URL`` set still gets a version check — we only need the
        URL, not full tokens.
        """
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url",
            return_value="https://from-credentials.example",
        ) as resolve, patch(
            "urllib.request.urlopen",
            return_value=_make_url_response({"version": "1.2.3"}),
        ) as urlopen:
            cli._check_cli_version()
            resolve.assert_called_once()
            # The /api/version GET should target the URL credentials returned.
            url = urlopen.call_args[0][0]
            assert url == "https://from-credentials.example/api/version"

    def test_loads_dotenv_before_resolving(self, monkeypatch, tmp_path):
        """A project ``.env`` containing BIFROST_API_URL is honored.

        ``_check_cli_version`` runs before the rest of the CLI imports
        ``bifrost.client`` (which is where dotenv is normally loaded). The
        check must load dotenv itself so a CWD-local ``.env`` resolves the
        URL just like the rest of the CLI would.
        """
        env_file = tmp_path / ".env"
        env_file.write_text("BIFROST_API_URL=https://from-dotenv.example\n")

        monkeypatch.chdir(tmp_path)
        # Make sure the env var isn't already set in the test process so we
        # know the value got there via dotenv, not inheritance.
        monkeypatch.delenv("BIFROST_API_URL", raising=False)

        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        # No persistent-store credentials should be returned — we want the
        # resolution to land on the env var written by dotenv.
        with patch(
            "bifrost.credentials.get_persistent_backend"
        ) as get_backend, patch(
            "urllib.request.urlopen",
            return_value=_make_url_response({"version": "1.2.3"}),
        ) as urlopen:
            backend = get_backend.return_value
            backend.list_urls.return_value = []
            backend.get.return_value = None
            cli._check_cli_version()

        # urlopen should have been called against the URL from .env.
        assert urlopen.called, "urlopen never called — dotenv URL not resolved"
        url = urlopen.call_args[0][0]
        assert url == "https://from-dotenv.example/api/version"

    def test_cwd_dotenv_overrides_stale_env_url(self, monkeypatch, tmp_path):
        """The current project's ``.env`` wins over a stale inherited URL."""
        env_file = tmp_path / ".env"
        env_file.write_text("BIFROST_API_URL=https://from-current-dotenv.example\n")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("BIFROST_API_URL", "https://from-old-env.example")

        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        with patch(
            "bifrost.credentials.get_persistent_backend"
        ) as get_backend, patch(
            "urllib.request.urlopen",
            return_value=_make_url_response({"version": "1.2.3"}),
        ) as urlopen:
            backend = get_backend.return_value
            backend.list_urls.return_value = []
            backend.get.return_value = None
            cli._check_cli_version()

        url = urlopen.call_args[0][0]
        assert url == "https://from-current-dotenv.example/api/version"
