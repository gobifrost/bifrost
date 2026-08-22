"""Tests for the server-controlled CLI floor in ``bifrost.cli._check_cli_version``.

This is the two-signal behavior (supersedes the pure version-string policy
in ``test_cli_version_check.py`` for the cases that changed):

* **Minimum CLI (HARD).** A CLI below ``min_cli_version`` exits.
* **Build drift (SOFT).** The hard gate passes but the build
  ``version`` differs → a one-line stderr notice, deduped per (url, version) via
  a temp-dir marker. Never exits.
* **Legacy ``contract_version``.** Ignored by new CLIs; it remains server-side
  for one release so clients shipped without minimum gating force-upgrade.
* **Un-reachable verdict** (network error / malformed / missing ``version``) →
  visible stderr warning, never exits. (Q2: no more silent ``logger.debug``.)
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import httpx
import pytest


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Reset credentials backend memo and point the notice marker at tmp."""
    from bifrost.credentials import _reset_persistent_backend_for_tests

    _reset_persistent_backend_for_tests()
    # Notice dedupe writes to the OS temp dir; isolate it per test.
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    yield
    _reset_persistent_backend_for_tests()
    import os

    os.environ.pop("BIFROST_API_URL", None)


def _patch_version(monkeypatch, value: str) -> None:
    monkeypatch.setattr("bifrost.__version__", value, raising=False)


def _resp(payload, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", "http://server.example/api/version"),
    )


def _run(monkeypatch, payload, *, installed="1.2.3"):
    """Invoke the gate with a mocked server response; return captured stderr."""
    _patch_version(monkeypatch, installed)
    from bifrost import cli

    stderr = io.StringIO()
    with patch(
        "bifrost.credentials._resolve_url", return_value="http://server.example"
    ), patch("httpx.get", return_value=_resp(payload)), patch("sys.stderr", stderr):
        cli._check_cli_version()
    return stderr.getvalue()


# --------------------------------------------------------------------------- #
# Legacy contract bridge — not a new-CLI runtime policy
# --------------------------------------------------------------------------- #


class TestContractGate:
    def test_contract_mismatch_does_not_override_server_minimum(self, monkeypatch):
        out = _run(
            monkeypatch,
            {
                "version": "1.2.3",
                "min_cli_version": "1.2.2",
                "contract_version": 999,
            },
            installed="1.2.2",
        )
        assert "newer Bifrost CLI is available" in out

    def test_contract_metadata_is_ignored_when_build_matches(self, monkeypatch):
        """Legacy bridge metadata is not a second new-CLI runtime gate."""
        out = _run(
            monkeypatch,
            {"version": "1.2.3", "contract_version": 999},
        )
        assert out == ""


# --------------------------------------------------------------------------- #
# Gate 1 — minimum supported CLI (HARD)
# --------------------------------------------------------------------------- #


class TestMinimumCliGate:
    def test_below_minimum_exits_without_contract_metadata(self, monkeypatch):
        _patch_version(monkeypatch, "1.2.1")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url", return_value="http://server.example"
        ), patch(
            "httpx.get",
            return_value=_resp(
                {
                    "version": "1.2.2",
                    "min_cli_version": "1.2.2",
                }
            ),
        ), pytest.raises(SystemExit) as excinfo:
            cli._check_cli_version()

        assert excinfo.value.code == 1

    def test_matching_stable_build_below_minimum_still_exits(self, monkeypatch):
        _patch_version(monkeypatch, "1.2.1")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url", return_value="http://server.example"
        ), patch(
            "httpx.get",
            return_value=_resp(
                {
                    "version": "1.2.1",
                    "min_cli_version": "1.2.2",
                }
            ),
        ), pytest.raises(SystemExit):
            cli._check_cli_version()

    def test_below_minimum_message_has_upgrade_instructions(
        self, monkeypatch, capsys
    ):
        _patch_version(monkeypatch, "1.2.1")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url", return_value="http://server.example"
        ), patch(
            "httpx.get",
            return_value=_resp(
                {
                    "version": "1.2.2",
                    "min_cli_version": "1.2.2",
                }
            ),
        ), pytest.raises(SystemExit):
            cli._check_cli_version()

        output = capsys.readouterr().err
        assert "minimum supported version 1.2.2" in output
        assert "pipx install --force" in output

    def test_matching_dev_build_is_not_blocked_by_future_stable_floor(
        self, monkeypatch
    ):
        out = _run(
            monkeypatch,
            {
                "version": "1.2.1-dev.16",
                "min_cli_version": "1.2.2",
            },
            installed="1.2.1-dev.16",
        )
        assert out == ""

    def test_at_or_above_minimum_uses_soft_build_drift_notice(self, monkeypatch):
        out = _run(
            monkeypatch,
            {
                "version": "1.2.3",
                "min_cli_version": "1.2.2",
            },
            installed="1.2.2",
        )
        assert "newer Bifrost CLI is available" in out


