"""The `solution start` Vite child must ride the LOCAL PROXY, not the upstream.

The proxy is where install scope gets injected (?solution=, auth, app header).
Pointing the app bundle's BIFROST_API_URL at the upstream API bypasses that
injection entirely: local workflow edits silently don't run locally, the
install's own tables 404, and declared-location file writes 403 (drive
finding, 2026-07-02). The one authoritative origin for a `solution start`
browser session is the proxy origin.
"""
from bifrost.commands import solution as solution_cmd
from bifrost.commands.solution import _scaffold_api_url, _vite_child_env


class TestScaffoldApiUrl:
    """scaffold-app must never bake the hardcoded localhost:8000 fallback when
    a real URL is knowable — explicit flag > env > the authenticated client."""

    def test_explicit_flag_wins(self, monkeypatch):
        monkeypatch.setenv("BIFROST_API_URL", "http://env:1")
        assert _scaffold_api_url("http://flag:2") == "http://flag:2"

    def test_env_wins_over_client(self, monkeypatch):
        monkeypatch.setenv("BIFROST_API_URL", "http://env:1")
        monkeypatch.setattr(
            solution_cmd.BifrostClient,
            "get_instance",
            staticmethod(lambda require_auth=True: type("C", (), {"api_url": "http://client:3"})()),
        )
        assert _scaffold_api_url(None) == "http://env:1"

    def test_logged_in_client_beats_hardcoded_fallback(self, monkeypatch):
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.setattr(
            solution_cmd.BifrostClient,
            "get_instance",
            staticmethod(lambda require_auth=True: type("C", (), {"api_url": "http://client:3"})()),
        )
        assert _scaffold_api_url(None) == "http://client:3"

    def test_hardcoded_fallback_only_when_logged_out(self, monkeypatch):
        monkeypatch.delenv("BIFROST_API_URL", raising=False)

        def _not_logged_in(require_auth=True):
            raise RuntimeError("Not logged in")

        monkeypatch.setattr(
            solution_cmd.BifrostClient, "get_instance", staticmethod(_not_logged_in)
        )
        assert _scaffold_api_url(None) == "http://localhost:8000"


def test_vite_child_env_points_bundle_at_local_proxy():
    env = _vite_child_env(
        {"PATH": "/usr/bin", "BIFROST_API_URL": "http://upstream:34173"},
        app_id="2a9d06da-cc86-49ff-b3b5-26748c31f73e",
        org_id="",
        proxy_origin="http://127.0.0.1:3777",
        access_token="tok",
    )
    # The bundle-visible API URL is the PROXY, never the upstream.
    assert env["BIFROST_API_URL"] == "http://127.0.0.1:3777"
    assert env["VITE_BIFROST_APP_ID"] == "2a9d06da-cc86-49ff-b3b5-26748c31f73e"
    assert env["VITE_BIFROST_ORG_ID"] == ""
    assert env["BIFROST_ACCESS_TOKEN"] == "tok"
    # Base env is inherited, not replaced.
    assert env["PATH"] == "/usr/bin"
