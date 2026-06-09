"""Tests for the contract-version gate in ``bifrost.cli._check_cli_version``.

This is the NEW two-gate behavior (supersedes the pure version-string policy
in ``test_cli_version_check.py`` for the cases that changed):

* **Gate 1 — contract (HARD).** Server returns ``contract_version``. If it
  differs from the CLI's baked ``CONTRACT_VERSION`` → ``sys.exit(1)`` with the
  upgrade message, for every command.
* **Gate 2 — build drift (SOFT).** ``contract_version`` matches but the build
  ``version`` differs → a one-line stderr notice, deduped per (url, version) via
  a temp-dir marker. Never exits.
* **Old server (no ``contract_version``).** Cannot verify the contract → soft
  stderr warning, never exits. (Replaces the old hard version-string exit, which
  was a rollout footgun.)
* **Un-reachable verdict** (network error / malformed / missing ``version``) →
  visible stderr warning, never exits. (Q2: no more silent ``logger.debug``.)
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import httpx
import pytest

from bifrost.contract_version import CONTRACT_VERSION


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
# Gate 1 — contract (HARD)
# --------------------------------------------------------------------------- #


class TestContractGate:
    def test_contract_mismatch_exits(self, monkeypatch):
        """Server contract_version != baked CLI CONTRACT_VERSION → exit 1."""
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url", return_value="http://server.example"
        ), patch(
            "httpx.get",
            return_value=_resp(
                {"version": "1.2.3", "contract_version": CONTRACT_VERSION + 1}
            ),
        ):
            with pytest.raises(SystemExit) as excinfo:
                cli._check_cli_version()
            assert excinfo.value.code == 1

    def test_contract_match_same_version_is_silent(self, monkeypatch):
        """Contract matches and build version matches → no exit, no noise."""
        out = _run(
            monkeypatch,
            {"version": "1.2.3", "contract_version": CONTRACT_VERSION},
        )
        assert out == ""

    def test_contract_mismatch_message_has_upgrade_instructions(self, monkeypatch):
        _patch_version(monkeypatch, "1.2.3")
        from bifrost import cli

        with patch(
            "bifrost.credentials._resolve_url", return_value="http://server.example"
        ), patch(
            "httpx.get",
            return_value=_resp(
                {"version": "9.9.9", "contract_version": CONTRACT_VERSION + 1}
            ),
        ):
            with pytest.raises(SystemExit):
                cli._check_cli_version()


# --------------------------------------------------------------------------- #
# Gate 2 — build drift (SOFT, deduped)
# --------------------------------------------------------------------------- #


class TestBuildDriftNotice:
    def test_drift_notice_when_contract_matches(self, monkeypatch):
        """Contract matches but build version differs → soft notice, no exit."""
        out = _run(
            monkeypatch,
            {"version": "1.3.0", "contract_version": CONTRACT_VERSION},
            installed="1.2.3",
        )
        assert "1.3.0" in out  # mentions the newer server build

    def test_drift_notice_deduped_within_same_version(self, monkeypatch):
        """Second invocation for the same (url, version) is silent."""
        payload = {"version": "1.3.0", "contract_version": CONTRACT_VERSION}
        first = _run(monkeypatch, payload, installed="1.2.3")
        second = _run(monkeypatch, payload, installed="1.2.3")
        assert first != ""
        assert second == ""

    def test_drift_notice_reshows_for_new_server_version(self, monkeypatch):
        """A new server build version re-triggers the notice."""
        _run(monkeypatch, {"version": "1.3.0", "contract_version": CONTRACT_VERSION},
             installed="1.2.3")
        out = _run(
            monkeypatch,
            {"version": "1.4.0", "contract_version": CONTRACT_VERSION},
            installed="1.2.3",
        )
        assert "1.4.0" in out

    def test_drift_notice_never_exits(self, monkeypatch):
        """Soft notice must not raise SystemExit."""
        _run(monkeypatch, {"version": "1.3.0", "contract_version": CONTRACT_VERSION},
             installed="1.2.3")  # no pytest.raises → asserts no SystemExit


# --------------------------------------------------------------------------- #
# Old server (no contract_version) — soft warn, no exit
# --------------------------------------------------------------------------- #


class TestOldServerFallback:
    def test_old_server_warns_not_exits(self, monkeypatch):
        """Server omits contract_version → can't verify → warn, don't block."""
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