# --------------------------------------------------------------------------- #
# Gate 2 — build drift (SOFT, deduped)
# --------------------------------------------------------------------------- #


class TestBuildDriftNotice:
    def test_drift_notice_above_minimum(self, monkeypatch):
        """A supported but different build produces a soft notice."""
        out = _run(
            monkeypatch,
            {"version": "1.3.0", "min_cli_version": "1.2.2"},
            installed="1.2.3",
        )
        assert "1.3.0" in out  # mentions the newer server build

    def test_drift_notice_deduped_within_same_version(self, monkeypatch):
        """Second invocation for the same (url, version) is silent."""
        payload = {"version": "1.3.0", "min_cli_version": "1.2.2"}
        first = _run(monkeypatch, payload, installed="1.2.3")
        second = _run(monkeypatch, payload, installed="1.2.3")
        assert first != ""
        assert second == ""

    def test_drift_notice_reshows_for_new_server_version(self, monkeypatch):
        """A new server build version re-triggers the notice."""
        _run(
            monkeypatch,
            {"version": "1.3.0", "min_cli_version": "1.2.2"},
            installed="1.2.3",
        )
        out = _run(
            monkeypatch,
            {"version": "1.4.0", "min_cli_version": "1.2.2"},
            installed="1.2.3",
        )
        assert "1.4.0" in out

    def test_drift_notice_never_exits(self, monkeypatch):
        """Soft notice must not raise SystemExit."""
        _run(
            monkeypatch,
            {"version": "1.3.0", "min_cli_version": "1.2.2"},
            installed="1.2.3",
        )  # no pytest.raises → asserts no SystemExit


# --------------------------------------------------------------------------- #
# Old server (no minimum floor) — build drift remains soft
# --------------------------------------------------------------------------- #


class TestOldServerFallback:
    def test_old_server_warns_not_exits(self, monkeypatch):
        """Server omits min_cli_version → build drift warns, don't block."""
        out = _run(
            monkeypatch,
            {"version": "9.9.9"},  # no contract_version key
            installed="1.2.3",
        )
        assert out != ""  # a visible warning was emitted
        # Did not raise SystemExit (else _run would have propagated it).

    def test_old_server_same_version_silent(self, monkeypatch):
        """Old server but versions happen to match → nothing to warn about."""
        out = _run(
            monkeypatch,
            {"version": "1.2.3"},  # no contract_version, version equal
            installed="1.2.3",
        )
        assert out == ""


# --------------------------------------------------------------------------- #
# Un-reachable verdict — visible warning, no exit (Q2 fix)
# --------------------------------------------------------------------------- #


class TestUnreachableVerdict:
    def test_network_error_warns_not_silent(self, monkeypatch):
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        stderr = io.StringIO()
        with patch(
            "bifrost.credentials._resolve_url", return_value="http://server.example"
        ), patch("httpx.get", side_effect=OSError("network down")), patch(
            "sys.stderr", stderr
        ):
            cli._check_cli_version()  # no SystemExit
        assert stderr.getvalue() != ""  # Q2: not silent

    def test_malformed_response_warns_not_silent(self, monkeypatch):
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        bad = httpx.Response(
            status_code=200,
            content=b"<html>not json</html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "http://server.example/api/version"),
        )
        stderr = io.StringIO()
        with patch(
            "bifrost.credentials._resolve_url", return_value="http://server.example"
        ), patch("httpx.get", return_value=bad), patch("sys.stderr", stderr):
            cli._check_cli_version()  # no SystemExit
        assert stderr.getvalue() != ""

    def test_missing_version_field_warns_not_silent(self, monkeypatch):
        """Server returns neither version nor contract_version → warn."""
        out = _run(monkeypatch, {}, installed="1.2.3")
        assert out != ""

    def test_non_dict_json_warns_not_crashes(self, monkeypatch):
        """Valid JSON that isn't an object (proxy error array/string) must not
        raise — it's an un-reachable verdict, warn and continue."""
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        stderr = io.StringIO()
        with patch(
            "bifrost.credentials._resolve_url", return_value="http://server.example"
        ), patch("httpx.get", return_value=_resp(["unexpected", "array"])), patch(
            "sys.stderr", stderr
        ):
            cli._check_cli_version()  # must NOT raise (no AttributeError/SystemExit)
        assert stderr.getvalue() != ""


# --------------------------------------------------------------------------- #
# Dev/source installs still skip entirely
# --------------------------------------------------------------------------- #


class TestDevInstallSkips:
    def test_unknown_version_skips(self, monkeypatch):
        _patch_version(monkeypatch, "unknown")
        from bifrost import cli

        with patch("httpx.get") as get:
            cli._check_cli_version()
            get.assert_not_called()

    def test_source_marker_skips(self, monkeypatch):
        _patch_version(monkeypatch, "0.0.0+source")
        from bifrost import cli

        with patch("httpx.get") as get:
            cli._check_cli_version()
            get.assert_not_called()
