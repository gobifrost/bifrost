"""Tests for ``bifrost.cli._check_cli_version`` — URL resolution and transport.

The *behavioral* contract of the gate (contract-version hard gate, build-drift
soft notice, old-server fallback, un-reachable warning) lives in
``test_cli_contract_gate.py``. This file keeps the still-valid cross-cutting
concerns the gate must honor regardless of which gate fires:

* Skip silently for source/dev installs (``__version__`` of ``"unknown"`` or
  ``"0.0.0+source"``).
* Resolve the API URL via ``credentials._resolve_url`` so a project
  ``.env`` (loaded by python-dotenv before the call) is honored alongside
  the keyring/JSON store. We use ``_resolve_url`` (not ``get_credentials``)
  because the version check only needs the URL — a logged-out CLI with
  ``BIFROST_API_URL`` set in ``.env`` should still get checked.
* Route the request through httpx (not urllib) so CDN/WAF UA blocking doesn't
  silently no-op the check.

Note: network/parse/missing-version failures now emit a visible stderr warning
(not a silent skip) — see ``test_cli_contract_gate.py::TestUnreachableVerdict``.
The skip-case tests below only assert "does not raise SystemExit"; they no
longer assert silence.
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import httpx
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


def _make_url_response(payload: dict, status_code: int = 200) -> httpx.Response:
    """Return an httpx.Response matching what ``httpx.get`` would yield.

    ``request=`` must be set or ``raise_for_status()`` raises a
    ``RuntimeError`` instead of evaluating the status code.
    """
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "http://test.example/api/version"),
    )


# --------------------------------------------------------------------------- #
# Skip cases
# --------------------------------------------------------------------------- #


class TestSkipCases:
    def test_skips_when_version_is_unknown(self, monkeypatch):
        """Source/dev installs (no baked version) skip the check entirely."""
        _patch_version(monkeypatch, "unknown")
        from bifrost import cli

        with patch("httpx.get") as urlopen:
            cli._check_cli_version()
            urlopen.assert_not_called()

    def test_skips_when_version_is_source_marker(self, monkeypatch):
        """The ``0.0.0+source`` marker from pyproject.toml is also a dev install."""
        _patch_version(monkeypatch, "0.0.0+source")
        from bifrost import cli

        with patch("httpx.get") as urlopen:
            cli._check_cli_version()
            urlopen.assert_not_called()

    def test_skips_when_no_credentials(self, monkeypatch):
        """No api_url anywhere → nothing to compare against, return silently."""
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        with patch("bifrost.credentials._resolve_url", return_value=None), \
             patch("httpx.get") as urlopen:
            cli._check_cli_version()
            urlopen.assert_not_called()

    def test_skips_on_network_error(self, monkeypatch):
        """A connection failure must not block the user's command."""
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url",
            return_value="http://server.example",
        ), patch("httpx.get", side_effect=OSError("network down")):
            cli._check_cli_version()  # must not raise SystemExit

    def test_skips_on_malformed_response(self, monkeypatch):
        """Server returns non-JSON or unexpected shape — best-effort, skip."""
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        bad = httpx.Response(
            status_code=200,
            content=b"<html>not json</html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "http://server.example/api/version"),
        )
        with patch(
            "bifrost.credentials._resolve_url",
            return_value="http://server.example",
        ), patch("httpx.get", return_value=bad):
            cli._check_cli_version()  # must not raise SystemExit

    def test_skips_when_server_version_missing(self, monkeypatch):
        """Server omits ``version`` — skip rather than block on an empty string."""
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url",
            return_value="http://server.example",
        ), patch(
            "httpx.get",
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
            return_value="http://server.example",
        ), patch(
            "httpx.get",
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
            return_value="http://server.example",
        ), patch(
            "httpx.get",
            return_value=_make_url_response({"version": "v1.2.3"}),
        ):
            cli._check_cli_version()  # no SystemExit

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
            return_value="http://server.example",
        ), patch(
            "httpx.get",
            return_value=_make_url_response({"version": "0.8.1-dev.47"}),
        ):
            cli._check_cli_version()  # no SystemExit

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
            return_value="http://from-credentials.example",
        ) as resolve, patch(
            "httpx.get",
            return_value=_make_url_response({"version": "1.2.3"}),
        ) as urlopen:
            cli._check_cli_version()
            resolve.assert_called_once()
            # The /api/version GET should target the URL credentials returned.
            url = urlopen.call_args[0][0]
            assert url == "http://from-credentials.example/api/version"

    def test_loads_dotenv_before_resolving(self, monkeypatch, tmp_path):
        """A project ``.env`` containing BIFROST_API_URL is honored.

        ``_check_cli_version`` runs before the rest of the CLI imports
        ``bifrost.client`` (which is where dotenv is normally loaded). The
        check must load dotenv itself so a CWD-local ``.env`` resolves the
        URL just like the rest of the CLI would.
        """
        env_file = tmp_path / ".env"
        env_file.write_text("BIFROST_API_URL=http://from-dotenv.example\n")

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
            "httpx.get",
            return_value=_make_url_response({"version": "1.2.3"}),
        ) as urlopen:
            backend = get_backend.return_value
            backend.list_urls.return_value = []
            backend.get.return_value = None
            cli._check_cli_version()

        # httpx.get should have been called against the URL from .env.
        assert urlopen.called, "httpx.get never called — dotenv URL not resolved"
        url = urlopen.call_args[0][0]
        assert url == "http://from-dotenv.example/api/version"


class TestTransport:
    def test_uses_httpx_not_urllib(self, monkeypatch):
        """Regression: the request must go through httpx, not urllib.

        urllib's default ``Python-urllib/X.Y`` User-Agent is blocked with
        HTTP 403 by CDNs/WAFs in front of production Bifrost instances
        (Cloudflare on bifrost.gocovi.com is the live case). The 403 was
        swallowed by the best-effort except clause, so the entire version
        check silently no-op'd in prod. httpx's UA gets through and matches
        what every other SDK request already sends.

        Patch both transports — only the httpx mock should be hit.
        """
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url",
            return_value="http://example.com",
        ), patch(
            "httpx.get",
            return_value=_make_url_response({"version": "1.2.3"}),
        ) as httpx_get, patch("urllib.request.urlopen") as urlopen:
            cli._check_cli_version()

        assert httpx_get.called, "version check did not route through httpx"
        urlopen.assert_not_called()
